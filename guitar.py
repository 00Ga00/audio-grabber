"""吉他弹唱 / Solo 扒谱（主程序这边）：准备音频 → 交给 AI 进程 → 把结果存到输出文件夹。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import concert
import transcribe

HERE = Path(__file__).resolve().parent
WORKER = HERE / "guitar_worker.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SPEEDS = {"100%": 1.0, "85%": 0.85, "75%": 0.75, "60%": 0.6, "50%": 0.5}


def _site_packages() -> list[Path]:
    env = concert.AI_ENV
    return [env / "Lib" / "site-packages"] + list(env.glob("lib/python*/site-packages"))


def has_guitarpro() -> bool:
    return any((p / "guitarpro").is_dir() for p in _site_packages())


def _ensure_guitarpro(log) -> None:
    """写 Guitar Pro 文件用的小组件（纯 Python，约 1 MB）；以前装的扒谱组件里没有，缺了就补装。"""
    if has_guitarpro():
        return
    log("正在补装 Guitar Pro 文件组件（约 1 MB）……")
    if concert._run_stream([str(concert.ai_python()), "-m", "pip", "install", "--disable-pip-version-check", "pyguitarpro"],
                           log) != 0:
        raise RuntimeError("Guitar Pro 文件组件安装失败，请检查网络后重试。")


def run(source: str, ffmpeg: str, output_dir: str, do_chords: bool, do_solo: bool, mono: bool, size_label: str,
        clean: bool, log=lambda _m: None, progress=lambda _f, _s="": None,
        start: float | None = None, length: float | None = None) -> dict:
    if not concert.ai_available():
        raise RuntimeError("需要先在“设置”里安装演唱会降噪组件（吉他分离要用）。")
    if do_solo:
        if not transcribe.available():
            raise RuntimeError("扒 Solo 需要扒谱组件，请先到左下角“设置”里安装。")
        if not transcribe.get_token():
            raise RuntimeError("还没有设置 Hugging Face 授权（扒谱模型需要）。请先在“设置”里填写。")
    concert._RUNNING["cancelled"] = False
    size, beam, parallel = transcribe.SIZES.get(size_label, ("large", 1, 1))
    if do_solo:
        transcribe.ensure_model(size)
        _ensure_guitarpro(log)
    folder = transcribe._session("guitar")
    audio = transcribe.prepare_audio(source, ffmpeg, clean, folder, log, lambda f, s="": progress(0.1 * f, s), start, length)
    title = Path(source).stem

    def on_event(event):
        kind = event.get("type")
        if kind == "progress":
            progress(0.1 + 0.85 * float(event.get("fraction", 0)), event.get("stage", ""))
        elif kind == "notice":
            log(event.get("message", ""))
        elif kind == "device":
            log(("使用显卡：" if event.get("gpu") else "使用：") + event.get("name", ""))
        elif kind == "download":
            progress(0.1, f"下载模型 {event.get('name', '')}：{event.get('done', 0) / 1e6:.0f} / {max(event.get('total', 1), 1) / 1e6:.0f} MB")

    args = ["--guitar", audio, "--out", str(folder), "--models", str(concert.MODELS_DIR), "--title", title,
            "--size", size, "--beam", str(beam), "--parallel", str(parallel)]
    args += ["--chords"] if do_chords else []
    args += ["--solo"] if do_solo else []
    args += ["--mono"] if mono else []
    args += concert._overlap_args()
    concert.run_worker(args, on_event, script=WORKER)
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    result["offset"] = start or 0.0
    result["folder"] = str(folder)
    result["exported"] = export(result, ffmpeg, output_dir, title, start, log, progress)
    return result


def export(result: dict, ffmpeg: str, output_dir: str, title: str, start, log, progress) -> list[str]:
    """存到输出文件夹：和弦谱（HTML，可打印）、Guitar Pro、六线谱 PDF / MusicXML（装了 MuseScore 时）、练习音频。"""
    out = Path(output_dir) / f"{title}_吉他"
    if start:
        mm, ss = divmod(int(start), 60)
        out = Path(str(out) + f"_{mm}分{ss:02d}秒起")
    out.mkdir(parents=True, exist_ok=True)
    files = result["files"]
    saved = []
    if files.get("sheet_html"):
        target = out / f"{title}_吉他谱.html"          # 和弦按法图 + 和弦进行 + Solo 六线谱 + 指板图，浏览器打开、可打印
        shutil.copyfile(files["sheet_html"], target)
        saved.append(str(target))
        pdf = html_to_pdf(str(target), str(target.with_suffix(".pdf")))
        if pdf:
            saved.insert(0, pdf)
    if files.get("gp5"):
        target = out / f"{title}_六线谱.gp5"            # Guitar Pro / TuxGuitar / MuseScore 都能打开
        shutil.copyfile(files["gp5"], target)
        saved.append(str(target))
    if files.get("musicxml"):
        target = out / f"{title}_五线谱+六线谱.musicxml"
        shutil.copyfile(files["musicxml"], target)
        saved.append(str(target))
        musescore = transcribe.find_musescore()
        if musescore:
            progress(0.97, "用 MuseScore 导出 PDF")
            pdf = str(target.with_suffix(".pdf"))
            subprocess.run([musescore, "-o", pdf, str(target)], capture_output=True, creationflags=NO_WINDOW, timeout=600)
            if os.path.isfile(pdf):
                saved.append(pdf)
    for key, name in (("backing", "伴奏（去掉吉他）"), ("guitar", "只有吉他")):
        if files.get(key) and os.path.isfile(files[key]):
            target = out / f"{title}_{name}.flac"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", files[key], "-c:a", "flac", str(target)],
                           capture_output=True, creationflags=NO_WINDOW)
            if target.is_file():
                saved.append(str(target))
    log(f"已保存到：{out}")
    return saved


def find_edge() -> str | None:
    """Windows 自带的 Edge 浏览器（用来把吉他谱存成 PDF）。"""
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")):
        path = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.isfile(path):
            return path
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


def html_to_pdf(html: str, pdf: str) -> str | None:
    """用 Edge 的无界面模式把 HTML 打印成 PDF（整份吉他谱：按法图、和弦进行、六线谱、指板图）。"""
    browser = find_edge()
    if not browser:
        return None
    try:
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}",
                        Path(html).resolve().as_uri()], capture_output=True, creationflags=NO_WINDOW, timeout=90)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return pdf if os.path.isfile(pdf) and os.path.getsize(pdf) > 1000 else None


def practice_audio(result: dict, track: str, speed: float, ffmpeg: str) -> str:
    """练习用的音频：原曲 / 伴奏（去掉吉他）/ 只有吉他，按速度放慢（音高不变）。做过一次就留着。"""
    source = result["files"].get(track) or result["files"]["mix"]
    if abs(speed - 1.0) < 1e-3:
        return source
    target = str(Path(result["folder"]) / f"play_{track}_{int(speed * 100)}.wav")
    if not os.path.isfile(target):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-af", f"atempo={speed:.3f}",
                        "-c:a", "pcm_s16le", target], capture_output=True, creationflags=NO_WINDOW)
    return target if os.path.isfile(target) else source
