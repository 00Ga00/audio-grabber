"""乐理小工具（纯 Python）：音阶 / 调式、调内和弦、和弦进行推荐、常用进行，以及试听用的简单吉他音色合成和 MIDI 导出。"""

from __future__ import annotations

import math
import random
import struct
import wave

import chords as ch

# 音阶 / 调式：名字 → 相对主音的半音
SCALES = {
    "大调（自然大调）": (0, 2, 4, 5, 7, 9, 11),
    "小调（自然小调）": (0, 2, 3, 5, 7, 8, 10),
    "大调五声": (0, 2, 4, 7, 9),
    "小调五声": (0, 3, 5, 7, 10),
    "布鲁斯": (0, 3, 5, 6, 7, 10),
    "多利亚 Dorian": (0, 2, 3, 5, 7, 9, 10),
    "弗里几亚 Phrygian": (0, 1, 3, 5, 7, 8, 10),
    "利底亚 Lydian": (0, 2, 4, 6, 7, 9, 11),
    "混合利底亚 Mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "和声小调": (0, 2, 3, 5, 7, 8, 11),
    "旋律小调": (0, 2, 3, 5, 7, 9, 11),
}
INTERVAL_NAMES = {0: "1", 1: "b2", 2: "2", 3: "b3", 4: "3", 5: "4", 6: "b5", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"}
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII"]


def scale_notes(tonic: int, scale: str) -> list[int]:
    return [(tonic + i) % 12 for i in SCALES[scale]]


def uses_flats(tonic: int, scale: str) -> bool:
    """这个调写降号还是升号（F、Bb、Eb… 以及它们的关系小调写降号）。"""
    minorish = SCALES[scale][2] == 3 if len(SCALES[scale]) == 7 else scale.startswith("小调") or scale == "布鲁斯"
    major_tonic = (tonic + 3) % 12 if minorish else tonic
    return major_tonic in (5, 10, 3, 8, 1, 6)


def note_name(pc: int, flats: bool) -> str:
    return (ch.FLAT_NAMES if flats else ch.SHARP_NAMES)[pc % 12]


def diatonic_chords(tonic: int, scale: str, sevenths: bool = False) -> list[dict]:
    """七声音阶的调内和弦（三和弦或七和弦），带级数（I、ii、V7…）。五声 / 布鲁斯按对应的大调 / 小调给。"""
    base = scale
    if len(SCALES[scale]) != 7:
        base = "大调（自然大调）" if scale == "大调五声" else "小调（自然小调）"
    steps = SCALES[base]
    flats = uses_flats(tonic, base)
    out = []
    for degree in range(7):
        root = steps[degree]
        third = (steps[(degree + 2) % 7] - root) % 12
        fifth = (steps[(degree + 4) % 7] - root) % 12
        seventh = (steps[(degree + 6) % 7] - root) % 12
        if third == 4 and fifth == 7:
            quality, roman = "", ROMAN[degree]
        elif third == 3 and fifth == 7:
            quality, roman = "m", ROMAN[degree].lower()
        elif third == 3 and fifth == 6:
            quality, roman = "dim", ROMAN[degree].lower() + "°"
        else:
            quality, roman = "", ROMAN[degree] + "+"
        if sevenths and quality in ("", "m"):
            if quality == "" and seventh == 11:
                quality, roman = "maj7", roman + "maj7"
            elif quality == "" and seventh == 10:
                quality, roman = "7", roman + "7"
            elif quality == "m" and seventh == 10:
                quality, roman = "m7", roman + "7"
        name = ch.make_name((tonic + root) % 12, quality, flats=flats, sharps=not flats)
        out.append({"degree": degree, "roman": roman, "name": name})
    return out


# 和弦走向的“习惯”：从某一级到下一级有多常见（流行 / 摇滚 / 后摇里常见的走法）
_NEXT = {
    0: {3: 5, 4: 5, 5: 4, 1: 2, 2: 1, 6: 1},       # I → IV / V / vi …
    1: {4: 6, 3: 2, 0: 1, 5: 1},                   # ii → V
    2: {5: 5, 3: 3, 1: 1},                         # iii → vi / IV
    3: {0: 5, 4: 5, 1: 2, 5: 2, 2: 1},             # IV → I / V
    4: {0: 6, 5: 4, 3: 2},                         # V → I / vi
    5: {3: 5, 4: 3, 1: 3, 2: 2, 0: 2},             # vi → IV / V / ii
    6: {0: 5, 2: 2, 5: 1},                         # vii° → I
}
_NEXT_MINOR = {
    0: {5: 5, 2: 4, 6: 4, 3: 3, 4: 2},             # i → VI / III / VII / iv
    1: {4: 5, 6: 2},
    2: {5: 4, 6: 4, 3: 2},
    3: {0: 4, 6: 3, 4: 3},
    4: {0: 6, 5: 2},
    5: {6: 5, 2: 4, 3: 3, 0: 2},                   # VI → VII / III
    6: {0: 5, 2: 4, 5: 2},                         # VII → i / III
}

# 常用进行：名字 → 级数（0 = I）
PROGRESSIONS_MAJOR = {
    "流行 I–V–vi–IV": [0, 4, 5, 3],
    "卡农 I–V–vi–iii–IV–I–IV–V": [0, 4, 5, 2, 3, 0, 3, 4],
    "悲伤流行 vi–IV–I–V": [5, 3, 0, 4],
    "50 年代 I–vi–IV–V": [0, 5, 3, 4],
    "后摇：I–iii–vi–IV（渐强常用）": [0, 2, 5, 3],
    "后摇：IV–I（来回推）": [3, 0, 3, 0],
    "后摇：I–IV–vi–V": [0, 3, 5, 4],
    "民谣 I–IV–I–V": [0, 3, 0, 4],
    "爵士 ii–V–I": [1, 4, 0, 0],
}
PROGRESSIONS_MINOR = {
    "小调 i–VI–III–VII": [0, 5, 2, 6],
    "小调 i–VII–VI–VII": [0, 6, 5, 6],
    "安达卢西亚 i–VII–VI–V": [0, 6, 5, 4],
    "后摇：i–VI–III–VII（大段落）": [0, 5, 2, 6],
    "后摇：i–iv–VI–V": [0, 3, 5, 4],
    "摇滚 i–VI–VII–i": [0, 5, 6, 0],
}


def is_minor(scale: str) -> bool:
    steps = SCALES[scale]
    return (3 in steps and 4 not in steps) or scale.startswith("小调")


def suggest_next(last_degree: int | None, scale: str, count: int = 4) -> list[int]:
    table = _NEXT_MINOR if is_minor(scale) else _NEXT
    if last_degree is None:
        return [0, 3, 4, 5] if not is_minor(scale) else [0, 5, 2, 6]
    options = sorted(table.get(last_degree, {}).items(), key=lambda kv: -kv[1])
    return [d for d, _ in options][:count]


def progressions(scale: str) -> dict:
    return PROGRESSIONS_MINOR if is_minor(scale) else PROGRESSIONS_MAJOR


# ------------------------------------------------------------------ 试听：简单的拨弦音色（Karplus-Strong）

def _pluck(freq: float, seconds: float, rate: int, seed: int) -> list[float]:
    rnd = random.Random(seed)
    period = max(2, int(rate / freq))
    buf = [rnd.uniform(-1, 1) for _ in range(period)]
    out = []
    n = int(seconds * rate)
    idx = 0
    decay = 0.996
    for _ in range(n):
        a = buf[idx]
        b = buf[(idx + 1) % period]
        value = decay * 0.5 * (a + b)
        buf[idx] = value
        out.append(a)
        idx = (idx + 1) % period
    return out


def render_progression(names: list[str], bpm: float, path: str, beats_per_chord: int = 4, rate: int = 22050) -> None:
    """把一串和弦按吉他按法扫弦，写成 WAV 试听（每个和弦 beats_per_chord 拍，第 1、3 拍扫弦）。"""
    beat = 60.0 / bpm
    total = int((len(names) * beats_per_chord * beat + 1.5) * rate)
    mix = [0.0] * total
    for i, name in enumerate(names):
        frets = ch.shape(name)[0]
        pitches = [ch.STANDARD_TUNING[s] + f for s, f in enumerate(frets) if f >= 0]
        for hit in range(0, beats_per_chord, 2):
            start = int((i * beats_per_chord + hit) * beat * rate)
            length = beat * 2 + 0.3
            for k, p in enumerate(pitches):
                wave_ = _pluck(440 * 2 ** ((p - 69) / 12), length, rate, seed=p * 7 + hit)
                offset = start + int(k * 0.012 * rate)          # 扫弦：一根一根依次响
                gain = 0.22 * (0.9 if hit else 1.0)
                for j, v in enumerate(wave_):
                    if offset + j < total:
                        mix[offset + j] += v * gain
    peak = max(1e-6, max(abs(v) for v in mix))
    scale = 0.9 / peak if peak > 0.9 else 1.0
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v * scale)) * 32767)) for v in mix))


def write_midi(names: list[str], bpm: float, path: str, beats_per_chord: int = 4) -> None:
    """和弦进行导出成 MIDI（每个和弦一个全音符长度的柱式和弦），可以拖进宿主软件。"""
    ticks = 480

    def var(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)
    events = bytearray()
    tempo = int(60_000_000 / bpm)
    events += b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    events += b"\x00\xc0\x19"                                   # 钢弦吉他音色
    for name in names:
        frets = ch.shape(name)[0]
        pitches = [ch.STANDARD_TUNING[s] + f for s, f in enumerate(frets) if f >= 0]
        for p in pitches:
            events += b"\x00" + bytes([0x90, p, 80])
        for k, p in enumerate(pitches):
            events += var(ticks * beats_per_chord if k == 0 else 0) + bytes([0x80, p, 0])
    events += b"\x00\xff\x2f\x00"
    with open(path, "wb") as handle:
        handle.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ticks))
        handle.write(b"MTrk" + struct.pack(">I", len(events)) + bytes(events))
