using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using LoopbackRecorder.Interop;

namespace LoopbackRecorder
{
    public enum CaptureSource { System, IncludeProcess, ExcludeProcess }
    public enum OutputFormat { Wav, Flac }

    public sealed class CaptureOptions
    {
        public CaptureSource Source;
        public uint ProcessId;               // 进程模式下的目标（进程树的根）
        public OutputFormat Format;
        public int BitsPerSample = 16;       // 16 或 24
        public string Path;
    }

    public sealed class AudioHResultException : Exception
    {
        public AudioHResultException(string message, int hr) : base(message) { HResult = hr; }
    }

    /// <summary>
    /// WASAPI 环回录制：整机声音，或只录/排除某个进程树的声音。
    /// 采集线程只负责取数据和格式转换，编码与写盘在独立线程进行，磁盘卡顿不会造成丢音。
    /// </summary>
    public sealed class LoopbackCapture : IDisposable
    {
        const int AUDCLNT_E_DEVICE_INVALIDATED = unchecked((int)0x88890004);
        const int AUDCLNT_E_SERVICE_NOT_RUNNING = unchecked((int)0x88890010);

        readonly CaptureOptions _opt;
        Thread _thread, _writerThread;
        volatile bool _stop;
        readonly ManualResetEvent _ready = new ManualResetEvent(false);
        Exception _startError;
        volatile Exception _writerError;
        IAudioWriter _writer;
        readonly BlockingCollection<Chunk> _queue = new BlockingCollection<Chunk>();
        readonly ConcurrentBag<int[]> _pool = new ConcurrentBag<int[]>();
        long _frames;
        Process _target;

        struct Chunk { public int[] Data; public int Frames; }

        public event Action<Exception> Failed;     // 录制途中出错（录制线程上触发），已录部分会保存
        public event Action TargetExited;          // 被录程序退出（录制线程上触发）
        public event Action<string> Notice;        // 状态提示，例如“设备已切换”
        public Action<int[], int> Tap;             // 可选：每批采样（自检用）

        public float Peak;                         // 最近一段的峰值 0..1，供界面电平表使用
        public volatile bool Paused;               // 暂停：丢弃采集到的数据，也不补静音
        /// <summary>已录下的帧数（含补的静音），用于显示时长。</summary>
        public long FramesWritten { get { return Interlocked.Read(ref _frames); } }
        public int SampleRate { get; private set; }
        public int Channels { get; private set; }
        public int PendingChunks { get { return _queue.Count; } }

        public LoopbackCapture(CaptureOptions options) { _opt = options; }

        public void Start()
        {
            if (_opt.Source != CaptureSource.System)
            {
                try { _target = Process.GetProcessById((int)_opt.ProcessId); }
                catch (ArgumentException) { throw new InvalidOperationException("目标程序已经退出（PID " + _opt.ProcessId + "）。"); }
            }
            _thread = new Thread(Run) { IsBackground = true, Name = "LoopbackCapture", Priority = ThreadPriority.Highest };
            _thread.SetApartmentState(ApartmentState.MTA);
            _thread.Start();
            _ready.WaitOne();
            if (_startError != null) { _thread.Join(); throw _startError; }
        }

        public void Stop()
        {
            _stop = true;
            if (_thread != null) _thread.Join();
        }

        public void Dispose()
        {
            Stop();
            _ready.Close();
            if (_target != null) _target.Dispose();
        }

        // ------------------------------------------------------------------

        sealed class Stream : IDisposable
        {
            public IAudioClient Client;
            public IAudioCaptureClient Capture;
            public SampleFormat Format;
            public AutoResetEvent Event = new AutoResetEvent(false);
            public bool Started;

            public void Dispose()
            {
                try { if (Started) Client.Stop(); } catch { }
                if (Capture != null) { try { Marshal.ReleaseComObject(Capture); } catch { } }
                if (Client != null) { try { Marshal.ReleaseComObject(Client); } catch { } }
                Event.Close();
            }
        }

        bool _deviceLoopback;   // 进程环回不可用（旧系统）时退回设备环回

        Stream Open()
        {
            if (_opt.Source == CaptureSource.System && !_deviceLoopback)
            {
                // “整个系统声音”优先用进程环回“排除本进程”：直接拿到所有程序混音前的声音，
                // 不受播放设备静音/独占/切换影响。旧系统上失败时再用传统的设备环回。
                try
                {
                    var ps = OpenProcessLoopback((uint)Process.GetCurrentProcess().Id, false);
                    Raise(Notice, "采集方式：所有程序的声音（进程环回）");
                    return ps;
                }
                catch (Exception ex)
                {
                    _deviceLoopback = true;
                    Raise(Notice, "进程环回不可用，改用设备环回：" + ex.Message);
                }
            }
            if (_opt.Source == CaptureSource.System) return OpenDeviceLoopback();
            return OpenProcessLoopback(_opt.ProcessId, _opt.Source == CaptureSource.IncludeProcess);
        }

