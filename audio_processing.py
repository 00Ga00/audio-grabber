"""音频后期处理与可选 AI 人声增强。"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
from pathlib import Path


NO_WINDOW = 0x08000000 if os.name == "nt" else 0
OUTPUT_CODECS = {
    "wav": ["-c:a", "pcm_s24le"],
    "flac": ["-c:a", "flac", "-compression_level", "8"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "opus": ["-c:a", "libopus", "-b:a", "160k"],
}


def unique_path(folder: str, stem: str, extension: str) -> str:
    path = os.path.join(folder, f"{stem}.{extension}")
    index = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({index}).{extension}")
        index += 1
    return path


def media_duration(path: str, ffmpeg: str) -> float:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("无法读取音频时长。")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def split_boundaries(path: str, ffmpeg: str, threshold_db: float, silence_seconds: float) -> list[tuple[float, float]]:
    duration = media_duration(path, ffmpeg)
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            path,
            "-af",
            f"silencedetect=n={threshold_db}dB:d={silence_seconds}",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    cuts = []
    for start, end in zip(starts, ends):
        if start > 0.25 and end < duration - 0.25:
            cuts.append((start + end) / 2)
    points = [0.0] + cuts + [duration]
    return [(start, end) for start, end in zip(points, points[1:]) if end - start >= 0.5]


def _filters(trim_silence: bool, normalize: bool, threshold_db: float) -> str | None:
    filters = []
    if trim_silence:
        trim = f"silenceremove=start_periods=1:start_duration=0.08:start_threshold={threshold_db}dB"
        filters += [trim, "areverse", trim, "areverse"]
    if normalize:
        filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
    return ",".join(filters) or None


def postprocess_recording(
    source_wav: str,
    output_dir: str,
    stem: str,
    output_format: str,
    ffmpeg: str,
    trim_silence: bool,
    split_on_silence: bool,
    normalize: bool,
    threshold_db: float = -45.0,
    silence_seconds: float = 1.2,
    log=lambda _message: None,
) -> list[str]:
    os.makedirs(output_dir, exist_ok=True)
    ranges = (
        split_boundaries(source_wav, ffmpeg, threshold_db, silence_seconds)
        if split_on_silence
        else [(0.0, media_duration(source_wav, ffmpeg))]
    )
    outputs = []
    filter_chain = _filters(trim_silence, normalize, threshold_db)
    for index, (start, end) in enumerate(ranges, 1):
        suffix = f"_{index:03d}" if len(ranges) > 1 else ""
        output = unique_path(output_dir, stem + suffix, output_format)
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", source_wav]
        if filter_chain:
            command += ["-af", filter_chain]
        command += OUTPUT_CODECS[output_format] + [output]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        if completed.returncode != 0:
            raise RuntimeError("录音后期处理失败：\n" + completed.stderr.strip()[-1200:])
        if os.path.getsize(output) == 0:
            raise RuntimeError("录音输出文件为空。")
        outputs.append(output)
        log(f"已生成：{output}")
    return outputs


DEEPFILTER_VERSION = "0.5.6"
DEEPFILTER_URL = (
    "https://github.com/Rikorose/DeepFilterNet/releases/download/"
    f"v{DEEPFILTER_VERSION}/deep-filter-{DEEPFILTER_VERSION}-x86_64-pc-windows-msvc.exe"
)
DEEPFILTER_MIN_BYTES = 10_000_000


def deepfilter_path() -> Path:
    name = "deep-filter.exe" if os.name == "nt" else "deep-filter"
    return Path(__file__).resolve().parent / ".tools" / "deepfilter" / name


def ai_available() -> bool:
    path = deepfilter_path()
    return path.is_file() and path.stat().st_size >= DEEPFILTER_MIN_BYTES


def install_ai(log=lambda _message: None) -> None:
    """下载 DeepFilterNet3 官方独立程序（约 26 MB，模型已内置，不需要 PyTorch）。"""
    import urllib.request

    target = deepfilter_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    log("正在下载 AI 人声增强组件 DeepFilterNet3（约 26 MB）……")
    last = [-1]

    def progress(blocks, block_size, total):
        if total > 0:
            percent = min(100, blocks * block_size * 100 // total)
            if percent // 10 != last[0] // 10:
                last[0] = percent
                log(f"  {percent}%")

    try:
        urllib.request.urlretrieve(DEEPFILTER_URL, partial, progress)
        if partial.stat().st_size < DEEPFILTER_MIN_BYTES:
            raise RuntimeError("下载的文件不完整。")
        os.replace(partial, target)
    except Exception as error:
        try:
            partial.unlink()
        except OSError:
            pass
        raise RuntimeError(f"AI 组件下载失败：{error}\n请检查网络后重试。") from error
    log("AI 组件安装完成。")


def enhance_audio(
    source: str,
    output_dir: str,
    output_format: str,
    mode: str,
    ffmpeg: str,
    normalize: bool,
    log=lambda _message: None,
    ai_strength: int = 100,
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    source_path = Path(source)
    stem = source_path.stem + ("_AI增强" if mode == "AI 人声增强" else "_清晰增强")
    with tempfile.TemporaryDirectory(prefix="audio_enhance_") as temp_dir:
        working = os.path.join(temp_dir, "input.wav")
        prepare = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", working],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        if prepare.returncode != 0:
            raise RuntimeError("无法读取输入音频：\n" + prepare.stderr.strip()[-1000:])

        processed = working
        if mode == "AI 人声增强":
            if not ai_available():
                raise RuntimeError("AI 组件还没有安装。请点击“安装 AI 组件”，或重新双击 start.bat。")
            ai_dir = os.path.join(temp_dir, "ai")
            os.makedirs(ai_dir)
            log("正在运行 DeepFilterNet3 本地人声模型……")
            result = subprocess.run(
                [str(deepfilter_path()), "--compensate-delay", "--atten-lim-db", str(ai_strength), "--output-dir", ai_dir, working],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
            candidates = glob.glob(os.path.join(ai_dir, "*.wav"))
            if result.returncode != 0 or not candidates:
                raise RuntimeError("AI 增强失败：\n" + (result.stderr or result.stdout).strip()[-1500:])
            processed = candidates[0]

        output = unique_path(output_dir, stem, output_format)
        filters = []
        if mode == "清晰增强（传统）":
            filters += ["highpass=f=70", "lowpass=f=15000", "afftdn=nf=-25"]
        if normalize:
            filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", processed]
        if filters:
            command += ["-af", ",".join(filters)]
        command += OUTPUT_CODECS[output_format] + [output]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError("增强音频导出失败：\n" + result.stderr.strip()[-1200:])
        return output
