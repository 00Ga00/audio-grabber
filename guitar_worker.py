"""吉他扒谱（AI 环境里运行，由常驻 AI 进程调用）：
  --guitar IN --out DIR --models DIR [--chords] [--solo] [--mono] [--size large --beam 1 --parallel 1]

步骤：六轨分离（得到吉他、贝斯、伴奏）→ 找拍子和小节线 → 认和弦（弹唱）→ 扒吉他音符 → 排六线谱把位
→ 写 Guitar Pro（.gp5）、和弦谱（HTML）和 result.json。"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chords as ch  # noqa: E402
from concert_worker import emit  # noqa: E402

GUITAR_GROUPS = ["acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar"]
UNITS = 12            # 每拍 12 个单位：16 分音符 = 3，三连音 8 分 = 4
MAX_FRET = 22


# ------------------------------------------------------------------ 分离

def separate(path: str, models_dir: str, out: str) -> dict:
    """BS-Roformer SW 六轨分离；存下 吉他.wav、伴奏（去掉吉他）.wav，返回各轨的数组。"""
    import tempfile
    import numpy as np
    import soundfile as sf
    import concert_worker as cw
    from transcribe_worker import STEM_MODEL

    cw._install_hooks()
    cw.hook_byte_downloads("BS-Roformer SW 六轨分离模型")
    tracks = {}
    with tempfile.TemporaryDirectory(prefix="guitar_") as tmp:
        if not os.path.isfile(os.path.join(models_dir, STEM_MODEL)):
            emit("notice", message="第一次使用，正在下载六轨分离模型（BS-Roformer SW）……")
        sep = cw.make_separator(models_dir, tmp)
        sep.load_model(STEM_MODEL)
        cw.PROGRESS["cb"] = lambda x: emit("progress", stage="分离吉他、贝斯和其他声部", fraction=0.03 + 0.32 * x)
        outputs = sep.separate(path)
        cw.PROGRESS["cb"] = None
        rate = 44100
        for name in outputs:
            full = name if os.path.isabs(name) else os.path.join(tmp, name)
            label = os.path.basename(full).rsplit("(", 1)[-1].split(")")[0].strip().lower()
            data, rate = sf.read(full, dtype="float32", always_2d=True)
            tracks[label] = data
    length = min(len(v) for v in tracks.values())
    tracks = {k: v[:length] for k, v in tracks.items()}
    mix = sum(tracks.values())
    guitar = tracks.get("guitar", np.zeros_like(mix))
    sf.write(os.path.join(out, "guitar.wav"), guitar, rate, subtype="PCM_16")
    sf.write(os.path.join(out, "backing.wav"), np.clip(mix - guitar, -1, 1), rate, subtype="PCM_16")
    sf.write(os.path.join(out, "mix.wav"), np.clip(mix, -1, 1), rate, subtype="PCM_16")
    tracks["_rate"] = rate
    return tracks


# ------------------------------------------------------------------ 拍子

def detect_beats(mix, rate: int, gpu: bool):
    """Beat This!（和扒谱用的同一个节拍模型）；不可用时退回 librosa。返回 (拍子时间, 强拍时间)。"""
    import numpy as np
    mono = mix.mean(axis=1).astype(np.float32)
    try:
        import concert_worker as cw
        cw.DOWNLOAD_NAME["name"] = "Beat This! 节拍模型"
        cw.hook_byte_downloads()
        from beat_this.inference import Audio2Beats
        beats, downbeats = Audio2Beats(checkpoint_path="final0", device="cuda" if gpu else "cpu", dbn=False)(mono, rate)
        beats, downbeats = np.asarray(beats, float), np.asarray(downbeats, float)
        if len(beats) >= 8:
            return beats, downbeats
    except Exception as error:
        emit("notice", message=f"节拍模型不可用（{type(error).__name__}），改用简单的节拍检测。")
    import librosa
    y = librosa.resample(mono, orig_sr=rate, target_sr=22050)
    _, frames = librosa.beat.beat_track(y=y, sr=22050, hop_length=512, units="frames")
    beats = librosa.frames_to_time(frames, sr=22050, hop_length=512)
    if len(beats) < 4:
        beats = np.arange(0, len(mono) / rate, 0.5)
    if len(beats) >= 8 and 60.0 / float(np.median(np.diff(beats))) > 140:   # 常见的“快一倍”错误：隔一拍取一拍
        frames, beats = frames[::2], beats[::2]
    # 强拍：每 4 拍一个，从低频最重的相位开始
    onset = librosa.onset.onset_strength(y=y, sr=22050, hop_length=512, fmax=200)
    strength = [onset[np.clip(frames[i::4], 0, len(onset) - 1)].mean() if len(frames[i::4]) else 0 for i in range(4)]
    phase = int(np.argmax(strength)) if len(frames) >= 8 else 0
    return beats, beats[phase::4]


def beat_frame(beats, downbeats, duration: float):
    """把拍子补满整段（前后按平均拍长外推），并算出每小节几拍、第一个强拍在第几拍。"""
    import numpy as np
    period = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5
    before = np.arange(beats[0] - period, -period * 4, -period)[::-1] if len(beats) else np.array([])
    after = np.arange(beats[-1] + period, duration + period * 4, period)
    full = np.concatenate([before, beats, after])
    first_db = downbeats[0] if len(downbeats) else beats[0]
    d0 = int(np.argmin(np.abs(full - first_db)))
    per_bar = 4
    if len(downbeats) >= 3:
        idx = [int(np.argmin(np.abs(full - d))) for d in downbeats]
        gaps = [b - a for a, b in zip(idx, idx[1:]) if 2 <= b - a <= 7]
        if gaps:
            per_bar = max(set(gaps), key=gaps.count)
    start = d0 % per_bar           # 从第一小节的强拍对齐（前面不足一小节的作弱起）
    return full, start, per_bar, 60.0 / period


# ------------------------------------------------------------------ 和弦

def chord_templates():
    """和弦模板：N（没有和弦）+ 12 个根音 × 8 种和弦；少见的种类略减分，避免乱认。"""
    import numpy as np
    labels = ["N"] + [ch.make_name(r, q) for q in ch.QUALITIES for r in range(12)]
    templates, prior = [np.zeros(12)], [0.0]
    penalty = {"": 0.0, "m": 0.0, "7": 0.04, "maj7": 0.05, "m7": 0.04, "sus4": 0.07, "sus2": 0.08, "dim": 0.08}
    for q, intervals in ch.QUALITIES.items():
        for r in range(12):
            t = np.zeros(12)
            for i in intervals:
                t[(r + i) % 12] = 1.0
            t[r] = 1.3                                  # 根音稍微加重
            templates.append(t / np.linalg.norm(t))
            prior.append(-penalty[q])
    return labels, np.array(templates), np.array(prior)


def recognize_chords(tracks: dict, beats_full, bar_start: int, per_bar: int, duration: float) -> dict:
    import librosa
    import numpy as np

    rate = tracks["_rate"]
    harm = sum(v for k, v in tracks.items() if k in ("guitar", "piano", "other", "bass")).mean(axis=1)
    bass = tracks.get("bass")
    sr = 22050
    y = librosa.resample(harm.astype(np.float32), orig_sr=rate, target_sr=sr)
    tuning = float(librosa.estimate_tuning(y=y, sr=sr))
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop, tuning=tuning, n_octaves=6, bins_per_octave=36)
    if bass is not None:
        yb = librosa.resample(bass.mean(axis=1).astype(np.float32), orig_sr=rate, target_sr=sr)
        bass_chroma = librosa.feature.chroma_cqt(y=yb, sr=sr, hop_length=hop, tuning=tuning, fmin=librosa.note_to_hz("E1"),
                                                 n_octaves=3, bins_per_octave=36)
    else:
        bass_chroma = np.zeros_like(chroma)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    n = chroma.shape[1]
    beat_frames = np.clip(librosa.time_to_frames(beats_full, sr=sr, hop_length=hop), 0, n)
    beat_frames = np.unique(np.concatenate([[0], beat_frames, [n]]))
    edges = list(zip(beat_frames[:-1], beat_frames[1:]))
    times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)

    labels, templates, prior = chord_templates()
    loud = np.percentile(rms, 90) + 1e-9
    scores, bass_pcs = [], []
    for a, b in edges:
        if b <= a:
            b = a + 1
        c = chroma[:, a:b].mean(axis=1)
        bc = bass_chroma[:, a:b].mean(axis=1)
        energy = rms[a:b].mean() / loud if b <= len(rms) else 0
        v = c / (np.linalg.norm(c) + 1e-9)
        s = templates @ v + np.array(prior)
        bn = bc / (bc.max() + 1e-9)
        for k in range(1, len(labels)):
            root = (k - 1) % 12
            s[k] += 0.15 * bn[root]                      # 贝斯弹的是根音时加分
        s[0] = 0.55 if energy < 0.08 else -1.0           # 很安静 = 没有和弦
        scores.append(s)
        bass_pcs.append((int(np.argmax(bc)), float(bc.max() / (bc.mean() + 1e-9))))
    scores = np.array(scores) * 10
    # Viterbi：换和弦要付出代价，避免每拍乱跳
    change = 2.2
    m = len(scores)
    best = scores[0].copy()
    back = np.zeros((m, len(labels)), dtype=int)
    for i in range(1, m):
        stay = best
        move = best.max() - change
        back[i] = np.where(stay >= move, np.arange(len(labels)), int(best.argmax()))
        best = np.maximum(stay, move) + scores[i]
    path = [int(best.argmax())]
    for i in range(m - 1, 0, -1):
        path.append(back[i][path[-1]])
    path = path[::-1]

    segments = []
    for i, k in enumerate(path):
        name = labels[k]
        if segments and segments[-1]["name"] == name:
            segments[-1]["end"] = float(times[i + 1])
            segments[-1]["beats"] += 1
            segments[-1]["_bass"].append(bass_pcs[i])
        else:
            segments.append({"name": name, "start": float(times[i]), "end": float(times[i + 1]), "beats": 1,
                             "_bass": [bass_pcs[i]]})
    # 转位：贝斯稳定地弹和弦里的另一个音（三音、五音、七音）→ C/E
    for seg in segments:
        if seg["name"] != "N":
            votes = [pc for pc, peak in seg.pop("_bass") if peak > 2.0]
            root = ch.split_name(seg["name"])[0]
            if votes:
                pc = max(set(votes), key=votes.count)
                if votes.count(pc) >= max(2, len(votes) * 0.6) and pc != root and pc in ch.chord_tones(seg["name"]):
                    seg["name"] = ch.make_name(root, ch.split_name(seg["name"])[1], pc)
        else:
            seg.pop("_bass")
    segments = [s for s in segments if s["end"] > 0 and s["start"] < duration]

    # 调性（Krumhansl）
    total = chroma.mean(axis=1)
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    best_key = max(((np.corrcoef(total, np.roll(p, k))[0, 1], k, is_minor) for is_minor, p in ((False, major), (True, minor))
                    for k in range(12)))
    flats = best_key[1] in (1, 3, 5, 8, 10) or (best_key[2] and best_key[1] in (0, 2, 7))
    if flats:
        for seg in segments:
            if seg["name"] != "N":
                r, q, b = ch.split_name(seg["name"])
                seg["name"] = ch.make_name(r, q, b, flats=True)

    # 按小节排：每小节最多两个和弦
    beat_times = beats_full
    bars = []
    first = bar_start
    while first < len(beat_times) and beat_times[first] < duration:
        a = beat_times[first]
        b = beat_times[first + per_bar] if first + per_bar < len(beat_times) else duration
        if b <= 0:
            first += per_bar
            continue
        a = max(a, 0.0)
        names = []
        for seg in segments:
            overlap = min(seg["end"], b) - max(seg["start"], a)
            if overlap > (b - a) * 0.2 and (not names or names[-1] != seg["name"]):
                names.append(seg["name"])
        bars.append({"start": float(a), "chords": names[:3] or ["N"]})
        first += per_bar
    counts = {}
    for seg in segments:
        counts[seg["name"]] = counts.get(seg["name"], 0) + seg["beats"]
    capo = ch.suggest_capo(counts)
    return {"segments": segments, "bars": bars, "key": ch.key_name(best_key[1], best_key[2]),
            "tuning_cents": round(tuning * 100), "capo": capo["capo"], "capo_shapes": capo["shapes"], "counts": counts}


# ------------------------------------------------------------------ 扒音符

def transcribe_notes(guitar_path: str, size: str, beam: int, parallel: int, gpu: bool, mono: bool) -> list[dict]:
    import torch
    from muscriptor.events import NoteEndEvent, ProgressEvent
    from muscriptor.transcription_model import TranscriptionModel
    import concert_worker as cw

    cw.hook_byte_downloads(f"MuScriptor 扒谱模型（{size}）")
    emit("progress", stage="加载扒谱模型", fraction=0.4)
    model = TranscriptionModel.load_model(weights_path=size, device="cuda" if gpu else "cpu",
                                          dtype=torch.float16 if gpu else None)
    options = dict(instruments=GUITAR_GROUPS, beam_size=max(1, beam))
    if parallel > 1 and beam <= 1:
        options.update(batch_size=parallel, prelude_forcing=False)
    notes = []
    for event in model.transcribe(guitar_path, **options):
        if isinstance(event, ProgressEvent):
            if event.total:
                emit("progress", stage=f"扒吉他音符 {event.completed}/{event.total} 段",
                     fraction=0.45 + 0.4 * event.completed / event.total)
        elif isinstance(event, NoteEndEvent):
            start = event.start_event
            if 36 <= start.pitch <= 90:
                notes.append({"start": max(0.0, start.start_time - 0.02), "end": max(start.start_time + 0.03, event.end_time - 0.02),
                              "pitch": int(start.pitch)})
    del model
    notes.sort(key=lambda n: (n["start"], -n["pitch"]))
    if mono:
        notes = skyline(notes)
    return notes


def skyline(notes: list[dict]) -> list[dict]:
    """Solo 只要旋律：同时响的音只留最高的，并把前一个音截到下一个音开始。"""
    kept = []
    for n in notes:
        if kept and abs(n["start"] - kept[-1]["start"]) < 0.03:
            if n["pitch"] > kept[-1]["pitch"]:
                kept[-1] = dict(n)
            continue
        if kept and kept[-1]["end"] > n["start"]:
            kept[-1]["end"] = n["start"]
        kept.append(dict(n))
    return [n for n in kept if n["end"] - n["start"] > 0.02]


# ------------------------------------------------------------------ 六线谱把位

def positions(pitch: int, tuning=ch.STANDARD_TUNING) -> list[tuple[int, int]]:
    """(弦 0=6 弦…5=1 弦, 品) 的所有按法。"""
    return [(s, pitch - open_) for s, open_ in enumerate(tuning) if 0 <= pitch - open_ <= MAX_FRET]


def assign_tab(groups: list[list[int]]) -> list[list[tuple[int, int]]]:
    """动态规划选最顺手的把位：手的位置移动最少、同一组音在 4 品以内、低把位和空弦略优先。
    groups：按时间顺序的“同时弹的音”列表。"""
    import itertools

    def options(group):
        per = [positions(p) for p in group]
        out = []
        for combo in itertools.product(*per):
            strings = [s for s, _ in combo]
            if len(set(strings)) != len(strings):
                continue
            fretted = [f for _, f in combo if f > 0]
            span = (max(fretted) - min(fretted)) if fretted else 0
            if span > 4:
                continue
            out.append(combo)
        if not out:   # 按不出来：去掉最低的音再试
            return options(sorted(group)[1:]) if len(group) > 1 else [((0, 0),)]
        return out[:200]

    def hand(combo):
        fretted = [f for _, f in combo if f > 0]
        return sum(fretted) / len(fretted) if fretted else None

    def own_cost(combo):
        fretted = [f for _, f in combo if f > 0]
        cost = 0.03 * sum(fretted) / max(len(combo), 1)          # 低把位略优先
        cost += 0.2 * ((max(fretted) - min(fretted)) if fretted else 0)
        return cost

    layers = [options(g) for g in groups]
    if not layers:
        return []
    costs = [own_cost(c) for c in layers[0]]
    backs = []
    for i in range(1, len(layers)):
        new_costs, back = [], []
        for c in layers[i]:
            h = hand(c)
            best, arg = None, 0
            for j, p in enumerate(layers[i - 1]):
                hp = hand(p)
                move = abs(h - hp) if (h is not None and hp is not None) else 0.5
                total = costs[j] + move + (0.8 if move > 4 else 0)
                if best is None or total < best:
                    best, arg = total, j
            new_costs.append(best + own_cost(c))
            back.append(arg)
        costs = new_costs
        backs.append(back)
    k = min(range(len(costs)), key=costs.__getitem__)
    chosen = [k]
    for back in reversed(backs):
        k = back[k]
        chosen.append(k)
    chosen.reverse()
    return [list(layers[i][k]) for i, k in enumerate(chosen)]


# ------------------------------------------------------------------ 对齐到节拍、写 Guitar Pro

def to_units(t: float, beats_full, bar_start: int, grids: dict) -> int:
    import numpy as np
    idx = float(np.interp(t, beats_full, np.arange(len(beats_full)))) - bar_start
    beat = math.floor(idx)
    frac = idx - beat
    triplet = grids.get(beat, False)
    step = 4 if triplet else 3
    return int(beat * UNITS + round(frac * UNITS / step) * step)


def choose_grids(notes: list[dict], beats_full, bar_start: int) -> dict:
    """每一拍选 16 分音符网格还是三连音网格（哪个离实际起音更近）。"""
    import numpy as np
    per_beat = {}
    for n in notes:
        idx = float(np.interp(n["start"], beats_full, np.arange(len(beats_full)))) - bar_start
        per_beat.setdefault(math.floor(idx), []).append(idx - math.floor(idx))
    grids = {}
    for beat, fracs in per_beat.items():
        straight = sum(min(abs(f - k / 4) for k in range(5)) for f in fracs)
        triplet = sum(min(abs(f - k / 3) for k in range(4)) for f in fracs)
        grids[beat] = triplet < straight * 0.6
    return grids


# 单位长度 → (时值, 附点, 三连音)
_DURATIONS = {48: (1, False, False), 36: (2, True, False), 24: (2, False, False), 18: (4, True, False), 12: (4, False, False),
              9: (8, True, False), 6: (8, False, False), 3: (16, False, False),
              8: (4, False, True), 4: (8, False, True), 2: (16, False, True), 1: (32, False, True)}


def split_units(position: int, length: int, bar_units: int) -> list[int]:
    """把一段时长拆成标准时值（不跨小节；拍内的三连音不跨拍）。"""
    parts = []
    while length > 0:
        in_bar = bar_units - position % bar_units
        room = min(length, in_bar)
        off = position % UNITS
        if off == 0 and room >= 12:
            piece = next(u for u in (48, 36, 24, 18, 12) if u <= room)
        else:
            beat_room = min(room, UNITS - off)
            straight = [u for u in (9, 6, 3) if u <= beat_room and off % 3 == 0]
            triple = [u for u in (8, 4, 2) if u <= beat_room and off % 2 == 0 and (off % 4 == 0 or u == 2)]
            piece = max(straight + triple + [1])
        parts.append(piece)
        position += piece
        length -= piece
    return parts


def write_gp5(path: str, title: str, tempo: float, per_bar: int, events: list[dict], chord_marks: dict) -> None:
    """events：[{"at": 单位, "len": 单位, "frets": [(弦, 品), ...]}]（按时间、不重叠）；chord_marks：{单位: 和弦名}。"""
    import guitarpro as gp
    from guitarpro import models as m

    bar_units = per_bar * UNITS
    end = max([e["at"] + e["len"] for e in events] + [bar_units])
    n_bars = max(1, math.ceil(end / bar_units))
    song = gp.Song()
    song.title = title
    song.tempo = int(round(tempo))
    track = song.tracks[0]
    track.name = "吉他"
    track.channel.instrument = 25          # 钢弦吉他音色（Guitar Pro 里试听用）
    for _ in range(n_bars - 1):
        song.newMeasure()
    for header in song.measureHeaders:
        header.timeSignature = m.TimeSignature(numerator=per_bar, denominator=m.Duration(value=4))

    timeline = []            # (起点, 长度, frets 或 None=休止, 是否延音)
    cursor = 0
    for e in sorted(events, key=lambda x: x["at"]):
        if e["at"] > cursor:
            timeline.append((cursor, e["at"] - cursor, None))
        timeline.append((e["at"], e["len"], e["frets"]))
        cursor = e["at"] + e["len"]
    if cursor < n_bars * bar_units:
        timeline.append((cursor, n_bars * bar_units - cursor, None))

    marks = sorted(chord_marks.items())
    for at, length, frets in timeline:
        position, first = at, True
        for piece in split_units(at, length, bar_units):
            bar = position // bar_units
            voice = track.measures[bar].voices[0]
            value, dotted, triplet = _DURATIONS[piece]
            duration = m.Duration(value=value, isDotted=dotted,
                                  tuplet=m.Tuplet(enters=3, times=2) if triplet else m.Tuplet(1, 1))
            beat = m.Beat(voice, duration=duration, status=m.BeatStatus.normal if frets else m.BeatStatus.rest)
            label = None
            while marks and marks[0][0] <= position:      # 和弦名标在它开始后的第一个拍子上
                label = marks.pop(0)[1]
            if label:
                beat.text = label
            if frets:
                for string, fret in frets:
                    beat.notes.append(m.Note(beat, value=fret, string=6 - string,
                                             type=m.NoteType.normal if first else m.NoteType.tie))
            voice.beats.append(beat)
            position += piece
            first = False
    for measure in track.measures:
        if not measure.voices[0].beats:
            measure.voices[0].beats.append(m.Beat(measure.voices[0], duration=m.Duration(value=1), status=m.BeatStatus.rest))
    gp.write(song, path, version=(5, 1, 0), encoding="utf-8")   # MuseScore 默认按 UTF-8 读 Guitar Pro 文件


def _timeline(events: list[dict], bar_units: int) -> tuple[list, int]:
    end = max([e["at"] + e["len"] for e in events] + [bar_units])
    n_bars = max(1, math.ceil(end / bar_units))
    timeline, cursor = [], 0
    for e in sorted(events, key=lambda x: x["at"]):
        if e["at"] > cursor:
            timeline.append((cursor, e["at"] - cursor, None))
        timeline.append((e["at"], e["len"], e["frets"]))
        cursor = e["at"] + e["len"]
    if cursor < n_bars * bar_units:
        timeline.append((cursor, n_bars * bar_units - cursor, None))
    return timeline, n_bars


_KINDS = {"": "major", "m": "minor", "7": "dominant", "maj7": "major-seventh", "m7": "minor-seventh",
          "sus4": "suspended-fourth", "sus2": "suspended-second", "dim": "diminished"}
_TYPES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "16th", 32: "32nd"}


def write_musicxml(path: str, title: str, tempo: float, per_bar: int, events: list[dict], chord_marks: dict,
                   flats: bool = False) -> None:
    """五线谱 + 六线谱（TAB，写明第几弦第几品）两行谱，和弦名写在上面；MuseScore 打开或转 PDF 都带六线谱。"""
    from xml.sax.saxutils import escape

    bar_units = per_bar * UNITS
    timeline, n_bars = _timeline(events, bar_units)
    names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"] if flats else ch.NOTE_NAMES
    marks = sorted(chord_marks.items())
    bars = [[] for _ in range(n_bars)]      # 每小节：[(harmony, 时值, notes, 是否延音开始/结束)]
    for at, length, frets in timeline:
        position = at
        pieces = split_units(at, length, bar_units)
        for i, piece in enumerate(pieces):
            label = None
            while marks and marks[0][0] <= position:
                label = marks.pop(0)[1]
            bars[position // bar_units].append((label, piece, frets, i > 0, i < len(pieces) - 1))
            position += piece

    def pitch_xml(midi):
        name = names[midi % 12]
        alter = 1 if "#" in name else -1 if "b" in name[1:] else 0
        return (f"<pitch><step>{name[0]}</step>" + (f"<alter>{alter}</alter>" if alter else "") +
                f"<octave>{midi // 12 - 1}</octave></pitch>")

    def harmony_xml(label):
        root, quality, bass = ch.split_name(label)
        def step(pc):
            n = names[pc % 12]
            alter = 1 if "#" in n else -1 if "b" in n[1:] else 0
            return n[0], alter
        r, ra = step(root)
        xml = (f"<harmony><root><root-step>{r}</root-step>" + (f"<root-alter>{ra}</root-alter>" if ra else "") +
               f"</root><kind text=\"{escape(quality)}\">{_KINDS.get(quality, 'major')}</kind>")
        if bass is not None:
            b, ba = step(bass)
            xml += f"<bass><bass-step>{b}</bass-step>" + (f"<bass-alter>{ba}</bass-alter>" if ba else "") + "</bass>"
        return xml + "</harmony>"

    def notes_xml(piece, frets, tie_stop, tie_start, staff):
        value, dotted, triplet = _DURATIONS[piece]
        common = (f"<duration>{piece}</duration>" + ("<tie type=\"stop\"/>" if tie_stop else "") +
                  ("<tie type=\"start\"/>" if tie_start else "") + f"<voice>{staff}</voice><type>{_TYPES[value]}</type>" +
                  ("<dot/>" if dotted else "") +
                  ("<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>" if triplet else "") +
                  f"<staff>{staff}</staff>")
        if not frets:
            return f"<note><rest/>{common}</note>"
        out = []
        for k, (string, fret) in enumerate(sorted(frets, key=lambda x: -x[0])):
            midi = ch.STANDARD_TUNING[string] + fret
            notations = ""
            ties = ("<tied type=\"stop\"/>" if tie_stop else "") + ("<tied type=\"start\"/>" if tie_start else "")
            technical = f"<technical><string>{6 - string}</string><fret>{fret}</fret></technical>" if staff == 2 else ""
            if ties or technical:
                notations = f"<notations>{ties}{technical}</notations>"
            out.append(f"<note>{'<chord/>' if k else ''}{pitch_xml(midi)}{common}{notations}</note>")
        return "".join(out)

    parts = []
    for index, content in enumerate(bars):
        xml = [f'<measure number="{index + 1}">']
        if index == 0:
            tuning = "".join(f'<staff-tuning line="{i + 1}"><tuning-step>{"EADGBE"[i]}</tuning-step>'
                             f'<tuning-octave>{[2, 2, 3, 3, 3, 4][i]}</tuning-octave></staff-tuning>' for i in range(6))
            xml.append(f"<attributes><divisions>{UNITS}</divisions><key><fifths>0</fifths></key>"
                       f"<time><beats>{per_bar}</beats><beat-type>4</beat-type></time><staves>2</staves>"
                       "<clef number=\"1\"><sign>G</sign><line>2</line><clef-octave-change>-1</clef-octave-change></clef>"
                       "<clef number=\"2\"><sign>TAB</sign><line>5</line></clef>"
                       f"<staff-details number=\"2\"><staff-lines>6</staff-lines>{tuning}</staff-details></attributes>")
            xml.append(f'<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>'
                       f'<per-minute>{int(round(tempo))}</per-minute></metronome></direction-type><sound tempo="{int(round(tempo))}"/></direction>')
        for label, piece, frets, tie_stop, tie_start in content:
            if label:
                xml.append(harmony_xml(label))
            xml.append(notes_xml(piece, frets, tie_stop, tie_start, 1))
        xml.append(f"<backup><duration>{bar_units}</duration></backup>")
        for label, piece, frets, tie_stop, tie_start in content:
            xml.append(notes_xml(piece, frets, tie_stop, tie_start, 2))
        xml.append("</measure>")
        parts.append("".join(xml))
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
           '"http://www.musicxml.org/dtds/partwise.dtd">\n<score-partwise version="4.0">'
           f"<work><work-title>{escape(title)}</work-title></work>"
           '<part-list><score-part id="P1"><part-name>吉他</part-name><midi-instrument id="P1-I1"><midi-program>26</midi-program>'
           '</midi-instrument></score-part></part-list><part id="P1">' + "".join(parts) + "</part></score-partwise>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(doc)


# ------------------------------------------------------------------ 和弦谱 HTML

def diagram_svg(name: str, frets: list[int], size: float = 1.0) -> str:
    fretted = [f for f in frets if f > 0]
    low = min(fretted) if fretted and max(fretted) > 4 else 1
    w, h, x0, y0, dx, dy = 96, 120, 18, 30, 12, 16
    parts = [f'<svg viewBox="0 0 {w} {h}" width="{w * size:.0f}" height="{h * size:.0f}" class="dia">',
             f'<text x="{w / 2}" y="14" class="cn">{name}</text>']
    for s in range(6):
        parts.append(f'<line x1="{x0 + s * dx}" y1="{y0}" x2="{x0 + s * dx}" y2="{y0 + 4 * dy}" class="st"/>')
    for f in range(5):
        cls = "nut" if (f == 0 and low == 1) else "fr"
        parts.append(f'<line x1="{x0}" y1="{y0 + f * dy}" x2="{x0 + 5 * dx}" y2="{y0 + f * dy}" class="{cls}"/>')
    if low > 1:
        parts.append(f'<text x="{x0 + 5 * dx + 6}" y="{y0 + dy * 0.7}" class="pos">{low}</text>')
    for s, f in enumerate(frets):
        x = x0 + s * dx
        if f < 0:
            parts.append(f'<text x="{x}" y="{y0 - 5}" class="mk">×</text>')
        elif f == 0:
            parts.append(f'<circle cx="{x}" cy="{y0 - 9}" r="3.2" class="open"/>')
        else:
            parts.append(f'<circle cx="{x}" cy="{y0 + (f - low + 0.5) * dy}" r="4.8" class="dot"/>')
    parts.append("</svg>")
    return "".join(parts)


def tab_svg(events: list[dict], chord_marks: dict, per_bar: int, bars_per_line: int = 4) -> str:
    """六线谱（SVG）：每行 4 小节，数字 = 品位，最上面一根线是 1 弦；和弦名写在上面，小竖线是拍子。"""
    bar_units = per_bar * UNITS
    end = max([e["at"] + e["len"] for e in events] + [bar_units])
    n_bars = max(1, math.ceil(end / bar_units))
    line_w, bar_w, left, gap = 900, 900 / bars_per_line, 0, 14
    height_line = 5 * gap + 58
    lines = math.ceil(n_bars / bars_per_line)
    out = [f'<svg viewBox="-24 0 {line_w + 30} {lines * height_line + 10}" class="tab">']
    for li in range(lines):
        top = li * height_line + 30
        out.append(f'<text x="-20" y="{top + gap * 1.2}" class="tabl">T</text><text x="-20" y="{top + gap * 2.9}" class="tabl">A</text>'
                   f'<text x="-20" y="{top + gap * 4.6}" class="tabl">B</text>')
        for s_ in range(6):
            out.append(f'<line x1="0" y1="{top + s_ * gap}" x2="{line_w}" y2="{top + s_ * gap}" class="tl"/>')
        for b in range(bars_per_line + 1):
            if li * bars_per_line + b <= n_bars:
                x = b * bar_w
                out.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top + 5 * gap}" class="bl"/>')
                if b < bars_per_line and li * bars_per_line + b < n_bars:
                    out.append(f'<text x="{x + 3}" y="{top - 18}" class="bn">{li * bars_per_line + b + 1}</text>')
                    for k in range(per_bar):
                        bx = x + bar_w * (k + 0.5) / per_bar
                        out.append(f'<line x1="{bx}" y1="{top + 5 * gap + 8}" x2="{bx}" y2="{top + 5 * gap + 14}" class="bt"/>')
    xpos = lambda u: ((u % (bars_per_line * bar_units)) / bar_units) * bar_w + bar_w * 0.5 / (per_bar * 2)
    ypos = lambda u: (u // (bars_per_line * bar_units)) * height_line + 30
    for at, name in sorted(chord_marks.items()):
        out.append(f'<text x="{xpos(at)}" y="{ypos(at) - 6}" class="tc">{name}</text>')
    for e in events:
        for string, fret in e["frets"]:
            x, y = xpos(e["at"]), ypos(e["at"]) + (5 - string) * gap
            out.append(f'<rect x="{x - 7}" y="{y - 7}" width="14" height="14" class="fb"/><text x="{x}" y="{y + 4.5}" class="fn">{fret}</text>')
    out.append("</svg>")
    return "".join(out)


def write_chord_html(path: str, title: str, info: dict | None, tempo: float, tab: str = "") -> None:
    if not info:
        info = {"capo": 0, "capo_shapes": {}, "segments": [], "bars": [], "counts": {}, "key": "", "tuning_cents": 0}
    capo = info["capo"]
    shapes = info["capo_shapes"]
    used = sorted({s["name"] for s in info["segments"] if s["name"] != "N"}, key=lambda n: -info["counts"].get(n, 0))
    diagrams = "".join(f'<div class="cell">{diagram_svg(shapes.get(n, n), ch.shape(shapes.get(n, n))[0])}'
                       f'{"<div class=real>实际 " + n + "</div>" if capo else ""}</div>' for n in used)
    rows = []
    for i, bar in enumerate(info["bars"]):
        names = [shapes.get(c, c) if c != "N" else "–" for c in bar["chords"]]
        mm, ss = divmod(int(bar["start"]), 60)
        rows.append(f'<div class="bar"><span class="t">{mm}:{ss:02d}</span>{"&nbsp;&nbsp;".join(names)}</div>')
    capo_text = f"变调夹第 {capo} 品（按下面的指法弹）" if capo else "不用变调夹"
    tuning = info["tuning_cents"]
    tuning_text = "标准调弦" if abs(tuning) < 20 else f"标准调弦，但整首歌偏{'高' if tuning > 0 else '低'}约 {abs(tuning)} 音分（可以把吉他整体调{'高' if tuning > 0 else '低'}一点跟着弹）"
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>{title} · 吉他谱</title>
<style>
body{{font-family:"Microsoft YaHei UI",system-ui,sans-serif;margin:24px auto;max-width:960px;color:#1f2a3d;padding:0 16px}}
h1{{font-size:22px;margin:0 0 6px}} .meta{{color:#5b6678;margin-bottom:16px;line-height:1.7}}
.cells{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}} .cell{{text-align:center}}
.real{{font-size:11px;color:#8a94a6;margin-top:-6px}}
.dia .cn{{font:bold 14px sans-serif;text-anchor:middle;fill:#1f2a3d}} .st,.fr{{stroke:#8a94a6;stroke-width:1}}
.nut{{stroke:#1f2a3d;stroke-width:3}} .dot{{fill:#1f2a3d}} .open{{fill:none;stroke:#1f2a3d;stroke-width:1.2}}
.mk{{font:11px sans-serif;text-anchor:middle;fill:#b0413e}} .pos{{font:11px sans-serif;fill:#5b6678}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid #d5dce7}}
.bar{{border-bottom:1px solid #d5dce7;border-right:1px solid #d5dce7;padding:10px 8px;font-size:18px;font-weight:bold;min-height:24px}}
.bar:nth-child(4n+1){{border-left:1px solid #d5dce7}} .t{{display:block;font-size:10px;color:#8a94a6;font-weight:normal}}
h2{{font-size:17px;margin:22px 0 8px}} .tab{{width:100%}} .tl{{stroke:#9aa4b5;stroke-width:1}} .bl{{stroke:#1f2a3d;stroke-width:1.4}}
.bt{{stroke:#b8c0cc}} .bn{{font:10px sans-serif;fill:#8a94a6}} .tabl{{font:bold 12px sans-serif;fill:#1f2a3d}}
.tc{{font:bold 13px sans-serif;fill:#d9480f;text-anchor:middle}} .fb{{fill:#ffffff}} .fn{{font:bold 13px sans-serif;text-anchor:middle;fill:#1f2a3d}}
@media print{{body{{margin:0}}}}
</style></head><body>
<h1>{title}</h1>
<div class="meta">{"调性：" + info["key"] + " · " if info["key"] else ""}速度约 {tempo:.0f} BPM · {capo_text}<br>{tuning_text}<br>
AI 自动识别，初稿请对照原曲校对；每格一小节。</div>
{"<h2>和弦</h2><div class=cells>" + diagrams + "</div><div class=grid>" + "".join(rows) + "</div>" if rows else ""}
{"<h2>Solo 六线谱</h2><div class=meta>最上面一根线是 1 弦（最细的），数字是按第几品；0 = 空弦。每行 4 小节，下面的小竖线是拍子。和弦名是实际和弦（不按变调夹换算）。</div>" + tab if tab else ""}
</body></html>"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)


# ------------------------------------------------------------------ 主流程

def run(path: str, out: str, models_dir: str, do_chords: bool, do_solo: bool, mono: bool,
        size: str, beam: int, parallel: int, title: str) -> None:
    import soundfile as sf
    import torch

    os.makedirs(out, exist_ok=True)
    gpu = torch.cuda.is_available()
    emit("device", name=torch.cuda.get_device_name(0) if gpu else "CPU（没有检测到 NVIDIA 显卡，会比较慢）", gpu=gpu)
    if not gpu and size == "large":
        size = "medium"
    info = sf.info(path)
    duration = info.frames / info.samplerate
    tracks = separate(path, models_dir, out)
    mix = sum(v for k, v in tracks.items() if not k.startswith("_"))
    emit("progress", stage="找拍子和小节线", fraction=0.36)
    beats, downbeats = detect_beats(mix, tracks["_rate"], gpu)
    beats_full, bar_start, per_bar, tempo = beat_frame(beats, downbeats, duration)
    result = {"title": title, "duration": duration, "tempo": round(tempo, 1), "beats_per_bar": per_bar,
              "beats": [round(float(b), 3) for b in beats_full if -1 <= b <= duration + 1],
              "files": {"guitar": os.path.join(out, "guitar.wav"), "backing": os.path.join(out, "backing.wav"),
                        "mix": os.path.join(out, "mix.wav")}}
    chord_info = None
    if do_chords:
        emit("progress", stage="识别和弦", fraction=0.38)
        chord_info = recognize_chords(tracks, beats_full, bar_start, per_bar, duration)
        result["chords"] = chord_info
    del tracks
    if do_solo:
        notes = transcribe_notes(result["files"]["guitar"], size, beam, parallel, gpu, mono)
        emit("progress", stage="排六线谱把位", fraction=0.88)
        grids = choose_grids(notes, beats_full, bar_start)
        groups, events = [], []
        for n in notes:
            at = to_units(n["start"], beats_full, bar_start, grids)
            stop = max(at + 2, to_units(n["end"], beats_full, bar_start, grids))
            if groups and groups[-1]["at"] == at:
                groups[-1]["pitches"].append(n["pitch"])
                groups[-1]["stop"] = max(groups[-1]["stop"], stop)
                groups[-1]["notes"].append(n)
            else:
                groups.append({"at": at, "stop": stop, "pitches": [n["pitch"]], "notes": [n]})
        groups = [g for g in groups if g["at"] >= 0]
        for i, g in enumerate(groups):   # 单声部：下一组开始前结束
            if i + 1 < len(groups):
                g["stop"] = min(g["stop"], groups[i + 1]["at"])
            g["pitches"] = sorted(set(g["pitches"]))[-6:]
        tabs = assign_tab([g["pitches"] for g in groups])
        tab_notes = []
        for g, frets in zip(groups, tabs):
            if g["stop"] > g["at"]:
                events.append({"at": g["at"], "len": g["stop"] - g["at"], "frets": frets})
            start = min(n["start"] for n in g["notes"])
            end = max(n["end"] for n in g["notes"])
            for string, fret in frets:
                tab_notes.append({"start": round(start, 3), "end": round(end, 3), "string": string, "fret": fret,
                                  "pitch": ch.STANDARD_TUNING[string] + fret})
        result["notes"] = tab_notes
        marks = {}
        if chord_info:
            for seg in chord_info["segments"]:
                if seg["name"] != "N":
                    marks[max(0, to_units(seg["start"], beats_full, bar_start, {}) // 3 * 3)] = seg["name"]
        flats = bool(chord_info) and any("b" in seg["name"][1:2] for seg in chord_info["segments"])
        xml_path = os.path.join(out, "guitar.musicxml")
        write_musicxml(xml_path, title, tempo, per_bar, events, dict(marks), flats)
        result["files"]["musicxml"] = xml_path
        gp_path = os.path.join(out, "guitar.gp5")
        write_gp5(gp_path, title, tempo, per_bar, events, marks)
        result["files"]["gp5"] = gp_path
        per_bar_counts = {}
        for e in events:
            per_bar_counts[e["at"] // (per_bar * UNITS)] = per_bar_counts.get(e["at"] // (per_bar * UNITS), 0) + 1
        dense = max(per_bar_counts.values() or [0])
        result["_tab"] = tab_svg(events, marks, per_bar, 4 if dense <= 8 else 3 if dense <= 12 else 2)
        if not notes:
            emit("notice", message="吉他轨里没有识别到音符（这首歌可能没有明显的吉他）。")
    html = os.path.join(out, "sheet.html")
    write_chord_html(html, title, chord_info, tempo, result.pop("_tab", ""))
    result["files"]["sheet_html"] = html
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    emit("progress", stage="完成", fraction=1.0)
    emit("done", output=os.path.join(out, "result.json"))


def _wav_format(path: str):
    """读 WAV 头：(采样率, 声道数, 位深, 是否浮点, 数据开始的位置)。"""
    import struct
    with open(path, "rb") as handle:
        head = handle.read(4096)
    if head[:4] != b"RIFF":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(head):
        chunk, size = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if chunk == b"fmt ":
            tag, channels, rate = struct.unpack("<HHI", head[pos + 8:pos + 16])
            bits = struct.unpack("<H", head[pos + 22:pos + 24])[0]
            fmt = (rate, channels, bits, tag == 3 or (tag == 0xFFFE and bits == 32))
        elif chunk == b"data":
            return fmt + (pos + 8,) if fmt else None
        pos += 8 + size + (size & 1)
    return None


def live_chords(wav: str, stop_file: str) -> None:
    """边录边认和弦：每 0.5 秒看一眼录音文件新写进来的声音，用最近 2 秒认当前和弦。
    stop_file 出现（录音停止）就结束。"""
    import time
    import librosa
    import numpy as np

    labels, templates, prior = chord_templates()
    fmt, offset, buffer = None, 0, np.zeros(0, dtype=np.float32)
    current, candidate, streak, counts, started = "N", None, 0, {}, time.time()
    last_emit = 0.0
    while True:
        if os.path.exists(stop_file):
            break
        if fmt is None and os.path.exists(wav):
            fmt = _wav_format(wav)
            if fmt:
                offset = fmt[4]
        if fmt is None:
            if time.time() - started > 30:
                raise RuntimeError("录音文件一直没有出现，请检查录音组件。")
            time.sleep(0.3)
            continue
        rate, channels, bits, is_float, _ = fmt
        frame = channels * bits // 8
        with open(wav, "rb") as handle:
            handle.seek(offset)
            raw = handle.read()
        raw = raw[:len(raw) // frame * frame]
        offset += len(raw)
        if raw:
            if is_float:
                data = np.frombuffer(raw, dtype="<f4")
            elif bits == 16:
                data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
            else:   # 24 位
                b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
                data = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)) << 8 >> 8
                        ).astype(np.float32) / 8388608
            mono = data.reshape(-1, channels).mean(axis=1)
            buffer = np.concatenate([buffer, mono])[-int(rate * 2.0):]
        if len(buffer) >= rate * 1.0 and time.time() - last_emit >= 0.45:
            last_emit = time.time()
            y = librosa.resample(buffer, orig_sr=rate, target_sr=22050)
            rms = float(np.sqrt(np.mean(y ** 2)))
            if rms < 0.004:
                name = "N"
            else:
                c = librosa.feature.chroma_cqt(y=y, sr=22050, hop_length=1024, n_octaves=6, bins_per_octave=36).mean(axis=1)
                bass = librosa.feature.chroma_cqt(y=y, sr=22050, hop_length=1024, fmin=librosa.note_to_hz("E1"), n_octaves=2,
                                                  bins_per_octave=36).mean(axis=1)
                v = c / (np.linalg.norm(c) + 1e-9)
                score = templates @ v + prior
                bn = bass / (bass.max() + 1e-9)
                score[1:] += 0.15 * bn[(np.arange(1, len(labels)) - 1) % 12]
                score[0] = -1
                name = labels[int(np.argmax(score))]
            # 连续两次都认成同一个新和弦才换（避免闪来闪去）
            if name != current:
                streak = streak + 1 if name == candidate else 1
                candidate = name
                if streak >= 2:
                    current, streak = name, 0
            else:
                streak = 0
            if current != "N":
                counts[current] = counts.get(current, 0) + 1
            capo = ch.suggest_capo(counts) if counts else {"capo": 0, "shapes": {}}
            emit("live_chord", name=current, seconds=round(offset / max(rate * frame, 1), 1), level=round(rms, 4),
                 capo=capo["capo"], shape=capo["shapes"].get(current, current))
        time.sleep(0.15)
    emit("done", output=wav)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import traceback

    parser = argparse.ArgumentParser()
    parser.add_argument("--guitar")
    parser.add_argument("--live")
    parser.add_argument("--stop-file")
    parser.add_argument("--out", required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--title", default="吉他")
    parser.add_argument("--chords", action="store_true")
    parser.add_argument("--solo", action="store_true")
    parser.add_argument("--mono", action="store_true")
    parser.add_argument("--size", default="large")
    parser.add_argument("--beam", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--overlap", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        import concert_worker as cw
        cw.FAST["overlap"] = args.overlap
        if args.live:
            live_chords(args.live, args.stop_file)
            return 0
        run(args.guitar, args.out, args.models, args.chords, args.solo, args.mono, args.size, args.beam, args.parallel,
            args.title)
        return 0
    except Exception as error:
        emit("error", message=str(error), detail=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
