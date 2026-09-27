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
    tonic, is_minor = best_key[1], best_key[2]
    flats = tonic in (1, 3, 5, 8, 10) if not is_minor else tonic in (0, 2, 5, 7, 10)
    sharps = tonic in (2, 4, 6, 7, 9, 11) if not is_minor else tonic in (1, 3, 4, 6, 8, 11)
    if flats or sharps:          # 按调号写升降号：E 大调写 G#m 而不是 Abm
        for seg in segments:
            if seg["name"] != "N":
                r, q, b = ch.split_name(seg["name"])
                seg["name"] = ch.make_name(r, q, b, flats=flats, sharps=sharps)

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
    return {"segments": segments, "bars": bars, "key": ch.key_name(best_key[1], best_key[2], sharps=sharps, flats=flats),
            "tuning_cents": round(tuning * 100), "capo": capo["capo"], "capo_shapes": capo["shapes"], "counts": counts}


# ------------------------------------------------------------------ 扒音符

_MODEL_CACHE: dict = {}


def _load_model(size: str, gpu: bool):
    """扒谱模型载入一次就留在常驻 AI 进程里（第二次扒不用再等十几秒）；显存小的电脑不留。"""
    import torch
    from muscriptor.transcription_model import TranscriptionModel
    import concert_worker as cw
    key = (size, gpu)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    cw.hook_byte_downloads(f"MuScriptor 扒谱模型（{size}）")
    emit("progress", stage="加载扒谱模型", fraction=0.4)
    model = TranscriptionModel.load_model(weights_path=size, device="cuda" if gpu else "cpu",
                                          dtype=torch.float16 if gpu else None)
    if not cw.FAST.get("low_vram"):
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = model
    return model


def transcribe_notes(guitar_path: str, size: str, beam: int, parallel: int, gpu: bool, mono: bool,
                     instruments: list[str] | None = GUITAR_GROUPS, stage: str = "扒吉他音符",
                     span: tuple[float, float] = (0.45, 0.4)) -> list[dict]:
    """instruments=None：不限定乐器，AI 自己判断（后摇模式里用来从“其他”声部里捞出吉他）。"""
    from muscriptor.events import NoteEndEvent, ProgressEvent

    model = _load_model(size, gpu)
    options = dict(instruments=instruments, beam_size=max(1, beam))
    if parallel > 1 and beam <= 1:
        options.update(batch_size=parallel, prelude_forcing=False)
    notes = []
    for event in model.transcribe(guitar_path, **options):
        if isinstance(event, ProgressEvent):
            if event.total:
                emit("progress", stage=f"{stage} {event.completed}/{event.total} 段",
                     fraction=span[0] + span[1] * event.completed / event.total)
        elif isinstance(event, NoteEndEvent):
            start = event.start_event
            if 36 <= start.pitch <= 90:
                notes.append({"start": max(0.0, start.start_time - 0.02), "end": max(start.start_time + 0.03, event.end_time - 0.02),
                              "pitch": int(start.pitch), "instrument": start.instrument})
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


# ------------------------------------------------------------------ 后摇模式：音量拉平、两把吉他分开、去掉延音回声、震音

TUNINGS = {   # 名字 → 6 弦到 1 弦的空弦音高
    "标准 EADGBE": [40, 45, 50, 55, 59, 64],
    "降半音 Eb": [39, 44, 49, 54, 58, 63],
    "Drop D": [38, 45, 50, 55, 59, 64],
    "DADGAD": [38, 45, 50, 55, 57, 62],
    "Open D": [38, 45, 50, 54, 57, 62],
    "Drop C#": [37, 44, 49, 54, 58, 63],
}