        Stream OpenDeviceLoopback()
        {
            var s = new Stream();
            IntPtr fmtPtr = IntPtr.Zero;
            try
            {
                s.Client = ActivateDefaultRenderClient();
                Check(s.Client.GetMixFormat(out fmtPtr), "GetMixFormat");
                s.Format = SampleFormat.FromWaveFormat(fmtPtr);
                Check(s.Client.Initialize(0,
                    Native.AUDCLNT_STREAMFLAGS_LOOPBACK | Native.AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
                    2000000, 0, fmtPtr, IntPtr.Zero), "IAudioClient.Initialize");
                return Finish(s);
            }
            catch { s.Dispose(); throw; }
            finally { if (fmtPtr != IntPtr.Zero) Native.CoTaskMemFree(fmtPtr); }
        }

        Stream OpenProcessLoopback(uint pid, bool include)
        {
            var s = new Stream();
            IntPtr fmtPtr = IntPtr.Zero;
            try
            {
                int rate = SampleRate > 0 ? SampleRate : GetDefaultMixRate();
                int ch = Channels > 0 ? Channels : 2;
                s.Format = new SampleFormat { Float = true, BitsPerSample = 32, ValidBits = 32, Channels = ch, SampleRate = rate };
                fmtPtr = s.Format.ToWaveFormatEx();
                s.Client = ActivateProcessLoopbackClient(pid, include);
                Check(s.Client.Initialize(0,
                    Native.AUDCLNT_STREAMFLAGS_LOOPBACK | Native.AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
                    Native.AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | Native.AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                    2000000, 0, fmtPtr, IntPtr.Zero), "IAudioClient.Initialize");
                return Finish(s);
            }
            catch { s.Dispose(); throw; }
            finally { if (fmtPtr != IntPtr.Zero) Marshal.FreeHGlobal(fmtPtr); }
        }

        Stream Finish(Stream s)
        {
            Check(s.Client.SetEventHandle(s.Event.SafeWaitHandle.DangerousGetHandle()), "SetEventHandle");
            var iid = Native.IID_IAudioCaptureClient;
            object svc;
            Check(s.Client.GetService(ref iid, out svc), "GetService(IAudioCaptureClient)");
            s.Capture = (IAudioCaptureClient)svc;
            Check(s.Client.Start(), "IAudioClient.Start");
            s.Started = true;
            return s;
        }

        void Run()
        {
            Stream stream = null;
            try
            {
                stream = Open();
                SampleRate = stream.Format.SampleRate;
                Channels = stream.Format.Channels;

                _writer = _opt.Format == OutputFormat.Flac
                    ? (IAudioWriter)new FlacWriter(_opt.Path, SampleRate, Channels, _opt.BitsPerSample)
                    : new WavWriter(_opt.Path, SampleRate, Channels, _opt.BitsPerSample);
                _writerThread = new Thread(WriterLoop) { IsBackground = true, Name = "AudioWriter" };
                _writerThread.Start();
                _ready.Set();

                Loop(ref stream);
            }
            catch (Exception ex)
            {
                if (!_ready.WaitOne(0)) { _startError = ex; _ready.Set(); }
                else { var h = Failed; if (h != null) h(ex); }
            }
            finally
            {
                if (stream != null) stream.Dispose();
                _queue.CompleteAdding();
                if (_writerThread != null) _writerThread.Join();
                try { if (_writer != null) _writer.Dispose(); } catch { }
                if (_startError != null && _opt.Path != null)
                {
                    try { System.IO.File.Delete(_opt.Path); } catch { }
                }
            }
        }

        void WriterLoop()
        {
            foreach (var c in _queue.GetConsumingEnumerable())
            {
                if (_writerError == null)
                {
                    try { _writer.Write(c.Data, c.Frames); }
                    catch (Exception ex) { _writerError = ex; }
                }
                _pool.Add(c.Data);
            }
        }

        int[] Rent(int n)
        {
            int[] a;
            if (_pool.TryTake(out a) && a.Length >= n) return a;
            return new int[Math.Max(n, 48000 * Math.Max(2, Channels) / 10)];
        }

