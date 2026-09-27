"""吉他和弦：名字 ↔ 音、按法（指法图）、难度，以及变调夹建议。
纯 Python，主程序和 AI 进程都能用。弦的顺序：6 弦（低音 E）→ 1 弦（高音 e）；-1 = 不弹，0 = 空弦。"""

from __future__ import annotations

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_ALIASES = {"Db": 1, "D#": 3, "Gb": 6, "G#": 8, "A#": 10, "Cb": 11, "E#": 5, "Fb": 4, "B#": 0}
STANDARD_TUNING = [40, 45, 50, 55, 59, 64]   # E2 A2 D3 G3 B3 E4（MIDI 音高）

# 和弦种类 → 组成音（相对根音的半音数）
QUALITIES = {
    "": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
    "sus4": (0, 5, 7), "sus2": (0, 2, 7), "dim": (0, 3, 6),
}

# 常用开放和弦（好按）
OPEN_SHAPES = {
    "C": [-1, 3, 2, 0, 1, 0], "C7": [-1, 3, 2, 3, 1, 0], "Cmaj7": [-1, 3, 2, 0, 0, 0], "Csus2": [-1, 3, 0, 0, 1, 3],
    "D": [-1, -1, 0, 2, 3, 2], "Dm": [-1, -1, 0, 2, 3, 1], "D7": [-1, -1, 0, 2, 1, 2], "Dmaj7": [-1, -1, 0, 2, 2, 2],
    "Dm7": [-1, -1, 0, 2, 1, 1], "Dsus4": [-1, -1, 0, 2, 3, 3], "Dsus2": [-1, -1, 0, 2, 3, 0],
    "E": [0, 2, 2, 1, 0, 0], "Em": [0, 2, 2, 0, 0, 0], "E7": [0, 2, 0, 1, 0, 0], "Em7": [0, 2, 2, 0, 3, 0],
    "Emaj7": [0, 2, 1, 1, 0, 0], "Esus4": [0, 2, 2, 2, 0, 0],
    "Fmaj7": [-1, -1, 3, 2, 1, 0],
    "G": [3, 2, 0, 0, 0, 3], "G7": [3, 2, 0, 0, 0, 1], "Gmaj7": [3, 2, 0, 0, 0, 2], "Gsus4": [3, 3, 0, 0, 1, 3],
    "A": [-1, 0, 2, 2, 2, 0], "Am": [-1, 0, 2, 2, 1, 0], "A7": [-1, 0, 2, 0, 2, 0], "Am7": [-1, 0, 2, 0, 1, 0],
    "Amaj7": [-1, 0, 2, 1, 2, 0], "Asus4": [-1, 0, 2, 2, 3, 0], "Asus2": [-1, 0, 2, 2, 0, 0],
    "B7": [-1, 2, 1, 2, 0, 2],
}

# 横按：根音在 6 弦（E 型）或 5 弦（A 型）；数字是相对根音所在品的偏移，None = 不弹
_E_SHAPE = {"": [0, 2, 2, 1, 0, 0], "m": [0, 2, 2, 0, 0, 0], "7": [0, 2, 0, 1, 0, 0], "m7": [0, 2, 0, 0, 0, 0],
            "sus4": [0, 2, 2, 2, 0, 0]}
_A_SHAPE = {"": [None, 0, 2, 2, 2, 0], "m": [None, 0, 2, 2, 1, 0], "7": [None, 0, 2, 0, 2, 0],
            "m7": [None, 0, 2, 0, 1, 0], "maj7": [None, 0, 2, 1, 2, 0], "sus4": [None, 0, 2, 2, 3, 0],
            "sus2": [None, 0, 2, 2, 0, 0], "dim": [None, 0, 1, 2, 1, None]}


def pitch_class(name: str) -> int:
    name = name.strip()
    if name[:2] in _ALIASES:
        return _ALIASES[name[:2]]
    if name[:2] in NOTE_NAMES:
        return NOTE_NAMES.index(name[:2])
    return NOTE_NAMES.index(name[:1])


