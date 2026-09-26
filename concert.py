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
import threading
import time
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
MEDIA_TYPES = [("视频或音频", "*.mp4 *.mov *.mkv *.m4v *.avi *.webm *.flv *.mts *.m2ts *.3gp *.ts *.wmv *.mka *.wma *.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus"),
               ("所有文件", "*.*")]
MEDIA_EXTENSIONS = {"." + ext.split(".")[-1] for ext in MEDIA_TYPES[0][1].split()}


# ----------------------------------------------------------------- 环境

def ai_python() -> Path:
    return AI_ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ai_available() -> bool:
    return READY_FLAG.is_file() and ai_python().is_file()


class Transfer:
    """所有下载（pip 安装包、AI 模型）的进度汇总，界面每隔一会儿读一次画出来。线程安全。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.task = ""            # 正在做的事（例如“安装扒谱组件”）
            self.name, self.done, self.total = "", 0, 0
            self.finished = []        # [(名字, 字节数)]
            self.samples = []         # [(时间, 累计字节)] 用来算速度和画速度曲线
            self.bytes_before = 0     # 已完成文件的字节数
            self.status = ""
            self.active = False
            self.updated = time.time()

    def begin(self, task: str):
        self.reset()
        with self.lock:
            self.task, self.active = task, True

    def end(self, status: str = ""):
        with self.lock:
            self._close_item()
            self.active, self.name = False, ""
            self.status = status
            self.updated = time.time()

    def _close_item(self):
        if self.name and self.total:
            self.finished = (self.finished + [(self.name, self.total)])[-8:]
            self.bytes_before += self.total
        self.name, self.done, self.total = "", 0, 0

    def update(self, name: str, done: int, total: int):
        with self.lock:
            if name != self.name:
                self._close_item()
                self.name = name
            self.done, self.total = int(done), int(total or 0)
            self.active = True
            now = time.time()
            self.samples = [x for x in self.samples if now - x[0] < 120] + [(now, self.bytes_before + self.done)]
            self.updated = now
            if self.total and self.done >= self.total:
                finished_name = self.name
                self._close_item()
                if not self.task:     # 处理途中顺便下载的模型：下完就收起
                    self.active, self.status = False, finished_name + " 下载完成"

    def note(self, text: str):
        with self.lock:
            self.status = text[-200:]
            self.updated = time.time()

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            recent = [x for x in self.samples if now - x[0] < 4]
            speed = 0.0
            if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                speed = (recent[-1][1] - recent[0][1]) / max(now - recent[0][0], 0.5)
            if self.samples and now - self.samples[-1][0] > 6:
                speed = 0.0
            return {"task": self.task, "name": self.name, "done": self.done, "total": self.total, "speed": speed,
                    "finished": list(self.finished), "samples": list(self.samples), "status": self.status,
                    "active": self.active, "updated": self.updated}


TRANSFER = Transfer()
_PIP_RAW: dict[str, bool] = {}


def _pip_supports_raw(python: str) -> bool:
    """pip 24.1 起支持 --progress-bar raw（逐行输出“Progress 已下载 of 总数”），用来画下载进度。"""
    if python not in _PIP_RAW:
        try:
            out = subprocess.run([python, "-m", "pip", "--version"], capture_output=True, text=True,
                                 creationflags=NO_WINDOW, timeout=30).stdout
            major, minor = (int(x) for x in re.search(r"pip (\d+)\.(\d+)", out).groups())
            _PIP_RAW[python] = (major, minor) >= (24, 1)
        except Exception:
            _PIP_RAW[python] = False
    return _PIP_RAW[python]


def _pip_name(filename: str) -> str:
    """torch-2.14.0+cu130-cp312-...whl → torch 2.14.0"""
    parts = filename.split("/")[-1].split("-")
    return f"{parts[0]} {parts[1]}" if len(parts) > 1 else filename


def _run_stream(command: list[str], log, env=None) -> int:
    """运行命令并把输出逐行交给 log；pip 安装时顺便把下载进度交给 TRANSFER。"""
    is_pip = "pip" in command and "install" in command
    if is_pip and _pip_supports_raw(command[0]):
        at = command.index("install") + 1
        command = command[:at] + ["--progress-bar", "raw"] + command[at:]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env=env,
    )
    assert process.stdout
    current = ""
    for line in process.stdout:
        line = line.rstrip()
        match = re.match(r"\s*Progress (\d+) of (\d+)", line)
        if match:
            TRANSFER.update(current or "下载中", int(match.group(1)), int(match.group(2)))
            continue
        match = re.match(r"\s*Downloading (\S+)", line)
        if match and not match.group(1).endswith(".metadata"):
            current = _pip_name(match.group(1))
        if is_pip and line.strip():
            TRANSFER.note(line.strip())
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


# 安装版本：配置低的电脑不下载用不上的大模型（以后想要可以在“设置”里升级，或勾选时自动补下）
EDITIONS = {
    "lite": ("精简版", ["crowd", "denoise"], "去观众声 + 去底噪；适合没有 NVIDIA 显卡的电脑"),
    "standard": ("标准版", ["crowd", "denoise", "split", "restore"], "再加人声/伴奏分离、音质修复；适合 4–8 GB 显存"),
    "full": ("完整版", ["crowd", "denoise", "split", "restore", "dereverb"], "再加去混响（现场感）；适合 8 GB 以上显存"),
}
MODEL_MB = {"crowd": 913, "denoise": 913, "split": 913, "dereverb": 913, "restore": 147}
MODEL_FILES = {"crowd": "mel_band_roformer_crowd_aufr33_viperx_sdr_8.7144.ckpt",
               "denoise": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt",
               "split": "vocals_mel_band_roformer.ckpt",
               "dereverb": "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
               "restore": "apollo_universal_model.ckpt"}
EDITION_FILE = AI_ENV / "edition.txt"


def edition_size_gb(edition: str, gpu: bool) -> float:
    """大约要下载多少（显卡版 PyTorch 约 2.6 GB，CPU 版约 0.3 GB，其他依赖约 0.4 GB）。"""
    models = sum(MODEL_MB[k] for k in EDITIONS[edition][1] if not model_present(k)) / 1024
    runtime = 0.0 if ai_available() else (2.6 if gpu else 0.3) + 0.4
    return round(runtime + models, 1)


def model_present(step: str) -> bool:
    path = MODELS_DIR / MODEL_FILES[step]
    return path.is_file() and path.stat().st_size > 10_000_000


def installed_edition() -> str | None:
    if not ai_available():
        return None
    try:
        name = EDITION_FILE.read_text(encoding="utf-8").strip()
        if name in EDITIONS:
            return name
    except OSError:
        pass
    # 旧版本装的：看实际下了哪些模型
    return next((e for e in ("full", "standard", "lite") if all(model_present(k) for k in EDITIONS[e][1])), "lite")


def install_ai(log=lambda _message: None, edition: str = "standard", repair: bool = False) -> None:
    """按版本安装演唱会降噪的 AI 环境和模型（之后不需要联网）。已装好时只补下缺的模型（升级版本）。"""
    if ai_available() and not repair:
        _download_edition(edition, log)
        return
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
        log("没有检测到 NVIDIA 显卡，使用 CPU 版 PyTorch（小很多）。")
    _verify_imports(py, log)
    _download_edition(edition, log)


def _download_edition(edition: str, log) -> None:
    name, steps, _ = EDITIONS[edition]
    missing = [k for k in steps if not model_present(k)]
    if missing:
        log(f"正在下载{name}的模型（{len(missing)} 个，约 {sum(MODEL_MB[k] for k in missing) / 1024:.1f} GB）……")
        run_worker(["--download", "--models", str(MODELS_DIR), "--download-steps", ",".join(missing)],
                   lambda event: log(event.get("stage", "")) if event.get("type") == "progress" else None)
    READY_FLAG.write_text("ok", encoding="utf-8")
    EDITION_FILE.write_text(edition, encoding="utf-8")
    log(f"演唱会降噪组件（{name}）安装完成。")


# ----------------------------------------------------------------- worker

_REACH: dict[str, bool] = {}


def _reachable(host: str) -> bool:
    if host not in _REACH:
        import socket
        try:
            socket.create_connection((host, 443), timeout=5).close()
            _REACH[host] = True
        except OSError:
            _REACH[host] = False
    return _REACH[host]


def _worker_env() -> dict:
    """AI 进程的环境变量：audio-separator 要求 PATH 里能找到名叫 ffmpeg 的程序。
    程序自带的 ffmpeg（imageio-ffmpeg）文件名不是 ffmpeg.exe，所以复制一份到 .tools/ffmpeg-bin。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    token_file = HERE / ".tools" / "hf_token.txt"
    if token_file.is_file():
        env["HF_TOKEN"] = token_file.read_text(encoding="utf-8").strip()
    if "HF_ENDPOINT" not in env and not _reachable("huggingface.co"):
        env["HF_ENDPOINT"] = "https://hf-mirror.com"   # Hugging Face 连不上时，扒谱模型等改从镜像下载
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