        void Enqueue(int[] data, int frames)
        {
            var tap = Tap; if (tap != null) tap(data, frames);
            Interlocked.Add(ref _frames, frames);
            _queue.Add(new Chunk { Data = data, Frames = frames });
        }

        void Loop(ref Stream stream)
        {
            var clock = Stopwatch.StartNew();
            double lastPacket = 0, lastTargetCheck = 0, pausedTotal = 0, pauseStart = -1;
            int bps = _opt.BitsPerSample;
            var conv = new Converter();

            while (!_stop)
            {
                if (_writerError != null) throw new System.IO.IOException("写入文件失败：" + _writerError.Message, _writerError);

                bool gotPacket = false;
                float peak = 0;
                if (stream != null)
                {
                    stream.Event.WaitOne(50);
                    try
                    {
                        uint next;
                        while (true)
                        {
                            Check(stream.Capture.GetNextPacketSize(out next), "GetNextPacketSize");
                            if (next == 0) break;

                            IntPtr data; uint frames, flags; ulong devPos, qpc;
                            Check(stream.Capture.GetBuffer(out data, out frames, out flags, out devPos, out qpc), "GetBuffer");
                            if (Paused)
                            {
                                stream.Capture.ReleaseBuffer(frames);
                                gotPacket = true;
                                continue;
                            }
                            int n = (int)frames * Channels;
                            int[] buf = Rent(n);
                            if ((flags & Native.AUDCLNT_BUFFERFLAGS_SILENT) != 0 || data == IntPtr.Zero)
                                Array.Clear(buf, 0, n);
                            else
                                peak = Math.Max(peak, conv.Convert(stream.Format, data, n, buf, bps));
                            stream.Capture.ReleaseBuffer(frames);
                            Enqueue(buf, (int)frames);
                            gotPacket = true;
                        }
                    }
                    catch (AudioHResultException ex)
                    {
                        if (ex.HResult != AUDCLNT_E_DEVICE_INVALIDATED && ex.HResult != AUDCLNT_E_SERVICE_NOT_RUNNING) throw;
                        // 播放设备被拔掉/切换：关掉旧流，下面按时钟补静音，同时尝试重新连接
                        stream.Dispose();
                        stream = null;
                        Raise(Notice, "播放设备已变化，正在重新连接…");
                    }
                }
                else
                {
                    Thread.Sleep(200);
                    try
                    {
                        var s = Open();
                        if (s.Format.SampleRate != SampleRate || s.Format.Channels != Channels)
                        {
                            s.Dispose();
                            throw new NotSupportedException(string.Format(
                                "新的播放设备格式（{0} Hz / {1} 声道）和录音开始时（{2} Hz / {3} 声道）不同，无法接着录进同一个文件。",
                                s.Format.SampleRate, s.Format.Channels, SampleRate, Channels));
                        }
                        stream = s;
                        Raise(Notice, "已切换到新的播放设备，继续录制");
                    }
                    catch (AudioHResultException) { /* 还没有可用设备，稍后再试 */ }
                }

                double now = clock.Elapsed.TotalSeconds;
                if (Paused && pauseStart < 0) pauseStart = now;
                else if (!Paused && pauseStart >= 0) { pausedTotal += now - pauseStart; pauseStart = -1; lastPacket = now; }
                if (gotPacket || Paused) lastPacket = now;
                else
                {
                    // 环回流在没有声音播放时不送数据；按时钟补静音，让录音长度与真实时间一致。
                    long expected = (long)((now - pausedTotal) * SampleRate);
                    long behind = expected - FramesWritten;
                    if (now - lastPacket >= 0.1 && behind > SampleRate / 20)
                    {
                        int frames = (int)Math.Min(behind, SampleRate / 2);
                        int[] buf = Rent(frames * Channels);
                        Array.Clear(buf, 0, frames * Channels);
                        Enqueue(buf, frames);
                    }
                }
                Peak = gotPacket ? peak : 0;

                if (_target != null && now - lastTargetCheck >= 0.5)
                {
                    lastTargetCheck = now;
                    bool exited;
                    try { exited = _target.HasExited; } catch { exited = false; }
                    if (exited)
                    {
                        _target.Dispose(); _target = null;
                        if (_opt.Source == CaptureSource.IncludeProcess) { var h = TargetExited; if (h != null) h(); }
                    }
                }
            }
        }

        static void Raise(Action<string> h, string msg) { if (h != null) h(msg); }

        // ------------------------------------------------------------------ 激活