def split_name(chord: str) -> tuple[int, str, int | None]:
    """"F#m7/C#" → (根音, "m7", 低音)。"""
    main, _, bass = chord.partition("/")
    root_len = 2 if len(main) > 1 and main[1] in "#b" else 1
    root = pitch_class(main[:root_len])
    quality = main[root_len:]
    return root, (quality if quality in QUALITIES else ""), (pitch_class(bass) if bass else None)


SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def make_name(root: int, quality: str, bass: int | None = None, flats: bool = False, sharps: bool = False) -> str:
    names = FLAT_NAMES if flats else SHARP_NAMES if sharps else NOTE_NAMES
    text = names[root % 12] + quality
    if bass is not None and bass % 12 != root % 12:
        text += "/" + names[bass % 12]
    return text


def transpose(chord: str, semitones: int) -> str:
    if chord in ("N", ""):
        return chord
    root, quality, bass = split_name(chord)
    return make_name(root + semitones, quality, None if bass is None else bass + semitones)


def chord_tones(chord: str) -> set[int]:
    root, quality, bass = split_name(chord)
    tones = {(root + i) % 12 for i in QUALITIES[quality]}
    if bass is not None:
        tones.add(bass % 12)
    return tones


def shape(chord: str) -> tuple[list[int], int]:
    """(6 根弦的品位, 难度)。难度：开放和弦 1，横按 3（高把位再 +1），其他 4。"""
    if chord in ("N", ""):
        return [-1] * 6, 0
    root, quality, bass = split_name(chord)
    base = make_name(root, quality)
    for key in (base, make_name(root, quality, flats=True)):
        if key in OPEN_SHAPES:
            frets = list(OPEN_SHAPES[key])
            return _with_bass(frets, bass), 1
    options = []
    for table, string_root in ((_E_SHAPE, 40), (_A_SHAPE, 45)):
        if quality in table:
            fret = (root - string_root) % 12 or 12
            frets = [-1 if o is None else fret + o for o in table[quality]]
            options.append((fret, frets))
    if not options:   # 不认识的种类：退回三和弦
        return shape(make_name(root, "", bass))
    fret, frets = min(options)
    return _with_bass(frets, bass), 3 + (1 if fret > 7 else 0) + (1 if quality == "dim" else 0)


def _with_bass(frets: list[int], bass: int | None) -> list[int]:
    """转位和弦（C/E）：尽量让最低的一根弦弹指定的低音。"""
    if bass is None:
        return frets
    frets = list(frets)
    for string in (0, 1):   # 6 弦或 5 弦上找低音（不超过 4 品）
        for fret in range(0, 5):
            if (STANDARD_TUNING[string] + fret) % 12 == bass % 12:
                frets[string] = fret
                for lower in range(string):
                    frets[lower] = -1
                return frets
    return frets


def suggest_capo(chords: dict[str, float], max_capo: int = 7) -> dict:
    """chords：{和弦名: 出现的拍数}。试 0–7 品变调夹，选按起来最省力的；分数一样时选低的品。"""
    best = None
    for capo in range(0, max_capo + 1):
        cost, shapes = 0.0, {}
        for name, weight in chords.items():
            if name == "N":
                continue
            played = transpose(name, -capo)
            shapes[name] = played
            cost += shape(played)[1] * weight
        cost += capo * 0.5 * (sum(chords.values()) / 50.0)   # 同样好按时，变调夹越低越好
        if best is None or cost < best["cost"] - 1e-6:
            best = {"capo": capo, "cost": cost, "shapes": shapes}
    return best


def key_name(tonic: int, minor: bool, sharps: bool = False, flats: bool = False) -> str:
    return make_name(tonic, "m" if minor else "", sharps=sharps, flats=flats) + (" 小调" if minor else " 大调")
