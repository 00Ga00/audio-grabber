"""扒谱和声部分析的 AI 进程（运行在 .venv-ai 里）。

  --transcribe IN --out DIR [--size large] [--beam 4] [--instruments violin,cello]
        MuScriptor 多乐器扒谱 + Beat This! 节拍/小节线 → raw.mid、summary.json
  --notate MIDI --plan PLAN.json --out DIR
        music21 记谱：量化到节拍网格、调号、分钢琴上下谱表、移调乐器 → MusicXML 总谱 + 分谱 + MIDI
  --stems IN --out JSON --models DIR
        BS-Roformer SW 六轨分离（人声/鼓/贝斯/吉他/钢琴/其他）→ 每秒响度，给“声部音量地图”
每行输出一个 JSON 事件（和 concert_worker 相同的格式）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import copy
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from concert_worker import emit  # noqa: E402  同一套事件格式

STEM_MODEL = "BS-Roformer-SW.ckpt"
# MuScriptor 乐器组 → (中文名, music21 乐器名, 是否用大谱表, 总谱里的顺序)
GROUPS = {
    "flutes": ("长笛", "Flute", False, 10), "oboe": ("双簧管", "Oboe", False, 11),
    "english_horn": ("英国管", "English Horn", False, 12), "clarinet": ("单簧管", "Clarinet", False, 13),
    "bassoon": ("大管", "Bassoon", False, 14), "soprano_and_alto_sax": ("中音萨克斯", "Alto Saxophone", False, 15),
    "tenor_sax": ("次中音萨克斯", "Tenor Saxophone", False, 16), "baritone_sax": ("上低音萨克斯", "Baritone Saxophone", False, 17),
    "french_horn": ("圆号", "Horn", False, 20), "trumpet": ("小号", "Trumpet", False, 21),
    "trombone": ("长号", "Trombone", False, 22), "tuba": ("大号", "Tuba", False, 23),
    "brass_section": ("铜管组", "Brass", False, 24), "timpani": ("定音鼓", "Timpani", False, 30),
    "drums": ("鼓", "Drumset", False, 31), "chromatic_percussion": ("有音高打击乐", "Glockenspiel", False, 32),
    "orchestral_harp": ("竖琴", "Harp", True, 40), "acoustic_piano": ("钢琴", "Piano", True, 41),
    "electric_piano": ("电钢琴", "Electric Piano", True, 42), "organ": ("管风琴", "Organ", True, 43),
    "synth_lead": ("合成器主音", "Synthesizer", False, 44), "synth_pad": ("合成器铺底", "Synthesizer", True, 45),
    "acoustic_guitar": ("木吉他", "Acoustic Guitar", False, 50), "clean_electric_guitar": ("电吉他（清音）", "Electric Guitar", False, 51),
    "distorted_electric_guitar": ("电吉他（失真）", "Electric Guitar", False, 52), "voice": ("人声", "Voice", False, 55),
    "violin": ("小提琴", "Violin", False, 60), "viola": ("中提琴", "Viola", False, 61), "cello": ("大提琴", "Violoncello", False, 62),
    "contrabass": ("低音提琴", "Contrabass", False, 63), "string_ensemble": ("弦乐组", "Strings", True, 64),
    "synth_strings": ("合成弦乐", "Strings", True, 65), "acoustic_bass": ("原声贝斯", "Acoustic Bass", False, 70),
    "electric_bass": ("电贝斯", "Electric Bass", False, 71), "orchestra_hit": ("管弦齐奏", "Orchestra", False, 80),
}


# ------------------------------------------------------------------ 扒谱

def transcribe(path: str, out: str, size: str, beam: int, instruments: list[str]) -> None:
    import torch
    from muscriptor.events import ProgressEvent
    from muscriptor.transcription_model import TranscriptionModel

    os.makedirs(out, exist_ok=True)
    gpu = torch.cuda.is_available()
    emit("device", name=torch.cuda.get_device_name(0) if gpu else "CPU（没有检测到 NVIDIA 显卡，大模型会非常慢）", gpu=gpu)
    if not gpu and size == "large":
        size = "medium"
        emit("notice", message="没有显卡，自动改用中等模型。")
    emit("progress", stage="加载扒谱模型（第一次会下载，大模型约 1.4 GB）", fraction=0.01)
    model = TranscriptionModel.load_model(weights_path=size, device="cuda" if gpu else "cpu")
    emit("progress", stage="找节拍和小节线（Beat This!）", fraction=0.04)
    grid = model.detect_beat_grid_for(path, "best-effort")
    if grid is None:
        emit("notice", message="没找到稳定的节拍，按自由节奏记谱（小节线可能不准）。")

    def events():
        for event in model.transcribe(path, instruments=instruments or None, beam_size=max(1, beam)):
            if isinstance(event, ProgressEvent):
                if event.total:
                    emit("progress", stage=f"识别音符 {event.completed}/{event.total} 段（每段 5 秒）",
                         fraction=0.05 + 0.9 * event.completed / event.total)
                continue
            yield event

    midi_path = os.path.join(out, "raw.mid")
    with open(midi_path, "wb") as handle:
        handle.write(model.events_to_midi_bytes(events(), beat_grid=grid))
    summary = summarize(midi_path)
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False)
    emit("summary", **summary)
    emit("done", output=midi_path)


GROUP_BY_PROGRAM = {}


def _group_for(name: str, program: int, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    key = (name or "").strip().lower().replace(" ", "_")
    if key in GROUPS:
        return key
    from muscriptor.tokenizer.mt3 import MT3_FULL_PLUS_GROUP_NAMES
    for group, number in MT3_FULL_PLUS_GROUP_NAMES.items():   # 按 General MIDI 音色归组
        if program // 8 == number // 8 and group in GROUPS:
            GROUP_BY_PROGRAM.setdefault(program, group)
    return GROUP_BY_PROGRAM.get(program, "acoustic_piano")


def summarize(midi_path: str) -> dict:
    """每条音轨：乐器、音符数、音域、每秒有几个音在响（声部活动图用）。"""
    import mido
    mid = mido.MidiFile(midi_path)
    tempo_map = [(0, 500000)]
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((tick, msg.tempo))
    tempo_map.sort()

    def seconds(tick):
        total, last_tick, last_tempo = 0.0, 0, 500000
        for t, tempo in tempo_map:
            if t > tick:
                break
            total += (t - last_tick) * last_tempo / 1e6 / mid.ticks_per_beat
            last_tick, last_tempo = t, tempo
        return total + (tick - last_tick) * last_tempo / 1e6 / mid.ticks_per_beat

    tracks, length = [], 0.0
    for index, track in enumerate(mid.tracks):
        tick, name, program, drum, starts, notes = 0, "", 0, False, {}, []
        for msg in track:
            tick += msg.time
            if msg.type == "track_name":
                name = msg.name
            elif msg.type == "program_change":
                program = msg.program
            if msg.type in ("note_on", "note_off"):
                drum = drum or msg.channel == 9
                if msg.type == "note_on" and msg.velocity > 0:
                    starts.setdefault(msg.note, []).append(tick)
                elif starts.get(msg.note):
                    begin = starts[msg.note].pop(0)
                    notes.append((seconds(begin), seconds(tick), msg.note))
        if not notes:
            continue
        group = _group_for(name, program, drum)
        end = max(n[1] for n in notes)
        length = max(length, end)
        activity = [0.0] * (int(end) + 1)
        for begin, finish, _ in notes:
            for second in range(int(begin), min(len(activity), int(finish) + 1)):
                overlap = min(finish, second + 1) - max(begin, second)
                if overlap > 0:
                    activity[second] += overlap
        pitches = [n[2] for n in notes]
        tracks.append({"track": index, "group": group, "name_zh": GROUPS[group][0], "notes": len(notes),
                       "low": min(pitches), "high": max(pitches), "activity": [round(a, 2) for a in activity]})
    return {"tracks": tracks, "length": round(length, 1), "time_signatures": _time_signatures(mid),
            "tempo": round(60e6 / tempo_map[-1][1]) if len(tempo_map) > 1 else None}


def _time_signatures(mid) -> list[str]:
    found = []
    for track in mid.tracks:
        for msg in track:
            if msg.type == "time_signature":
                found.append(f"{msg.numerator}/{msg.denominator}")
    return found


# ------------------------------------------------------------------ 记谱

def notate(midi_path: str, plan_path: str, out: str) -> None:
    """plan = {"title": 标题, "parts": [{"track": 3, "group": "violin", "instrument": "Violin", "label": "小提琴",
                                          "grand": false}], "fine": false}"""
    from music21 import clef, converter, key, layout, metadata, stream

    with open(plan_path, encoding="utf-8") as handle:
        plan = json.load(handle)
    os.makedirs(os.path.join(out, "分谱"), exist_ok=True)
    emit("progress", stage="读入音符并对齐到节拍网格", fraction=0.1)
    divisors = (8, 6) if plan.get("fine") else (4, 3)   # 默认到 16 分音符和八分三连音；“细致”到 32 分和 16 分三连音
    source = converter.parse(midi_path, quantizePost=True, quarterLengthDivisors=divisors)
    source_parts = list(source.parts)
    by_track = _match_tracks(midi_path, source_parts)

    # 调号：用所有有音高的声部一起分析（比单个声部稳）
    pitched = stream.Score()
    for spec in plan["parts"]:
        part = by_track.get(spec["track"])
        if part is not None and spec["group"] != "drums":
            pitched.insert(0, part)
    detected_key = pitched.analyze("key") if len(pitched.recurse().notes) > 8 else None
    emit("notice", message=f"识别到调性：{_key_name(detected_key)}" if detected_key else "音符太少，不写调号。")

    score = stream.Score()
    title = plan.get("title") or "扒谱"
    score.insert(0, metadata.Metadata(title=title, composer="Audio Studio 扒谱（初稿，请校对）"))
    reference = source_parts[0].flatten()
    time_signatures = list(reference.getElementsByClass("TimeSignature"))
    tempos = list(reference.getElementsByClass("MetronomeMark"))
    ordered = sorted(plan["parts"], key=lambda spec: GROUPS.get(spec["group"], ("", "", False, 99))[3])
    staff_groups = []
    for i, spec in enumerate(ordered):
        emit("progress", stage=f"整理声部 {i + 1}/{len(ordered)}：{spec['label']}", fraction=0.2 + 0.6 * i / max(len(ordered), 1))
        part = by_track.get(spec["track"])
        if part is None:
            continue
        events = [e for e in part.flatten().notes if e.duration.quarterLength > 0 and not e.duration.isGrace]
        polyphonic = spec.get("grand") or spec["group"] in POLYPHONIC
        if spec.get("grand"):
            layers = [(_filter_pitches(events, True), clef.TrebleClef()), (_filter_pitches(events, False), clef.BassClef())]
        else:
            layers = [(events, None)]
        made = []
        for n, (layer, fixed_clef) in enumerate(layers):
            staff = _build_staff(layer, time_signatures, tempos if not score.parts else [], polyphonic)
            staff.partName = spec["label"] if n == 0 else ""
            staff.partAbbreviation = spec["label"][:4] if n == 0 else ""
            staff.atSoundingPitch = True
            measures = staff.getElementsByClass("Measure")
            if not measures:
                continue
            first = measures[0]
            for old in list(first.getElementsByClass(("Clef", "KeySignature", "Instrument"))):
                first.remove(old)
            first.insert(0, _instrument(spec["instrument"]))
            if spec["group"] == "drums":
                first.insert(0, clef.PercussionClef())
            else:
                first.insert(0, fixed_clef or clef.bestClef(staff, recurse=True))
                if detected_key is not None:
                    first.insert(0, key.KeySignature(detected_key.sharps))
            made.append(staff)
            score.insert(0, staff)
        if len(made) > 1:
            staff_groups.append(layout.StaffGroup(made, name=spec["label"], symbol="brace", barTogether=True))
    for group in staff_groups:
        score.insert(0, group)
    if not score.parts:
        raise RuntimeError("没有选任何声部。")

    emit("progress", stage="写出总谱", fraction=0.85)
    written = score.toWrittenPitch(inPlace=False)   # 移调乐器写实际记谱音高（降 B 单簧管等），MuseScore 会读到移调信息
    full_path = os.path.join(out, f"{title}_总谱.musicxml")
    written.write("musicxml", fp=full_path)
    score.write("midi", fp=os.path.join(out, f"{title}_整理后.mid"))
    parts_out = []
    for spec in ordered:
        parts_list = list(written.parts)
        members = []
        for index, member in enumerate(parts_list):
            if member.partName == spec["label"]:
                members = [member] + ([parts_list[index + 1]] if spec.get("grand") and index + 1 < len(parts_list) else [])
                break
        if not members:
            continue
        single = stream.Score()
        single.insert(0, metadata.Metadata(title=f"{title} · {spec['label']}", composer="Audio Studio 扒谱（初稿，请校对）"))
        for member in members:
            single.insert(0, copy.deepcopy(member))
        if len(members) > 1:
            single.insert(0, layout.StaffGroup(list(single.parts), name=spec["label"], symbol="brace", barTogether=True))
        path = os.path.join(out, "分谱", f"{title}_{_safe(spec['label'])}.musicxml")
        single.write("musicxml", fp=path)
        parts_out.append(path)
    emit("progress", stage="完成", fraction=1.0)
    emit("notation", score=full_path, parts=parts_out, key=_key_name(detected_key) if detected_key else "")
    emit("done", output=full_path)


POLYPHONIC = {"acoustic_guitar", "clean_electric_guitar", "distorted_electric_guitar", "string_ensemble", "synth_strings",
              "synth_pad", "brass_section", "organ", "electric_piano", "orchestral_harp", "acoustic_piano", "chromatic_percussion"}


def _filter_pitches(events, high: bool):
    """钢琴类分上下谱表：中央 C 及以上给右手，以下给左手；和弦拆开分别归到两行。"""
    import copy
    from music21 import chord, note
    kept = []
    for element in events:
        pitches = [p for p in element.pitches if (p.midi >= 60) == high]
        if not pitches:
            continue
        made = chord.Chord(pitches) if len(pitches) > 1 else note.Note(pitches[0])
        made.duration = copy.deepcopy(element.duration)
        made.offset = element.offset
        kept.append(made)
    return kept


def _build_staff(events, time_signatures, tempos, polyphonic: bool):
    """从音符列表重新搭一行谱：同时开始的音并成和弦；
    单声部乐器把重叠部分截掉（识别出的尾音常会拖到下一个音里）；和声乐器用 chordify 变成可读的和弦 + 连音线；
    最后交给 music21 自动分小节、补休止符、加连音线。"""
    import copy
    from music21 import chord, note, stream
    part = stream.Part()
    for ts in time_signatures:
        part.insert(ts.offset, copy.deepcopy(ts))
    for mm in tempos:
        part.insert(mm.offset, copy.deepcopy(mm))
    by_onset = {}
    for element in events:
        by_onset.setdefault(round(float(element.offset), 4), []).append(element)
    onsets = sorted(by_onset)
    for index, onset in enumerate(onsets):
        group = by_onset[onset]
        pitches = sorted({p.midi for e in group for p in e.pitches})
        length = max(float(e.duration.quarterLength) for e in group)
        if not polyphonic and index + 1 < len(onsets):
            length = min(length, onsets[index + 1] - onset)
        if length <= 0:
            continue
        made = chord.Chord(pitches) if len(pitches) > 1 else note.Note(pitches[0])
        made.quarterLength = length
        part.insert(onset, made)
    if polyphonic:
        part = part.chordify()
    part.makeNotation(inPlace=True)
    return part


def _match_tracks(midi_path: str, parts: list) -> dict:
    """music21 读 MIDI 时会跳过空音轨，这里按顺序把“有音符的音轨号”对到 music21 的声部上。"""
    import mido
    mid = mido.MidiFile(midi_path)
    tracks_with_notes = [i for i, t in enumerate(mid.tracks) if any(m.type == "note_on" and m.velocity > 0 for m in t)]
    return dict(zip(tracks_with_notes, parts))


def _instrument(name: str):
    from music21 import instrument
    try:
        return instrument.fromString(name)
    except Exception:
        made = instrument.Instrument()
        made.instrumentName = name
        return made


def _key_name(k) -> str:
    if k is None:
        return ""
    names = {"major": "大调", "minor": "小调"}
    return f"{k.tonic.name.replace('-', '♭').replace('#', '♯')} {names.get(k.mode, k.mode)}"


def _safe(text: str) -> str:
    return "".join(ch for ch in text if ch not in '\\/:*?"<>|').strip() or "声部"


# ------------------------------------------------------------------ 声部音量地图

def stems(path: str, out_json: str, models_dir: str) -> None:
    import numpy as np
    import soundfile as sf
    import tempfile
    import concert_worker as cw

    cw._install_hooks()
    with tempfile.TemporaryDirectory(prefix="stems_") as tmp:
        if not os.path.isfile(os.path.join(models_dir, STEM_MODEL)):
            emit("notice", message="第一次使用，正在下载六轨分离模型（BS-Roformer SW）……")
        sep = cw.make_separator(models_dir, tmp)
        sep.load_model(STEM_MODEL)
        cw.PROGRESS["cb"] = lambda x: emit("progress", stage="分离六个声部（人声/鼓/贝斯/吉他/钢琴/其他）", fraction=0.05 + 0.85 * x)
        outputs = sep.separate(path)
        cw.PROGRESS["cb"] = None
        result = {}
        for name in outputs:
            full = name if os.path.isabs(name) else os.path.join(tmp, name)
            label = os.path.basename(full).rsplit("(", 1)[-1].split(")")[0].strip().lower()
            data, rate = sf.read(full, dtype="float32", always_2d=True)
            mono = data.mean(axis=1)
            levels = []
            for start in range(0, len(mono), rate):
                block = mono[start:start + rate]
                levels.append(round(10 * math.log10(float(np.mean(block ** 2)) + 1e-10), 1))
            result[label] = levels
    names = {"vocals": "人声", "drums": "鼓", "bass": "贝斯", "guitar": "吉他", "piano": "钢琴", "other": "其他"}
    rows = [{"key": k, "name": names.get(k, k), "levels": v} for k, v in result.items()]
    order = list(names)
    rows.sort(key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump({"rows": rows}, handle, ensure_ascii=False)
    emit("progress", stage="完成", fraction=1.0)
    emit("done", output=out_json)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcribe")
    parser.add_argument("--notate")
    parser.add_argument("--stems")
    parser.add_argument("--plan")
    parser.add_argument("--out", required=True)
    parser.add_argument("--models")
    parser.add_argument("--size", default="large")
    parser.add_argument("--beam", type=int, default=4)
    parser.add_argument("--instruments", default="")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        if args.transcribe:
            transcribe(args.transcribe, args.out, args.size, args.beam, [i for i in args.instruments.split(",") if i])
        elif args.notate:
            notate(args.notate, args.plan, args.out)
        elif args.stems:
            stems(args.stems, args.out, args.models)
        return 0
    except Exception as error:
        emit("error", message=str(error), detail=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