def analyze_reference(ffmpeg: str, path: str) -> dict:
    """参考曲（录音室版等）→ 频谱，用来做音色匹配。"""
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "reference.wav")
        _ffmpeg(ffmpeg, ["-i", path, "-vn", "-ac", "2", "-ar", str(WORK_RATE), "-t", "900", "-c:a", "pcm_f32le", "-y", wav], "读取参考曲")
        found = {}
        run_worker(["--models", str(MODELS_DIR), "--reference", wav],
                   lambda event: found.update(levels=event["levels"]) if event.get("type") == "reference" else None)
    if not found.get("levels"):
        raise RuntimeError("参考曲分析失败。")
    return {"name": Path(path).name, "levels": found["levels"]}


def _write_ir(path: Path, seconds: float, predelay: float, rt60: float, seed: int) -> None:
    """合成一个立体声“音乐厅”冲激响应（左右去相关的指数衰减噪声，高频衰减更快）。纯 Python，不需要 numpy。"""
    import random
    import struct
    import wave
    rnd = random.Random(seed)
    rate = WORK_RATE
    n = int(rate * seconds)
    start = int(rate * predelay)
    decay = 6.91 / (rt60 * rate)                 # 60 dB 衰减所需时间 = rt60
    frames = bytearray()
    low = [0.0, 0.0]
    peak = 0.0
    samples = []
    for i in range(n):
        env = 0.0 if i < start else pow(2.718281828, -decay * (i - start))
        pair = []
        for ch in range(2):
            alpha = 0.35 + 0.5 * min(1.0, (i - start) / (rate * rt60)) if i >= start else 0.35
            low[ch] = low[ch] + (rnd.uniform(-1, 1) - low[ch]) * (1 - alpha)   # 越往后越暗
            pair.append(low[ch] * env)
        samples.append(pair)
        peak = max(peak, abs(pair[0]), abs(pair[1]))
    # 能量归一：卷积后的响度和原声差不多（再由滑块决定送多少）；同时保证不超过 16 位范围
    energy = max(sum(a * a for a, _ in samples), sum(b * b for _, b in samples)) or 1.0
    scale = min(1.0 / energy ** 0.5, 0.99 / (peak or 1.0))
    for left, right in samples:
        frames += struct.pack("<hh", int(left * scale * 32767), int(right * scale * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def impulse(kind: str) -> str:
    """hall = 现场感用的大厅混响；room = 耳机空间音频用的短早期反射。第一次用时生成，之后复用。"""
    path = HERE / ".tools" / f"ir_{kind}.wav"
    if not path.is_file():
        if kind == "hall":
            _write_ir(path, 2.2, 0.025, 1.8, 7)
        else:
            _write_ir(path, 0.35, 0.004, 0.25, 11)
    return str(path)


class Cancelled(Exception):
    """用户点了“取消”。"""


_RUNNING = {"process": None, "cancelled": False}


def cancel() -> None:
    """取消正在进行的 AI 处理（直接结束 AI 进程）。"""
    _RUNNING["cancelled"] = True
    process = _RUNNING.get("process")
    if process and process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def run_worker(args: list[str], on_event, on_log=None, script: Path | None = None) -> dict:
    """运行 AI 进程，把 JSON 事件交给 on_event；返回 done 事件，失败时抛出异常。"""
    if _RUNNING["cancelled"]:
        raise Cancelled()
    process = subprocess.Popen(
        [str(ai_python()), "-u", str(script or WORKER)] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW, cwd=str(HERE), env=_worker_env(),
    )
    _RUNNING["process"] = process
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
                if event.get("type") == "download":
                    TRANSFER.update(event.get("name", "") or "模型", event.get("done", 0), event.get("total", 0))
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
    _RUNNING["process"] = None
    if TRANSFER.active and not TRANSFER.task:
        TRANSFER.end("模型下载中断" if code else "模型下载完成")
    if _RUNNING["cancelled"]:
        raise Cancelled()
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
    "inst_soften": 0.0,  # 伴奏动态柔化：只在特别刺耳的时刻压 2–5 kHz（0–1）
    "loudness": "match",  # match = 和原视频一样响；normalize = 统一到 -14 LUFS；off = 不调整
    "width": 0.0,        # 伪立体声宽度（0–1）：伴奏往两边展开，人声留在中间；单声道播放时完全抵消，不会变味
    "ambience": 0.0,     # 现场感（-1–1）：负 = 减少场馆混响（需勾选“去混响”）；正 = 加音乐厅空间感
    "headphone": False,  # 耳机空间音频：交叉馈送 + 早期反射，像站在场馆里听
    "reference": None,   # 参考曲音色：{"name": 文件名, "levels": 1/3 倍频程频谱}
}
LOUDNESS_MODES = {"和原视频一样响（推荐）": "match", "统一到 -14 LUFS（流媒体标准）": "normalize", "不调整": "off"}
BASS_HZ, MID_HZ, TREBLE_HZ, HARSH_HZ = 120.0, 1500.0, 5000.0, 3200.0


TONE_CENTERS = [round(31.5 * 2 ** (i / 3), 1) for i in range(28)]


def reference_curve(processed: list[float], reference: list[float], cutoff: float | None) -> dict:
    """参考曲音色匹配：和 tone.tone_curve 同样的规则（平滑、上限、高频最多 +3 dB），只是目标换成参考曲。"""
    def norm(levels):
        mids = [v for c, v in zip(TONE_CENTERS, levels) if 200 <= c <= 2000]
        ref = sum(mids) / len(mids)
        return [v - ref for v in levels]
    diff = [(t - m) * 0.8 for t, m in zip(norm(reference), norm(processed))]
    for _ in range(2):
        padded = [diff[0]] + diff + [diff[-1]]
        diff = [(padded[i] + padded[i + 1] + padded[i + 2]) / 3 for i in range(len(diff))]
    top = min(cutoff or 16000.0, 16000.0)
    out = {}
    for c, g in zip(TONE_CENTERS, diff):
        g = max(-10.0, min(6.0, g))
        if c > top * 0.9 or c < 40:
            g = min(g, 0.0)
        if c > 6000:
            g = min(g, 3.0)
        out[c] = g
    mid = sum(g for c, g in out.items() if 200 <= c <= 2000) / sum(1 for c in out if 200 <= c <= 2000)
    return {c: round(g - mid, 2) for c, g in out.items()}


def eq_curve(settings: dict, analysis: dict | None, part: str | None = None) -> list[tuple[float, float]]:
    """把“自动校正 + 低/中/高频”合成一条均衡曲线：[(频率 Hz, 增益 dB)]。
    part="inst" 时再叠加伴奏专用的低频 / 刺耳 / 高频调整。"""
    import math

    centers = (analysis or {}).get("centers") or [31.5 * 2 ** (i / 3) for i in range(28)]
    auto = dict((round(c, 1), g) for c, g in (analysis or {}).get("auto_curve") or [])
    reference = settings.get("reference")
    if reference and analysis and analysis.get("processed"):
        auto = reference_curve(analysis["processed"], reference["levels"], analysis.get("cutoff"))
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
    return f"firequalizer=gain_entry='{spec}':delay=0.1:zero_phase=off:gain='gain_interpolate(f)'"


# 线性相位 FIR（firequalizer，delay=0.1 s）会把声音整体推后 0.1 s（zero_phase 只改时间戳、不改数据），
# 所以这里显式对齐：各条支路补齐到相同延迟，最后整体裁掉，保证和原视频画面严格同步
FIR_DELAY = int(WORK_RATE * 0.1)


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
_TEMP = {"root": None}


def set_temp_root(folder: str | None) -> None:
    """中间文件放在哪（整场演唱会每小时约 3 GB）；None = 系统临时文件夹。"""
    _TEMP["root"] = Path(folder) / "audio_studio_concert" if folder else None


def session_root() -> Path:
    return _TEMP["root"] or SESSION_ROOT


def run_ai(source: str, ffmpeg: str, steps: list[str], preview_start: float | None = None,
           preview_length: float = 30.0, log=lambda _m: None, progress=lambda _f, _s="": None,
           fast: bool = True, low_vram: bool = False) -> dict:
    """运行 AI 部分（慢），返回一个 session；之后可以用 render() 按不同设置反复导出（快）。"""
    import json
    import shutil as _shutil
    import uuid

    if not ai_available():
        raise RuntimeError("演唱会降噪组件还没有安装。请先到左下角“设置”里安装。")
    _RUNNING["cancelled"] = False
    info = probe(ffmpeg, source)
    preview = preview_start is not None
    if preview:
        preview_start = max(0.0, min(preview_start, max(0.0, info["duration"] - 1)))
    kind = "preview" if preview else "full"
    root = session_root()
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob(kind + "_*"):   # 同类的旧结果删掉，避免占满硬盘
        _shutil.rmtree(old, ignore_errors=True)
    folder = root / f"{kind}_{uuid.uuid4().hex[:8]}"
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
        elif kind_ == "download":
            done_mb, total_mb = event.get("done", 0) / 1e6, max(event.get("total", 1), 1) / 1e6
            progress(0.05, f"下载模型 {event.get('name', '')}：{done_mb:.0f} / {total_mb:.0f} MB")
        elif kind_ == "progress":
            progress(0.05 + 0.9 * float(event.get("fraction", 0)), event.get("stage", ""))

    run_worker(["--input", original, "--output", processed, "--models", str(MODELS_DIR),
                "--steps", ",".join(steps), "--analysis", analysis_path] + ([] if fast else ["--no-fast"])
               + (["--low-vram"] if low_vram else []), on_event)
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
    soften = float(settings.get("inst_soften", 0.0)) if split and only != "vocals" else 0.0
    eq_all = _eq_filter(eq_curve(settings, analysis))
    # 每条支路经过的 FIR 个数必须一样（否则错位）；柔化让伴奏多过一个 FIR，其他支路补一样的延迟
    pad = f",adelay={FIR_DELAY}S:all=1" if soften > 0.01 else ""
    parts, finals = [], []

    def chans(start):
        return f"pan=stereo|c0=c{start}|c1=c{start + 1}"

    if split:
        v = layout["vocals"]
        gv, gi = _db(settings.get("vocal_level", 0.0)), _db(settings.get("inst_level", 0.0))
        parts.append("[0:a]asplit=3[s1][s2][s3]")
        if only != "inst":
            parts.append(f"[s1]{chans(v)},volume={wet * gv:.8f},{eq_all}{pad}[V]")
            finals.append("[V]")
        else:
            parts.append("[s1]anullsink")
        if only != "vocals":
            # 伴奏 = 处理后 − 人声（反相相加），再单独均衡
            parts.append(f"[s2]{chans(0)},volume={wet * gi:.8f}[b]")
            parts.append(f"[s3]{chans(v)},volume={-wet * gi:.8f}[n]")
            eq_inst = _eq_filter(eq_curve(settings, analysis, "inst"))
            if soften > 0.01:
                parts.append(f"[b][n]amix=inputs=2:normalize=0:duration=first,{eq_inst}[Ipre]")
                parts.extend(_soften_graph("[Ipre]", "[I]", soften, analysis, settings, wet * gi))
            else:
                parts.append(f"[b][n]amix=inputs=2:normalize=0:duration=first,{eq_inst}[I]")
            width = float(settings.get("width", 0.0)) if only is None else 0.0
            if width > 0.01:
                parts.append("[I]asplit=2[Imain][Iside]")
                finals.append("[Imain]")
            else:
                finals.append("[I]")
        else:
            parts.append("[s2]anullsink")
            parts.append("[s3]anullsink")
    rest = []
    if not split and only is None:
        parts.append(f"[0:a]{chans(0)},volume={wet:.8f}[w]")
        rest.append("[w]")
    if only is None and dry > 0:
        parts.append(f"[1:a]volume={dry:.8f}[d]")
        rest.append("[d]")
    ambience = float(settings.get("ambience", 0.0)) if only is None else 0.0
    if layout.get("reverb") is not None and ambience < -0.01:
        # 混响 = 处理后 − 去混响后；减去一部分混响 = 场馆回声变少
        parts.append(f"[0:a]{chans(layout['reverb'])},volume={wet * ambience:.8f}[rv]")
        rest.append("[rv]")
    if layout.get("highs") is not None and air > 0 and only != "vocals":
        parts.append(f"[0:a]{chans(layout['highs'])},volume={air:.8f}[h]")
        rest.append("[h]")
    if rest:
        joined = "".join(rest)
        mix = f"amix=inputs={len(rest)}:normalize=0:duration=first," if len(rest) > 1 else ""
        parts.append(f"{joined}{mix}{eq_all}{pad}[R]")
        finals.append("[R]")
    if not finals:
        raise RuntimeError("没有可以导出的声音。")
    total_delay = FIR_DELAY * (2 if soften > 0.01 else 1)
    extra_inputs = []
    if len(finals) > 1:
        parts.append(f"{''.join(finals)}amix=inputs={len(finals)}:normalize=0:duration=first[pre]")
    else:
        parts.append(f"{finals[0]}anull[pre]")
    cur = "[pre]"
    if only is None and ambience > 0.01:
        # 加音乐厅空间感：混响“送”一部分，干声不变
        extra_inputs.append(impulse("hall"))
        idx = 1 + len(extra_inputs)
        parts.append(f"{cur}asplit=2[hd][hs]")
        parts.append(f"[hs][{idx}:a]afir=gtype=none:irnorm=-1:irgain=1,volume={0.35 * ambience:.4f}[hw]")
        parts.append("[hd][hw]amix=inputs=2:normalize=0:duration=first[amb]")
        cur = "[amb]"
    width = float(settings.get("width", 0.0)) if only is None else 0.0
    if width > 0.01:
        # 伪立体声：侧声道 = 伴奏（没分离时用整体）的去相关版本；左 = 中 + 侧，右 = 中 − 侧（单声道播放时完全抵消）
        if not split:
            parts.append(f"{cur}asplit=2[wm][Iside]")
            cur = "[wm]"
        parts.append(f"[Iside]pan=mono|c0=0.5*c0+0.5*c1,highpass=f=250,adelay=13,volume={0.7 * width:.4f}[side]")
        parts.append(f"{cur}[side]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1-c2[wide]")
        cur = "[wide]"
    if only is None and settings.get("headphone"):
        # 耳机空间音频：交叉馈送（像用音箱听，左右耳都能听到对面）+ 左右不同的短早期反射
        extra_inputs.append(impulse("room"))
        idx = 1 + len(extra_inputs)
        parts.append(f"{cur}crossfeed=strength=0.3:range=0.55,asplit=2[hpd][hps]")
        parts.append(f"[hps][{idx}:a]afir=gtype=none:irnorm=-1:irgain=1,volume=0.3[hpr]")
        parts.append("[hpd][hpr]amix=inputs=2:normalize=0:duration=first[hp]")
        cur = "[hp]"
    frames = wav_frames(session["processed"])
    end = f":end_sample={total_delay + frames}" if frames else ""
    parts.append(f"{cur}atrim=start_sample={total_delay}{end},asetpts=N/SR/TB[out]")
    graph = ";".join(parts)
    mixed = target + ".mix.wav"
    inputs = ["-i", session["processed"], "-i", session["original"]]
    for extra in extra_inputs:
        inputs += ["-i", extra]
    _ffmpeg(ffmpeg, inputs + ["-filter_complex", graph,
                     "-map", "[out]", "-c:a", "pcm_f32le", "-y", mixed], "混合与均衡")
    try:
        mode = settings.get("loudness") or ("normalize" if settings.get("normalize") else "match")
        chain = []
        if only is None and mode == "normalize":
            chain.append("loudnorm=I=-14:LRA=20:TP=-1")   # 宽 LRA：只调整整体音量，尽量不压缩动态
        elif only is None and mode == "match":
            chain += _match_loudness(ffmpeg, session, mixed)
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


def wav_frames(path: str) -> int:
    """读 WAV 文件头得到采样点数（不需要 numpy/soundfile）。读不出来返回 0。"""
    import struct
    try:
        with open(path, "rb") as handle:
            if handle.read(4) != b"RIFF":
                return 0
            handle.read(8)
            block_align = 0
            while True:
                header = handle.read(8)
                if len(header) < 8:
                    return 0
                chunk, size = header[:4], struct.unpack("<I", header[4:])[0]
                if chunk == b"fmt ":
                    fmt = handle.read(size)
                    block_align = struct.unpack("<H", fmt[12:14])[0]
                elif chunk == b"data":
                    return size // block_align if block_align else 0
                else:
                    handle.seek(size + (size & 1), 1)
    except (OSError, struct.error):
        return 0


def _soften_graph(source: str, target: str, amount: float, analysis: dict, settings: dict, scale: float) -> list[str]:
    """伴奏动态柔化：把 2–5 kHz 单独拿出来压缩，只在特别刺耳的那几秒起作用。
    不起作用时 原声 − 这段 + 这段 = 原声，逐点一致。阈值按这首歌自己的刺耳度分布自动定。"""
    import math

    line = ((analysis.get("timeline") or {}).get("inst_harsh") or (analysis.get("timeline") or {}).get("harsh") or [])
    line = sorted(v for v in line if v > -90)
    # 阈值 = 这首歌伴奏刺耳度的中位数往下 0–3 dB；越刺耳的时刻超过阈值越多、压得越多
    level = (line[len(line) // 2] if line else -30.0) - 3.0 * amount
    eq_inst = dict(eq_curve(settings, analysis, "inst"))
    band_eq = sum(g for f, g in eq_inst.items() if 2000 <= f <= 5000) / max(1, sum(1 for f in eq_inst if 2000 <= f <= 5000))
    threshold_db = level + 20 * math.log10(max(scale, 1e-6)) + band_eq
    threshold = min(1.0, max(0.000977, 10 ** (threshold_db / 20)))
    ratio = 1 + 3 * amount
    band = ("firequalizer=gain_entry='entry(0,-90);entry(1500,-90);entry(2000,0);entry(5000,0);entry(6300,-90);entry(24000,-90)'"
            ":delay=0.1:zero_phase=off:gain='gain_interpolate(f)'")
    flat = "firequalizer=gain_entry='entry(0,0);entry(24000,0)':delay=0.1:zero_phase=off:gain='gain_interpolate(f)'"
    s1, s2, bp, bp1, bp2, bc, bn = (f"[{target[1:-1]}_{k}]" for k in ("a", "b", "bp", "bp1", "bp2", "bc", "bn"))
    return [
        f"{source}asplit=2{s1}{s2}",
        f"{s2}{band}{bp}",
        f"{bp}asplit=2{bp1}{bp2}",
        f"{bp1}acompressor=threshold={threshold:.6f}:ratio={ratio:.2f}:attack=5:release=150:knee=2:makeup=1:detection=rms{bc}",
        f"{bp2}volume=-1{bn}",
        f"{s1}{flat}{s1[:-1]}f]",   # 和带通支路同样的延迟，相减才能严格抵消
        f"{s1[:-1]}f]{bn}{bc}amix=inputs=3:normalize=0:duration=first{target}",
    ]


def _loudness(ffmpeg: str, path: str, pre_gain: float = 1.0) -> tuple[float, float]:
    """(响度 LUFS, 真峰值 dBTP)"""
    import json as _json
    text = _ffmpeg(ffmpeg, ["-i", path, "-af", f"volume={pre_gain:.8f},loudnorm=print_format=json", "-f", "null", "-"], "测量响度")
    data = _json.loads(text[text.rindex("{"):text.rindex("}") + 1])
    return float(data["input_i"]), float(data["input_tp"])


def _match_loudness(ffmpeg: str, session: dict, mixed: str) -> list[str]:
    """让导出的响度和原视频一样（大家会下意识觉得更响的更好听，这样对比才公平）。
    需要时用前瞻限幅器压住峰值，但限幅最多 4 dB——宁可稍微小声一点，也不压扁、不削波。"""
    if "orig_loudness" not in session:
        session["orig_loudness"] = _loudness(ffmpeg, session["original"], session["restore"])
    target, _ = session["orig_loudness"]
    current, peak = _loudness(ffmpeg, mixed)
    if not (-70 < target < 0 and -70 < current < 0):
        return []
    gain = target - current
    over = peak + gain + 1.0                               # 超过 -1 dBTP 多少
    if over > 4.0:
        gain -= over - 4.0
        over = 4.0
    chain = [f"volume={gain:.2f}dB"]
    if over > 0:
        chain.append("alimiter=limit=0.891:attack=5:release=80:level=false")
    else:
        chain.append("alimiter=limit=0.998:attack=1:release=50:level=false")  # 只防意外削波
    return chain


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


def quick_timeline(ffmpeg: str, path: str) -> dict:
    """选好文件后马上算（不用 AI）：每秒的整体响度和 2–5 kHz 刺耳度（dB）。用于时间轴显示。"""
    def run(chain: str) -> list[float]:
        text = _ffmpeg(ffmpeg, ["-i", path, "-vn", "-ac", "1", "-af",
                                f"aresample=16000,{chain}asetnsamples=n=16000:p=0,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,"
                                "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"], "分析时间轴")
        values = []
        for match in re.finditer(r"RMS_level=(-?[\d.]+|-inf)", text):
            values.append(-120.0 if match.group(1) == "-inf" else round(float(match.group(1)), 1))
        return values

    loud = run("")
    harsh = run("highpass=f=2000:poles=2,lowpass=f=5000:poles=2,")
    return {"loud": loud, "harsh": harsh[:len(loud)]}


class Player:
    """试听播放器（Windows 自带的 MCI，不用装任何东西）：可以在两个文件之间无缝切换到同一时间点，
    用来“按住听原声、松开听处理后”。"""

    def __init__(self):
        self.files = {}
        self.current = None

    def _mci(self, command: str) -> str:
        if os.name != "nt":
            return ""
        import ctypes
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciSendStringW(command, buffer, 255, 0)
        return buffer.value

    def load(self, **files) -> None:
        """load(original=..., processed=...)：换文件前先关掉，免得文件被占用无法覆盖。"""
        self.close()
        for name, path in files.items():
            if path and os.path.isfile(path):
                self._mci(f'open "{path}" type waveaudio alias as_{name}')
                self._mci(f"set as_{name} time format milliseconds")
                self.files[name] = path

    def close(self) -> None:
        for name in list(self.files):
            self._mci(f"close as_{name}")
        self.files.clear()
        self.current = None

    def position(self) -> int:
        if not self.current:
            return 0
        try:
            return int(self._mci(f"status as_{self.current} position") or 0)
        except ValueError:
            return 0

    def playing(self) -> bool:
        return bool(self.current) and self._mci(f"status as_{self.current} mode") == "playing"

    def play(self, name: str, position: int | None = None) -> None:
        if name not in self.files:
            return
        start = self.position() if position is None and self.playing() else (position or 0)
        if self.current and self.current != name:
            self._mci(f"stop as_{self.current}")
        self.current = name
        self._mci(f"play as_{name} from {max(0, start)}")

    def switch(self, name: str) -> None:
        """切到另一个文件，从同一个时间点接着播。"""
        self.play(name, self.position() if self.playing() else 0)

    def stop(self) -> None:
        for name in self.files:
            self._mci(f"stop as_{name}")
        self.current = None


PLAYER = Player()


def play_wav(path: str | None) -> None:
    """兼容旧接口：None = 停止。"""
    if path is None:
        PLAYER.stop()
    else:
        PLAYER.load(single=path)
        PLAYER.play("single", 0)
