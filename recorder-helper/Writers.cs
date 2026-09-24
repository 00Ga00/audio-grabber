using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;

namespace LoopbackRecorder
{
    /// <summary>接收交错排列的整型 PCM 采样（已缩放到目标位深）。</summary>
    public interface IAudioWriter : IDisposable
    {
        int SampleRate { get; }
        int Channels { get; }
        int BitsPerSample { get; }
        long FramesWritten { get; }
        void Write(int[] interleaved, int frameCount);
    }

    // ------------------------------------------------------------------ WAV

    public sealed class WavWriter : IAudioWriter
    {
        readonly FileStream _fs;
        readonly int _bytesPerSample;
        byte[] _buf = new byte[0];
        long _dataBytes;
        DateTime _lastHeaderUpdate = DateTime.UtcNow;

        public int SampleRate { get; private set; }
        public int Channels { get; private set; }
        public int BitsPerSample { get; private set; }
        public long FramesWritten { get; private set; }

        public WavWriter(string path, int sampleRate, int channels, int bitsPerSample)
        {
            if (bitsPerSample != 16 && bitsPerSample != 24) throw new ArgumentException("仅支持 16 或 24 位");
            SampleRate = sampleRate; Channels = channels; BitsPerSample = bitsPerSample;
            _bytesPerSample = bitsPerSample / 8;
            _fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read, 1 << 16);
            WriteHeader();
        }

        void WriteHeader()
        {
            // 超过 4 GB 时 RIFF 尺寸字段会溢出，这里钳到最大值，数据仍然完整写入。
            uint data = (uint)Math.Min(_dataBytes, uint.MaxValue - 36);
            var h = new BinaryWriter(new MemoryStream(44));
            h.Write(Encoding.ASCII.GetBytes("RIFF"));
            h.Write(36u + data);
            h.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            h.Write(16);
            h.Write((short)1);                      // PCM
            h.Write((short)Channels);
            h.Write(SampleRate);
            h.Write(SampleRate * Channels * _bytesPerSample);
            h.Write((short)(Channels * _bytesPerSample));
            h.Write((short)BitsPerSample);
            h.Write(Encoding.ASCII.GetBytes("data"));
            h.Write(data);
            h.Flush();
            var bytes = ((MemoryStream)h.BaseStream).ToArray();
            long pos = _fs.Position;
            _fs.Position = 0;
            _fs.Write(bytes, 0, bytes.Length);
            _fs.Position = Math.Max(pos, 44);
        }

        public void Write(int[] s, int frameCount)
        {
            int n = frameCount * Channels;
            int need = n * _bytesPerSample;
            if (_buf.Length < need) _buf = new byte[need];
            int p = 0;
            if (_bytesPerSample == 2)
                for (int i = 0; i < n; i++) { int v = s[i]; _buf[p++] = (byte)v; _buf[p++] = (byte)(v >> 8); }
            else
                for (int i = 0; i < n; i++) { int v = s[i]; _buf[p++] = (byte)v; _buf[p++] = (byte)(v >> 8); _buf[p++] = (byte)(v >> 16); }
            _fs.Write(_buf, 0, need);
            _dataBytes += need;
            FramesWritten += frameCount;

            // 每秒刷新一次文件头：即使程序意外退出，文件也能正常打开。
            if ((DateTime.UtcNow - _lastHeaderUpdate).TotalSeconds >= 1)
            {
                WriteHeader();
                _fs.Flush();
                _lastHeaderUpdate = DateTime.UtcNow;
            }
        }

