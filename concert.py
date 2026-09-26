"""演唱会降噪：从视频/音频里取出声音，用音乐专用 AI 模型去掉观众声和底噪。

AI 部分在独立环境 .venv-ai（PyTorch + audio-separator）里由 concert_worker.py 执行，
本模块负责：安装 AI 环境、取音频、调用 worker、按强度混合、导出 FLAC、放回视频、试听片段。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_ENV = HERE / ".venv-ai"
MODELS_DIR = HERE / ".tools" / "concert-models"
READY_FLAG = AI_ENV / "concert_ready"
WORKER = HERE / "concert_worker.py"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
AI_PYTHON_VERSION = "3.12"
WORK_RATE = 44100          # 模型的采样率
HEADROOM = 0.5             # 送进模型前把峰值压到 0.5，避免模型内部自动缩放/削波

STRENGTHS = {              # 名称 → 处理后声音所占比例（其余为原声）
    "轻（保留一点现场气氛）": 0.75,
    "标准（推荐）": 0.9,
    "最强": 1.0,
}
MEDIA_TYPES = [("视频或音频", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.flv *.mts *.m2ts *.3gp *.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus"),
               ("所有文件", "*.*")]


# ----------------------------------------------------------------- 环境

def ai_python() -> Path:
    return AI_ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ai_available() -> bool:
    return READY_FLAG.is_file() and ai_python().is_file()


def _run_stream(command: list[str], log, env=None) -> int:
    """运行命令并把输出逐行交给 log（pip 的进度等）。"""
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env=env,
    )
    assert process.stdout
    for line in process.stdout:
        line = line.rstrip()
        if line and not line.startswith("  ") and "━" not in line:
            log(line[-300:])
    return process.wait()


def _find_ai_base_python(log) -> list[str]:
    """找一个适合 PyTorch 的 Python（优先 3.12）；没有就用 Python 安装管理器自动装。"""
    env = dict(os.environ, PYTHON_MANAGER_CONFIRM="false")
    for version in (AI_PYTHON_VERSION, "3.13", "3.11"):
        probe = subprocess.run(["py", f"-V:{version}", "-c", "import sys;print(sys.version)"],
                               capture_output=True, text=True, creationflags=NO_WINDOW, env=env)
        if probe.returncode == 0:
            return ["py", f"-V:{version}"]
    log(f"正在自动安装 Python {AI_PYTHON_VERSION}（AI 组件需要）……")
    if _run_stream(["py", "install", "-y", AI_PYTHON_VERSION], log, env) == 0:
        return ["py", f"-V:{AI_PYTHON_VERSION}"]
    # 退而求其次：用当前这个 Python（新版本上个别依赖可能装不上）
    log("没能安装 Python 3.12，改用当前 Python 试试。")
    return [sys.executable]


def has_nvidia() -> bool:
    """有没有 NVIDIA 显卡：先问 nvidia-smi，再问 Windows 显卡列表。"""
    candidates = ["nvidia-smi", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")]
    for exe in candidates:
        try:
            if subprocess.run([exe, "-L"], capture_output=True, creationflags=NO_WINDOW, timeout=15).returncode == 0:
                return True
        except Exception:
            pass
    if os.name == "nt":
        try:
            names = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30).stdout
            return "nvidia" in names.lower()
        except Exception:
            pass
    return False


# 显卡版 PyTorch：先试最新的 CUDA 13（需要较新的显卡驱动），不行再用 CUDA 12.8
CUDA_BUILDS = [
    ("https://download.pytorch.org/whl/cu130", {"torch": "torch", "torchvision": "torchvision", "torchaudio": "torchaudio"}),
    ("https://download.pytorch.org/whl/cu128", {"torch": "torch==2.11.0", "torchvision": "torchvision==0.26.0", "torchaudio": "torchaudio==2.11.0"}),
]
TORCH_STATUS = ("import importlib.util as u, torch\n"
                "print(torch.__version__, torch.version.cuda, torch.cuda.is_available(),"
                " u.find_spec('torchvision') is not None, u.find_spec('torchaudio') is not None)")


def _torch_status(py: list[str]) -> list[str]:
    result = subprocess.run(py + ["-c", TORCH_STATUS], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return result.stdout.split() if result.returncode == 0 else []


def _ensure_cuda_torch(py: list[str], log) -> bool:
    """确保装的是显卡版 PyTorch，并且真的能用上显卡。其他依赖有时会把它换成 CPU 版，这里换回来。"""
    status = _torch_status(py)
    if len(status) == 5 and status[2] == "True":
        log(f"PyTorch {status[0]} 已启用显卡加速。")
        return True
    for index, pins in CUDA_BUILDS:
        status = _torch_status(py) or ["", "None", "False", "True", "False"]
        packages = [pins["torch"]] + [pins[name] for name, present in (("torchvision", status[3]), ("torchaudio", status[4])) if present == "True"]
        log(f"正在安装显卡加速版 PyTorch（{index.rsplit('/', 1)[-1]}，约 2.5 GB）……")
        if _run_stream(py + ["-m", "pip", "install", "--disable-pip-version-check", "--force-reinstall", "--no-deps",
                             *packages, "--index-url", index], log) != 0:
            continue
        status = _torch_status(py)
        if len(status) == 5 and status[2] == "True":
            log(f"PyTorch {status[0]} 已启用显卡加速。")
            return True
        log("这个版本在你的显卡驱动上用不了，换一个试试……")
    log("⚠ 没能启用显卡加速（可以更新 NVIDIA 驱动后重新安装），暂时用 CPU 处理，会比较慢。")
    return False


# audioread：新版 librosa 不再自动带上，但 audio-separator 需要它
AI_PACKAGES = ["audio-separator", "onnxruntime", "audioread"]


def _verify_imports(py: list[str], log) -> None:
    """试着导入分离器；缺哪个模块就自动补装哪个（最多补 5 次）。"""
    check = "import torch, soundfile\nfrom audio_separator.separator import Separator\nprint('ok')"
    for _ in range(6):
        result = subprocess.run(py + ["-c", check], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if result.returncode == 0:
            return
        match = re.search(r"No module named '([\w.]+)'", result.stderr)
        if not match:
            raise RuntimeError("AI 组件自检失败：\n" + result.stderr[-1500:])
        module = match.group(1).split(".")[0]
        log(f"补装缺少的组件：{module}")
        package = {"cv2": "opencv-python", "sklearn": "scikit-learn", "yaml": "pyyaml"}.get(module, module)
        if _run_stream(py + ["-m", "pip", "install", "--disable-pip-version-check", package], log) != 0:
            raise RuntimeError(f"补装 {package} 失败，请检查网络后重试。")
    raise RuntimeError("AI 组件自检多次失败。")


def install_ai(log=lambda _message: None) -> None:
    """安装演唱会降噪的 AI 环境和两个模型（首次约 4 GB，之后不需要联网）。"""
    base = _find_ai_base_python(log)
    if not ai_python().is_file():
        log("正在创建 AI 独立环境……")
        if _run_stream(base + ["-m", "venv", str(AI_ENV)], log) != 0:
            raise RuntimeError("创建 AI 环境失败。")
    py = [str(ai_python())]
    _run_stream(py + ["-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], log)
    log("正在安装音频分离组件……")
    if _run_stream(py + ["-m", "pip", "install", "--disable-pip-version-check", *AI_PACKAGES], log) != 0:
        raise RuntimeError("audio-separator 安装失败，请检查网络后重试。")
    if has_nvidia():
        log("检测到 NVIDIA 显卡。")
        _ensure_cuda_torch(py, log)
    else:
        log("没有检测到 NVIDIA 显卡，使用 CPU 版 PyTorch。")
    _verify_imports(py, log)
    log("正在下载两个 AI 模型（去观众声、去底噪，共约 1.8 GB）……")
    run_worker(["--download", "--models", str(MODELS_DIR)], lambda event: log(event.get("stage", "")) if event.get("type") == "progress" else None)
    READY_FLAG.write_text("ok", encoding="utf-8")
    log("演唱会降噪组件安装完成。")


# ----------------------------------------------------------------- worker

def _worker_env() -> dict:
    """AI 进程的环境变量：audio-separator 要求 PATH 里能找到名叫 ffmpeg 的程序。
    程序自带的 ffmpeg（imageio-ffmpeg）文件名不是 ffmpeg.exe，所以复制一份到 .tools/ffmpeg-bin。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if shutil.which("ffmpeg"):
        return env
    try:
        import imageio_ffmpeg
        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return env
    bin_dir = HERE / ".tools" / "ffmpeg-bin"
    target = bin_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not target.is_file() or target.stat().st_size != source.stat().st_size:
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def run_worker(args: list[str], on_event, on_log=None) -> dict:
    """运行 AI 进程，把 JSON 事件交给 on_event；返回 done 事件，失败时抛出异常。"""
    process = subprocess.Popen(
        [str(ai_python()), "-u", str(WORKER)] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW, cwd=str(HERE), env=_worker_env(),
    )
    done, error, tail = None, None, []
    assert process.stdout
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if event:
                if event.get("type") == "done":
                    done = event
                elif event.get("type") == "error":
                    error = event
                on_event(event)
                continue
        tail = (tail + [line])[-20:]
        if on_log:
            on_log(line)
    code = process.wait()
    if error:
        raise RuntimeError("AI 处理失败：" + error.get("message", "") + "\n" + error.get("detail", "")[-1500:])
    if code != 0 or not done:
        raise RuntimeError("AI 处理进程异常退出：\n" + "\n".join(tail)[-1500:])
    return done


