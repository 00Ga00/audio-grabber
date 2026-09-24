// Audio Studio 录音组件：WASAPI 环回（整机 / 只录某程序 / 排除某程序），通过 stdin/stdout 和界面通信。
// 用 Windows 自带的 .NET Framework 编译器编译（C# 5），不需要安装任何东西：
//   %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /out:AudioRecorderHelper.exe *.cs
//
// 参数：--mode system|process|exclude  --pid N  --output x.wav  --timer 秒  --silence-stop 秒  --threshold-db -45  --bits 24
// 输入（每行一个）：pause / resume / stop
// 输出（每行一个 JSON）：ready / level / paused / resumed / notice / stopped / error
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using LoopbackRecorder;
using LoopbackRecorder.Interop;

static class Program
{
    static readonly object OutLock = new object();
    static volatile bool StopRequested;
    static string StopReason = "manual";

    static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        LoopbackCapture cap = null;
        try
        {
            var o = ParseArgs(args);
            string mode = Get(o, "mode", "system").ToLowerInvariant();
            string output = Get(o, "output", null);
            if (string.IsNullOrEmpty(output)) throw new ArgumentException("缺少 --output 参数");
            double timer = Num(Get(o, "timer", "0"));
            double silenceStop = Num(Get(o, "silence-stop", "0"));
            double thresholdDb = Num(Get(o, "threshold-db", "-45"));
            int bits = (int)Num(Get(o, "bits", "24"));
            uint pid = (uint)Num(Get(o, "pid", "0"));
            output = Path.GetFullPath(output);
            Directory.CreateDirectory(Path.GetDirectoryName(output));

            var opt = new CaptureOptions { Format = OutputFormat.Wav, BitsPerSample = bits == 16 ? 16 : 24, Path = output };
            if (mode == "process" || mode == "exclude")
            {
                if (pid == 0) throw new ArgumentException("请选择目标程序。");
                // 选中的往往是窗口所在进程；沿父进程找到同名程序的根，这样浏览器的音频子进程也会被录进来
                opt.ProcessId = FindRoot(pid);
                opt.Source = mode == "process" ? CaptureSource.IncludeProcess : CaptureSource.ExcludeProcess;
            }
            else opt.Source = CaptureSource.System;

            cap = new LoopbackCapture(opt);
            double lastSound = 0;
            long lastLevelTicks = 0;
            double sumSq = 0; long count = 0;
            Exception failure = null;
            cap.Failed += ex => { failure = ex; StopRequested = true; };
            cap.Notice += msg => Emit("notice", "message", msg);
            cap.TargetExited += () => Emit("notice", "message", "被录的程序已经退出，之后录到的都是静音。");
            double scale = 1.0 / (opt.BitsPerSample == 16 ? 32768.0 : 8388608.0);
            var capRef = cap;
            cap.Tap = (buf, frames) =>
            {
                int n = frames * capRef.Channels;
                double s = 0;
                for (int i = 0; i < n; i++) { double x = buf[i] * scale; s += x * x; }
                double chunkDb = n > 0 && s > 0 ? 10 * Math.Log10(s / n) : -100;
                double active = (capRef.FramesWritten + frames) / (double)capRef.SampleRate;
                if (chunkDb >= thresholdDb) lastSound = active;
                sumSq += s; count += n;
                long now = DateTime.UtcNow.Ticks;
                if (now - lastLevelTicks > TimeSpan.TicksPerMillisecond * 200)
                {
                    double db = count > 0 && sumSq > 0 ? 10 * Math.Log10(sumSq / count) : -100;
                    Emit("level", "db", Math.Round(db, 1), "seconds", Math.Round(active, 2));
                    sumSq = 0; count = 0; lastLevelTicks = now;
                }
            };

            cap.Start();
            Emit("ready", "format", string.Format("{0} Hz · {1} 声道 · {2} 位", cap.SampleRate, cap.Channels, opt.BitsPerSample), "output", output);

            var input = new Thread(() =>
            {
                try
                {
                    string line;
                    while (!StopRequested && (line = Console.ReadLine()) != null)
                    {
                        switch (line.Trim().ToLowerInvariant())
                        {
                            case "pause":
                                if (!capRef.Paused) { capRef.Paused = true; Emit("paused"); }
                                break;
                            case "resume":
                                if (capRef.Paused)
                                {
                                    lastSound = capRef.FramesWritten / (double)capRef.SampleRate;
                                    capRef.Paused = false;
                                    Emit("resumed");
                                }
                                break;
                            case "stop":
                                StopReason = "manual"; StopRequested = true;
                                break;
                        }
                    }
                    // 界面进程关闭了管道：也停止，保证文件完整
                    StopRequested = true;
                }
                catch { StopRequested = true; }
            }) { IsBackground = true };
            input.Start();

            while (!StopRequested)
            {
                Thread.Sleep(100);
                if (cap.Paused) continue;
                double active = cap.FramesWritten / (double)cap.SampleRate;
                if (timer > 0 && active >= timer) { StopReason = "timer"; StopRequested = true; }
                else if (silenceStop > 0 && active - lastSound >= silenceStop) { StopReason = "silence"; StopRequested = true; }
            }

            double seconds = cap.FramesWritten / (double)cap.SampleRate;
            cap.Dispose();
            cap = null;
            if (failure != null) throw failure;
            long bytes = File.Exists(output) ? new FileInfo(output).Length : 0;
            Emit("stopped", "reason", StopReason, "bytes", bytes, "seconds", Math.Round(seconds, 2), "output", output);
            return 0;
        }
        catch (Exception ex)
        {
            if (cap != null) { try { cap.Dispose(); } catch { } }
            Emit("error", "message", ex.Message, "detail", ex.ToString());
            return 1;
        }
    }

    // ------------------------------------------------------------------ 工具

    static Dictionary<string, string> ParseArgs(string[] args)
    {
        var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < args.Length; i++)
            if (args[i].StartsWith("--") && i + 1 < args.Length) d[args[i].Substring(2)] = args[++i];
        return d;
    }

    static string Get(Dictionary<string, string> d, string key, string def)
    {
        string v;
        return d.TryGetValue(key, out v) ? v : def;
    }

    static double Num(string s)
    {
        return double.Parse(s, NumberStyles.Float, CultureInfo.InvariantCulture);
    }

    static uint FindRoot(uint pid)
    {
        var parent = new Dictionary<uint, uint>();
        var exe = new Dictionary<uint, string>();
        IntPtr snap = Native.CreateToolhelp32Snapshot(Native.TH32CS_SNAPPROCESS, 0);
        if (snap == IntPtr.Zero || snap == new IntPtr(-1)) return pid;
        try
        {
            var e = new Native.PROCESSENTRY32W { dwSize = (uint)Marshal.SizeOf(typeof(Native.PROCESSENTRY32W)) };
            if (Native.Process32FirstW(snap, ref e))
                do { parent[e.th32ProcessID] = e.th32ParentProcessID; exe[e.th32ProcessID] = e.szExeFile ?? ""; }
                while (Native.Process32NextW(snap, ref e));
        }
        finally { Native.CloseHandle(snap); }

        var seen = new HashSet<uint>();
        uint cur = pid;
        while (seen.Add(cur))
        {
            uint p;
            if (!parent.TryGetValue(cur, out p) || p == 0 || p == cur || !exe.ContainsKey(p)) break;
            if (!string.Equals(exe[p], exe[cur], StringComparison.OrdinalIgnoreCase)) break;
            cur = p;
        }
        return cur;
    }

    /// <summary>输出一行 JSON：Emit("level", "db", -20.5, "seconds", 3.2)</summary>
    static void Emit(string type, params object[] kv)
    {
        var sb = new StringBuilder("{\"type\":\"").Append(type).Append('"');
        for (int i = 0; i + 1 < kv.Length; i += 2)
        {
            sb.Append(",\"").Append(kv[i]).Append("\":");
            object v = kv[i + 1];
            if (v is string) AppendString(sb, (string)v);
            else if (v is double) sb.Append(((double)v).ToString("0.###", CultureInfo.InvariantCulture));
            else if (v is float) sb.Append(((float)v).ToString("0.###", CultureInfo.InvariantCulture));
            else sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
        }
        sb.Append('}');
        lock (OutLock) { Console.Out.WriteLine(sb.ToString()); Console.Out.Flush(); }
    }

    static void AppendString(StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default:
                    if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }
}
