"""扒谱与声部音量地图（主程序这边）：准备音频、调用 AI 进程、调用 MuseScore 导出 PDF。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import concert

HERE = Path(__file__).resolve().parent
WORKER = HERE / "transcribe_worker.py"
TOKEN_FILE = HERE / ".tools" / "hf_token.txt"
READY_FLAG = concert.AI_ENV / "transcribe_ready"
NO_WINDOW = concert.NO_WINDOW
# 显示名 → (模型, 束宽, 并行段数)；从准到快排列
SIZES = {"最准（大模型 + 束搜索，慢）": ("large", 4, 1), "较准（大模型）": ("large", 1, 1),
         "较快（大模型，多段并行）": ("large", 1, 8), "快速（中模型）": ("medium", 1, 1)}
MUSESCORE_CANDIDATES = [
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
    r"C:\Program Files (x86)\MuseScore 3\bin\MuseScore3.exe",
]
# 下拉框里可选的“记谱为”乐器：(显示名, music21 名称)
TARGETS = [
    ("钢琴", "Piano"), ("小提琴", "Violin"), ("中提琴", "Viola"), ("大提琴", "Violoncello"), ("低音提琴", "Contrabass"),
    ("长笛", "Flute"), ("短笛", "Piccolo"), ("双簧管", "Oboe"), ("英国管", "English Horn"), ("单簧管（降B）", "Clarinet"),
    ("大管", "Bassoon"), ("中音萨克斯（降E）", "Alto Saxophone"), ("次中音萨克斯（降B）", "Tenor Saxophone"),
    ("上低音萨克斯（降E）", "Baritone Saxophone"), ("圆号（F）", "Horn"), ("小号（降B）", "Trumpet"), ("长号", "Trombone"),
    ("大号", "Tuba"), ("竖琴", "Harp"), ("管风琴", "Organ"), ("木吉他", "Acoustic Guitar"), ("电吉他", "Electric Guitar"),
    ("电贝斯", "Electric Bass"), ("人声", "Voice"), ("定音鼓", "Timpani"), ("鼓", "Drumset"), ("钟琴", "Glockenspiel"),
    ("弦乐组", "Strings"), ("合成器", "Synthesizer"),
]
# MuScriptor 乐器组（中文名 → 模型里的名字），给“指定乐器”用
GROUP_NAMES = {
    "钢琴": "acoustic_piano", "电钢琴": "electric_piano", "管风琴": "organ", "有音高打击乐": "chromatic_percussion",
    "木吉他": "acoustic_guitar", "电吉他（清音）": "clean_electric_guitar", "电吉他（失真）": "distorted_electric_guitar",
    "原声贝斯": "acoustic_bass", "电贝斯": "electric_bass", "小提琴": "violin", "中提琴": "viola", "大提琴": "cello",
    "低音提琴": "contrabass", "竖琴": "orchestral_harp", "定音鼓": "timpani", "弦乐组": "string_ensemble",
    "合成弦乐": "synth_strings", "人声": "voice", "小号": "trumpet", "长号": "trombone", "大号": "tuba", "圆号": "french_horn",
    "铜管组": "brass_section", "中音萨克斯": "soprano_and_alto_sax", "次中音萨克斯": "tenor_sax", "上低音萨克斯": "baritone_sax",
    "双簧管": "oboe", "英国管": "english_horn", "大管": "bassoon", "单簧管": "clarinet", "长笛": "flutes",
    "合成器主音": "synth_lead", "合成器铺底": "synth_pad", "鼓": "drums",
}


# 识别到的乐器组 → 默认“记谱为”的乐器
GROUP_TARGET = {
    "acoustic_piano": "Piano", "electric_piano": "Piano", "organ": "Organ", "chromatic_percussion": "Glockenspiel",
    "acoustic_guitar": "Acoustic Guitar", "clean_electric_guitar": "Electric Guitar", "distorted_electric_guitar": "Electric Guitar",
    "acoustic_bass": "Contrabass", "electric_bass": "Electric Bass", "violin": "Violin", "viola": "Viola", "cello": "Violoncello",
    "contrabass": "Contrabass", "orchestral_harp": "Harp", "timpani": "Timpani", "string_ensemble": "Strings",
    "synth_strings": "Strings", "voice": "Voice", "trumpet": "Trumpet", "trombone": "Trombone", "tuba": "Tuba",
    "french_horn": "Horn", "brass_section": "Trumpet", "soprano_and_alto_sax": "Alto Saxophone", "tenor_sax": "Tenor Saxophone",
    "baritone_sax": "Baritone Saxophone", "oboe": "Oboe", "english_horn": "English Horn", "bassoon": "Bassoon",
    "clarinet": "Clarinet", "flutes": "Flute", "synth_lead": "Synthesizer", "synth_pad": "Synthesizer", "drums": "Drumset",
}


def available() -> bool:
    return concert.ai_available() and READY_FLAG.is_file()


def install(log=lambda _m: None) -> None:
    """在已有的 AI 环境里装扒谱组件（MuScriptor + music21，约 300 MB，模型第一次用时再下载）。"""
    if not concert.ai_available():
        raise RuntimeError("请先在“演唱会降噪”页安装 AI 组件（扒谱和它共用显卡环境）。")
    concert._stop_server()   # 常驻 AI 进程占着文件时 Windows 上装不上
    py = [str(concert.ai_python())]
    log("正在安装扒谱组件（MuScriptor、Beat This!、music21）……")
    if concert._run_stream(py + ["-m", "pip", "install", "--disable-pip-version-check", "muscriptor", "music21", "mido", "pyguitarpro", "sounddevice"], log) != 0:
        raise RuntimeError("扒谱组件安装失败，请检查网络后重试。")
    concert._ensure_cuda_torch(py, log)   # 新依赖有时会把显卡版 PyTorch 换掉，这里确认一遍
    READY_FLAG.write_text("ok", encoding="utf-8")
    log("扒谱组件安装完成。")


def get_token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_token(token: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")


def find_musescore() -> str | None:
    found = shutil.which("MuseScore4") or shutil.which("mscore")
    if found:
        return found
    for path in MUSESCORE_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _session(kind: str) -> Path:
    root = concert.session_root()
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(kind + "_*"):
        shutil.rmtree(old, ignore_errors=True)
    folder = root / f"{kind}_{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    return folder


def prepare_audio(source: str, ffmpeg: str, clean: bool, folder: Path, log, progress,
                  start: float | None = None, length: float | None = None) -> str:
    """扒谱前的音频：44.1 kHz 立体声；可选先用演唱会降噪去掉观众声和底噪（识别会更准）；可以只取一段。"""
    target = str(folder / "input.wav")
    if clean:
        # 扒谱前的清理用“快速分离”（快约 1.9 倍）：差别约 −38 dB，对识别音符没有影响
        saved = concert.SPEED.get("overlap")
        concert.SPEED["overlap"] = 2
        try:
            session = concert.run_ai(source, ffmpeg, ["crowd", "denoise"], preview_start=start,
                                     preview_length=length or 30.0, log=log, kind_name="score",
                                     progress=lambda f, s="": progress(0.35 * f, s))
        finally:
            concert.SPEED["overlap"] = saved
        concert.render(ffmpeg, session, dict(concert.DEFAULT_SETTINGS, strength=1.0, auto=0.0, air=0.0, loudness="off"),
                       target, ["-c:a", "pcm_f32le"])
    else:
        info = concert.probe(ffmpeg, source)
        concert.extract(ffmpeg, source, target, 1.0, start, length, mono=info.get("mono", False))
    return target


def model_cached(size: str) -> bool:
    """扒谱模型是否已在 Hugging Face 缓存里（只看文件，不联网、不启动 AI 进程）。"""
    import glob
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(
        os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub")
    pattern = os.path.join(hub, f"models--MuScriptor--muscriptor-{size}", "snapshots", "*", "model.safetensors")
    return any(os.path.isfile(path) and os.path.getsize(path) > 1_000_000 for path in glob.glob(pattern))


def ensure_model(size: str) -> None:
    """耗时的清理之前先确认扒谱模型能用：已下载过就直接通过（不联网、不启动进程），否则检查下载权限。"""
    if model_cached(size):
        return
    access = {}
    concert.run_worker(["--check-model", size, "--out", str(HERE)],
                       lambda event: access.update(event) if event.get("type") == "model_access" else None,
                       script=WORKER)
    if not access.get("ok"):
        raise RuntimeError(access.get("message") or "无法确认扒谱模型是否可用。")


def run_transcription(source: str, ffmpeg: str, size_label: str, instruments: list[str], clean: bool,
                      log=lambda _m: None, progress=lambda _f, _s="": None,
                      start: float | None = None, length: float | None = None) -> dict:
    if not available():
        raise RuntimeError("还没有安装扒谱组件。")
    if not get_token():
        raise RuntimeError("还没有设置 Hugging Face 授权（扒谱模型需要）。请先点“Hugging Face 授权…”。")
    concert._RUNNING["cancelled"] = False
    size, beam, parallel = SIZES.get(size_label, ("large", 1, 1))
    ensure_model(size)
    folder = _session("score")
    audio = prepare_audio(source, ffmpeg, clean, folder, log, progress, start, length)
    base = 0.35 if clean else 0.02
    found = {}

    def on_event(event):
        kind = event.get("type")
        if kind == "progress":
            progress(base + (0.97 - base) * float(event.get("fraction", 0)), event.get("stage", ""))
        elif kind in ("notice",):
            log(event.get("message", ""))
        elif kind == "device":
            log(("使用显卡：" if event.get("gpu") else "使用：") + event.get("name", ""))
        elif kind == "download":
            progress(base, f"下载模型 {event.get('name', '')}：{event.get('done', 0) / 1e6:.0f} / {max(event.get('total', 1), 1) / 1e6:.0f} MB")
        elif kind == "summary":
            found.update(event)

    concert.run_worker(["--transcribe", audio, "--out", str(folder), "--size", size, "--beam", str(beam), "--parallel", str(parallel),
                        "--instruments", ",".join(instruments)], on_event, script=WORKER)
    if not found.get("tracks"):
        raise RuntimeError("没有识别到任何音符。")
    return {"folder": str(folder), "audio": audio, "midi": str(folder / "raw.mid"), "summary": found,
            "source": source, "title": Path(source).stem}


def export_score(session: dict, parts: list[dict], output_dir: str, fine: bool, make_pdf: bool,
                 log=lambda _m: None, progress=lambda _f, _s="": None) -> dict:
    """parts = [{"track", "group", "instrument", "label", "grand"}]；生成 MusicXML 总谱/分谱、MIDI，可选 PDF。"""
    out = Path(output_dir) / f"{session['title']}_扒谱"
    out.mkdir(parents=True, exist_ok=True)
    plan = Path(session["folder"]) / "plan.json"
    plan.write_text(json.dumps({"title": session["title"], "parts": parts, "fine": fine}, ensure_ascii=False), encoding="utf-8")
    result = {}

    def on_event(event):
        if event.get("type") == "progress":
            progress(0.7 * float(event.get("fraction", 0)), event.get("stage", ""))
        elif event.get("type") == "notice":
            log(event.get("message", ""))
        elif event.get("type") == "notation":
            result.update(event)

    concert.run_worker(["--notate", session["midi"], "--plan", str(plan), "--out", str(out)], on_event, script=WORKER)
    result["folder"] = str(out)
    musescore = find_musescore() if make_pdf else None
    if make_pdf and not musescore:
        log("没找到 MuseScore，只生成了 MusicXML（可以用 MuseScore、Sibelius、Finale、Dorico 打开）。")
    if musescore:
        files = [result["score"]] + list(result.get("parts", []))
        for i, path in enumerate(files):
            progress(0.7 + 0.3 * i / len(files), f"用 MuseScore 导出 PDF {i + 1}/{len(files)}")
            pdf = str(Path(path).with_suffix(".pdf"))
            subprocess.run([musescore, "-o", pdf, path], capture_output=True, creationflags=NO_WINDOW, timeout=600)
        log("已用 MuseScore 导出 PDF。")
    progress(1.0, "完成")
    return result


def run_stems(source: str, ffmpeg: str, clean: bool, log=lambda _m: None, progress=lambda _f, _s="": None,
              audio: str | None = None) -> dict:
    """声部音量地图：六轨分离后每秒响度。已有扒谱用的音频时直接复用。"""
    if not concert.ai_available():
        raise RuntimeError("需要先安装演唱会降噪组件。")
    concert._RUNNING["cancelled"] = False
    folder = _session("stems")
    if not audio or not os.path.isfile(audio):
        audio = prepare_audio(source, ffmpeg, clean, folder, log, progress)
    out = folder / "stems.json"

    def on_event(event):
        if event.get("type") == "progress":
            progress(0.35 + 0.63 * float(event.get("fraction", 0)), event.get("stage", ""))
        elif event.get("type") == "notice":
            log(event.get("message", ""))
        elif event.get("type") == "download":
            progress(0.35, f"下载模型：{event.get('done', 0) / 1e6:.0f} / {max(event.get('total', 1), 1) / 1e6:.0f} MB")

    concert.run_worker(["--stems", audio, "--out", str(out), "--models", str(concert.MODELS_DIR)] + concert._overlap_args(),
                       on_event, script=WORKER)
    return json.loads(out.read_text(encoding="utf-8"))