        static IAudioClient ActivateDefaultRenderClient()
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorCo();
            IMMDevice device;
            Check(enumerator.GetDefaultAudioEndpoint(Native.eRender, Native.eConsole, out device), "找不到默认播放设备");
            var iid = Native.IID_IAudioClient;
            object o;
            Check(device.Activate(ref iid, Native.CLSCTX_ALL, IntPtr.Zero, out o), "IMMDevice.Activate");
            return (IAudioClient)o;
        }

        static int GetDefaultMixRate()
        {
            try
            {
                var c = ActivateDefaultRenderClient();
                IntPtr p;
                if (c.GetMixFormat(out p) == 0)
                {
                    int rate = Marshal.ReadInt32(p, 4);
                    Native.CoTaskMemFree(p);
                    Marshal.ReleaseComObject(c);
                    if (rate >= 8000 && rate <= 384000) return rate;
                }
            }
            catch { }
            return 48000;
        }

        sealed class CompletionHandler : IActivateAudioInterfaceCompletionHandler, IAgileObject
        {
            public readonly ManualResetEvent Done = new ManualResetEvent(false);
            public void ActivateCompleted(IActivateAudioInterfaceAsyncOperation operation) { Done.Set(); }
        }

        static IAudioClient ActivateProcessLoopbackClient(uint pid, bool includeTree)
        {
            // AUDIOCLIENT_ACTIVATION_PARAMS { ActivationType; { TargetProcessId; ProcessLoopbackMode } }
            IntPtr prm = Marshal.AllocHGlobal(12);
            Marshal.WriteInt32(prm, 0, 1);                        // AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK
            Marshal.WriteInt32(prm, 4, (int)pid);
            Marshal.WriteInt32(prm, 8, includeTree ? 0 : 1);      // INCLUDE_TARGET_PROCESS_TREE / EXCLUDE_...

            // PROPVARIANT { VT_BLOB, BLOB { cbSize, pBlobData } }
            int pvSize = 8 + 2 * IntPtr.Size;
            IntPtr pv = Marshal.AllocHGlobal(pvSize);
            for (int i = 0; i < pvSize; i++) Marshal.WriteByte(pv, i, 0);
            Marshal.WriteInt16(pv, 0, 65);                        // VT_BLOB
            Marshal.WriteInt32(pv, 8, 12);
            Marshal.WriteIntPtr(pv, 8 + IntPtr.Size, prm);

            var handler = new CompletionHandler();
            try
            {
                var iid = Native.IID_IAudioClient;
                IActivateAudioInterfaceAsyncOperation op;
                try
                {
                    Native.ActivateAudioInterfaceAsync(Native.VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, ref iid, pv, handler, out op);
                }
                catch (EntryPointNotFoundException)
                {
                    throw new NotSupportedException("当前系统不支持按程序录音，需要 Windows 10 2004（19041）或更新版本。");
                }
                if (!handler.Done.WaitOne(10000)) throw new TimeoutException("激活进程环回超时。");

                int hr; object iface;
                op.GetActivateResult(out hr, out iface);
                Check(hr, "激活进程环回");
                return (IAudioClient)iface;
            }
            finally
            {
                Marshal.FreeHGlobal(pv);
                Marshal.FreeHGlobal(prm);
                handler.Done.Close();
            }
        }