def level_audio(src: str, dst: str, max_boost_db: float = 24.0) -> None:
    """后摇里安静段落和爆发段落差 30 dB 以上，安静的地方 AI 容易听漏。
    扒谱前把音量“拉平”：每 3 秒左右算一次响度，安静的地方最多提 24 dB（只用于识别，不改导出的音频）。"""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(src, dtype="float32", always_2d=True)
    mono = np.abs(y).mean(axis=1)
    hop = sr // 2
    frames = [float(np.sqrt(np.mean(y[i:i + sr * 3] ** 2)) + 1e-7) for i in range(0, len(y), hop)]
    target = max(frames) * 0.5
    gain_db = np.clip(20 * np.log10(target / np.array(frames)), 0, max_boost_db)
    gain_db[20 * np.log10(np.array(frames) + 1e-9) < -80] = 0          # 真正的静音不放大
    k = 4
    gain_db = np.convolve(np.pad(gain_db, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
    gains = np.interp(np.arange(len(y)), np.arange(len(gain_db)) * hop, 10 ** (gain_db / 20)).astype(np.float32)
    out = y * gains[:, None]
    peak = float(np.abs(out).max()) or 1.0
    if peak > 0.95:
        out = np.tanh(out / peak * 1.2) / np.tanh(1.2) * 0.95
    sf.write(dst, out, sr, subtype="FLOAT")
    del mono


def note_features(stem_path: str, notes: list[dict]) -> None:
    """每个音的响度和左右位置（-1 = 全左，+1 = 全右）：看这个音的基频和泛音在左右声道各有多响。"""
    import numpy as np
    import soundfile as sf
    y, sr = sf.read(stem_path, dtype="float32", always_2d=True)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    n_fft, hop = 4096, 1024
    import librosa
    left = np.abs(librosa.stft(y[:, 0], n_fft=n_fft, hop_length=hop))
    right = np.abs(librosa.stft(y[:, 1], n_fft=n_fft, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    for n in notes:
        a = int(n["start"] * sr / hop)
        b = max(a + 1, int(min(n["end"], n["start"] + 0.4) * sr / hop))
        f0 = 440.0 * 2 ** ((n["pitch"] - 69) / 12)
        bins = [int(np.argmin(np.abs(freqs - f0 * h))) for h in (1, 2, 3) if f0 * h < sr / 2]
        l_ = float(left[bins, a:b].sum()) + 1e-9
        r_ = float(right[bins, a:b].sum()) + 1e-9
        n["pan"] = (r_ - l_) / (r_ + l_)
        n["level"] = 20 * math.log10((l_ + r_) / max(1, (b - a) * len(bins)))


def merge_tremolo(notes: list[dict]) -> list[dict]:
    """后摇常用的震音（同一个音飞快地来回拨）：4 个以上、间隔很短的同音并成一个长音，标“震音”。"""
    out, i = [], 0
    notes = sorted(notes, key=lambda n: (n["start"], -n["pitch"]))
    while i < len(notes):
        n = notes[i]
        run = [n]
        j = i + 1
        while j < len(notes):
            nxt = notes[j]
            if abs(nxt["start"] - run[-1]["start"]) < 0.03 and nxt["pitch"] != n["pitch"]:
                j += 1
                continue
            if nxt["pitch"] == n["pitch"] and 0.03 <= nxt["start"] - run[-1]["start"] <= 0.14:
                run.append(nxt)
                j += 1
                continue
            break
        if len(run) >= 4:
            merged = dict(n)
            merged["end"] = max(r["end"] for r in run)
            merged["tremolo"] = True
            out.append(merged)
            ids = {id(r) for r in run}
            for k in range(i + 1, j):
                if id(notes[k]) not in ids:
                    out.append(notes[k])
            i = j
        else:
            out.append(n)
            i += 1
    return sorted(out, key=lambda n: (n["start"], -n["pitch"]))


def remove_echoes(notes: list[dict]) -> tuple[list[dict], float | None]:
    """延音效果器（delay）会让每个音过一会儿再响几遍、越来越小，AI 会把回声也当成音符。
    先找出这首歌的延音时间（同一个音、更小声、固定间隔反复出现），再删掉这些回声。"""
    import numpy as np
    gaps = []
    by_pitch = {}
    for n in notes:
        by_pitch.setdefault(n["pitch"], []).append(n)
    for group in by_pitch.values():
        for a, b in zip(group, group[1:]):
            d = b["start"] - a["start"]
            if 0.15 <= d <= 1.2 and b.get("level", 0) < a.get("level", 0) - 4:
                gaps.append(round(d / 0.02) * 0.02)
    if len(gaps) < 8:
        return notes, None
    values, counts = np.unique(gaps, return_counts=True)
    delay = float(values[int(np.argmax(counts))])
    if counts.max() < max(6, len(notes) * 0.05):
        return notes, None
    drop = set()
    for group in by_pitch.values():
        for i, a in enumerate(group):
            if id(a) in drop:
                continue
            level, t = a.get("level", 0), a["start"]
            for b in group[i + 1:]:
                d = b["start"] - t
                if d > delay * 1.3:
                    break
                if abs(d - delay) < 0.04 and b.get("level", 0) < level - 3:
                    drop.add(id(b))
                    level, t = b.get("level", 0), b["start"]    # 回声的回声
    return [n for n in notes if id(n) not in drop], delay


def split_guitars(notes: list[dict]) -> tuple[list[list[dict]], str]:
    """把两把吉他分开：
    1) 左右声道：后摇乐队的两把吉他常常一左一右，按每个音的左右位置分两堆；
    2) AI 标的音色：清音 / 失真 / 木吉他各自成一把；
    3) 都不行时按“声部”分：同时响的音高的归主奏（吉他 1），其余归伴奏（吉他 2），并保持各自旋律连贯。"""
    import numpy as np
    if len(notes) < 8:
        return [notes], "只有一把"
    pans = np.array([n.get("pan", 0.0) for n in notes])
    if len(pans) >= 16:
        lo, hi = np.percentile(pans, 20), np.percentile(pans, 80)
        if hi - lo > 0.5:
            center = (lo + hi) / 2
            a = [n for n in notes if n.get("pan", 0) < center]
            b = [n for n in notes if n.get("pan", 0) >= center]
            if min(len(a), len(b)) >= max(6, len(notes) * 0.12):
                return _lead_first([a, b]), "按左右声道分开"
    from_other = [n for n in notes if n.get("source") == "other"]
    if len(from_other) >= max(6, len(notes) * 0.15) and len(notes) - len(from_other) >= max(6, len(notes) * 0.15):
        main = [n for n in notes if n.get("source") != "other"]
        return _lead_first([main, from_other]), "按声部分开（混响很重的那把单独一把）"
    kinds = {}
    for n in notes:
        kinds.setdefault(n.get("instrument", ""), []).append(n)
    big = [v for v in kinds.values() if len(v) >= max(6, len(notes) * 0.12)]
    if len(big) >= 2:
        big.sort(key=len, reverse=True)
        rest = [n for v in kinds.values() if v not in big[:2] for n in v]
        big[1].extend(rest)
        return _lead_first([sorted(big[0], key=lambda n: n["start"]), sorted(big[1], key=lambda n: n["start"])]), "按音色分开"
    lead, second = [], []
    for n in sorted(notes, key=lambda n: (n["start"], -n["pitch"])):
        sounding = [m for m in lead if m["end"] > n["start"] + 0.03]
        if sounding and n["pitch"] <= max(m["pitch"] for m in sounding) and abs(n["start"] - sounding[-1]["start"]) < 0.05:
            second.append(n)                        # 和主奏同时起、比它低 → 第二把
            continue
        if lead and n["pitch"] < lead[-1]["pitch"] - 9 and lead[-1]["end"] > n["start"]:
            second.append(n)
            continue
        lead.append(n)
    if len(second) < max(6, len(notes) * 0.1):
        return [notes], "只有一把"
    return [lead, second], "按声部分开（高的旋律为吉他 1）"


def _lead_first(parts):
    """旋律（平均音高更高、单音更多）的那把排第一。"""
    def score(p):
        return sum(n["pitch"] for n in p) / max(len(p), 1)
    return sorted(parts, key=score, reverse=True)


def choose_tuning(parts: list[list[dict]], wanted: str) -> str:
    """自动选调弦：先保证最低的音弹得出来；再比较整首排出来的指法哪个最顺手（标准调弦略优先）。"""
    if wanted in TUNINGS:
        return wanted
    lowest = min((n["pitch"] for p in parts for n in p), default=40)
    best = None
    for name, tuning in TUNINGS.items():
        if lowest < tuning[0]:
            continue
        cost = 0.0 if name.startswith("标准") else 3.0
        for p in parts:
            groups = [[n["pitch"]] for n in p[:400]]
            cost += tab_cost(groups, tuning, [n["start"] for n in p[:400]])
        if best is None or cost < best[0]:
            best = (cost, name)
    return best[1] if best else "标准 EADGBE"


# ------------------------------------------------------------------ 演奏技巧：滑音、击弦/勾弦、推弦、揉弦

def detect_techniques(guitar_path: str, notes: list[dict]) -> list[dict]:
    """用音高曲线（pYIN）和起音强度判断相邻两个音之间是怎么过去的：
    - 滑音：音高连续地滑过中间的音（相差 2 个半音以上）
    - 推弦：向上 1–2 个半音、连续地推上去（高音弦上）；推上去又回来 = 推弦回放
    - 击弦 / 勾弦（h / p）：没有新的拨弦（起音很弱），音高直接跳过去
    - 揉弦（~）：长音上的音高有规律地来回摆动
    每个音加上 tech = {"to_next": "slide"/"hammer"/"pull"/None, "bend": 半音数, "release": 是否放回, "vibrato": bool}。
    推弦的目标音会并进前一个音（同一个品位，推上去）。"""
    import librosa
    import numpy as np

    for n in notes:
        n["tech"] = {"to_next": None, "bend": 0, "release": False, "vibrato": False}
    if len(notes) < 2:
        return notes
    sr, hop = 22050, 256
    y, _ = librosa.load(guitar_path, sr=sr, mono=True)
    f0, voiced, _prob = librosa.pyin(y, fmin=75, fmax=1400, sr=sr, frame_length=2048, hop_length=hop)
    midi = np.where(voiced, librosa.hz_to_midi(np.nan_to_num(f0, nan=1.0)), np.nan)
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

    def frames(a, b):
        i, j = np.searchsorted(times, a), np.searchsorted(times, b)
        return slice(max(0, i), max(i + 1, j))

    attack = [float(onset[frames(n["start"] - 0.03, n["start"] + 0.05)].max()) for n in notes]
    typical = float(np.median(attack)) or 1.0

    # 揉弦
    for n in notes:
        if n["end"] - n["start"] >= 0.3:
            seg = midi[frames(n["start"] + 0.05, n["end"] - 0.03)]
            seg = seg[~np.isnan(seg)]
            if len(seg) > 12:
                k = max(3, int(0.12 * sr / hop))
                trend = np.convolve(seg, np.ones(k) / k, mode="same")
                wobble = (seg - trend)[k:-k] if len(seg) > 2 * k + 4 else seg - trend
                crossings = np.sum(np.diff(np.sign(wobble)) != 0) / 2 / max((len(wobble) * hop / sr), 1e-3)
                if np.std(wobble) > 0.12 and 3 <= crossings <= 9:
                    n["tech"]["vibrato"] = True

    kept, i = [], 0
    while i < len(notes):
        a = notes[i]
        kept.append(a)
        if i + 1 >= len(notes):
            break
        b = notes[i + 1]
        gap = b["start"] - a["end"]
        interval = b["pitch"] - a["pitch"]
        if gap > 0.08 or interval == 0 or abs(interval) > 7:
            i += 1
            continue
        soft = attack[i + 1] < 0.55 * typical
        seg = midi[frames(b["start"] - 0.18, b["start"] + 0.08)]
        seg = seg[~np.isnan(seg)]
        low, high = min(a["pitch"], b["pitch"]), max(a["pitch"], b["pitch"])
        between = int(np.sum((seg > low + 0.3) & (seg < high - 0.3)))
        glide = between >= (2 if abs(interval) >= 2 else 3)
        if glide and 0 < interval <= 2 and a["pitch"] >= 55 and soft:
            # 推弦：并进前一个音；如果后面马上回到原来的音（也没有新拨弦），就是推弦回放
            a["tech"]["bend"] = interval
            a["end"] = b["end"]
            a["tech"]["vibrato"] = a["tech"]["vibrato"] or b["tech"]["vibrato"]
            if i + 2 < len(notes) and notes[i + 2]["pitch"] == a["pitch"] and attack[i + 2] < 0.55 * typical \
                    and notes[i + 2]["start"] - b["end"] < 0.08:
                a["tech"]["release"] = True
                a["end"] = notes[i + 2]["end"]
                i += 3
            else:
                i += 2
            continue
        if glide:
            a["tech"]["to_next"] = "slide"
        elif soft:
            a["tech"]["to_next"] = "hammer" if interval > 0 else "pull"
        i += 1
    return kept


def finalize_tech(tech: dict, frets: list, next_frets: list | None, gap_units: int) -> dict:
    """排好把位后再核对一遍技巧记号：必须同一根弦、品位不同；空弦没法滑（改成击弦 / 勾弦）；
    击弦 / 勾弦按品位高低定方向；中间停顿太久的不算连奏。"""
    tech = dict(tech or {})
    how = tech.get("to_next")
    if not how:
        return tech
    if not next_frets or len(frets) != 1 or len(next_frets) != 1 or next_frets[0][0] != frets[0][0]:
        tech["to_next"] = None
        return tech
    f1, f2 = frets[0][1], next_frets[0][1]
    if f1 == f2 or (how in ("hammer", "pull") and gap_units > 3):
        tech["to_next"] = None
    elif how == "slide" and (f1 == 0 or f2 == 0):
        tech["to_next"] = "hammer" if f2 > f1 else "pull"
    elif how in ("hammer", "pull"):
        tech["to_next"] = "hammer" if f2 > f1 else "pull"
    return tech


# ------------------------------------------------------------------ 六线谱把位

def positions(pitch: int, tuning=ch.STANDARD_TUNING) -> list[tuple[int, int]]:
    """(弦 0=6 弦…5=1 弦, 品) 的所有按法。"""
    return [(s_, pitch - open_) for s_, open_ in enumerate(tuning) if 0 <= pitch - open_ <= MAX_FRET]


def _tab_dp(groups, links=None, times=None, slides=None, tuning=ch.STANDARD_TUNING, overlaps=None, ends=None,
            open_ok=False, one_string=False):
    """按“弹起来顺不顺手”选把位的动态规划（整首一起算，不是一个音一个音地看）。
    左手有一个把位（食指在第几品，四个手指管 4 个品），代价包括：
    - 换把：越远越贵；句子越快越贵；前面有休止（手有时间挪）就便宜
    - 换弦：能在一根弦上弹完的旋律尽量留在一根弦上（慢的句子沿着弦走，快的句子才在把位里换弦）
    - 滑音 / 击弦 / 勾弦必须在同一根弦上（滑音本身就是换把，不额外扣分）
    - 分解和弦 / 让音延续：前一个音还在响时，下一个音不能在同一根弦上（会把它掐断）
    - 高把位时尽量不用空弦（后摇模式除外：空弦延音很常见）；小指伸展、跨弦略扣分；同样顺手时低把位略优先
    one_string=True（一根弦模式）：旋律尽量整段在一根弦上，沿着弦滑来滑去；只有音超出这根弦的范围、
    或者又快又远的大跳实在够不着时才换弦（换了就留在新弦上）。"""
    import itertools

    links, slides, overlaps = links or set(), slides or set(), overlaps or set()

    def options(group):
        per = [positions(p, tuning) for p in group]
        out = []
        for combo in itertools.product(*per):
            strings = [s_ for s_, _ in combo]
            if len(set(strings)) != len(strings):
                continue
            fretted = [f for _, f in combo if f > 0]
            if fretted and max(fretted) - min(fretted) > 4:
                continue
            out.append(combo)
        if not out:   # 按不出来：去掉最低的音再试
            return options(sorted(group)[1:]) if len(group) > 1 else [((0, 0),)]
        return out[:120]

    def hand_positions(combo):
        fretted = [f for _, f in combo if f > 0]
        if not fretted:
            return list(range(1, 18))          # 全是空弦：手可以停在任何把位（记住手在哪）
        lo, hi = min(fretted), max(fretted)
        return [p_ for p_ in range(max(1, hi - 3), lo + 2) if p_ - 1 <= lo and hi <= p_ + 3]

    def own_cost(combo, pos):
        cost = 0.0
        if one_string:
            cost += sum(0.25 * (f - 14) for _, f in combo if f > 14)     # 太高的品不好按，换弦前先考虑低一点的弦
        for _, f in combo:
            if f == 0:
                cost += 0.1 if ((pos or 0) <= 4 or open_ok) else 1.2
            elif pos is not None and (f == pos - 1 or f == pos + 3):
                cost += 0.25
        return cost + 0.03 * (pos or 0)

    layers = [[(c, pos) for c in options(g) for pos in hand_positions(c)] for g in groups]
    if not layers:
        return 0.0, []
    costs = [own_cost(c, pos) for c, pos in layers[0]]
    backs = []
    for i in range(1, len(layers)):
        ioi = (times[i] - times[i - 1]) if times is not None and i < len(times) else 0.3
        rest = (times[i] - ends[i - 1]) if (times is not None and ends is not None) else 0.0
        speed = 2.0 if ioi < 0.16 else 1.3 if ioi < 0.3 else 1.0
        relax = 0.35 if rest >= 0.5 else 1.0          # 休止时手有时间挪
        linked, slide, ringing = (i - 1) in links, (i - 1) in slides, (i - 1) in overlaps
        new_costs, back = [], []
        for c, pos in layers[i]:
            single = len(c) == 1
            best, arg = None, 0
            for j, (pc, ppos) in enumerate(layers[i - 1]):
                total = costs[j]
                same_string = single and len(pc) == 1 and c[0][0] == pc[0][0]
                if one_string and single and len(pc) == 1:
                    if same_string:
                        jump = abs(c[0][1] - pc[0][1])
                        total += 0.12 * jump                               # 沿着弦挪：很便宜
                        if ioi < 0.15 and jump > 5:
                            total += 0.6 * (jump - 5)                      # 又快又远：够不着
                    else:
                        total += 6.0 + 1.0 * (abs(c[0][0] - pc[0][0]) - 1)  # 换弦：实在不行才换
                        if linked:
                            total += 50.0
                    if ringing and same_string:
                        total += 6.0                                       # 分解和弦还在响：还是分开弦
                    if best is None or total < best:
                        best, arg = total, j
                    continue
                if pos is not None and ppos is not None and pos != ppos:
                    shift = abs(pos - ppos)
                    if slide and same_string:
                        total += 0.1 * shift
                    elif same_string and ioi >= 0.25:
                        total += 0.25 * shift * relax         # 慢的旋律：沿着同一根弦挪过去很自然
                    else:
                        total += (1.0 + 0.35 * shift) * speed * relax
                if single and len(pc) == 1:
                    skip = abs(c[0][0] - pc[0][0])
                    if skip:
                        total += (0.35 if ioi >= 0.25 else 0.15) + 0.25 * (skip - 1)   # 换弦（慢的旋律更想留在一根弦上）
                    if linked and not same_string:
                        total += 50.0
                    if ringing and same_string:
                        total += 6.0                           # 前一个音还在响：别在同一根弦上掐断它
                if best is None or total < best:
                    best, arg = total, j
            new_costs.append(best + own_cost(c, pos))
            back.append(arg)
        costs = new_costs
        backs.append(back)
    k = min(range(len(costs)), key=costs.__getitem__)
    total = costs[k]
    chosen = [k]
    for back in reversed(backs):
        k = back[k]
        chosen.append(k)
    chosen.reverse()
    return total, [list(layers[i][k][0]) for i, k in enumerate(chosen)]


def assign_tab(groups: list[list[int]], links: set | None = None, times: list[float] | None = None,
               slides: set | None = None, tuning=ch.STANDARD_TUNING, overlaps: set | None = None,
               ends: list[float] | None = None, open_ok: bool = False, one_string: bool = False) -> list[list[tuple[int, int]]]:
    return _tab_dp(groups, links, times, slides, tuning, overlaps, ends, open_ok, one_string)[1]


def tab_cost(groups, tuning, times) -> float:
    return _tab_dp(groups, times=times, tuning=tuning)[0]


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


def write_gp5(path: str, title: str, tempo: float, per_bar: int, parts: list[dict], chord_marks: dict,
              tuning=ch.STANDARD_TUNING) -> None:
    """每把吉他一个音轨。parts：[{"name", "events": [{"at": 单位, "len": 单位, "frets": [(弦, 品)], "tech"}]}]。"""
    import guitarpro as gp
    from guitarpro import models as m

    bar_units = per_bar * UNITS
    end = max([e["at"] + e["len"] for p in parts for e in p["events"]] + [bar_units])
    n_bars = max(1, math.ceil(end / bar_units))
    song = gp.Song()
    song.title = title
    song.tempo = int(round(tempo))
    for _ in range(n_bars - 1):
        song.newMeasure()
    for header in song.measureHeaders:
        header.timeSignature = m.TimeSignature(numerator=per_bar, denominator=m.Duration(value=4))
    for k, part in enumerate(parts):
        if k == 0:
            track = song.tracks[0]
        else:
            track = m.Track(song, number=k + 1)
            track.channel = m.MidiChannel(channel=k * 2, effectChannel=k * 2 + 1)
            for header in song.measureHeaders:
                track.measures.append(m.Measure(track, header))
            song.tracks.append(track)
        track.name = part["name"]
        track.channel.instrument = 25 if k == 0 else 27        # 钢弦 / 清音电吉他音色（试听用）
        track.strings = [m.GuitarString(number=i + 1, value=v) for i, v in enumerate(reversed(tuning))]
        _gp5_fill(m, track, part["events"], chord_marks if k == 0 else {}, bar_units)
    gp.write(song, path, version=(5, 1, 0), encoding="utf-8")   # MuseScore 默认按 UTF-8 读 Guitar Pro 文件


def _gp5_fill(m, track, events, chord_marks, bar_units) -> None:
    timeline, _ = _timeline(events, bar_units, len(track.measures))
    marks = sorted(chord_marks.items())
    for at, length, frets, tech in timeline:
        position, first = at, True
        pieces = split_units(at, length, bar_units)
        for k, piece in enumerate(pieces):
            last = k == len(pieces) - 1
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
                    note = m.Note(beat, value=fret, string=6 - string, type=m.NoteType.normal if first else m.NoteType.tie)
                    if tech and len(frets) == 1:
                        effect = note.effect
                        if first and tech.get("bend"):
                            semis = tech["bend"]
                            points = ([m.BendPoint(0, 0), m.BendPoint(4, semis), m.BendPoint(8, semis), m.BendPoint(12, 0)]
                                      if tech.get("release") else [m.BendPoint(0, 0), m.BendPoint(6, semis), m.BendPoint(12, semis)])
                            effect.bend = m.BendEffect(type=m.BendType.bendRelease if tech.get("release") else m.BendType.bend,
                                                       value=semis * 50, points=points)
                        if last and tech.get("to_next") == "slide":
                            effect.slides = [m.SlideType.legatoSlideTo]
                        if last and tech.get("to_next") in ("hammer", "pull"):
                            effect.hammer = True
                        if tech.get("vibrato"):
                            effect.vibrato = True
                    if tech and tech.get("tremolo"):
                        note.effect.tremoloPicking = m.TremoloPickingEffect(duration=m.Duration(value=16))
                    beat.notes.append(note)
            voice.beats.append(beat)
            position += piece
            first = False
    for measure in track.measures:
        if not measure.voices[0].beats:
            measure.voices[0].beats.append(m.Beat(measure.voices[0], duration=m.Duration(value=1), status=m.BeatStatus.rest))


def _timeline(events: list[dict], bar_units: int, min_bars: int = 1) -> tuple[list, int]:
    end = max([e["at"] + e["len"] for e in events] + [bar_units])
    n_bars = max(1, min_bars, math.ceil(end / bar_units))
    timeline, cursor = [], 0
    for e in sorted(events, key=lambda x: x["at"]):
        if e["at"] > cursor:
            timeline.append((cursor, e["at"] - cursor, None, None))
        timeline.append((e["at"], e["len"], e["frets"], e.get("tech")))
        cursor = e["at"] + e["len"]
    if cursor < n_bars * bar_units:
        timeline.append((cursor, n_bars * bar_units - cursor, None, None))
    return timeline, n_bars


_KINDS = {"": "major", "m": "minor", "7": "dominant", "maj7": "major-seventh", "m7": "minor-seventh",
          "sus4": "suspended-fourth", "sus2": "suspended-second", "dim": "diminished"}
_TYPES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "16th", 32: "32nd"}


def write_musicxml(path: str, title: str, tempo: float, per_bar: int, parts: list[dict], chord_marks: dict,
                   flats: bool = False, tuning=ch.STANDARD_TUNING) -> None:
    """每把吉他一个声部：五线谱 + 六线谱（写明第几弦第几品）；和弦名写在第一把上面。"""
    from xml.sax.saxutils import escape
    bar_units = per_bar * UNITS
    n_bars = max(max([e["at"] + e["len"] for p in parts for e in p["events"]] + [bar_units]) // bar_units + 1, 1)
    part_list, bodies = [], []
    for k, part in enumerate(parts):
        pid = f"P{k + 1}"
        part_list.append(f'<score-part id="{pid}"><part-name>{escape(part["name"])}</part-name><midi-instrument id="{pid}-I1">'
                         f'<midi-program>{26 if k == 0 else 28}</midi-program></midi-instrument></score-part>')
        body = _musicxml_measures(tempo, per_bar, part["events"], chord_marks if k == 0 else {}, flats, tuning, n_bars, k == 0)
        bodies.append(f'<part id="{pid}">{body}</part>')
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
           '"http://www.musicxml.org/dtds/partwise.dtd">\n<score-partwise version="4.0">'
           f"<work><work-title>{escape(title)}</work-title></work><part-list>{''.join(part_list)}</part-list>"
           + "".join(bodies) + "</score-partwise>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(doc)


def _musicxml_measures(tempo: float, per_bar: int, events: list[dict], chord_marks: dict, flats: bool, tuning,
                       n_bars_total: int, with_tempo: bool) -> str:
    from xml.sax.saxutils import escape

    bar_units = per_bar * UNITS
    timeline, n_bars = _timeline(events, bar_units, n_bars_total)
    names = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"] if flats else ch.NOTE_NAMES
    marks = sorted(chord_marks.items())
    bars = [[] for _ in range(n_bars)]      # 每小节：[(harmony, 时值, notes, 是否延音开始/结束)]
    prev_tech = None
    for at, length, frets, tech in timeline:
        position = at
        pieces = split_units(at, length, bar_units)
        for i, piece in enumerate(pieces):
            label = None
            while marks and marks[0][0] <= position:
                label = marks.pop(0)[1]
            info = {"first": i == 0, "last": i == len(pieces) - 1, "tech": tech if frets else None,
                    "from": prev_tech.get("to_next") if (i == 0 and frets and prev_tech) else None}
            bars[position // bar_units].append((label, piece, frets, i > 0, i < len(pieces) - 1, info))
            position += piece
        if frets:
            prev_tech = tech or {}
        elif length > 3:
            prev_tech = None

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

    def notes_xml(piece, frets, tie_stop, tie_start, staff, info=None):
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
            midi = tuning[string] + fret
            notations = ""
            ties = ("<tied type=\"stop\"/>" if tie_stop else "") + ("<tied type=\"start\"/>" if tie_start else "")
            tech, extra, marks_ = (info or {}).get("tech") or {}, "", ""
            if len(frets) == 1 and info:
                came = info.get("from")
                if came == "slide":
                    marks_ += '<slide type="stop" number="1"/>'
                if came in ("hammer", "pull"):
                    extra += f'<{"hammer-on" if came == "hammer" else "pull-off"} type="stop" number="1"/>'
                if info.get("last") and tech.get("to_next") == "slide":
                    marks_ += '<slide type="start" number="1" line-type="solid"/>'
                if info.get("last") and tech.get("to_next") in ("hammer", "pull"):
                    kind = "hammer-on" if tech["to_next"] == "hammer" else "pull-off"
                    extra += f'<{kind} type="start" number="1">{"H" if kind == "hammer-on" else "P"}</{kind}>'
                if info.get("first") and tech.get("bend"):
                    extra += f'<bend><bend-alter>{tech["bend"]}</bend-alter></bend>'
                    if tech.get("release"):
                        extra += f'<bend><bend-alter>{-tech["bend"]}</bend-alter><release/></bend>'
            technical = (f"<technical><string>{6 - string}</string><fret>{fret}</fret>{extra}</technical>" if staff == 2
                         else (f"<technical>{extra}</technical>" if extra else ""))
            orn = ""
            if tech.get("vibrato") and info and info.get("first"):
                orn += "<wavy-line type=\"start\"/><wavy-line type=\"stop\"/>"
            if tech.get("tremolo"):
                orn += "<tremolo type=\"single\">3</tremolo>"
            ornaments = f"<ornaments>{orn}</ornaments>" if orn and staff == 1 else ""
            if ties or technical or marks_ or ornaments:
                notations = f"<notations>{ties}{marks_}{ornaments}{technical}</notations>"
            out.append(f"<note>{'<chord/>' if k else ''}{pitch_xml(midi)}{common}{notations}</note>")
        return "".join(out)

    parts = []
    for index, content in enumerate(bars):
        xml = [f'<measure number="{index + 1}">']
        if index == 0:
            def tuning_xml(i, midi):
                name = names[midi % 12]
                alter = 1 if "#" in name else -1 if "b" in name[1:] else 0
                return (f'<staff-tuning line="{i + 1}"><tuning-step>{name[0]}</tuning-step>' +
                        (f"<tuning-alter>{alter}</tuning-alter>" if alter else "") +
                        f'<tuning-octave>{midi // 12 - 1}</tuning-octave></staff-tuning>')
            tuning_text = "".join(tuning_xml(i, midi) for i, midi in enumerate(tuning))
            xml.append(f"<attributes><divisions>{UNITS}</divisions><key><fifths>0</fifths></key>"
                       f"<time><beats>{per_bar}</beats><beat-type>4</beat-type></time><staves>2</staves>"
                       "<clef number=\"1\"><sign>G</sign><line>2</line><clef-octave-change>-1</clef-octave-change></clef>"
                       "<clef number=\"2\"><sign>TAB</sign><line>5</line></clef>"
                       f"<staff-details number=\"2\"><staff-lines>6</staff-lines>{tuning_text}</staff-details></attributes>")
            if with_tempo:
                xml.append(f'<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>'
                           f'<per-minute>{int(round(tempo))}</per-minute></metronome></direction-type><sound tempo="{int(round(tempo))}"/></direction>')
        for label, piece, frets, tie_stop, tie_start, info in content:
            if label:
                xml.append(harmony_xml(label))
            xml.append(notes_xml(piece, frets, tie_stop, tie_start, 1, info))
        xml.append(f"<backup><duration>{bar_units}</duration></backup>")
        for label, piece, frets, tie_stop, tie_start, info in content:
            xml.append(notes_xml(piece, frets, tie_stop, tie_start, 2, info))
        xml.append("</measure>")
        parts.append("".join(xml))
    return "".join(parts)


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
    ordered = sorted(events, key=lambda e: e["at"])
    for k, e in enumerate(ordered):
        tech = e.get("tech") or {}
        for string, fret in e["frets"]:
            x, y = xpos(e["at"]), ypos(e["at"]) + (5 - string) * gap
            text = str(fret)
            if len(e["frets"]) == 1 and tech.get("bend"):
                text += f"b{fret + tech['bend']}" + ("r" if tech.get("release") else "")   # 7b9 = 7 品推到 9 品的音高
            if len(e["frets"]) == 1 and tech.get("vibrato"):
                text += "~"
            if tech.get("tremolo"):
                text += "≡"          # 震音：同一个音快速来回拨
            width = 8 * len(text) + 4
            out.append(f'<rect x="{x - width / 2}" y="{y - 7}" width="{width}" height="14" class="fb"/>'
                       f'<text x="{x}" y="{y + 4.5}" class="fn">{text}</text>')
        nxt = ordered[k + 1] if k + 1 < len(ordered) else None
        how = tech.get("to_next")
        if how and nxt and len(e["frets"]) == 1 and len(nxt["frets"]) == 1 and ypos(e["at"]) == ypos(nxt["at"]):
            (s1, f1), (_, f2) = e["frets"][0], nxt["frets"][0]
            x1, x2, y = xpos(e["at"]), xpos(nxt["at"]), ypos(e["at"]) + (5 - s1) * gap
            if how == "slide":       # 斜线：往高品滑 /，往低品滑 \
                up = f2 > f1
                if x2 - x1 >= 30:
                    out.append(f'<line x1="{x1 + 11}" y1="{y + (4 if up else -4)}" x2="{x2 - 11}" y2="{y + (-4 if up else 4)}" class="sl"/>')
                else:                # 太挤：在两个数字中间上方写 / 或 反斜线
                    out.append(f'<text x="{(x1 + x2) / 2}" y="{y - 9}" class="hp">{"/" if up else chr(92)}</text>')
            else:                    # 击弦 h / 勾弦 p：弧线 + 字母
                mid = (x1 + x2) / 2
                out.append(f'<path d="M{x1 + 4} {y - 9} Q{mid} {y - 19} {x2 - 4} {y - 9}" class="arc"/>'
                           f'<text x="{mid}" y="{y - 15}" class="hp">{"h" if how == "hammer" else "p"}</text>')
    out.append("</svg>")
    return "".join(out)


def fretboard_svgs(events: list[dict], chord_marks: dict, per_bar: int) -> str:
    """静态指板图：每小节一张，圆圈里的数字是弹的先后顺序，细线连起下一个音；
    滑音是粗线、h/p 是击弦/勾弦、↑ 是推弦、~ 是揉弦。和程序里的动态指板是同一种画法。"""
    bar_units = per_bar * UNITS
    by_bar = {}
    for e in sorted(events, key=lambda x: x["at"]):
        by_bar.setdefault(e["at"] // bar_units, []).append(e)
    chords_by_bar = {}
    for at, name in sorted(chord_marks.items()):
        chords_by_bar.setdefault(at // bar_units, []).append(name)
    current_chord = None
    cards = []
    ordered = sorted(events, key=lambda x: x["at"])
    nxt_of = {id(e): ordered[i + 1] for i, e in enumerate(ordered[:-1])}
    for bar in range(0, max(by_bar) + 1 if by_bar else 0):
        if bar in chords_by_bar:
            current_chord = chords_by_bar[bar][-1]
        items = by_bar.get(bar)
        if not items:
            continue
        frets = [f for e in items for _, f in e["frets"]] + [f + (e.get("tech") or {}).get("bend", 0) for e in items for _, f in e["frets"]]
        fretted = [f for f in frets if f > 0] or [1]
        lo = max(0, min(fretted) - 1)
        hi = max(lo + 5, max(fretted) + 1)
        w, h, left, top, gap = 430, 150, 30, 34, 16
        span = hi - lo + 1
        fw = (w - left - 10) / span
        fx = lambda f: left + (f - lo + 0.5) * fw if f > 0 else left - 12
        sy = lambda st: top + (5 - st) * gap
        svg = [f'<svg viewBox="0 0 {w} {h}" class="fbd">']
        names = " → ".join(dict.fromkeys(chords_by_bar.get(bar, []))) or (current_chord or "")
        svg.append(f'<text x="4" y="14" class="fbt">第 {bar + 1} 小节</text><text x="{w - 6}" y="14" class="fbc">{names}</text>')
        for f in range(lo, hi + 1):
            x = left + (f - lo) * fw
            svg.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top + 5 * gap}" class="{"nut2" if f == 0 else "fw"}"/>')
            if f + 1 <= hi and (f + 1) in (1, 3, 5, 7, 9, 12, 15, 17, 19, 21):
                svg.append(f'<text x="{x + fw / 2}" y="{top + 5 * gap + 16}" class="fnum">{f + 1}</text>')
        for st in range(6):
            svg.append(f'<line x1="{left}" y1="{sy(st)}" x2="{w - 10}" y2="{sy(st)}" class="sw"/>')
            svg.append(f'<text x="8" y="{sy(st) + 4}" class="snum">{6 - st}</text>')
        # 先画连线，再画圆点
        spots = {}
        for n, e in enumerate(items, 1):
            for st, f in e["frets"]:
                spots.setdefault((st, f), []).append(n)
            nxt = nxt_of.get(id(e))
            if nxt is not None and nxt["at"] // bar_units == bar and len(e["frets"]) == 1 and len(nxt["frets"]) == 1:
                (s1, f1), (s2, f2) = e["frets"][0], nxt["frets"][0]
                how = (e.get("tech") or {}).get("to_next")
                cls = "lnk-s" if how == "slide" else "lnk"
                svg.append(f'<line x1="{fx(f1)}" y1="{sy(s1)}" x2="{fx(f2)}" y2="{sy(s2)}" class="{cls}"/>')
                if how in ("hammer", "pull"):
                    svg.append(f'<text x="{(fx(f1) + fx(f2)) / 2}" y="{sy(s1) - 9}" class="hp">{"h" if how == "hammer" else "p"}</text>')
        for (st, f), order in spots.items():
            x, y = fx(f), sy(st)
            svg.append(f'<circle cx="{x}" cy="{y}" r="8" class="{"odot" if f == 0 else "fdot"}"/>'
                       f'<text x="{x}" y="{y + 3.5}" class="ord">{order[0] if len(order) == 1 else ""}</text>')
            if len(order) > 1:
                svg.append(f'<text x="{x}" y="{y + 3.5}" class="ord2">{order[0]}</text>'
                           f'<text x="{x + 10}" y="{y - 8}" class="more">{",".join(map(str, order[1:4]))}{"…" if len(order) > 4 else ""}</text>')
        for e in items:
            tech = e.get("tech") or {}
            if len(e["frets"]) == 1 and (tech.get("bend") or tech.get("vibrato") or tech.get("tremolo")):
                st, f = e["frets"][0]
                label = (f"↑{tech['bend']}" + ("↓" if tech.get("release") else "")) if tech.get("bend") else ""
                label += "~" if tech.get("vibrato") else ""
                label += " 震音" if tech.get("tremolo") else ""
                svg.append(f'<text x="{fx(f) + 10}" y="{sy(st) + 13}" class="bend">{label}</text>')
        svg.append("</svg>")
        cards.append("".join(svg))
    return "".join(f'<div class="fbcard">{c}</div>' for c in cards)


def write_chord_html(path: str, title: str, info: dict | None, tempo: float, tab="", boards="", extra: str = "") -> None:
    """tab / boards 可以是一段 SVG，也可以是 [(吉他名, SVG), ...]（两把吉他各一份）。"""
    if isinstance(tab, list):
        multi = len(tab) > 1
        tab = "".join((f"<h3>{name}</h3>" if multi else "") + svg for name, svg in tab)
    if isinstance(boards, list):
        multi = len(boards) > 1
        boards = "".join((f"<h3 class=span2>{name}</h3>" if multi else "") + html for name, html in boards)
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
    tuning_text = "音准正常（A = 440 Hz）" if abs(tuning) < 20 else f"标准调弦，但整首歌偏{'高' if tuning > 0 else '低'}约 {abs(tuning)} 音分（可以把吉他整体调{'高' if tuning > 0 else '低'}一点跟着弹）"
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
h2{{font-size:17px;margin:22px 0 8px}} h3{{font-size:14px;margin:14px 0 4px;color:#5b6678}} .span2{{grid-column:1 / -1}} .tab{{width:100%}} .tl{{stroke:#9aa4b5;stroke-width:1}} .bl{{stroke:#1f2a3d;stroke-width:1.4}}
.bt{{stroke:#b8c0cc}} .bn{{font:10px sans-serif;fill:#8a94a6}} .tabl{{font:bold 12px sans-serif;fill:#1f2a3d}}
.tc{{font:bold 13px sans-serif;fill:#d9480f;text-anchor:middle}} .sl{{stroke:#1f2a3d;stroke-width:1.6}}
.arc{{fill:none;stroke:#5b6678;stroke-width:1}}
.fbgrid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .fbcard{{border:1px solid #e3e8ef;border-radius:6px;padding:4px;break-inside:avoid;background:#fbf8f2}}
.fbd{{width:100%}} .fbt{{font:bold 12px sans-serif;fill:#1f2a3d}} .fbc{{font:bold 12px sans-serif;fill:#d9480f;text-anchor:end}}
.fw{{stroke:#b8a58c;stroke-width:1}} .nut2{{stroke:#3b2f25;stroke-width:4}} .sw{{stroke:#6b5b4b;stroke-width:1}}
.fnum{{font:10px sans-serif;fill:#8a7a66;text-anchor:middle}} .snum{{font:9px sans-serif;fill:#8a7a66}}
.lnk{{stroke:#9aa4b5;stroke-width:1.2;stroke-dasharray:3 2}} .lnk-s{{stroke:#d9480f;stroke-width:3}}
.fdot{{fill:#d9480f}} .odot{{fill:#fbf8f2;stroke:#d9480f;stroke-width:2}} .ord{{font:bold 10px sans-serif;fill:#fff;text-anchor:middle}} .odot + .ord{{fill:#d9480f}}
.ord2{{font:bold 10px sans-serif;fill:#fff;text-anchor:middle}} .more{{font:9px sans-serif;fill:#5b6678}} .bend{{font:bold 10px sans-serif;fill:#1f6feb}} .hp{{font:italic 10px sans-serif;fill:#5b6678;text-anchor:middle}} .fb{{fill:#ffffff}} .fn{{font:bold 13px sans-serif;text-anchor:middle;fill:#1f2a3d}}
@media print{{body{{margin:0}}}}
</style></head><body>
<h1>{title}</h1>
<div class="meta">{"调性：" + info["key"] + " · " if info["key"] else ""}速度约 {tempo:.0f} BPM · {capo_text}<br>{tuning_text}{"<br>" + extra if extra else ""}<br>
AI 自动识别，初稿请对照原曲校对；每格一小节。</div>
{"<h2>和弦</h2><div class=cells>" + diagrams + "</div><div class=grid>" + "".join(rows) + "</div>" if rows else ""}
{"<h2>六线谱</h2><div class=meta>最上面一根线是 1 弦（最细的），数字是按第几品；0 = 空弦。斜线 = 滑音，h = 击弦，p = 勾弦，7b9 = 在 7 品把音推高到 9 品的音（r = 再放回来），~ = 揉弦。和弦名是实际和弦（不按变调夹换算）。</div>" + tab if tab else ""}
{"<h2>指板图（每小节一张）</h2><div class=meta>和程序里的动态指板一样：最上面是 1 弦；圆圈里的数字是这一小节里弹的先后顺序（同一个位置弹几次，旁边的小数字是后面几次）；细线连到下一个音，粗线 = 滑音，h / p = 击弦 / 勾弦，↑1 / ↑2 = 推弦半音 / 全音（↓ = 放回），~ = 揉弦；空心圈是空弦。</div><div class=fbgrid>" + boards + "</div>" if boards else ""}
</body></html>"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(html)


# ------------------------------------------------------------------ 主流程

def build_part(notes: list[dict], beats_full, bar_start: int, tuning: list[int], open_ok: bool,
               one_string: bool = False) -> tuple[list, list]:
    """一把吉他：对齐到拍子 → 按整首的指法排布选把位 → 核对技巧记号。返回 (events, 带弦和品的音)。"""
    grids = choose_grids(notes, beats_full, bar_start)
    groups = []
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
    real_end = [max(n["end"] for n in g["notes"]) for g in groups]
    starts = [min(n["start"] for n in g["notes"]) for g in groups]
    overlaps = {i for i in range(len(groups) - 1) if real_end[i] > starts[i + 1] + 0.06}   # 前一个音还在响（分解和弦）
    for i, g in enumerate(groups):   # 记谱是单声部：下一组开始前结束
        if i + 1 < len(groups):
            g["stop"] = min(g["stop"], groups[i + 1]["at"])
        g["pitches"] = sorted(set(g["pitches"]))[-6:]

    def group_tech(g):
        tech = dict((g["notes"][0].get("tech") or {}) if len(g["notes"]) == 1 else {})
        if any(n.get("tremolo") for n in g["notes"]):
            tech["tremolo"] = True
        return tech
    links = {i for i, g in enumerate(groups[:-1]) if group_tech(g).get("to_next")}
    slides = {i for i, g in enumerate(groups[:-1]) if group_tech(g).get("to_next") == "slide"}
    tabs = assign_tab([g["pitches"] for g in groups], links, starts, slides, tuning, overlaps, real_end, open_ok, one_string)
    events, tab_notes = [], []
    for i, (g, frets) in enumerate(zip(groups, tabs)):
        tech = finalize_tech(group_tech(g), frets, tabs[i + 1] if i + 1 < len(tabs) else None,
                             (groups[i + 1]["at"] - g["stop"]) if i + 1 < len(groups) else 99)
        if g["stop"] > g["at"]:
            events.append({"at": g["at"], "len": g["stop"] - g["at"], "frets": frets, "tech": tech})
        for string, fret in frets:
            tab_notes.append({"start": round(starts[i], 3), "end": round(real_end[i], 3), "string": string, "fret": fret,
                              "pitch": tuning[string] + fret, "tech": tech})
    return events, tab_notes


def run(path: str, out: str, models_dir: str, do_chords: bool, do_solo: bool, mono: bool,
        size: str, beam: int, parallel: int, title: str, post_rock: bool = False, tuning_name: str = "自动",
        one_string: bool = False) -> None:
    import numpy as np
    import soundfile as sf
    import torch

    os.makedirs(out, exist_ok=True)
    gpu = torch.cuda.is_available()
    emit("device", name=torch.cuda.get_device_name(0) if gpu else "CPU（没有检测到 NVIDIA 显卡，会比较慢）", gpu=gpu)
    if not gpu and size == "large":
        size = "medium"
    # 先把整体音量放到正常水平：电脑录音、播放器音量小时，录下来的声音可能只有 -30 dB，AI 会听漏
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    duration = len(data) / rate
    peak = float(np.abs(data).max()) or 1.0
    if peak < 0.5:
        emit("notice", message=f"录音音量很小（峰值 {20 * math.log10(peak):.0f} dB），已先放大到正常音量再识别。")
    normalized = os.path.join(out, "input_normalized.wav")
    sf.write(normalized, data * (0.89 / peak), rate, subtype="FLOAT")
    del data
    tracks = separate(normalized, models_dir, out)
    mix = sum(v for k, v in tracks.items() if not k.startswith("_"))
    emit("progress", stage="找拍子和小节线", fraction=0.36)
    beats, downbeats = detect_beats(mix, tracks["_rate"], gpu)
    beats_full, bar_start, per_bar, tempo = beat_frame(beats, downbeats, duration)
    result = {"title": title, "duration": duration, "tempo": round(tempo, 1), "beats_per_bar": per_bar,
              "beats": [round(float(b), 3) for b in beats_full if -1 <= b <= duration + 1], "post_rock": post_rock,
              "files": {"guitar": os.path.join(out, "guitar.wav"), "backing": os.path.join(out, "backing.wav"),
                        "mix": os.path.join(out, "mix.wav")}}
    chord_info = None
    if do_chords:
        emit("progress", stage="识别和弦", fraction=0.38)
        chord_info = recognize_chords(tracks, beats_full, bar_start, per_bar, duration)
        result["chords"] = chord_info
    guitar_stereo = tracks.get("guitar")
    stem_rate = tracks["_rate"]
    other_path = None
    if post_rock and do_solo and "other" in tracks:
        # 后摇的吉他常挂满混响和延音，分离模型会把一部分分进“其他”声部；等会儿从那里再捞一遍吉他
        other_path = os.path.join(out, "other.wav")
        sf.write(other_path, tracks["other"], stem_rate, subtype="FLOAT")
    del tracks
    tabs_html, boards_html = [], []
    if do_solo:
        guitar_path = result["files"]["guitar"]
        ai_input = guitar_path
        if post_rock:
            emit("progress", stage="后摇模式：把安静段落的音量拉平", fraction=0.39)
            ai_input = os.path.join(out, "guitar_leveled.wav")
            level_audio(guitar_path, ai_input)
        notes = transcribe_notes(ai_input, size, beam, parallel, gpu, mono and not post_rock,
                                 span=(0.45, 0.25 if other_path else 0.4))
        if other_path:
            leveled_other = os.path.join(out, "other_leveled.wav")
            level_audio(other_path, leveled_other)
            extra = transcribe_notes(leveled_other, size, beam, parallel, gpu, False, instruments=None,
                                     stage="从“其他”声部里找吉他", span=(0.7, 0.15))
            found = [n for n in extra if n.get("instrument") in GUITAR_GROUPS]
            fresh = [n for n in found if not any(abs(n["start"] - m["start"]) < 0.05 and n["pitch"] == m["pitch"] for m in notes)]
            for n in fresh:
                n["source"] = "other"
            if fresh:
                emit("notice", message=f"在“其他”声部里又找到 {len(fresh)} 个吉他音（混响很重的那把吉他常被分到这里）。")
                notes = sorted(notes + fresh, key=lambda n: (n["start"], -n["pitch"]))
        method = "只有一把"
        if post_rock and notes:
            emit("progress", stage="后摇模式：去掉延音回声、识别震音、分开两把吉他", fraction=0.86)
            note_features(guitar_path, [n for n in notes if n.get("source") != "other"])
            if other_path:
                note_features(other_path, [n for n in notes if n.get("source") == "other"])
            notes, delay = remove_echoes(notes)
            if delay:
                emit("notice", message=f"检测到延音效果（约 {delay * 1000:.0f} ms），已去掉回声音符。")
            notes = merge_tremolo(notes)
            parts, method = split_guitars(notes)
            if mono:
                parts[0] = skyline(parts[0])
        else:
            parts = [notes]
        # 分开后的两把吉他各自的练习音频（左右声道分开的情况）
        if method.startswith("按左右声道") and guitar_stereo is not None:
            for k, part in enumerate(parts):
                side = 0 if np.mean([n.get("pan", 0) for n in part]) < 0 else 1
                file = os.path.join(out, f"guitar{k + 1}.wav")
                sf.write(file, np.repeat(guitar_stereo[:, side:side + 1], 2, axis=1), stem_rate, subtype="PCM_16")
                result["files"][f"guitar{k + 1}"] = file
        emit("progress", stage="识别滑音、推弦、击勾弦", fraction=0.87)
        try:
            technique_audio = result["files"].get("guitar1", guitar_path)
            if mono or post_rock:
                parts[0] = detect_techniques(technique_audio, parts[0])
        except Exception as error:
            emit("notice", message=f"演奏技巧识别失败（{type(error).__name__}），只标音符。")
        tuning_name = choose_tuning(parts, tuning_name)
        tuning = TUNINGS[tuning_name]
        if not tuning_name.startswith("标准"):
            emit("notice", message=f"建议调弦：{tuning_name}（按这个调弦排的指法）")
        emit("progress", stage="排六线谱把位", fraction=0.9)
        marks = {}
        if chord_info:
            for seg in chord_info["segments"]:
                if seg["name"] != "N":
                    marks[max(0, to_units(seg["start"], beats_full, bar_start, {}) // 3 * 3)] = seg["name"]
        names = ["吉他 1（主奏）", "吉他 2"] if len(parts) > 1 else ["吉他"]
        built, tab_notes = [], []
        for k, part in enumerate(parts):
            events, notes_k = build_part(part, beats_full, bar_start, tuning, open_ok=post_rock or one_string,
                                         one_string=one_string and len(set(round(n["start"], 2) for n in part)) > len(part) * 0.7)
            for n in notes_k:
                n["guitar"] = k
            tab_notes += notes_k
            built.append({"name": names[k], "events": events})
            per_bar_counts = {}
            for e in events:
                per_bar_counts[e["at"] // (per_bar * UNITS)] = per_bar_counts.get(e["at"] // (per_bar * UNITS), 0) + 1
            dense = max(per_bar_counts.values() or [0])
            tabs_html.append((names[k], tab_svg(events, marks if k == 0 else {}, per_bar, 4 if dense <= 8 else 3 if dense <= 12 else 2)))
            boards_html.append((names[k], fretboard_svgs(events, marks if k == 0 else {}, per_bar)))
        result["notes"] = sorted(tab_notes, key=lambda n: n["start"])
        result["guitars"] = names
        result["tuning"] = {"name": tuning_name, "pitches": tuning}
        result["split_method"] = method
        flats = bool(chord_info) and any("b" in seg["name"][1:2] for seg in chord_info["segments"])
        xml_path = os.path.join(out, "guitar.musicxml")
        write_musicxml(xml_path, title, tempo, per_bar, built, dict(marks), flats, tuning)
        result["files"]["musicxml"] = xml_path
        gp_path = os.path.join(out, "guitar.gp5")
        write_gp5(gp_path, title, tempo, per_bar, built, marks, tuning)
        result["files"]["gp5"] = gp_path
        if not notes:
            emit("notice", message="吉他轨里没有识别到音符（这首歌可能没有明显的吉他）。")
    html = os.path.join(out, "sheet.html")
    extra = ""
    if result.get("tuning"):
        extra = f"六线谱调弦：{result['tuning']['name']}" + (f" · {result['split_method']}" if len(result.get("guitars", [])) > 1 else "")
    write_chord_html(html, title, chord_info, tempo, tabs_html, boards_html, extra)
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
    parser.add_argument("--post-rock", action="store_true")
    parser.add_argument("--tuning", default="自动")
    parser.add_argument("--one-string", action="store_true", help="一根弦模式：旋律尽量在一根弦上")
    args = parser.parse_args(argv)
    try:
        import concert_worker as cw
        cw.FAST["overlap"] = args.overlap
        if args.live:
            live_chords(args.live, args.stop_file)
            return 0
        run(args.guitar, args.out, args.models, args.chords, args.solo, args.mono, args.size, args.beam, args.parallel,
            args.title, args.post_rock, args.tuning, args.one_string)
        return 0
    except Exception as error:
        emit("error", message=str(error), detail=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