# ----------------------------------------------------------------- ffmpeg 工具

def _ffmpeg(ffmpeg: str, args: list[str], what: str) -> str:
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin"] + args, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"{what}失败：\n" + result.stderr.strip()[-1200:])
    return result.stderr


def probe(ffmpeg: str, path: str) -> dict:
    """返回 {duration, has_video, has_audio}。"""
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    text = result.stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)) if match else 0.0
    video = bool(re.search(r"Stream #\S+.*: Video: (?!mjpeg|png)", text))
    audio = "Audio:" in text
    if not audio:
        raise RuntimeError("这个文件里没有声音。")
    mono = bool(re.search(r"Stream #\S+.*: Audio: [^\n]*\bmono\b", text))
    return {"duration": duration, "has_video": video, "has_audio": audio, "mono": mono}


def peak_level(ffmpeg: str, path: str) -> float:
    """整段的最大峰值（线性，1.0 = 0 dBFS）。"""
    text = _ffmpeg(ffmpeg, ["-i", path, "-vn", "-af", "astats=measure_perchannel=none:measure_overall=Peak_level",
                            "-f", "null", "-"], "分析音量")
    values = re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)", text)
    if not values or values[-1] == "-inf":
        return 0.0
    return 10 ** (float(values[-1]) / 20)