        static void Check(int hr, string what)
        {
            if (hr >= 0) return;
            string hint = "";
            switch ((uint)hr)
            {
                case 0x88890004: hint = "（音频设备已被移除或切换）"; break;
                case 0x88890008: hint = "（设备不支持该格式）"; break;
                case 0x8889000A: hint = "（设备正被独占模式占用）"; break;
                case 0x88890010: hint = "（Windows 音频服务没有运行）"; break;
                case 0x80070057: hint = "（参数无效：该系统版本可能不支持进程环回）"; break;
                case 0x80004001: hint = "（系统不支持该功能）"; break;
                case 0x80070490: hint = "（没有可用的播放设备）"; break;
            }
            throw new AudioHResultException(string.Format("{0} 失败，HRESULT 0x{1:X8}{2}", what, hr, hint), hr);
        }
    }

    /// <summary>描述捕获缓冲区里的采样格式。</summary>
    sealed class SampleFormat
    {
        public bool Float;
        public int BitsPerSample, ValidBits, Channels, SampleRate;
        public int BlockAlign { get { return Channels * BitsPerSample / 8; } }

        public static SampleFormat FromWaveFormat(IntPtr p)
        {
            int tag = (ushort)Marshal.ReadInt16(p, 0);
            var f = new SampleFormat
            {
                Channels = Marshal.ReadInt16(p, 2),
                SampleRate = Marshal.ReadInt32(p, 4),
                BitsPerSample = Marshal.ReadInt16(p, 14),
            };
            f.ValidBits = f.BitsPerSample;
            if (tag == 0xFFFE)                                  // WAVE_FORMAT_EXTENSIBLE
            {
                int valid = Marshal.ReadInt16(p, 18);
                if (valid > 0) f.ValidBits = valid;
                tag = Marshal.ReadInt32(p, 24) & 0xFFFF;       // SubFormat GUID 的 Data1
            }
            if (tag == 3) f.Float = true;
            else if (tag != 1) throw new NotSupportedException("不支持的混音格式 tag=" + tag);
            if (f.Float && f.BitsPerSample != 32) throw new NotSupportedException("不支持的浮点位深 " + f.BitsPerSample);
            if (!f.Float && f.BitsPerSample != 16 && f.BitsPerSample != 24 && f.BitsPerSample != 32)
                throw new NotSupportedException("不支持的整数位深 " + f.BitsPerSample);
            if (f.Channels < 1 || f.Channels > 8) throw new NotSupportedException("不支持的声道数 " + f.Channels);
            return f;
        }

        public IntPtr ToWaveFormatEx()
        {
            IntPtr p = Marshal.AllocHGlobal(18);
            Marshal.WriteInt16(p, 0, (short)(Float ? 3 : 1));
            Marshal.WriteInt16(p, 2, (short)Channels);
            Marshal.WriteInt32(p, 4, SampleRate);
            Marshal.WriteInt32(p, 8, SampleRate * BlockAlign);
            Marshal.WriteInt16(p, 12, (short)BlockAlign);
            Marshal.WriteInt16(p, 14, (short)BitsPerSample);
            Marshal.WriteInt16(p, 16, 0);
            return p;
        }
    }

    /// <summary>把捕获缓冲区转换成目标位深的整数；复用临时数组避免频繁分配。</summary>
    sealed class Converter
    {
        float[] _f = new float[0];
        short[] _s = new short[0];
        int[] _i = new int[0];
        byte[] _b = new byte[0];

        /// <summary>返回归一化峰值 0..1。浮点按 2^(bits-1) 缩放，16 位源在 100% 音量下可逐位还原。</summary>
        public float Convert(SampleFormat fmt, IntPtr src, int n, int[] dst, int outBits)
        {
            float peak = 0;
            if (fmt.Float)
            {
                if (_f.Length < n) _f = new float[n];
                Marshal.Copy(src, _f, 0, n);
                double scale = outBits == 16 ? 32768.0 : 8388608.0;
                int max = outBits == 16 ? 32767 : 8388607, min = -max - 1;
                for (int i = 0; i < n; i++)
                {
                    float x = _f[i];
                    if (float.IsNaN(x)) x = 0;
                    float a = x < 0 ? -x : x;
                    if (a > peak) peak = a;
                    double v = Math.Round(x * scale);
                    dst[i] = v > max ? max : v < min ? min : (int)v;
                }
            }
            else if (fmt.BitsPerSample == 16)
            {
                if (_s.Length < n) _s = new short[n];
                Marshal.Copy(src, _s, 0, n);
                int sh = outBits - 16; int pk = 0;
                for (int i = 0; i < n; i++) { int v = _s[i]; if (v < 0 ? -v > pk : v > pk) pk = v < 0 ? -v : v; dst[i] = v << sh; }
                peak = pk / 32768f;
            }
            else if (fmt.BitsPerSample == 32)
            {
                if (_i.Length < n) _i = new int[n];
                Marshal.Copy(src, _i, 0, n);
                int sh = 32 - outBits; long pk = 0;
                for (int i = 0; i < n; i++) { long v = _i[i]; long a = v < 0 ? -v : v; if (a > pk) pk = a; dst[i] = (int)(v >> sh); }
                peak = (float)(pk / 2147483648.0);
            }
            else
            {
                int bytes = n * 3;
                if (_b.Length < bytes) _b = new byte[bytes];
                Marshal.Copy(src, _b, 0, bytes);
                int sh = 24 - outBits; int pk = 0;
                for (int i = 0, o = 0; i < n; i++, o += 3)
                {
                    int v = (_b[o] | _b[o + 1] << 8 | _b[o + 2] << 16) << 8 >> 8;
                    int a = v < 0 ? -v : v; if (a > pk) pk = a;
                    dst[i] = v >> sh;
                }
                peak = pk / 8388608f;
            }
            return Math.Min(peak, 1f);
        }
    }
}
