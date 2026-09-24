# -*- coding: utf-8 -*-
"""音频提取器：从 yt-dlp 支持的网站保存音频。"""

from __future__ import annotations

import glob
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


APP_TITLE = "音频提取器"
APP_VERSION = "2.0.1"
FORMATS = ["mp3", "m4a", "flac", "wav", "opus", "原始格式（不转码）"]
QUALITY_OPTIONS = ["高品质", "标准", "节省空间"]
LOGIN_NONE = "不登录"
LOGIN_FILE = "cookies.txt 文件"
LOGIN_OPTIONS = [LOGIN_NONE, "Firefox", "Edge", "Chrome", LOGIN_FILE]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TRACKING_QUERY_KEYS = {
    "vd_source",
    "spm_id_from",
    "share_source",
    "share_medium",
    "share_plat",
    "share_session_id",
    "share_tag",
    "unique_k",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}

CODEC_ARGS = {
    "mp3": {
        "高品质": ["-c:a", "libmp3lame", "-q:a", "0"],
        "标准": ["-c:a", "libmp3lame", "-b:a", "192k"],
        "节省空间": ["-c:a", "libmp3lame", "-b:a", "128k"],
    },
    "m4a": {
        "高品质": ["-c:a", "aac", "-b:a", "256k"],
        "标准": ["-c:a", "aac", "-b:a", "192k"],
        "节省空间": ["-c:a", "aac", "-b:a", "128k"],
    },
    "opus": {
        "高品质": ["-c:a", "libopus", "-b:a", "192k"],
        "标准": ["-c:a", "libopus", "-b:a", "128k"],
        "节省空间": ["-c:a", "libopus", "-b:a", "96k"],
    },
    "flac": {quality: ["-c:a", "flac"] for quality in QUALITY_OPTIONS},
    "wav": {quality: ["-c:a", "pcm_s16le"] for quality in QUALITY_OPTIONS},
}


class CancelledError(RuntimeError):
    """用户主动取消任务。"""


def settings_path() -> Path:
    base = Path(os.getenv("APPDATA") or Path.home()) / "AudioGrabber"
    return base / "settings.json"


def load_settings() -> dict:
    defaults = {
        "outdir": str(Path.home() / "Downloads" / "音频提取器"),
        "format": "mp3",
        "quality": "高品质",
        "login": LOGIN_NONE,
    }
    try:
        saved = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            defaults.update({key: saved[key] for key in defaults if key in saved})
    except (OSError, ValueError, TypeError):
        pass
    return defaults


def save_settings(values: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def parse_time(text: str) -> float | None:
    """把 1:23:45、2:30、90、90.5 转成秒；空文本返回 None。"""
    text = text.strip().replace("：", ":")
    if not text:
        return None
    parts = [part.strip() for part in text.split(":")]
    if len(parts) > 3 or not all(re.fullmatch(r"\d+(?:\.\d+)?", part) for part in parts):
        raise ValueError(f"时间格式不正确：{text}")
    if len(parts) > 1 and any(float(part) >= 60 for part in parts[1:]):
        raise ValueError("冒号后的分钟和秒必须小于 60。")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def validate_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入完整的视频网址（以 http:// 或 https:// 开头）。")
    clean_query = urlencode(
        [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
         if key.lower() not in TRACKING_QUERY_KEYS],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, clean_query, parsed.fragment))


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s" if hours else f"{minutes}m{secs:02d}s"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")
    return cleaned[:150] or "audio"


def unique_path(folder: str, stem: str, extension: str) -> str:
    candidate = os.path.join(folder, f"{stem}.{extension}")
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem} ({counter}).{extension}")
        counter += 1
    return candidate


