"""音色分析：自动校正手机现场录音的音色，以及检测有损压缩砍掉高频的位置。

只用 numpy / scipy。校正量有上限、并经过平滑，只做温和的均衡，不改变动态。
"""

from __future__ import annotations

import numpy as np

# 1/3 倍频程中心频率（31.5 Hz ~ 16 kHz）
CENTERS = [31.5 * 2 ** (i / 3) for i in range(28)]
# 目标音色：几段优秀现场录音（调音台和观众席录音）的平均长期频谱，200 Hz–2 kHz 归一到 0 dB
TARGET = [-20.3, -14.8, -9.6, -4.8, -4.9, -0.6, 0.5, 0.7, 0.8, -2.7, -1.4, -0.3, 2.9, 0.3, 1.3, -0.5,
          -1.2, 0.7, -2.0, -2.8, -6.4, -10.5, -12.2, -14.0, -17.0, -19.7, -23.0, -29.6]
MAX_BOOST = 6.0   # 最多提升 6 dB（提太多会放大压缩失真）
MAX_CUT = 10.0    # 最多衰减 10 dB
AMOUNT = 0.8      # 只校正差距的 80%，保留歌曲本身的特点


class SpectrumMeter:
    """分块累加的长期频谱（适合几小时的长音频）。"""

    def __init__(self, rate: int, nfft: int = 16384):
        self.rate, self.nfft = rate, nfft
        self.window = np.hanning(nfft).astype(np.float32)
        self.power = np.zeros(nfft // 2 + 1)
        self.frames = 0
        self.rest = np.zeros(0, dtype=np.float32)

    def add(self, block: np.ndarray) -> None:
        mono = block.mean(axis=1) if block.ndim > 1 else block
        data = np.concatenate([self.rest, mono.astype(np.float32)])
        hop = self.nfft // 2
        count = max(0, (len(data) - self.nfft) // hop + 1)
        for i in range(count):
            frame = data[i * hop:i * hop + self.nfft] * self.window
            self.power += np.abs(np.fft.rfft(frame)) ** 2
        self.frames += count
        self.rest = data[count * hop:]

    def spectrum(self):
        freqs = np.fft.rfftfreq(self.nfft, 1 / self.rate)
        return freqs, self.power / max(self.frames, 1)


def band_levels(freqs, power):
    """每个 1/3 倍频程的能量（dB，未归一化）。"""
    levels = []
    for c in CENTERS:
        band = (freqs >= c * 2 ** (-1 / 6)) & (freqs < c * 2 ** (1 / 6))
        levels.append(10 * np.log10(np.sum(power[band]) + 1e-20))
    return np.array(levels)


def third_octave(freqs, power):
    """1/3 倍频程频谱，200 Hz–2 kHz 的平均归一到 0 dB。"""
    levels = band_levels(freqs, power)
    mids = (np.array(CENTERS) >= 200) & (np.array(CENTERS) <= 2000)
    return levels - levels[mids].mean()


def detect_cutoff(freqs, power) -> float | None:
    """有损压缩通常把某个频率以上整段砍掉。返回这个频率（Hz）；频带完整时返回 None。"""
    db = 10 * np.log10(power + 1e-20)
    ref = db[(freqs >= 1000) & (freqs <= 4000)].mean()
    step = freqs[1] - freqs[0]
    width = max(1, int(300 / step))
    smooth = np.convolve(db, np.ones(width) / width, mode="same")
    alive = np.where((smooth > ref - 55) & (freqs > 2000))[0]
    if not len(alive):
        return None
    top = float(freqs[alive[-1]])
    nyquist = freqs[-1]
    if top > min(19000.0, nyquist * 0.9):
        return None
    # 截止处要够“陡”：再往上 1 kHz 至少比截止前低 30 dB，才认为是压缩造成的
    below = smooth[(freqs > top - 1500) & (freqs < top - 500)].mean()
    above = smooth[(freqs > top + 500) & (freqs < top + 1500)]
    if len(above) and below - above.mean() < 30:
        return None
    return top


def tone_curve(freqs, power, cutoff: float | None) -> list[tuple[float, float]]:
    """返回 [(频率, 增益 dB)]：把长期频谱往目标音色靠（平滑、有上限）。"""
    measured = third_octave(freqs, power)
    diff = (np.array(TARGET) - measured) * AMOUNT
    # 1 倍频程平滑两次，只做大方向的校正
    for _ in range(2):
        diff = np.convolve(np.pad(diff, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    diff = np.clip(diff, -MAX_CUT, MAX_BOOST)
    top = min(cutoff or 16000.0, 16000.0)
    points = []
    for c, g in zip(CENTERS, diff):
        if c > top * 0.9:           # 压缩砍掉的频段不去提升（那里只有噪声）
            g = min(g, 0.0)
        if c > 6000:                # 高频最多提 3 dB：压缩失真主要在这里，提多了会刺耳、发假
            g = min(g, 3.0)
        if c < 40:                  # 极低频只衰减不提升
            g = min(g, 0.0)
        points.append((round(c, 1), round(float(g), 2)))
    # 整体音量不变：以 200 Hz–2 kHz 的平均增益为 0
    mid = np.mean([g for c, g in points if 200 <= c <= 2000])
    return [(c, round(g - mid, 2)) for c, g in points]


def fir_from_curve(points, rate: int, taps: int = 4095) -> np.ndarray:
    """线性相位 FIR（频率采样法 + 窗），用于在 numpy 里直接套用 tone_curve。"""
    f = np.array([0.0] + [c for c, _ in points] + [rate / 2])
    g = np.array([points[0][1]] + [g for _, g in points] + [points[-1][1]])
    grid = np.linspace(0, rate / 2, taps // 2 + 1)
    gains = 10 ** (np.interp(np.log10(np.maximum(grid, 1)), np.log10(np.maximum(f, 1)), g) / 20)
    h = np.fft.irfft(gains, n=taps + 1)[: taps + 1]
    h = np.roll(h, taps // 2)[:taps] * np.hanning(taps)
    return h.astype(np.float64)


def highpass_fir(cutoff: float, rate: int, taps: int = 2047, transition: float = 800.0) -> np.ndarray:
    """线性相位高通（用来只取模型补出来的、截止频率以上的那一段）。"""
    from scipy.signal import firwin
    edge = min(cutoff + transition / 2, rate / 2 - 100)
    return firwin(taps, edge, pass_zero=False, fs=rate, window=("kaiser", 8.0))