_SOXR: dict[str, bool] = {}


def _resampler(ffmpeg: str) -> str:
    """高质量重采样：有 soxr 就用 soxr，没有（比如自带的 ffmpeg）就用高精度的内置重采样器。"""
    if ffmpeg not in _SOXR:
        try:
            conf = subprocess.run([ffmpeg, "-hide_banner", "-buildconf"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=30).stdout
        except Exception:
            conf = ""
        _SOXR[ffmpeg] = "--enable-libsoxr" in conf
    if _SOXR[ffmpeg]:
        return f"aresample={WORK_RATE}:resampler=soxr:precision=28"
    return f"aresample={WORK_RATE}:filter_size=64:phase_shift=10:cutoff=0.97"


def extract(ffmpeg: str, source: str, target: str, gain: float, start: float | None = None, length: float | None = None,
            mono: bool = False) -> None:
    """取出音频：44.1 kHz 立体声 32 位浮点（高质量重采样），并乘以 gain。"""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", source]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    # 单声道：左右声道都放原样的声音（ffmpeg 默认的单声道转立体声会把音量降低 3 dB）
    channels = ["-af", f"pan=stereo|c0=c0|c1=c0,{_resampler(ffmpeg)},volume={gain:.8f}"] if mono else \
        ["-ac", "2", "-af", f"{_resampler(ffmpeg)},volume={gain:.8f}"]
    args += ["-vn", *channels,
             "-c:a", "pcm_f32le", "-y", target]
    _ffmpeg(ffmpeg, args, "取出音频")


FLAC_24 = ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-compression_level", "8"]
PREVIEW_WAV = ["-c:a", "pcm_s16le"]


def unique_path(folder: str, stem: str, extension: str) -> str:
    path = os.path.join(folder, f"{stem}.{extension}")
    index = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({index}).{extension}")
        index += 1
    return path


def mux_video(ffmpeg: str, video: str, audio: str, target: str) -> None:
    """画面原样复制，只换声音（AAC 320k），不重新压缩画面。"""
    _ffmpeg(ffmpeg, ["-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-shortest", "-movflags", "+faststart",
                     "-y", target], "生成视频")


# ----------------------------------------------------------------- 主流程

# ----------------------------------------------------------------- 可调参数

DEFAULT_SETTINGS = {
    "strength": 0.9,   # 降噪强度：处理后声音所占比例（其余为原声）
    "auto": 0.8,       # 自动音色校正的程度（0 = 不校正）
    "bass": 0.0,       # 低频（dB）
    "mid": 0.0,        # 中频（dB）
    "treble": 0.0,     # 高频（dB）
    "air": 0.5,        # 补回的高频（音质修复）音量，0 = 不要
    "normalize": False,
    # 人声/伴奏分开调（需要勾选“分离人声和伴奏”）；都为 0 时和不分离完全一样
    "vocal_level": 0.0,  # 人声音量（dB）
    "inst_level": 0.0,   # 伴奏音量（dB）
    "inst_bass": 0.0,    # 伴奏低频（dB）
    "inst_harsh": 0.0,   # 伴奏刺耳频段 2–5 kHz（dB，负数 = 柔和）
    "inst_treble": 0.0,  # 伴奏高频（dB）
}
BASS_HZ, MID_HZ, TREBLE_HZ, HARSH_HZ = 120.0, 1500.0, 5000.0, 3200.0


def eq_curve(settings: dict, analysis: dict | None, part: str | None = None) -> list[tuple[float, float]]:
    """把“自动校正 + 低/中/高频”合成一条均衡曲线：[(频率 Hz, 增益 dB)]。
    part="inst" 时再叠加伴奏专用的低频 / 刺耳 / 高频调整。"""
    import math

    centers = (analysis or {}).get("centers") or [31.5 * 2 ** (i / 3) for i in range(28)]
    auto = dict((round(c, 1), g) for c, g in (analysis or {}).get("auto_curve") or [])
    points = []
    for f in list(centers) + [20000.0]:
        gain = settings.get("auto", 0.0) * auto.get(round(f, 1), 0.0)
        gain += settings.get("bass", 0.0) / (1 + (f / BASS_HZ) ** 2)                               # 低频搁架
        gain += settings.get("mid", 0.0) * math.exp(-math.log2(f / MID_HZ) ** 2 / (2 * 0.9 ** 2))  # 中频（很宽的峰）
        gain += settings.get("treble", 0.0) * (f / TREBLE_HZ) ** 2 / (1 + (f / TREBLE_HZ) ** 2)    # 高频搁架
        if part == "inst":
            gain += settings.get("inst_bass", 0.0) / (1 + (f / BASS_HZ) ** 2)
            gain += settings.get("inst_harsh", 0.0) * math.exp(-math.log2(f / HARSH_HZ) ** 2 / (2 * 0.7 ** 2))
            gain += settings.get("inst_treble", 0.0) * (f / TREBLE_HZ) ** 2 / (1 + (f / TREBLE_HZ) ** 2)
        points.append((float(f), round(gain, 2)))
    return points


def _db(value: float) -> float:
    return 10 ** (value / 20)


def predicted_levels(settings: dict, analysis: dict) -> list[float]:
    """可视化用：估算调整后每个频段的能量（dB）。"""
    import math

    wet, dry, air = settings["strength"], 1 - settings["strength"], settings.get("air", 0.0)
    eq = dict(eq_curve(settings, analysis))
    split = bool(analysis.get("vocals"))
    eq_inst = dict(eq_curve(settings, analysis, "inst")) if split else eq
    gv, gi = _db(settings.get("vocal_level", 0.0)), _db(settings.get("inst_level", 0.0))
    out = []
    for i, f in enumerate(analysis["centers"]):
        f = float(f)
        base = 10 ** (analysis["processed"][i] / 10)
        rest = dry ** 2 * 10 ** (analysis["original"][i] / 10)
        if analysis.get("highs"):
            rest += (air ** 2) * 10 ** (analysis["highs"][i] / 10)
        power = rest * 10 ** (eq.get(f, 0.0) / 10)
        if split:
            voc = min(10 ** (analysis["vocals"][i] / 10), base)
            inst = max(base - voc, base * 0.01)
            power += wet ** 2 * (gv ** 2 * voc * 10 ** (eq.get(f, 0.0) / 10) + gi ** 2 * inst * 10 ** (eq_inst.get(f, 0.0) / 10))
        else:
            power += wet ** 2 * base * 10 ** (eq.get(f, 0.0) / 10)
        out.append(10 * math.log10(power + 1e-20))
    return out


def _eq_filter(points: list[tuple[float, float]]) -> str:
    """ffmpeg 线性相位均衡（零延迟），频率分辨率约 5 Hz。"""
    entries = [(0.0, points[0][1])] + points + [(24000.0, points[-1][1])]
    spec = ";".join(f"entry({f:.1f},{g:.2f})" for f, g in entries)
    return f"firequalizer=gain_entry='{spec}':delay=0.1:zero_phase=on:gain='gain_interpolate(f)'"


PRESETS_FILE = HERE / ".tools" / "concert_presets.json"


def load_presets() -> dict:
    """{"_last": 上次用的设置, "预设名": 设置, ...}"""
    import json
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_presets(data: dict) -> None:
    import json
    try:
        PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# ----------------------------------------------------------------- 处理

SESSION_ROOT = Path(tempfile.gettempdir()) / "audio_studio_concert"


def run_ai(source: str, ffmpeg: str, steps: list[str], preview_start: float | None = None,
           preview_length: float = 30.0, log=lambda _m: None, progress=lambda _f, _s="": None) -> dict:
    """运行 AI 部分（慢），返回一个 session；之后可以用 render() 按不同设置反复导出（快）。"""
    import json
    import shutil as _shutil
    import uuid

    if not ai_available():
        raise RuntimeError("演唱会降噪组件还没有安装。请先点“安装演唱会降噪组件”。")
    info = probe(ffmpeg, source)
    preview = preview_start is not None
    if preview:
        preview_start = max(0.0, min(preview_start, max(0.0, info["duration"] - 1)))
    kind = "preview" if preview else "full"
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    for old in SESSION_ROOT.glob(kind + "_*"):   # 同类的旧结果删掉，避免占满硬盘
        _shutil.rmtree(old, ignore_errors=True)
    folder = SESSION_ROOT / f"{kind}_{uuid.uuid4().hex[:8]}"
    folder.mkdir()

    log("正在分析音量……")
    progress(0.01, "分析音量")
    peak = peak_level(ffmpeg, source) or 1.0
    gain = HEADROOM / peak
    original = str(folder / "original.wav")
    log("正在取出音频……")
    extract(ffmpeg, source, original, gain, preview_start if preview else None, preview_length if preview else None,
            mono=info.get("mono", False))
    processed = str(folder / "processed.wav")
    analysis_path = str(folder / "analysis.json")

    def on_event(event):
        kind_ = event.get("type")
        if kind_ == "device":
            log(("使用显卡：" if event.get("gpu") else "使用：") + event.get("name", ""))
        elif kind_ == "notice":
            log(event.get("message", ""))
        elif kind_ == "progress":
            progress(0.05 + 0.9 * float(event.get("fraction", 0)), event.get("stage", ""))

    run_worker(["--input", original, "--output", processed, "--models", str(MODELS_DIR),
                "--steps", ",".join(steps), "--analysis", analysis_path], on_event)
    with open(analysis_path, encoding="utf-8") as handle:
        analysis = json.load(handle)
    progress(0.97, "AI 处理完成")
    return {"source": source, "folder": str(folder), "original": original, "processed": processed,
            "analysis": analysis, "restore": 1.0 / gain, "info": info, "preview": preview}


def render(ffmpeg: str, session: dict, settings: dict, target: str, codec: list[str], only: str | None = None) -> None:
    """按设置混合并均衡（不重跑 AI，几秒钟）。
    整体 = 处理后 × 强度 + 原声 × (1-强度) + 补回的高频 × air；
    分离了人声时：处理后 = 人声 + 伴奏（伴奏 = 处理后 − 人声），两者各自调音量和均衡。
    only="vocals"/"inst"：只导出人声 / 伴奏分轨。"""
    analysis = session["analysis"]
    layout = analysis.get("layout") or {"base": 0, "highs": 2 if analysis.get("has_highs") else None, "vocals": None}
    restore = session["restore"]
    wet, dry = settings["strength"] * restore, (1 - settings["strength"]) * restore
    air = settings.get("air", 0.0) * restore
    split = layout.get("vocals") is not None
    eq_all = _eq_filter(eq_curve(settings, analysis))
    parts, finals = [], []

    def chans(start):
        return f"pan=stereo|c0=c{start}|c1=c{start + 1}"

    if split:
        v = layout["vocals"]
        gv, gi = _db(settings.get("vocal_level", 0.0)), _db(settings.get("inst_level", 0.0))
        parts.append(f"[0:a]asplit=3[s1][s2][s3]")
        if only != "inst":
            parts.append(f"[s1]{chans(v)},volume={wet * gv:.8f},{eq_all}[V]")
            finals.append("[V]")
        else:
            parts.append("[s1]anullsink")
        if only != "vocals":
            # 伴奏 = 处理后 − 人声（反相相加），再单独均衡
            parts.append(f"[s2]{chans(0)},volume={wet * gi:.8f}[b]")
            parts.append(f"[s3]{chans(v)},volume={-wet * gi:.8f}[n]")
            parts.append(f"[b][n]amix=inputs=2:normalize=0:duration=first,{_eq_filter(eq_curve(settings, analysis, 'inst'))}[I]")
            finals.append("[I]")
        else:
            parts.append("[s2]anullsink")
            parts.append("[s3]anullsink")
        base_source = None
    else:
        base_source = f"[0:a]{chans(0)},volume={wet:.8f}[w]"
    rest = []
    if base_source and only is None:
        parts.append(base_source)
        rest.append("[w]")
    if only is None and dry > 0:
        parts.append(f"[1:a]volume={dry:.8f}[d]")
        rest.append("[d]")
    if layout.get("highs") is not None and air > 0 and only != "vocals":
        parts.append(f"[0:a]{chans(layout['highs'])},volume={air:.8f}[h]")
        rest.append("[h]")
    if rest:
        joined = "".join(rest)
        if len(rest) > 1:
            parts.append(f"{joined}amix=inputs={len(rest)}:normalize=0:duration=first,{eq_all}[R]")
        else:
            parts.append(f"{joined}{eq_all}[R]")
        finals.append("[R]")
    if not finals:
        raise RuntimeError("没有可以导出的声音。")
    if len(finals) > 1:
        parts.append(f"{''.join(finals)}amix=inputs={len(finals)}:normalize=0:duration=first[out]")
    else:
        parts.append(f"{finals[0]}anull[out]")
    graph = ";".join(parts)
    mixed = target + ".mix.wav"
    _ffmpeg(ffmpeg, ["-i", session["processed"], "-i", session["original"], "-filter_complex", graph,
                     "-map", "[out]", "-c:a", "pcm_f32le", "-y", mixed], "混合与均衡")
    try:
        chain = []
        if settings.get("normalize") and only is None:
            chain.append("loudnorm=I=-14:LRA=20:TP=-1")   # 宽 LRA：只调整整体音量，尽量不压缩动态
        else:
            peak = peak_level(ffmpeg, mixed)
            if peak > 0.999:                               # 整体等比例降一点，绝不削波
                chain.append(f"volume={0.999 / peak:.8f}")
        args = ["-i", mixed]
        if chain:
            args += ["-af", ",".join(chain)]
        _ffmpeg(ffmpeg, args + codec + ["-y", target], "导出")
    finally:
        try:
            os.remove(mixed)
        except OSError:
            pass


def render_preview(ffmpeg: str, session: dict, settings: dict) -> dict:
    keep_dir = Path(tempfile.gettempdir()) / "audio_studio_preview"
    keep_dir.mkdir(exist_ok=True)
    a, b = str(keep_dir / "原声.wav"), str(keep_dir / "调整后.wav")
    if not os.path.isfile(a) or session.get("_orig_rendered") != session["folder"]:
        _ffmpeg(ffmpeg, ["-i", session["original"], "-af", f"volume={session['restore']:.8f}", "-c:a", "pcm_s16le", "-y", a], "生成试听")
        session["_orig_rendered"] = session["folder"]
    render(ffmpeg, session, settings, b, PREVIEW_WAV)
    return {"preview_original": a, "preview_processed": b}


def export(ffmpeg: str, session: dict, settings: dict, output_dir: str, make_video: bool,
           log=lambda _m: None, stems: bool = False) -> dict:
    """整段导出：24-bit FLAC，可选同时生成视频（画面不重新压缩）。"""
    source = session["source"]
    stem = Path(source).stem
    os.makedirs(output_dir, exist_ok=True)
    audio_out = unique_path(output_dir, f"{stem}_演唱会降噪", "flac")
    render(ffmpeg, session, settings, audio_out, FLAC_24)
    results = {"audio": audio_out}
    log(f"已生成音频：{audio_out}")
    if stems and (session["analysis"].get("layout") or {}).get("vocals") is not None:
        for part, name in (("vocals", "人声"), ("inst", "伴奏")):
            path = unique_path(output_dir, f"{stem}_{name}", "flac")
            render(ffmpeg, session, settings, path, FLAC_24, only=part)
            results[part] = path
            log(f"已生成{name}分轨：{path}")
    if make_video and session["info"]["has_video"]:
        ext = Path(source).suffix.lower()
        ext = ext if ext in (".mp4", ".mov", ".m4v", ".mkv") else ".mp4"
        video_out = unique_path(output_dir, f"{stem}_演唱会降噪", ext.lstrip("."))
        log("正在把处理后的声音放回视频（画面不重新压缩）……")
        mux_video(ffmpeg, source, audio_out, video_out)
        results["video"] = video_out
        log(f"已生成视频：{video_out}")
    return results


def process(source: str, output_dir: str, ffmpeg: str, steps: list[str], strength: float = 0.9,
            normalize: bool = False, make_video: bool = False, preview_start: float | None = None,
            preview_length: float = 30.0, log=lambda _m: None, progress=lambda _f, _s="": None,
            settings: dict | None = None) -> dict:
    """一步到位（兼容旧接口）：AI 处理 + 按设置导出。"""
    settings = dict(DEFAULT_SETTINGS, strength=strength, normalize=normalize, **(settings or {}))
    session = run_ai(source, ffmpeg, steps, preview_start, preview_length, log, progress)
    if session["preview"]:
        result = render_preview(ffmpeg, session, settings)
    else:
        result = export(ffmpeg, session, settings, output_dir, make_video, log)
    result["session"] = session
    progress(1.0, "完成")
    return result


def play_wav(path: str | None) -> None:
    """试听播放（Windows 自带，WAV，可随时切换）。path=None 停止。"""
    if os.name != "nt":
        return
    import winsound

    if path is None:
        winsound.PlaySound(None, 0)
    else:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