def find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def friendly_error(message: str, login: str) -> str:
    low = message.lower()
    if "cancelled by user" in low or "任务已取消" in message:
        return "任务已取消。"
    if "cookie" in low and login in ("Chrome", "Edge"):
        return (
            message
            + "\n\n建议：先完全关闭浏览器再试。若仍失败，请改用 Firefox，"
            "或在浏览器中导出 cookies.txt 后选择该文件。"
        )
    if "cookie" in low:
        return message + "\n\n建议：确认 cookies.txt 有效，或关闭对应浏览器后重试。"
    if "unsupported url" in low:
        return message + "\n\n这个链接暂不受支持，请检查链接是否完整，或运行 update.bat 更新。"
    if "sign in" in low or "login" in low:
        return message + "\n\n该内容可能需要登录，请在“登录信息”里选择浏览器或 cookies.txt。"
    if "ffmpeg" in low:
        return message + "\n\n请重新运行 start.bat；仍失败时可运行 update.bat 修复依赖。"
    return message


def run_job(opts: dict, log, progress, cancel_event: threading.Event) -> str:
    import yt_dlp

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg。")

    os.makedirs(opts["outdir"], exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix="audiograb_")
    try:
        class Logger:
            def debug(self, message):
                if not message.startswith("[debug]"):
                    log(message)

            info = debug

            def warning(self, message):
                log("⚠ " + message)

            def error(self, message):
                log("✖ " + message)

        def hook(data):
            if cancel_event.is_set():
                raise CancelledError("任务已取消。")
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if total:
                    progress(min(data.get("downloaded_bytes", 0) / total * 100, 100))

        ydl_options = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(temp_dir, "source.%(ext)s"),
            "noplaylist": True,
            "noprogress": True,
            "logger": Logger(),
            "progress_hooks": [hook],
            "ffmpeg_location": ffmpeg,
            "retries": 5,
            "fragment_retries": 5,
        }
        if opts["login"] == LOGIN_FILE:
            ydl_options["cookiefile"] = opts["cookie_file"]
        elif opts["login"] != LOGIN_NONE:
            ydl_options["cookiesfrombrowser"] = (opts["login"].lower(),)

        log("正在解析链接……")
        with yt_dlp.YoutubeDL(ydl_options) as downloader:
            info = downloader.extract_info(opts["url"], download=True)
        if cancel_event.is_set():
            raise CancelledError("任务已取消。")
        if info.get("entries"):
            info = next((entry for entry in info["entries"] if entry), info)

        source_files = [
            filename
            for filename in glob.glob(os.path.join(temp_dir, "source.*"))
            if not filename.endswith((".part", ".ytdl"))
        ]
        if not source_files:
            raise RuntimeError("下载失败：没有得到音频文件。")
        source = max(source_files, key=os.path.getsize)
        source_ext = os.path.splitext(source)[1].lstrip(".").lower()

        output_format = opts["format"]
        if output_format in CODEC_ARGS:
            extension = output_format
            codec_args = CODEC_ARGS[output_format][opts["quality"]]
        else:
            extension = {"webm": "opus", "mp4": "m4a"}.get(source_ext, source_ext)
            codec_args = ["-c:a", "copy"]

        title = info.get("title") or "audio"
        artist = info.get("artist") or info.get("uploader") or ""
        start, end = opts["start"], opts["end"]
        stem = safe_filename(title)
        if start is not None or end is not None:
            stem += f" [{clock(start or 0)}-{clock(end) if end is not None else '结尾'}]"
        output = unique_path(opts["outdir"], stem, extension)

        progress(None)
        log("正在保存音频" + ("并截取片段" if (start is not None or end is not None) else "") + "……")
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if start is not None:
            command += ["-ss", f"{start:.3f}"]
        command += ["-i", source]
        if end is not None:
            command += ["-t", f"{end - (start or 0):.3f}"]
        command += ["-vn", "-map", "0:a:0", "-map_metadata", "-1", "-metadata", f"title={title}"]
        if artist:
            command += ["-metadata", f"artist={artist}"]
        command += codec_args + [output]

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        while process.poll() is None:
            if cancel_event.wait(0.15):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                try:
                    os.remove(output)
                except OSError:
                    pass
                raise CancelledError("任务已取消。")
        _stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError("ffmpeg 转换失败：\n" + stderr.strip()[-1200:])
        if not os.path.isfile(output) or os.path.getsize(output) == 0:
            raise RuntimeError("音频处理结束，但输出文件为空。")
        progress(100)
        return output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    saved = load_settings()
    root = tk.Tk()
    root.title(f"{APP_TITLE} {APP_VERSION}")
    root.minsize(700, 590)
    root.geometry("780x650")
    font = ("Microsoft YaHei UI", 10)
    root.option_add("*Font", font)
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure(".", font=font)
    style.configure("Go.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=8)

    url_var = tk.StringVar()
    dir_var = tk.StringVar(value=saved["outdir"])
    format_var = tk.StringVar(value=saved["format"] if saved["format"] in FORMATS else "mp3")
    quality_var = tk.StringVar(value=saved["quality"] if saved["quality"] in QUALITY_OPTIONS else "高品质")
    clip_var = tk.BooleanVar(value=False)
    start_var = tk.StringVar()
    end_var = tk.StringVar()
    login_var = tk.StringVar(value=saved["login"] if saved["login"] in LOGIN_OPTIONS else LOGIN_NONE)
    cookie_var = tk.StringVar()
    status_var = tk.StringVar(value="准备就绪")

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="视频链接").grid(row=0, column=0, sticky="w", pady=5)
    url_entry = ttk.Entry(frame, textvariable=url_var)
    url_entry.grid(row=0, column=1, sticky="ew", padx=8)

    def paste() -> None:
        try:
            url_var.set(root.clipboard_get().strip())
        except tk.TclError:
            messagebox.showinfo(APP_TITLE, "剪贴板里没有可粘贴的文字。")

    ttk.Button(frame, text="粘贴", command=paste).grid(row=0, column=2, sticky="ew")

    ttk.Label(frame, text="保存到").grid(row=1, column=0, sticky="w", pady=5)
    ttk.Entry(frame, textvariable=dir_var).grid(row=1, column=1, sticky="ew", padx=8)

    def pick_dir() -> None:
        chosen = filedialog.askdirectory(initialdir=dir_var.get() or str(Path.home()))
        if chosen:
            dir_var.set(chosen)

    ttk.Button(frame, text="浏览…", command=pick_dir).grid(row=1, column=2, sticky="ew")

    ttk.Label(frame, text="输出格式").grid(row=2, column=0, sticky="w", pady=5)
    option_row = ttk.Frame(frame)
    option_row.grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
    format_box = ttk.Combobox(option_row, textvariable=format_var, values=FORMATS, state="readonly", width=20)
    format_box.pack(side="left")
    ttk.Label(option_row, text="音质").pack(side="left", padx=(18, 6))
    quality_box = ttk.Combobox(
        option_row, textvariable=quality_var, values=QUALITY_OPTIONS, state="readonly", width=10
    )
    quality_box.pack(side="left")

    def sync_quality(*_args) -> None:
        enabled = format_var.get() in {"mp3", "m4a", "opus"}
        quality_box.configure(state="readonly" if enabled else "disabled")

    format_box.bind("<<ComboboxSelected>>", sync_quality)
    sync_quality()

    clip_row = ttk.Frame(frame)
    clip_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Checkbutton(clip_row, text="只保存一段", variable=clip_var, command=lambda: toggle_clip()).pack(side="left")
    ttk.Label(clip_row, text="  从").pack(side="left")
    start_entry = ttk.Entry(clip_row, textvariable=start_var, width=10)
    start_entry.pack(side="left", padx=4)
    ttk.Label(clip_row, text="到").pack(side="left")
    end_entry = ttk.Entry(clip_row, textvariable=end_var, width=10)
    end_entry.pack(side="left", padx=4)
    ttk.Label(clip_row, text="例：1:30；结束留空表示到结尾", foreground="#666").pack(side="left", padx=8)

    def toggle_clip() -> None:
        entry_state = "normal" if clip_var.get() else "disabled"
        start_entry.configure(state=entry_state)
        end_entry.configure(state=entry_state)

    toggle_clip()

    ttk.Label(frame, text="登录信息").grid(row=4, column=0, sticky="w", pady=5)
    login_row = ttk.Frame(frame)
    login_row.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(8, 0))
    login_row.columnconfigure(1, weight=1)
    login_box = ttk.Combobox(login_row, textvariable=login_var, values=LOGIN_OPTIONS, state="readonly", width=16)
    login_box.grid(row=0, column=0, sticky="w")
    cookie_entry = ttk.Entry(login_row, textvariable=cookie_var)
    cookie_button = ttk.Button(
        login_row,
        text="选择文件…",
        command=lambda: cookie_var.set(
            filedialog.askopenfilename(filetypes=[("cookies.txt", "*.txt"), ("所有文件", "*.*")])
            or cookie_var.get()
        ),
    )
    login_hint = ttk.Label(login_row, foreground="#666")

    def on_login(*_args) -> None:
        for widget in (cookie_entry, cookie_button, login_hint):
            widget.grid_remove()
        selected = login_var.get()
        if selected == LOGIN_FILE:
            cookie_entry.grid(row=0, column=1, sticky="ew", padx=6)
            cookie_button.grid(row=0, column=2)
        elif selected == LOGIN_NONE:
            login_hint.configure(text="公开内容通常不需要登录")
            login_hint.grid(row=0, column=1, sticky="w", padx=8)
        else:
            login_hint.configure(text=f"读取 {selected} 登录状态；失败时请先完全关闭浏览器")
            login_hint.grid(row=0, column=1, sticky="w", padx=8)

    login_box.bind("<<ComboboxSelected>>", on_login)
    on_login()

    button_row = ttk.Frame(frame)
    button_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(16, 8))
    button_row.columnconfigure(0, weight=1)
    go_button = ttk.Button(button_row, text="开始提取", style="Go.TButton")
    go_button.grid(row=0, column=0, sticky="ew")
    cancel_button = ttk.Button(button_row, text="取消", state="disabled")
    cancel_button.grid(row=0, column=1, padx=(8, 0))

    progress_bar = ttk.Progressbar(frame, maximum=100)
    progress_bar.grid(row=6, column=0, columnspan=3, sticky="ew")
    ttk.Label(frame, textvariable=status_var, foreground="#555").grid(
        row=7, column=0, columnspan=3, sticky="w", pady=(5, 0)
    )

    log_box = tk.Text(
        frame,
        height=11,
        wrap="word",
        state="disabled",
        font=("Consolas", 9),
        relief="flat",
        background="#f5f5f5",
        padx=8,
        pady=8,
    )
    log_box.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
    frame.rowconfigure(8, weight=1)

    footer = ttk.Frame(frame)
    footer.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 0))
    open_button = ttk.Button(footer, text="打开文件位置", state="disabled")
    open_button.pack(side="right")
    copy_button = ttk.Button(
        footer,
        text="复制日志",
        command=lambda: (root.clipboard_clear(), root.clipboard_append(log_box.get("1.0", "end").strip())),
    )
    copy_button.pack(side="right", padx=(0, 8))

    events: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    state = {"busy": False, "last": None}

    def write_log(text: str) -> None:
        log_box.configure(state="normal")
        log_box.insert("end", text.rstrip() + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")

    def set_idle() -> None:
        state["busy"] = False
        go_button.configure(state="normal", text="开始提取")
        cancel_button.configure(state="disabled")

    def poll() -> None:
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "log":
                    write_log(value)
                elif kind == "progress":
                    if value is None:
                        progress_bar.configure(mode="indeterminate")
                        progress_bar.start(12)
                        status_var.set("正在处理音频……")
                    else:
                        progress_bar.stop()
                        progress_bar.configure(mode="determinate", value=value)
                        status_var.set(f"正在下载…… {value:.0f}%")
                elif kind == "done":
                    set_idle()
                    state["last"] = value
                    progress_bar.stop()
                    progress_bar.configure(mode="determinate", value=100)
                    status_var.set("完成")
                    write_log(f"✔ 已保存：{value}")
                    open_button.configure(state="normal")
                    root.bell()
                elif kind == "cancelled":
                    set_idle()
                    progress_bar.stop()
                    progress_bar.configure(mode="determinate", value=0)
                    status_var.set("已取消")
                    write_log("任务已取消。")
                elif kind == "error":
                    set_idle()
                    progress_bar.stop()
                    progress_bar.configure(mode="determinate", value=0)
                    status_var.set("失败")
                    write_log("✖ " + value)
                    messagebox.showerror(APP_TITLE, value)
        except queue.Empty:
            pass
        root.after(100, poll)

    def start() -> None:
        if state["busy"]:
            return
        try:
            url = validate_url(url_var.get())
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return
        output_dir = dir_var.get().strip()
        if not output_dir:
            messagebox.showwarning(APP_TITLE, "请选择保存位置。")
            return

        start_at = end_at = None
        if clip_var.get():
            try:
                start_at, end_at = parse_time(start_var.get()), parse_time(end_var.get())
            except ValueError as error:
                messagebox.showwarning(APP_TITLE, str(error))
                return
            if start_at is None and end_at is None:
                messagebox.showwarning(APP_TITLE, "请填写开始时间或结束时间。")
                return
            if start_at is not None and end_at is not None and end_at <= start_at:
                messagebox.showwarning(APP_TITLE, "结束时间必须晚于开始时间。")
                return
        if login_var.get() == LOGIN_FILE and not os.path.isfile(cookie_var.get()):
            messagebox.showwarning(APP_TITLE, "请选择有效的 cookies.txt 文件。")
            return

        options = {
            "url": url,
            "outdir": output_dir,
            "format": format_var.get(),
            "quality": quality_var.get(),
            "start": start_at,
            "end": end_at,
            "login": login_var.get(),
            "cookie_file": cookie_var.get(),
        }
        save_settings(
            {
                "outdir": output_dir,
                "format": format_var.get(),
                "quality": quality_var.get(),
                "login": login_var.get(),
            }
        )
        cancel_event.clear()
        state["busy"] = True
        state["last"] = None
        go_button.configure(state="disabled", text="处理中……")
        cancel_button.configure(state="normal")
        open_button.configure(state="disabled")
        progress_bar.configure(mode="determinate", value=0)
        status_var.set("正在准备……")
        write_log("—" * 56)

        def worker() -> None:
            try:
                output = run_job(
                    options,
                    lambda message: events.put(("log", message)),
                    lambda value: events.put(("progress", value)),
                    cancel_event,
                )
                events.put(("done", output))
            except CancelledError:
                events.put(("cancelled", None))
            except Exception as error:
                if cancel_event.is_set():
                    events.put(("cancelled", None))
                else:
                    events.put(("error", friendly_error(str(error), options["login"])))

        threading.Thread(target=worker, daemon=True).start()

    def cancel() -> None:
        if state["busy"]:
            cancel_event.set()
            cancel_button.configure(state="disabled")
            status_var.set("正在取消……")

    def open_output() -> None:
        path = state["last"]
        if path and os.path.exists(path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])

    def close_window() -> None:
        if state["busy"] and not messagebox.askyesno(APP_TITLE, "任务仍在进行。确定要取消并退出吗？"):
            return
        cancel_event.set()
        root.destroy()

    go_button.configure(command=start)
    cancel_button.configure(command=cancel)
    open_button.configure(command=open_output)
    url_entry.bind("<Return>", lambda _event: start())
    root.protocol("WM_DELETE_WINDOW", close_window)
    url_entry.focus_set()
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