        public void Dispose()
        {
            WriteHeader();
            _fs.Dispose();
        }
    }

    // ----------------------------------------------------------------- FLAC

    /// <summary>
    /// 纯托管 FLAC 编码器：固定预测 + LPC（最高 12 阶，Tukey 窗 + Levinson-Durbin）、
    /// 分区 Rice 编码、立体声去相关（左右/左侧/右侧/中侧自动挑选），每帧 4096 采样，
    /// 结束时写回 STREAMINFO（总采样数、帧长范围、MD5）。压缩率与 flac -5 接近。
    /// </summary>
    public sealed class FlacWriter : IAudioWriter
    {
        const int BlockSize = 4096;
        const int MaxLpcOrder = 12;
        const int StreamInfoOffset = 8; // "fLaC"(4) + 元数据块头(4)

        readonly FileStream _fs;
        readonly long[][] _block;       // 每声道缓冲
        readonly long[] _mid = new long[BlockSize], _side = new long[BlockSize];
        int _fill;
        long _frameIndex;
        int _minFrame = int.MaxValue, _maxFrame;
        readonly MD5 _md5 = MD5.Create();
        byte[] _md5Buf = new byte[0];
        readonly BitWriter _bw = new BitWriter();
        readonly SubframeEncoder _enc = new SubframeEncoder(BlockSize, MaxLpcOrder);
        readonly Plan[] _plans = new Plan[4];

        public int SampleRate { get; private set; }
        public int Channels { get; private set; }
        public int BitsPerSample { get; private set; }
        public long FramesWritten { get; private set; }

        public FlacWriter(string path, int sampleRate, int channels, int bitsPerSample)
        {
            if (bitsPerSample != 16 && bitsPerSample != 24) throw new ArgumentException("仅支持 16 或 24 位");
            if (channels < 1 || channels > 8) throw new ArgumentException("声道数必须为 1-8");
            SampleRate = sampleRate; Channels = channels; BitsPerSample = bitsPerSample;
            _block = new long[channels][];
            for (int c = 0; c < channels; c++) _block[c] = new long[BlockSize];
            for (int i = 0; i < _plans.Length; i++) _plans[i] = new Plan(BlockSize);
            _fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read, 1 << 16);
            _fs.Write(Encoding.ASCII.GetBytes("fLaC"), 0, 4);
            WriteStreamInfo(final: false);
        }

        public void Write(int[] s, int frameCount)
        {
            UpdateMd5(s, frameCount);
            int idx = 0, ch = Channels;
            while (idx < frameCount * ch)
            {
                int take = Math.Min(BlockSize - _fill, frameCount - idx / ch);
                for (int f = 0; f < take; f++)
                    for (int c = 0; c < ch; c++) _block[c][_fill + f] = s[idx++];
                _fill += take;
                if (_fill == BlockSize) { EncodeFrame(_fill); _fill = 0; }
            }
            FramesWritten += frameCount;
        }

        public void Dispose()
        {
            if (_fill > 0) { EncodeFrame(_fill); _fill = 0; }
            _md5.TransformFinalBlock(new byte[0], 0, 0);
            WriteStreamInfo(final: true);
            _fs.Dispose();
            _md5.Dispose();
        }

        void UpdateMd5(int[] s, int frameCount)
        {
            int n = frameCount * Channels, bps = BitsPerSample / 8;
            if (_md5Buf.Length < n * bps) _md5Buf = new byte[n * bps];
            int p = 0;
            if (bps == 2)
                for (int i = 0; i < n; i++) { int v = s[i]; _md5Buf[p++] = (byte)v; _md5Buf[p++] = (byte)(v >> 8); }
            else
                for (int i = 0; i < n; i++) { int v = s[i]; _md5Buf[p++] = (byte)v; _md5Buf[p++] = (byte)(v >> 8); _md5Buf[p++] = (byte)(v >> 16); }
            _md5.TransformBlock(_md5Buf, 0, p, null, 0);
        }

        void WriteStreamInfo(bool final)
        {
            var bw = new BitWriter();
            bw.Write(1, 1);            // last-metadata-block
            bw.Write(0, 7);            // STREAMINFO
            bw.Write(34, 24);
            bw.Write(BlockSize, 16);
            bw.Write(BlockSize, 16);
            bw.Write(final && _maxFrame > 0 ? (uint)_minFrame : 0, 24);
            bw.Write(final ? (uint)_maxFrame : 0, 24);
            bw.Write((uint)SampleRate, 20);
            bw.Write((uint)(Channels - 1), 3);
            bw.Write((uint)(BitsPerSample - 1), 5);
            ulong total = final ? (ulong)FramesWritten : 0;
            bw.Write((uint)(total >> 32) & 0xF, 4);
            bw.Write((uint)total, 32);
            byte[] md5 = final ? _md5.Hash : new byte[16];
            foreach (var b in md5) bw.Write(b, 8);

            long pos = _fs.Position;
            _fs.Position = StreamInfoOffset - 4;
            _fs.Write(bw.Buffer, 0, bw.Length);
            _fs.Position = final ? _fs.Length : Math.Max(pos, StreamInfoOffset + 34);
        }

        // ---- 帧编码

        void EncodeFrame(int n)
        {
            var bw = _bw;
            bw.Reset();
            int bps = BitsPerSample;

            int assignment = Channels - 1;
            Plan p0 = null, p1 = null;
            if (Channels == 2)
            {
                long[] L = _block[0], R = _block[1];
                for (int i = 0; i < n; i++) { _mid[i] = (L[i] + R[i]) >> 1; _side[i] = L[i] - R[i]; }
                Plan pl = _enc.Analyze(L, n, bps, _plans[0]);
                Plan pr = _enc.Analyze(R, n, bps, _plans[1]);
                Plan pm = _enc.Analyze(_mid, n, bps, _plans[2]);
                Plan ps = _enc.Analyze(_side, n, bps + 1, _plans[3]);
                long best = pl.Bits + pr.Bits; assignment = 1; p0 = pl; p1 = pr;
                if (pl.Bits + ps.Bits < best) { best = pl.Bits + ps.Bits; assignment = 8; p0 = pl; p1 = ps; }
                if (ps.Bits + pr.Bits < best) { best = ps.Bits + pr.Bits; assignment = 9; p0 = ps; p1 = pr; }
                if (pm.Bits + ps.Bits < best) { best = pm.Bits + ps.Bits; assignment = 10; p0 = pm; p1 = ps; }
            }

            // 帧头
            bw.Write(0xFFF8, 16);                       // 同步码 + 固定块长
            int bsCode = n == BlockSize ? 12 : 7;       // 12 → 4096；7 → 帧头末尾给出 16 位 (n-1)
            bw.Write((uint)bsCode, 4);
            bw.Write(0, 4);                             // 采样率取自 STREAMINFO
            bw.Write((uint)assignment, 4);
            bw.Write(bps == 16 ? 4u : 6u, 3);
            bw.Write(0, 1);
            WriteUtf8(bw, (ulong)_frameIndex);
            if (bsCode == 7) bw.Write((uint)(n - 1), 16);
            bw.Write(Crc.Crc8(bw.Buffer, 0, bw.Length), 8);

            if (Channels == 2)
            {
                _enc.Emit(bw, p0, n);
                _enc.Emit(bw, p1, n);
            }
            else
            {
                for (int c = 0; c < Channels; c++) _enc.Emit(bw, _enc.Analyze(_block[c], n, bps, _plans[0]), n);
            }

            bw.AlignToByte();
            bw.Write(Crc.Crc16(bw.Buffer, 0, bw.Length), 16);

            _fs.Write(bw.Buffer, 0, bw.Length);
            _minFrame = Math.Min(_minFrame, bw.Length);
            _maxFrame = Math.Max(_maxFrame, bw.Length);
            _frameIndex++;
        }

        static void WriteUtf8(BitWriter bw, ulong v)
        {
            if (v < 0x80) { bw.Write((uint)v, 8); return; }
            int extra = v < 0x800 ? 1 : v < 0x10000 ? 2 : v < 0x200000 ? 3 : v < 0x4000000 ? 4 : v < 0x80000000 ? 5 : 6;
            uint lead = (uint)(0xFF00 >> (extra + 1)) & 0xFF;          // 110xxxxx, 1110xxxx ...
            bw.Write(lead | (uint)(v >> (6 * extra)), 8);
            for (int i = extra - 1; i >= 0; i--) bw.Write(0x80u | (uint)((v >> (6 * i)) & 0x3F), 8);
        }
    }

    /// <summary>一个子帧的编码方案，连同残差（已 zigzag 映射）一起保存，挑选后直接写出。</summary>
    sealed class Plan
    {
        public int Kind;            // 0 常量, 1 原样, 2 固定预测, 3 LPC
        public int Order, PartOrder, Precision, Shift, Bps;
        public bool FiveBitParams;
        public readonly int[] Params = new int[256];
        public readonly int[] Coefs = new int[32];
        public long Bits;
        public long[] Samples;      // 原始信号（写 warm-up / 原样子帧用）
        public long[] Residual;     // zigzag 后的残差
        public Plan(int blockSize) { Residual = new long[blockSize]; }
    }

    sealed class SubframeEncoder
    {
        readonly int _maxLpc;
        long[] _scratch;
        readonly double[] _win, _xw;
        int _winN = -1;
        readonly double[] _autoc;
        readonly double[][] _lpc;       // _lpc[m-1] 为 m 阶预测系数
        readonly long[] _sums = new long[256];
        readonly int[] _tmpParams = new int[256];

        public SubframeEncoder(int blockSize, int maxLpc)
        {
            _maxLpc = maxLpc;
            _scratch = new long[blockSize];
            _win = new double[blockSize]; _xw = new double[blockSize];
            _autoc = new double[maxLpc + 1];
            _lpc = new double[maxLpc][];
            for (int i = 0; i < maxLpc; i++) _lpc[i] = new double[maxLpc];
        }

        public Plan Analyze(long[] x, int n, int bps, Plan plan)
        {
            plan.Samples = x; plan.Bps = bps;
            plan.Kind = 1; plan.Bits = 8 + (long)n * bps;

            bool constant = true;
            long x0 = x[0];
            for (int i = 1; i < n; i++) if (x[i] != x0) { constant = false; break; }
            if (constant) { plan.Kind = 0; plan.Bits = 8 + bps; return plan; }

            // ---- 固定预测：按残差绝对值之和挑阶数
            int maxFixed = Math.Min(4, n - 1);
            int bestOrder = 0; long bestSum = long.MaxValue;
            for (int o = 0; o <= maxFixed; o++)
            {
                long sum = 0;
                for (int i = o; i < n; i++) { long r = FixedResidual(x, i, o); sum += r < 0 ? -r : r; }
                if (sum < bestSum) { bestSum = sum; bestOrder = o; }
            }
            for (int i = bestOrder; i < n; i++) _scratch[i] = Zig(FixedResidual(x, i, bestOrder));
            TryCandidate(plan, 2, bestOrder, 0, 0, null, n, 8 + (long)bestOrder * bps);

            // ---- LPC
            if (n > 64) TryLpc(x, n, bps, plan);
            return plan;
        }

        static long FixedResidual(long[] x, int i, int order)
        {
            switch (order)
            {
                case 0: return x[i];
                case 1: return x[i] - x[i - 1];
                case 2: return x[i] - 2 * x[i - 1] + x[i - 2];
                case 3: return x[i] - 3 * x[i - 1] + 3 * x[i - 2] - x[i - 3];
                default: return x[i] - 4 * x[i - 1] + 6 * x[i - 2] - 4 * x[i - 3] + x[i - 4];
            }
        }

        static long Zig(long r) { return r >= 0 ? r << 1 : ((-r) << 1) - 1; }

        void TryLpc(long[] x, int n, int bps, Plan plan)
        {
            int maxOrder = Math.Min(_maxLpc, n - 1);
            if (_winN != n)
            {
                // Tukey(0.5) 窗
                double alpha = 0.5; int taper = (int)(alpha / 2 * n);
                for (int i = 0; i < n; i++)
                {
                    double w = 1;
                    if (i < taper) w = 0.5 * (1 - Math.Cos(Math.PI * i / taper));
                    else if (i >= n - taper) w = 0.5 * (1 - Math.Cos(Math.PI * (n - 1 - i) / taper));
                    _win[i] = w;
                }
                _winN = n;
            }
            for (int i = 0; i < n; i++) _xw[i] = x[i] * _win[i];
            for (int l = 0; l <= maxOrder; l++)
            {
                double s = 0;
                for (int i = l; i < n; i++) s += _xw[i] * _xw[i - l];
                _autoc[l] = s;
            }
            if (_autoc[0] <= 0) return;
            _autoc[0] *= 1.0 + 1e-9;   // 轻微对角加载，数值更稳

            // Levinson-Durbin，得到 1..maxOrder 各阶的预测系数
            var a = new double[maxOrder];
            double err = _autoc[0];
            for (int m = 1; m <= maxOrder; m++)
            {
                double acc = _autoc[m];
                for (int j = 1; j < m; j++) acc -= a[j - 1] * _autoc[m - j];
                double k = acc / err;
                var prev = (double[])a.Clone();
                a[m - 1] = k;
                for (int j = 1; j < m; j++) a[j - 1] = prev[j - 1] - k * prev[m - j - 1];
                err *= 1 - k * k;
                Array.Copy(a, _lpc[m - 1], m);
                if (err <= 0) { maxOrder = m; break; }
            }

            int precision = bps <= 16 ? 14 : 15;
            int[] orders = maxOrder > 8 ? new[] { 8, maxOrder } : new[] { maxOrder };
            foreach (int order in orders)
            {
                int shift;
                var q = Quantize(_lpc[order - 1], order, precision, out shift);
                if (q == null) continue;
                bool ok = true;
                for (int i = order; i < n; i++)
                {
                    long sum = 0;
                    for (int j = 0; j < order; j++) sum += q[j] * x[i - 1 - j];
                    long r = x[i] - (sum >> shift);
                    if (r > int.MaxValue / 2 || r < -(int.MaxValue / 2)) { ok = false; break; }
                    _scratch[i] = Zig(r);
                }
                if (!ok) continue;
                long headerBits = 8 + (long)order * bps + 4 + 5 + (long)order * precision;
                TryCandidate(plan, 3, order, precision, shift, q, n, headerBits);
            }
        }

        static int[] Quantize(double[] lp, int order, int precision, out int shift)
        {
            double cmax = 0;
            for (int i = 0; i < order; i++) cmax = Math.Max(cmax, Math.Abs(lp[i]));
            shift = 0;
            if (cmax <= 0 || double.IsNaN(cmax) || double.IsInfinity(cmax)) return null;
            int log2cmax = (int)Math.Floor(Math.Log(cmax, 2)) + 1;   // cmax < 2^log2cmax
            shift = precision - 1 - log2cmax;
            if (shift > 15) shift = 15;
            if (shift < 0) return null;
            int qmax = (1 << (precision - 1)) - 1, qmin = -(1 << (precision - 1));
            var q = new int[order];
            double e = 0;
            for (int i = 0; i < order; i++)
            {
                e += lp[i] * (1 << shift);
                long v = (long)Math.Round(e);
                if (v > qmax) v = qmax; else if (v < qmin) v = qmin;
                q[i] = (int)v;
                e -= v;
            }
            return q;
        }

        /// <summary>评估 _scratch 里的残差，若比当前方案省，则把它换成当前方案。</summary>
        void TryCandidate(Plan plan, int kind, int order, int precision, int shift, int[] coefs, int n, long headerBits)
        {
            int po; bool five;
            long resBits = RiceBits(_scratch, order, n, _tmpParams, out po, out five);
            long total = headerBits + resBits;
            if (total >= plan.Bits) return;
            plan.Kind = kind; plan.Order = order; plan.Precision = precision; plan.Shift = shift;
            if (coefs != null) Array.Copy(coefs, plan.Coefs, order);
            plan.PartOrder = po; plan.FiveBitParams = five; plan.Bits = total;
            Array.Copy(_tmpParams, plan.Params, 1 << po);
            var t = plan.Residual; plan.Residual = _scratch; _scratch = t;   // 交换缓冲区，避免拷贝
        }

        long RiceBits(long[] u, int order, int n, int[] outParams, out int bestPo, out bool bestFive)
        {
            int maxPo = 0;
            while (maxPo < 8 && n % (1 << (maxPo + 1)) == 0 && (n >> (maxPo + 1)) > order) maxPo++;

            int parts = 1 << maxPo, psize = n >> maxPo;
            for (int p = 0; p < parts; p++)
            {
                int start = p == 0 ? order : p * psize, end = (p + 1) * psize;
                long s = 0;
                for (int i = start; i < end; i++) s += u[i];
                _sums[p] = s;
            }

            long best = long.MaxValue; bestPo = 0; bestFive = false;
            var ks = new int[parts];
            for (int po = maxPo; po >= 0; po--)
            {
                int np = 1 << po, sz = n >> po;
                long bits = 6; bool five = false;
                for (int p = 0; p < np; p++)
                {
                    long cnt = p == 0 ? sz - order : sz;
                    long s = _sums[p];
                    int k = 0;
                    if (cnt > 0 && s > cnt) { long m = s / cnt; while (k < 30 && (m >> (k + 1)) > 0) k++; }
                    // 估算：每个值 k+1 位 + 商的和
                    long b0 = cnt * (k + 1) + (s >> k) - (k > 0 ? cnt / 2 : 0);
                    if (k > 0)
                    {
                        long b1 = cnt * k + (s >> (k - 1)) - (k > 1 ? cnt / 2 : 0);
                        if (b1 < b0) { b0 = b1; k--; }
                    }
                    if (k < 30)
                    {
                        long b2 = cnt * (k + 2) + (s >> (k + 1)) - cnt / 2;
                        if (b2 < b0) { b0 = b2; k++; }
                    }
                    ks[p] = k; bits += b0;
                    if (k > 14) five = true;
                }
                bits += (long)np * (five ? 5 : 4);
                if (bits < best) { best = bits; bestPo = po; bestFive = five; Array.Copy(ks, outParams, np); }
                // 合并到上一层
                for (int p = 0; p < np / 2; p++) _sums[p] = _sums[2 * p] + _sums[2 * p + 1];
            }
            return best;
        }

        public void Emit(BitWriter bw, Plan plan, int n)
        {
            int bps = plan.Bps;
            long[] x = plan.Samples;
            switch (plan.Kind)
            {
                case 0:
                    bw.Write(0, 8);
                    bw.WriteSigned(x[0], bps);
                    return;
                case 1:
                    bw.Write(0x02, 8);
                    for (int i = 0; i < n; i++) bw.WriteSigned(x[i], bps);
                    return;
                case 2:
                    bw.Write((uint)((0x08 | plan.Order) << 1), 8);
                    for (int i = 0; i < plan.Order; i++) bw.WriteSigned(x[i], bps);
                    break;
                default:
                    bw.Write((uint)((0x20 | (plan.Order - 1)) << 1), 8);
                    for (int i = 0; i < plan.Order; i++) bw.WriteSigned(x[i], bps);
                    bw.Write((uint)(plan.Precision - 1), 4);
                    bw.WriteSigned(plan.Shift, 5);
                    for (int i = 0; i < plan.Order; i++) bw.WriteSigned(plan.Coefs[i], plan.Precision);
                    break;
            }
            bw.Write(plan.FiveBitParams ? 1u : 0u, 2);
            bw.Write((uint)plan.PartOrder, 4);
            int parts = 1 << plan.PartOrder, psize = n >> plan.PartOrder;
            int pbits = plan.FiveBitParams ? 5 : 4;
            for (int p = 0; p < parts; p++)
            {
                int k = plan.Params[p];
                bw.Write((uint)k, pbits);
                int start = p == 0 ? plan.Order : p * psize, end = (p + 1) * psize;
                for (int i = start; i < end; i++) bw.WriteRice((ulong)plan.Residual[i], k);
            }
        }
    }

    // --------------------------------------------------------------- helpers

    public sealed class BitWriter
    {
        byte[] _buf = new byte[1 << 16];
        int _pos, _nbits;
        ulong _acc;

        public byte[] Buffer { get { return _buf; } }
        public int Length { get { return _pos; } }   // 只统计已完整写出的字节

        public void Reset() { _pos = 0; _nbits = 0; _acc = 0; }

        public void Write(uint value, int bits)
        {
            if (bits == 0) return;
            if (_pos + 8 > _buf.Length) Array.Resize(ref _buf, _buf.Length * 2);
            ulong mask = bits == 32 ? 0xFFFFFFFFUL : ((1UL << bits) - 1);
            _acc = (_acc << bits) | (value & mask);
            _nbits += bits;
            while (_nbits >= 8) { _nbits -= 8; _buf[_pos++] = (byte)(_acc >> _nbits); }
        }

        public void WriteSigned(long v, int bits) { Write((uint)(v & ((1L << bits) - 1)), bits); }

        public void WriteRice(ulong u, int k)
        {
            ulong q = u >> k;
            while (q >= 32) { Write(0, 32); q -= 32; }
            Write(1, (int)q + 1);                         // q 个 0 再跟一个 1
            if (k > 0) Write((uint)(u & ((1UL << k) - 1)), k);
        }

        public void AlignToByte() { if (_nbits > 0) Write(0, 8 - _nbits); }
    }

    static class Crc
    {
        static readonly byte[] T8 = new byte[256];
        static readonly ushort[] T16 = new ushort[256];

        static Crc()
        {
            for (int i = 0; i < 256; i++)
            {
                int c = i;
                for (int j = 0; j < 8; j++) c = (c & 0x80) != 0 ? ((c << 1) ^ 0x07) : (c << 1);
                T8[i] = (byte)c;
                int d = i << 8;
                for (int j = 0; j < 8; j++) d = (d & 0x8000) != 0 ? ((d << 1) ^ 0x8005) : (d << 1);
                T16[i] = (ushort)d;
            }
        }

        public static uint Crc8(byte[] b, int off, int len)
        {
            byte c = 0;
            for (int i = off; i < off + len; i++) c = T8[c ^ b[i]];
            return c;
        }

        public static uint Crc16(byte[] b, int off, int len)
        {
            ushort c = 0;
            for (int i = off; i < off + len; i++) c = (ushort)((c << 8) ^ T16[(c >> 8) ^ b[i]]);
            return c;
        }
    }
}
