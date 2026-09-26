# -*- coding: utf-8 -*-
"""Audio Studio 3：视频音频提取、WASAPI 录音和音质增强。"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import concert
import transcribe
from audio_processing import ai_available, enhance_audio, install_ai, postprocess_recording
from recording import GlobalHotkeys, RecordingController, helper_available, helper_path, install_helper, list_visible_processes


def load_download_core():
    path = Path(__file__).with_name("audio_grabber.pyw")
    loader = importlib.machinery.SourceFileLoader("download_core", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


core = load_download_core()
APP_TITLE = "Audio Studio"
APP_VERSION = "3.1.0"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def main() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    import i18n
    import updater
    i18n.install()   # 界面语言（中文 / English / Deutsch），必须在创建窗口之前

    root = tk.Tk()
    root.title(f"{APP_TITLE} {APP_VERSION}")
    # 按屏幕缩放（125%/150%/200%）放大窗口，高分屏上不会挤成一团
    dpi = root.winfo_fpixels("1i")
    try:
        import ctypes

        dpi = max(dpi, float(ctypes.windll.user32.GetDpiForSystem()))
    except Exception:
        pass
    scale = max(1.0, dpi / 96.0)
    root.tk.call("tk", "scaling", dpi / 72.0)  # 字体（磅）按真实 DPI 显示
    width = min(int(900 * scale), int(root.winfo_screenwidth() * 0.9))
    height = min(int(760 * scale), int(root.winfo_screenheight() * 0.85))
    root.geometry(f"{width}x{height}")
    root.minsize(min(int(800 * scale), width), min(int(650 * scale), height))
    root.configure(background="#eef2f7")
    root.option_add("*Font", ("Microsoft YaHei UI", 10))
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    style.configure("TNotebook", background="#eef2f7", borderwidth=0)
    style.configure("TNotebook.Tab", padding=(18, 9), font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("Primary.TButton", padding=(14, 9), font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("Card.TFrame", background="#ffffff")
    style.configure("Card.TLabel", background="#ffffff")
    style.configure("Muted.Card.TLabel", background="#ffffff", foreground="#5d6878")
    style.configure("Title.TLabel", background="#eef2f7", foreground="#172033", font=("Microsoft YaHei UI", 19, "bold"))
    style.configure("Subtitle.TLabel", background="#eef2f7", foreground="#667085")

    events: queue.Queue = queue.Queue()
    cancel_download = threading.Event()
    state = {"download_busy": False, "recording": False, "paused": False, "record_stopped": False, "enhance_busy": False, "record_config": None}
    recorder = RecordingController(lambda event: events.put(("record", event)))

    shell = ttk.Frame(root, padding=(20, 16))
    shell.pack(fill="both", expand=True)
    header = ttk.Frame(shell, style="TFrame")
    header.pack(fill="x")
    ttk.Label(header, text="Audio Studio", style="Title.TLabel").pack(side="left", anchor="w")
    update_button = ttk.Button(header, text="检查更新")
    update_button.pack(side="right")
    language_var = tk.StringVar(value=i18n.LANGUAGES[i18n.LANG])
    language_box = ttk.Combobox(header, textvariable=language_var, values=list(i18n.LANGUAGES.values()), state="readonly", width=9)
    language_box.pack(side="right", padx=(0, 10))
    ttk.Label(header, text="语言 / Language", style="Subtitle.TLabel").pack(side="right", padx=(0, 6))
    update_state = {"info": None}

    def language_changed(_event=None):
        code = next(k for k, v in i18n.LANGUAGES.items() if v == language_var.get())
        if code == i18n.LANG:
            return
        i18n.save_language(code)
        if messagebox.askyesno(APP_TITLE, {"zh": "语言已切换，重新打开程序后生效。现在重启吗？",
                                           "en": "Language changed. Restart Audio Studio now to apply it?",
                                           "de": "Sprache geändert. Audio Studio jetzt neu starten?"}[code]):
            updater.restart()
            root.destroy()

    language_box.bind("<<ComboboxSelected>>", language_changed)

    def check_updates(silent=False):
        def worker():
            try:
                events.put(("update_status", (updater.check(), silent)))
            except Exception as error:
                if not silent:
                    events.put(("update_error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def update_clicked():
        info = update_state["info"]
        if not info or not info.get("update"):
            update_button.configure(text="检查更新")
            check_updates(silent=False)
            return
        if updater.is_git_checkout():
            messagebox.showinfo(APP_TITLE, "这个文件夹是 git 仓库：请在 GitHub Desktop 里点 “Fetch origin” → “Pull origin” 更新。")
            return
        if not messagebox.askyesno(APP_TITLE, "发现新版本。现在下载并更新吗？\n只替换程序文件，AI 组件、模型、设置和你的文件都不动；更新完会自动重启。"):
            return
        update_button.configure(text="正在更新……", state="disabled")

        def worker():
            try:
                updater.apply()
                events.put(("update_done", None))
            except Exception as error:
                events.put(("update_error", str(error)))
        threading.Thread(target=worker, daemon=True).start()

    update_button.configure(command=update_clicked)
    ttk.Label(shell, text="提取 · 录制 · 增强，全程在本机处理", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 4))
    notebook = ttk.Notebook(shell)
    notebook.pack(fill="both", expand=True)

    def card(parent, padding=18):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=padding)
        frame.pack(fill="both", expand=True, padx=6, pady=10)
        return frame

    def choose_directory(variable):
        selected = filedialog.askdirectory(initialdir=variable.get() or str(Path.home()))
        if selected:
            variable.set(selected)

    def open_path(path: str):
        if path and os.path.exists(path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])

    # ------------------------------------------------------------- 视频提取
    download_tab = ttk.Frame(notebook, padding=8)
    notebook.add(download_tab, text="视频提取")
    download_card = card(download_tab)
    download_card.columnconfigure(1, weight=1)
    download_card.rowconfigure(7, weight=1)
    saved = core.load_settings()
    d_url = tk.StringVar()
    d_dir = tk.StringVar(value=saved["outdir"])
    d_format = tk.StringVar(value=saved["format"])
    d_quality = tk.StringVar(value=saved["quality"])
    d_clip = tk.BooleanVar(value=False)
    d_start = tk.StringVar()
    d_end = tk.StringVar()
    d_login = tk.StringVar(value=saved["login"])
    d_cookie = tk.StringVar()
    d_status = tk.StringVar(value="准备就绪")
    d_last = {"path": None}

    ttk.Label(download_card, text="从视频保存音频", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
    ttk.Label(download_card, text="视频链接", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=5)
    ttk.Entry(download_card, textvariable=d_url).grid(row=1, column=1, sticky="ew", padx=8)

    def paste_url():
        try:
            d_url.set(root.clipboard_get().strip())
        except tk.TclError:
            messagebox.showinfo(APP_TITLE, "剪贴板里没有文字。")

    ttk.Button(download_card, text="粘贴", command=paste_url).grid(row=1, column=2)
    ttk.Label(download_card, text="保存到", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
    ttk.Entry(download_card, textvariable=d_dir).grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Button(download_card, text="浏览…", command=lambda: choose_directory(d_dir)).grid(row=2, column=2)

    option_row = ttk.Frame(download_card, style="Card.TFrame")
    option_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=7)
    ttk.Label(option_row, text="格式", style="Card.TLabel").pack(side="left")
    ttk.Combobox(option_row, textvariable=d_format, values=core.FORMATS, state="readonly", width=19).pack(side="left", padx=(8, 18))
    ttk.Label(option_row, text="音质", style="Card.TLabel").pack(side="left")
    ttk.Combobox(option_row, textvariable=d_quality, values=core.QUALITY_OPTIONS, state="readonly", width=11).pack(side="left", padx=8)
    ttk.Label(option_row, text="登录", style="Card.TLabel").pack(side="left", padx=(18, 0))
    ttk.Combobox(option_row, textvariable=d_login, values=core.LOGIN_OPTIONS, state="readonly", width=16).pack(side="left", padx=8)

    clip_row = ttk.Frame(download_card, style="Card.TFrame")
    clip_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=5)
    d_send = tk.BooleanVar(value=False)
    ttk.Checkbutton(clip_row, text="只保存一段", variable=d_clip).pack(side="left")
    ttk.Label(clip_row, text="从", style="Card.TLabel").pack(side="left", padx=(15, 4))
    ttk.Entry(clip_row, textvariable=d_start, width=10).pack(side="left")
    ttk.Label(clip_row, text="到", style="Card.TLabel").pack(side="left", padx=(10, 4))
    ttk.Entry(clip_row, textvariable=d_end, width=10).pack(side="left")
    ttk.Label(clip_row, text="例：1:30；结束留空=到结尾", style="Muted.Card.TLabel").pack(side="left", padx=10)
    ttk.Checkbutton(clip_row, text="下载完直接送去“演唱会降噪”", variable=d_send).pack(side="left", padx=(10, 0))

    d_buttons = ttk.Frame(download_card, style="Card.TFrame")
    d_buttons.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(12, 6))
    d_buttons.columnconfigure(0, weight=1)
    d_start_button = ttk.Button(d_buttons, text="开始提取", style="Primary.TButton")
    d_start_button.grid(row=0, column=0, sticky="ew")
    d_cancel_button = ttk.Button(d_buttons, text="取消", state="disabled")
    d_cancel_button.grid(row=0, column=1, padx=(8, 0))
    d_open_button = ttk.Button(d_buttons, text="打开文件", state="disabled", command=lambda: open_path(d_last["path"]))
    d_open_button.grid(row=0, column=2, padx=(8, 0))
    d_progress = ttk.Progressbar(download_card, maximum=100)
    d_progress.grid(row=6, column=0, columnspan=3, sticky="ew")
    d_log = tk.Text(download_card, height=10, state="disabled", wrap="word", relief="flat", background="#f7f9fc", font=("Consolas", 9))
    d_log.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(9, 0))
    ttk.Label(download_card, textvariable=d_status, style="Muted.Card.TLabel").grid(row=8, column=0, columnspan=3, sticky="w", pady=(7, 0))

    def d_write(message):
        d_log.configure(state="normal")
        d_log.insert("end", str(message).rstrip() + "\n")
        d_log.see("end")
        d_log.configure(state="disabled")

    def start_download():
        if state["download_busy"]:
            return
        try:
            url = core.validate_url(d_url.get())
            start_at = end_at = None
            if d_clip.get():
                start_at, end_at = core.parse_time(d_start.get()), core.parse_time(d_end.get())
                if start_at is None and end_at is None:
                    raise ValueError("请填写开始时间或结束时间。")
                if start_at is not None and end_at is not None and end_at <= start_at:
                    raise ValueError("结束时间必须晚于开始时间。")
            if not d_dir.get().strip():
                raise ValueError("请选择保存位置。")
            if d_login.get() == core.LOGIN_FILE and not os.path.isfile(d_cookie.get()):
                chosen = filedialog.askopenfilename(filetypes=[("cookies.txt", "*.txt")])
                if not chosen:
                    return
                d_cookie.set(chosen)
        except ValueError as error:
            messagebox.showwarning(APP_TITLE, str(error))
            return
        # 要送去降噪时用原始格式，避免多一次有损压缩
        options = dict(url=url, outdir=d_dir.get().strip(), format="原始格式（不转码）" if d_send.get() else d_format.get(), quality=d_quality.get(), start=start_at, end=end_at, login=d_login.get(), cookie_file=d_cookie.get())
        core.save_settings({"outdir": options["outdir"], "format": options["format"], "quality": options["quality"], "login": options["login"]})
        state["download_busy"] = True
        cancel_download.clear()
        d_start_button.configure(state="disabled")
        d_cancel_button.configure(state="normal")
        d_open_button.configure(state="disabled")
        d_write("—" * 60)

        def worker():
            try:
                output = core.run_job(options, lambda value: events.put(("download_log", value)), lambda value: events.put(("download_progress", value)), cancel_download)
                events.put(("download_done", output))
            except core.CancelledError:
                events.put(("download_cancelled", None))
            except Exception as error:
                events.put(("download_error", core.friendly_error(str(error), options["login"])))

        threading.Thread(target=worker, daemon=True).start()

    d_start_button.configure(command=start_download)
    d_cancel_button.configure(command=lambda: cancel_download.set())

    # ------------------------------------------------------------- 电脑录音
    record_tab = ttk.Frame(notebook, padding=8)
    notebook.add(record_tab, text="电脑录音")
    record_card = card(record_tab)
    record_card.columnconfigure(1, weight=1)
    record_card.rowconfigure(10, weight=1)
    r_scope = tk.StringVar(value="全部电脑声音")
    r_process = tk.StringVar()
    r_output_dir = tk.StringVar(value=saved["outdir"])
    r_format = tk.StringVar(value="flac")
    r_timer_enabled = tk.BooleanVar(value=False)
    r_timer = tk.StringVar(value="30:00")
    r_silence_enabled = tk.BooleanVar(value=False)
    r_silence = tk.StringVar(value="10")
    r_trim = tk.BooleanVar(value=True)
    r_split = tk.BooleanVar(value=False)
    r_normalize = tk.BooleanVar(value=True)
    r_hotkeys = tk.BooleanVar(value=True)
    r_status = tk.StringVar(value="准备就绪 · Ctrl+Alt+R 开始/停止 · Ctrl+Alt+P 暂停/继续")
    process_map = {}
    record_last = {"path": None}

    ttk.Label(record_card, text="录制电脑声音", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(record_card, text="WASAPI 数字环回，不经过麦克风", style="Muted.Card.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 12))
    ttk.Label(record_card, text="录制范围", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
    scope_box = ttk.Combobox(record_card, textvariable=r_scope, values=["全部电脑声音", "只录一个程序", "除了一个程序都录"], state="readonly", width=18)
    scope_box.grid(row=2, column=1, sticky="w", padx=8)
    helper_label = ttk.Label(record_card, text="录音组件：检查中", style="Muted.Card.TLabel")
    helper_label.grid(row=2, column=2, sticky="e")

    ttk.Label(record_card, text="目标程序", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=5)
    process_box = ttk.Combobox(record_card, textvariable=r_process, state="readonly")
    process_box.grid(row=3, column=1, sticky="ew", padx=8)

    def refresh_processes():
        process_map.clear()
        for item in list_visible_processes():
            label = f"{item['name']} — {item['title'][:55]}  (PID {item['pid']})"
            process_map[label] = item["pid"]
        process_box.configure(values=list(process_map))
        if process_map and r_process.get() not in process_map:
            r_process.set(next(iter(process_map)))

    ttk.Button(record_card, text="刷新", command=refresh_processes).grid(row=3, column=2)
    refresh_processes()

    ttk.Label(record_card, text="保存到", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=5)
    ttk.Entry(record_card, textvariable=r_output_dir).grid(row=4, column=1, sticky="ew", padx=8)
    ttk.Button(record_card, text="浏览…", command=lambda: choose_directory(r_output_dir)).grid(row=4, column=2)

    format_row = ttk.Frame(record_card, style="Card.TFrame")
    format_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Label(format_row, text="保存格式", style="Card.TLabel").pack(side="left")
    ttk.Combobox(format_row, textvariable=r_format, values=["wav", "flac", "mp3", "m4a", "opus"], state="readonly", width=9).pack(side="left", padx=8)
    ttk.Checkbutton(format_row, text="自动去掉首尾静音", variable=r_trim).pack(side="left", padx=(18, 0))
    ttk.Checkbutton(format_row, text="按静音自动分段", variable=r_split).pack(side="left", padx=(14, 0))
    ttk.Checkbutton(format_row, text="音量标准化", variable=r_normalize).pack(side="left", padx=(14, 0))

    control_row = ttk.Frame(record_card, style="Card.TFrame")
    control_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=5)
    ttk.Checkbutton(control_row, text="定时停止", variable=r_timer_enabled).pack(side="left")
    ttk.Entry(control_row, textvariable=r_timer, width=9).pack(side="left", padx=(5, 18))
    ttk.Checkbutton(control_row, text="静音自动停止", variable=r_silence_enabled).pack(side="left")
    ttk.Entry(control_row, textvariable=r_silence, width=6).pack(side="left", padx=5)
    ttk.Label(control_row, text="秒", style="Card.TLabel").pack(side="left")
    ttk.Checkbutton(control_row, text="启用全局快捷键", variable=r_hotkeys).pack(side="left", padx=(18, 0))

    r_buttons = ttk.Frame(record_card, style="Card.TFrame")
    r_buttons.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(13, 6))
    r_buttons.columnconfigure(0, weight=1)
    r_start_button = ttk.Button(r_buttons, text="● 开始录音", style="Primary.TButton")
    r_start_button.grid(row=0, column=0, sticky="ew")
    r_pause_button = ttk.Button(r_buttons, text="暂停", state="disabled")
    r_pause_button.grid(row=0, column=1, padx=(8, 0))
    r_stop_button = ttk.Button(r_buttons, text="停止", state="disabled")
    r_stop_button.grid(row=0, column=2, padx=(8, 0))
    r_open_button = ttk.Button(r_buttons, text="打开文件", state="disabled", command=lambda: open_path(record_last["path"]))
    r_open_button.grid(row=0, column=3, padx=(8, 0))
    r_level = ttk.Progressbar(record_card, maximum=60)
    r_level.grid(row=8, column=0, columnspan=3, sticky="ew")
    ttk.Label(record_card, textvariable=r_status, style="Muted.Card.TLabel").grid(row=9, column=0, columnspan=3, sticky="w", pady=5)
    r_log = tk.Text(record_card, height=8, state="disabled", wrap="word", relief="flat", background="#f7f9fc", font=("Consolas", 9))
    r_log.grid(row=10, column=0, columnspan=3, sticky="nsew")

    def r_write(message):
        r_log.configure(state="normal")
        r_log.insert("end", str(message).rstrip() + "\n")
        r_log.see("end")
        r_log.configure(state="disabled")

    def update_helper_label():
        helper_label.configure(text="录音组件：已安装" if helper_available() else "录音组件：未安装")

    update_helper_label()

    def ensure_helper():
        if helper_available():
            return
        helper_label.configure(text="录音组件：正在安装……")

        def worker():
            try:
                install_helper(lambda text: events.put(("record_log", text)))
                events.put(("helper_done", None))
            except Exception as error:
                events.put(("helper_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(record_card, text="重新准备录音组件", command=lambda: (helper_path().unlink(missing_ok=True), ensure_helper())).grid(row=11, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ensure_helper()

    def start_recording():
        if state["recording"]:
            return
        if not helper_available():
            try:
                install_helper(r_write)
            except Exception as error:
                messagebox.showerror(APP_TITLE, str(error))
                return
            update_helper_label()
        try:
            timer_seconds = core.parse_time(r_timer.get()) if r_timer_enabled.get() else 0
            silence_seconds = float(r_silence.get()) if r_silence_enabled.get() else 0
            if silence_seconds < 0:
                raise ValueError
            mode = {"只录一个程序": "process", "除了一个程序都录": "exclude"}.get(r_scope.get(), "system")
            pid = process_map.get(r_process.get()) if mode != "system" else None
            if mode != "system" and not pid:
                raise ValueError("请选择目标程序。")
            output_dir = r_output_dir.get().strip()
            if not output_dir:
                raise ValueError("请选择保存位置。")
        except (ValueError, TypeError):
            messagebox.showwarning(APP_TITLE, "定时、静音时间或目标程序设置不正确。")
            return
        os.makedirs(output_dir, exist_ok=True)
        raw = os.path.join(tempfile.gettempdir(), f"audio_studio_{int(time.time())}.wav")
        state["record_config"] = dict(raw=raw, output_dir=output_dir, format=r_format.get(), trim=r_trim.get(), split=r_split.get(), normalize=r_normalize.get())
        try:
            recorder.start(mode, pid, raw, timer_seconds or 0, silence_seconds, -45)
        except Exception as error:
            messagebox.showerror(APP_TITLE, str(error))
            return
        state["recording"] = True
        state["paused"] = False
        state["record_stopped"] = False
        r_start_button.configure(state="disabled")
        r_pause_button.configure(state="normal", text="暂停")
        r_stop_button.configure(state="normal")
        r_open_button.configure(state="disabled")
        r_status.set("正在启动 WASAPI 录音……")
        r_write("—" * 60)

    def toggle_pause():
        if not state["recording"]:
            return
        if state["paused"]:
            recorder.resume()
        else:
            recorder.pause()

    def stop_recording():
        if state["recording"]:
            r_status.set("正在停止……")
            recorder.stop()

    r_start_button.configure(command=start_recording)
    r_pause_button.configure(command=toggle_pause)
    r_stop_button.configure(command=stop_recording)

    # ------------------------------------------------------------- 音质增强
    enhance_tab = ttk.Frame(notebook, padding=8)
    notebook.add(enhance_tab, text="音质增强")
    enhance_card = card(enhance_tab)
    enhance_card.columnconfigure(1, weight=1)
    e_input = tk.StringVar()
    e_output_dir = tk.StringVar(value=saved["outdir"])
    e_mode = tk.StringVar(value="AI 人声增强")
    e_format = tk.StringVar(value="flac")
    e_normalize = tk.BooleanVar(value=True)
    e_strength = tk.StringVar(value="强（去掉全部噪声）")
    strength_db = {"轻（保留一点环境声）": 12, "中": 24, "强（去掉全部噪声）": 100}
    e_status = tk.StringVar(value="AI 模型在本机运行，适合语音降噪和清晰度提升")
    e_last = {"path": None}

    ttk.Label(enhance_card, text="音质增强", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(enhance_card, text="AI 不会恢复原本不存在的细节；人声模型不建议用于音乐母带", style="Muted.Card.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 15))
    ttk.Label(enhance_card, text="输入音频", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=6)
    ttk.Entry(enhance_card, textvariable=e_input).grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Button(enhance_card, text="选择…", command=lambda: e_input.set(filedialog.askopenfilename(filetypes=[("音频", "*.wav *.flac *.mp3 *.m4a *.opus *.ogg"), ("所有文件", "*.*")]) or e_input.get())).grid(row=2, column=2)
    ttk.Label(enhance_card, text="保存到", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=6)
    ttk.Entry(enhance_card, textvariable=e_output_dir).grid(row=3, column=1, sticky="ew", padx=8)
    ttk.Button(enhance_card, text="浏览…", command=lambda: choose_directory(e_output_dir)).grid(row=3, column=2)
    e_options = ttk.Frame(enhance_card, style="Card.TFrame")
    e_options.grid(row=4, column=0, columnspan=3, sticky="w", pady=8)
    ttk.Label(e_options, text="模式", style="Card.TLabel").pack(side="left")
    ttk.Combobox(e_options, textvariable=e_mode, values=["AI 人声增强", "清晰增强（传统）"], state="readonly", width=19).pack(side="left", padx=8)
    ttk.Label(e_options, text="格式", style="Card.TLabel").pack(side="left", padx=(18, 0))
    ttk.Combobox(e_options, textvariable=e_format, values=["wav", "flac", "mp3", "m4a", "opus"], state="readonly", width=8).pack(side="left", padx=8)
    ttk.Checkbutton(e_options, text="响度标准化", variable=e_normalize).pack(side="left", padx=(18, 0))
    e_options2 = ttk.Frame(enhance_card, style="Card.TFrame")
    e_options2.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 6))
    ttk.Label(e_options2, text="AI 降噪强度", style="Card.TLabel").pack(side="left")
    ttk.Combobox(e_options2, textvariable=e_strength, values=list(strength_db), state="readonly", width=19).pack(side="left", padx=8)
    ai_label = ttk.Label(enhance_card, text="", style="Muted.Card.TLabel")
    ai_label.grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def update_ai_label():
        installed = ai_available()
        ai_label.configure(text="AI 组件：已安装（DeepFilterNet3）" if installed else "AI 组件：未安装（传统增强可以直接使用）")
        e_install_button.configure(text="重新下载 AI 组件" if installed else "安装 AI 组件")

    e_buttons = ttk.Frame(enhance_card, style="Card.TFrame")
    e_buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(15, 8))
    e_buttons.columnconfigure(0, weight=1)
    e_start_button = ttk.Button(e_buttons, text="开始增强", style="Primary.TButton")
    e_start_button.grid(row=0, column=0, sticky="ew")
    e_install_button = ttk.Button(e_buttons, text="安装 AI 组件")
    e_install_button.grid(row=0, column=1, padx=(8, 0))
    e_open_button = ttk.Button(e_buttons, text="打开文件", state="disabled", command=lambda: open_path(e_last["path"]))
    e_open_button.grid(row=0, column=2, padx=(8, 0))
    update_ai_label()
    ttk.Label(enhance_card, textvariable=e_status, style="Muted.Card.TLabel").grid(row=7, column=0, columnspan=3, sticky="w")
    e_log = tk.Text(enhance_card, height=12, state="disabled", wrap="word", relief="flat", background="#f7f9fc", font=("Consolas", 9))
    e_log.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(9, 0))
    enhance_card.rowconfigure(8, weight=1)

    def e_write(message):
        e_log.configure(state="normal")
        e_log.insert("end", str(message).rstrip() + "\n")
        e_log.see("end")
        e_log.configure(state="disabled")

    def install_ai_clicked():
        e_install_button.configure(state="disabled")
        e_status.set("正在安装 AI 组件……")

        def worker():
            try:
                install_ai(lambda text: events.put(("enhance_log", text)))
                events.put(("ai_done", None))
            except Exception as error:
                events.put(("ai_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def start_enhance():
        if state["enhance_busy"]:
            return
        if not os.path.isfile(e_input.get()):
            messagebox.showwarning(APP_TITLE, "请选择有效的输入音频。")
            return
        ffmpeg = core.find_ffmpeg()
        if not ffmpeg:
            messagebox.showerror(APP_TITLE, "找不到 ffmpeg，请重新运行 start.bat。")
            return
        state["enhance_busy"] = True
        e_start_button.configure(state="disabled")
        e_open_button.configure(state="disabled")
        e_status.set("正在增强……")

        def worker():
            try:
                output = enhance_audio(e_input.get(), e_output_dir.get(), e_format.get(), e_mode.get(), ffmpeg, e_normalize.get(), lambda text: events.put(("enhance_log", text)), ai_strength=strength_db.get(e_strength.get(), 100))
                events.put(("enhance_done", output))
            except Exception as error:
                events.put(("enhance_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    e_install_button.configure(command=install_ai_clicked)
    e_start_button.configure(command=start_enhance)

    # ------------------------------------------------------------- 演唱会降噪
    concert_tab = ttk.Frame(notebook, padding=8)
    notebook.insert(1, concert_tab, text="演唱会降噪")
    c_card = card(concert_tab)
    c_card.columnconfigure(1, weight=1)
    c_card.rowconfigure(11, weight=1)
    c_input = tk.StringVar()
    c_output_dir = tk.StringVar(value=saved["outdir"])
    c_crowd = tk.BooleanVar(value=True)
    c_denoise = tk.BooleanVar(value=True)
    c_restore = tk.BooleanVar(value=True)
    c_split = tk.BooleanVar(value=True)
    c_dereverb = tk.BooleanVar(value=False)
    c_stems = tk.BooleanVar(value=False)
    c_strength = tk.StringVar(value="标准（推荐）")
    c_video = tk.BooleanVar(value=True)
    c_normalize = tk.BooleanVar(value=False)
    c_preview_at = tk.StringVar(value="1:00")
    c_status = tk.StringVar(value="选择一段演唱会视频，先试听 30 秒，满意再处理整段")
    c_last = {"path": None, "preview": None}

    ttk.Label(c_card, text="演唱会降噪", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(c_card, text="从现场视频里取出声音，用音乐专用 AI 去掉观众尖叫、鼓掌和底噪，尽量保持原声", style="Muted.Card.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 6))
    ttk.Label(c_card, text="视频/音频", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=5)
    ttk.Entry(c_card, textvariable=c_input).grid(row=2, column=1, sticky="ew", padx=8)

    def choose_concert_file():
        chosen = filedialog.askopenfilename(filetypes=concert.MEDIA_TYPES)
        if chosen:
            load_concert_file(chosen)

    def load_concert_file(chosen):
        c_input.set(chosen)
        c_line.update(data=None, duration=0.0)
        ffmpeg = core.find_ffmpeg()

        def timeline_worker():
            try:
                events.put(("concert_timeline", (chosen, concert.quick_timeline(ffmpeg, chosen))))
            except Exception:
                pass

        threading.Thread(target=timeline_worker, daemon=True).start()
        try:
            info = concert.probe(ffmpeg, chosen)
            minutes = info["duration"] / 60
            c_status.set(f"时长 {minutes:.1f} 分钟" + ("（含画面，可生成降噪后的视频）" if info["has_video"] else "（纯音频）"))
            c_last["duration"] = info["duration"]
            c_line["duration"] = info["duration"]
            # 试听点放在中间（演唱会中段通常最有代表性），并保证后面还有 30 秒
            point = int(max(0.0, min(info["duration"] / 2, info["duration"] - 30)))
            c_preview_at.set(f"{point // 3600}:{point % 3600 // 60:02d}:{point % 60:02d}" if point >= 3600 else f"{point // 60}:{point % 60:02d}")
        except Exception as error:
            c_status.set(str(error))

    ttk.Button(c_card, text="选择…", command=choose_concert_file).grid(row=2, column=2)
    ttk.Label(c_card, text="保存到", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=5)
    ttk.Entry(c_card, textvariable=c_output_dir).grid(row=3, column=1, sticky="ew", padx=8)
    ttk.Button(c_card, text="浏览…", command=lambda: choose_directory(c_output_dir)).grid(row=3, column=2)

    c_opts = ttk.Frame(c_card, style="Card.TFrame")
    c_opts.grid(row=4, column=0, columnspan=3, sticky="w", pady=6)
    ttk.Checkbutton(c_opts, text="去观众声", variable=c_crowd).pack(side="left")
    ttk.Checkbutton(c_opts, text="去底噪", variable=c_denoise).pack(side="left", padx=(14, 0))
    ttk.Checkbutton(c_opts, text="音质修复（补高音）", variable=c_restore).pack(side="left", padx=(14, 0))
    c_opts2 = ttk.Frame(c_card, style="Card.TFrame")
    c_opts2.grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 6))
    ttk.Checkbutton(c_opts2, text="同时生成视频（画面不重新压缩）", variable=c_video).pack(side="left")
    c_loudness = tk.StringVar(value="和原视频一样响（推荐）")
    ttk.Label(c_opts2, text="音量", style="Card.TLabel").pack(side="left", padx=(14, 4))
    ttk.Combobox(c_opts2, textvariable=c_loudness, values=list(concert.LOUDNESS_MODES), state="readonly", width=24).pack(side="left")
    ttk.Checkbutton(c_opts, text="分离人声和伴奏", variable=c_split).pack(side="left", padx=(14, 0))
    ttk.Checkbutton(c_opts, text="去混响（可调现场感）", variable=c_dereverb).pack(side="left", padx=(14, 0))
    ttk.Checkbutton(c_opts2, text="另存人声、伴奏分轨", variable=c_stems).pack(side="left", padx=(14, 0))

    # ---- 调音：滑块 + 频谱可视化
    presets = concert.load_presets()
    start_values = dict(concert.DEFAULT_SETTINGS, **{k: v for k, v in presets.get("_last", {}).items() if k in concert.DEFAULT_SETTINGS})
    prefs = presets.setdefault("_prefs", {})
    concert.set_temp_root(prefs.get("temp_dir") or None)
    c_fast = tk.BooleanVar(value=bool(prefs.get("fast", True)))
    loud_names = {v: k for k, v in concert.LOUDNESS_MODES.items()}
    SLIDER_PAGES = [
        ("整体", [  # 键, 名称, 最小, 最大, 显示方式
            ("strength", "降噪强度", 0.5, 1.0, "pct"),
            ("auto", "自动音色校正", 0.0, 1.0, "pct"),
            ("bass", "低频（轰）", -12.0, 6.0, "db"),
            ("mid", "中频（人声/吉他）", -6.0, 8.0, "db"),
            ("treble", "高频（清晰）", -8.0, 6.0, "db"),
            ("air", "补回的高音（音质修复）", 0.0, 1.0, "pct"),
        ]),
        ("人声与伴奏（间奏）", [
            ("vocal_level", "人声音量", -6.0, 6.0, "db"),
            ("inst_level", "伴奏音量", -12.0, 6.0, "db"),
            ("inst_bass", "伴奏低频（轰）", -12.0, 6.0, "db"),
            ("inst_harsh", "伴奏刺耳（2–5 kHz）", -10.0, 4.0, "db"),
            ("inst_treble", "伴奏高频", -10.0, 6.0, "db"),
            ("inst_soften", "伴奏动态柔化", 0.0, 1.0, "pct"),
        ]),
        ("空间 / 参考曲", [
            ("width", "立体声宽度", 0.0, 1.0, "pct"),
            ("ambience", "现场感（干 ↔ 空间）", -1.0, 1.0, "spct"),
        ]),
    ]
    SLIDERS = [item for _, items in SLIDER_PAGES for item in items]
    c_vars = {key: tk.DoubleVar(value=float(start_values[key])) for key, *_ in SLIDERS}
    c_tune = ttk.Frame(c_card, style="Card.TFrame")
    c_tune.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(2, 6))
    c_tune.columnconfigure(1, weight=1)
    c_sliders = ttk.Frame(c_tune, style="Card.TFrame")
    c_sliders.grid(row=0, column=0, sticky="nw")
    c_pages = ttk.Notebook(c_sliders)
    c_pages.grid(row=0, column=0, columnspan=3, sticky="w")
    c_value_labels = {}

    def fmt(key, value):
        kind = next(k for k_, _, _, _, k in SLIDERS if k_ == key)
        if kind == "spct":
            return f"{value * 100:+.0f}%"
        return f"{value * 100:.0f}%" if kind == "pct" else f"{value:+.1f} dB"

    c_headphone = tk.BooleanVar(value=bool(start_values.get("headphone", False)))
    c_reference = {"value": start_values.get("reference")}

    def current_settings():
        values = {key: round(var.get(), 3) for key, var in c_vars.items()}
        values["loudness"] = concert.LOUDNESS_MODES.get(c_loudness.get(), "match")
        values["headphone"] = bool(c_headphone.get())
        values["reference"] = c_reference["value"]
        return values

    for page_name, items in SLIDER_PAGES:
        page = ttk.Frame(c_pages, style="Card.TFrame", padding=(4, 4))
        c_pages.add(page, text=page_name)
        for row, (key, name, low, high, kind) in enumerate(items):
            ttk.Label(page, text=name, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=0)
            slider = ttk.Scale(page, from_=low, to=high, variable=c_vars[key], length=int((170 if i18n.LANG == "zh" else 130) * scale),
                               command=lambda _v, k=key: on_setting_change(k))
            slider.grid(row=row, column=1, padx=6)
            slider.bind("<Double-Button-1>", lambda _e, k=key: (c_vars[k].set(float(concert.DEFAULT_SETTINGS[k])), on_setting_change(k)))
            label = ttk.Label(page, text=fmt(key, c_vars[key].get()), style="Muted.Card.TLabel", width=9)
            label.grid(row=row, column=2, sticky="w")
            c_value_labels[key] = label
        if page_name.startswith("空间"):
            ttk.Checkbutton(page, text="耳机空间音频（戴耳机听像在场馆里）", variable=c_headphone,
                            command=lambda: on_setting_change(None)).grid(row=len(items), column=0, columnspan=3, sticky="w", pady=(4, 0))
            ref_row = ttk.Frame(page, style="Card.TFrame")
            ref_row.grid(row=len(items) + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
            ttk.Label(ref_row, text="参考曲：", style="Card.TLabel").pack(side="left")
            c_ref_label = ttk.Label(ref_row, text="", style="Muted.Card.TLabel", width=16)
            c_ref_label.pack(side="left")
            ttk.Button(ref_row, text="选择…", command=lambda: choose_reference()).pack(side="left", padx=(4, 0))
            ttk.Button(ref_row, text="清除", command=lambda: set_reference(None)).pack(side="left", padx=(4, 0))
            ttk.Label(page, text="参考曲选同一首歌的录音室版；现场感往左需勾“去混响”",
                      style="Muted.Card.TLabel").grid(row=len(items) + 2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        if page_name.startswith("人声"):
            ttk.Label(page, text="只影响伴奏，人声不变（需勾选“分离人声和伴奏”）", style="Muted.Card.TLabel", wraplength=int(300 * scale)).grid(
                row=len(items), column=0, columnspan=3, sticky="w", pady=(2, 0))

    c_preset_row = ttk.Frame(c_sliders, style="Card.TFrame")
    c_preset_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 0))
    c_preset = tk.StringVar(value="")
    c_preset_box = ttk.Combobox(c_preset_row, textvariable=c_preset, state="readonly", width=14)
    c_preset_box.pack(side="left")

    def refresh_presets():
        names = [name for name in presets if not name.startswith("_")]
        c_preset_box.configure(values=["默认"] + names)

    def set_reference(value):
        c_reference["value"] = value
        c_ref_label.configure(text=(value or {}).get("name", "未选择")[:16])
        on_setting_change(None)

    def choose_reference():
        if not concert.ai_available():
            messagebox.showinfo(APP_TITLE, "需要先安装演唱会降噪组件。")
            return
        chosen = filedialog.askopenfilename(filetypes=concert.MEDIA_TYPES, title="选择参考曲（同一首歌的录音室版）")
        if not chosen:
            return
        ffmpeg = core.find_ffmpeg()
        c_status.set("正在分析参考曲……")

        def worker():
            try:
                events.put(("concert_reference", concert.analyze_reference(ffmpeg, chosen)))
            except Exception as error:
                events.put(("concert_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def apply_values(values):
        for key, var in c_vars.items():
            if key in values:
                var.set(float(values[key]))
        c_headphone.set(bool(values.get("headphone", False)))
        if "reference" in values:
            set_reference(values.get("reference"))
        if values.get("loudness") in loud_names:
            c_loudness.set(loud_names[values["loudness"]])
        on_setting_change(None)

    def preset_chosen(_event=None):
        name = c_preset.get()
        apply_values(concert.DEFAULT_SETTINGS if name == "默认" else presets.get(name, {}))

    def save_preset():
        from tkinter import simpledialog
        name = simpledialog.askstring(APP_TITLE, "给这组设置起个名字（比如歌手名）：", parent=root)
        if not name or name.startswith("_") or name == "默认":
            return
        presets[name.strip()] = current_settings()
        concert.save_presets(presets)
        refresh_presets()
        c_preset.set(name.strip())

    def delete_preset():
        name = c_preset.get()
        if name in presets and not name.startswith("_") and messagebox.askyesno(APP_TITLE, f"删除预设“{name}”？"):
            presets.pop(name)
            concert.save_presets(presets)
            refresh_presets()
            c_preset.set("")

    c_preset_box.bind("<<ComboboxSelected>>", preset_chosen)
    ttk.Button(c_preset_row, text="存为预设", command=save_preset).pack(side="left", padx=(6, 0))
    ttk.Button(c_preset_row, text="删除", command=delete_preset).pack(side="left", padx=(4, 0))
    ttk.Button(c_preset_row, text="恢复默认", command=lambda: (c_preset.set("默认"), apply_values(concert.DEFAULT_SETTINGS))).pack(side="left", padx=(4, 0))
    refresh_presets()
    c_ref_label.configure(text=(c_reference["value"] or {}).get("name", "未选择")[:16])

    c_right = ttk.Frame(c_tune, style="Card.TFrame")
    c_right.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
    c_right.columnconfigure(0, weight=1)
    c_canvas = tk.Canvas(c_right, height=int(128 * scale), background="#ffffff", highlightthickness=1, highlightbackground="#dde3ea")
    c_canvas.grid(row=0, column=0, sticky="nsew")
    c_timeline = tk.Canvas(c_right, height=int(44 * scale), background="#ffffff", highlightthickness=1, highlightbackground="#dde3ea", cursor="hand2")
    c_timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    c_line = {"data": None, "duration": 0.0}

    def draw_timeline(_event=None):
        cv = c_timeline
        cv.delete("all")
        w, h = max(cv.winfo_width(), 200), max(cv.winfo_height(), 30)
        data, duration = c_line["data"], c_line["duration"] or 0.0
        if not data or not data.get("loud"):
            cv.create_text(w / 2, h / 2, text="时间轴：选好文件后显示每一秒的响度（灰）和刺耳度（红）；点一下就从那里试听",
                           fill="#8a94a3", font=("Microsoft YaHei UI", 8))
            return
        loud, harsh = data["loud"], data.get("harsh") or []
        vocal = data.get("vocal")
        count = len(loud)
        top_db = max(loud) if loud else 0
        bar = (w - 4) / max(count, 1)
        for i, value in enumerate(loud):
            level = max(0.0, min(1.0, (value - (top_db - 30)) / 30))
            x = 2 + i * bar
            cv.create_rectangle(x, h - 2 - level * (h - 6), x + max(bar, 1), h - 2, fill="#c5ccd6", width=0)
            if i < len(harsh) and value > -90:
                # 刺耳度 = 2–5 kHz 比整体多多少：越红越刺耳
                ratio = max(0.0, min(1.0, (harsh[i] - value + 18) / 12))
                if ratio > 0.05:
                    cv.create_rectangle(x, h - 2 - level * (h - 6) * ratio, x + max(bar, 1), h - 2, fill="#e05a47", width=0, stipple="gray50")
        if vocal and len(vocal) >= count // 2:
            pts = []
            vmax = max(vocal)
            for i, value in enumerate(vocal[:count]):
                pts += [2 + (i + 0.5) * bar, h - 2 - max(0.0, min(1.0, (value - (vmax - 30)) / 30)) * (h - 6)]
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#2eaa6a", width=1.5, smooth=True)
        try:
            start = core.parse_time(c_preview_at.get()) or 0.0
        except ValueError:
            start = 0.0
        if duration:
            x0, x1 = 2 + start / duration * (w - 4), 2 + min(duration, start + 30) / duration * (w - 4)
            cv.create_rectangle(x0, 1, x1, h - 1, outline="#2f6fdf", width=2)
        legend = "灰=响度 红=刺耳" + (" 绿=人声" if vocal else "") + " 蓝框=试听段 · 点击跳转"
        item = cv.create_text(w - 5, 7, text=legend, anchor="e", fill="#4a5563", font=("Microsoft YaHei UI", 7))
        box = cv.bbox(item)
        if box:
            cv.tag_lower(cv.create_rectangle(box[0] - 3, box[1] - 1, box[2] + 2, box[3] + 1, fill="#ffffff", outline=""), item)

    def timeline_click(event):
        duration = c_line["duration"]
        if not duration:
            return
        w = max(c_timeline.winfo_width(), 200)
        t = max(0.0, min(duration - 30, (event.x - 2) / (w - 4) * duration - 15)) if duration > 30 else 0.0
        t = int(max(0.0, t))
        c_preview_at.set(f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}")
        draw_timeline()

    c_timeline.bind("<Configure>", draw_timeline)
    c_timeline.bind("<Button-1>", timeline_click)
    c_preview_at.trace_add("write", lambda *_: draw_timeline())

    c_session = {"preview": None, "full": None, "dirty": True}

    def active_analysis():
        session = c_session.get("full") or c_session.get("preview")
        return session["analysis"] if session else None

    def draw_spectrum(_event=None):
        import math
        cv = c_canvas
        cv.delete("all")
        w, h = max(cv.winfo_width(), 200), max(cv.winfo_height(), 120)
        left, right, top, bottom = 34, 10, 8, 20
        fmin, fmax = 30.0, 20000.0

        def x_of(f):
            return left + (math.log10(f) - math.log10(fmin)) / (math.log10(fmax) - math.log10(fmin)) * (w - left - right)

        for f, text in ((100, "100"), (1000, "1k"), (10000, "10k")):
            cv.create_line(x_of(f), top, x_of(f), h - bottom, fill="#eef1f5")
            cv.create_text(x_of(f), h - bottom + 9, text=text, fill="#8a94a3", font=("Microsoft YaHei UI", 8))
        cv.create_text(x_of(fmax) - 2, h - bottom + 9, text="Hz", anchor="e", fill="#8a94a3", font=("Microsoft YaHei UI", 8))
        analysis = active_analysis()
        settings = current_settings()
        # 均衡曲线（橙色，中线 = 0 dB，上下各 ±15 dB）
        mid_y = top + (h - top - bottom) / 2

        def y_eq(g):
            return mid_y - g / 15 * (h - top - bottom) / 2

        cv.create_line(left, mid_y, w - right, mid_y, fill="#f3d7b5", dash=(3, 3))
        eq = concert.eq_curve(settings, analysis)
        cv.create_line(*[v for f, g in eq if fmin <= f <= fmax for v in (x_of(f), y_eq(g))], fill="#e8871e", width=2, smooth=True)
        if analysis and analysis.get("vocals"):
            eq_inst = concert.eq_curve(settings, analysis, "inst")
            if any(abs(a[1] - b[1]) > 0.05 for a, b in zip(eq, eq_inst)):
                cv.create_line(*[v for f, g in eq_inst if fmin <= f <= fmax for v in (x_of(f), y_eq(g))],
                               fill="#9b59b6", width=2, smooth=True, dash=(4, 3))
                cv.create_text(left + 4, top + 22, text="紫虚线 = 伴奏均衡", anchor="w", fill="#9b59b6", font=("Microsoft YaHei UI", 8))
        cv.create_text(left - 4, y_eq(12), text="+12", anchor="e", fill="#e8871e", font=("Microsoft YaHei UI", 7))
        cv.create_text(left - 4, y_eq(-12), text="-12", anchor="e", fill="#e8871e", font=("Microsoft YaHei UI", 7))
        if not analysis:
            cv.create_text((w + left) / 2, top + 34, text="橙线 = 你设置的均衡\n生成试听后，这里会显示处理前后的频谱对比",
                           fill="#8a94a3", font=("Microsoft YaHei UI", 9), justify="center")
            return
        centers = analysis["centers"]
        mids = [v for c, v in zip(centers, analysis["original"]) if 200 <= c <= 2000]
        ref = sum(mids) / len(mids)
        after = concert.predicted_levels(settings, analysis)

        def y_spec(v):  # 频谱：+18 dB（顶）~ -42 dB（底），相对原声中频
            return top + (18 - (v - ref)) / 60 * (h - top - bottom)

        def poly(levels, color, width_, dash=None):
            pts = [v for c, lv in zip(centers, levels) for v in (x_of(c), min(max(y_spec(lv), top), h - bottom))]
            cv.create_line(*pts, fill=color, width=width_, smooth=True, dash=dash)

        if analysis.get("key") or analysis.get("bpm"):
            info = " · ".join(x for x in (analysis.get("key"), f"约 {analysis['bpm']} BPM" if analysis.get("bpm") else "") if x)
            cv.create_text((w + left) / 2, h - bottom - 8, text=info, fill="#4a5563", font=("Microsoft YaHei UI", 8, "bold"))
        poly(analysis["original"], "#9aa4b2", 2)
        poly(after, "#2f6fdf", 2)
        if analysis.get("cutoff"):
            xc = x_of(analysis["cutoff"])
            cv.create_line(xc, top, xc, h - bottom, fill="#c9d3e0", dash=(2, 4))
            cv.create_text(xc - 3, h - bottom - 8, text="压缩截止", anchor="e", fill="#8a94a3", font=("Microsoft YaHei UI", 7))
        for i, (color, text) in enumerate((("#9aa4b2", "原声"), ("#2f6fdf", "调整后"), ("#e8871e", "均衡"))):
            cv.create_line(w - 150 + i * 48, top + 8, w - 136 + i * 48, top + 8, fill=color, width=3)
            cv.create_text(w - 133 + i * 48, top + 8, text=text, anchor="w", fill="#4a5563", font=("Microsoft YaHei UI", 8))

    c_canvas.bind("<Configure>", draw_spectrum)

    def on_setting_change(key):
        for k, label in c_value_labels.items():
            label.configure(text=fmt(k, c_vars[k].get()))
        c_session["dirty"] = True
        draw_spectrum()
        presets["_last"] = current_settings()

    def remember_settings():
        presets["_last"] = current_settings()
        concert.save_presets(presets)

    if start_values.get("loudness") in loud_names:
        c_loudness.set(loud_names[start_values["loudness"]])
    c_loudness.trace_add("write", lambda *_: on_setting_change(None))

    def forget_sessions(*_):
        c_session.update(preview=None, full=None, dirty=True)
        for button in (c_play_a, c_play_b, c_reexport_button):
            button.configure(state="disabled")
        draw_spectrum()

    for var in (c_crowd, c_denoise, c_restore, c_split, c_dereverb, c_input):
        var.trace_add("write", forget_sessions)

    c_prev = ttk.Frame(c_card, style="Card.TFrame")
    c_prev.grid(row=7, column=0, columnspan=3, sticky="w", pady=(2, 4))
    ttk.Label(c_prev, text="试听：从", style="Card.TLabel").pack(side="left")
    ttk.Entry(c_prev, textvariable=c_preview_at, width=7).pack(side="left", padx=4)
    ttk.Label(c_prev, text="开始的 30 秒", style="Card.TLabel").pack(side="left")
    c_preview_button = ttk.Button(c_prev, text="生成试听（AI）")
    c_preview_button.pack(side="left", padx=(10, 0))
    c_play_a = ttk.Button(c_prev, text="▶ 原声", state="disabled", command=lambda: concert.PLAYER.play("original"))
    c_play_a.pack(side="left", padx=(10, 0))
    c_play_b = ttk.Button(c_prev, text="▶ 调整后", state="disabled")
    c_play_b.pack(side="left", padx=(6, 0))
    c_hold = ttk.Button(c_prev, text="按住=听原声", state="disabled")
    c_hold.pack(side="left", padx=(6, 0))
    c_hold.bind("<ButtonPress-1>", lambda _e: concert.PLAYER.switch("original") if concert.PLAYER.files else None)
    c_hold.bind("<ButtonRelease-1>", lambda _e: concert.PLAYER.switch("processed") if concert.PLAYER.files else None)
    c_stop = ttk.Button(c_prev, text="■ 停止", command=lambda: concert.PLAYER.stop())
    c_stop.pack(side="left", padx=(6, 0))

    c_buttons = ttk.Frame(c_card, style="Card.TFrame")
    c_buttons.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(2, 4))
    c_buttons.columnconfigure(0, weight=1)
    c_start_button = ttk.Button(c_buttons, text="开始处理整段", style="Primary.TButton")
    c_start_button.grid(row=0, column=0, sticky="ew")
    c_cancel_button = ttk.Button(c_buttons, text="取消", state="disabled")
    c_cancel_button.grid(row=0, column=1, padx=(8, 0))
    c_batch_button = ttk.Button(c_buttons, text="批量处理…")
    c_batch_button.grid(row=0, column=2, padx=(8, 0))
    c_reexport_button = ttk.Button(c_buttons, text="按当前设置重新导出", state="disabled")
    c_reexport_button.grid(row=0, column=3, padx=(8, 0))
    c_open_button = ttk.Button(c_buttons, text="打开文件", state="disabled", command=lambda: open_path(c_last["path"]))
    c_open_button.grid(row=0, column=4, padx=(8, 0))
    c_progress = ttk.Progressbar(c_card, maximum=100)
    c_progress.grid(row=9, column=0, columnspan=3, sticky="ew")
    ttk.Label(c_card, textvariable=c_status, style="Muted.Card.TLabel").grid(row=10, column=0, columnspan=3, sticky="w", pady=2)
    c_log = tk.Text(c_card, height=3, state="disabled", wrap="word", relief="flat", background="#f7f9fc", font=("Consolas", 9))
    c_log.grid(row=11, column=0, columnspan=3, sticky="nsew")
    c_bottom = ttk.Frame(c_card, style="Card.TFrame")
    c_bottom.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(2, 0))
    c_bottom.columnconfigure(0, weight=1)
    c_ai_label = ttk.Label(c_bottom, text="", style="Muted.Card.TLabel")
    c_ai_label.grid(row=0, column=0, sticky="w")
    c_settings_button = ttk.Button(c_bottom, text="设置…")
    c_settings_button.grid(row=0, column=1, padx=(8, 0))
    c_install_button = ttk.Button(c_bottom, text="安装演唱会降噪组件")
    c_install_button.grid(row=0, column=2, padx=(8, 0))

    def c_write(message):
        c_log.configure(state="normal")
        c_log.insert("end", str(message).rstrip() + "\n")
        c_log.see("end")
        c_log.configure(state="disabled")

    def update_concert_label():
        if concert.ai_available():
            c_ai_label.configure(text="AI 组件：已安装")
            c_install_button.configure(text="重新安装组件")
        else:
            c_ai_label.configure(text="AI 组件：未安装，第一次使用请点右边的按钮")
            c_install_button.configure(text="安装演唱会降噪组件（约 5 GB）")

    update_concert_label()

    def set_concert_busy(busy):
        state["concert_busy"] = busy
        for button in (c_start_button, c_preview_button, c_install_button, c_batch_button, c_settings_button):
            button.configure(state="disabled" if busy else "normal")
        c_cancel_button.configure(state="normal" if busy else "disabled")
        if not busy and c_session.get("full"):
            c_reexport_button.configure(state="normal")
        elif busy:
            c_reexport_button.configure(state="disabled")

    def install_concert_clicked():
        if state.get("concert_busy"):
            return
        if not messagebox.askyesno(APP_TITLE, "将下载并安装演唱会降噪组件（约 5 GB，需要联网，可能要 10～40 分钟）。\n安装完成后可以离线使用。现在开始吗？"):
            return
        set_concert_busy(True)
        c_status.set("正在安装演唱会降噪组件……")
        c_progress.configure(mode="indeterminate"); c_progress.start(12)

        def worker():
            try:
                concert.install_ai(lambda text: events.put(("concert_log", text)))
                events.put(("concert_install_done", None))
            except Exception as error:
                events.put(("concert_install_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def selected_steps():
        return [name for name, var in (("crowd", c_crowd), ("denoise", c_denoise), ("restore", c_restore), ("split", c_split), ("dereverb", c_dereverb)) if var.get()]

    def concert_job(preview):
        if state.get("concert_busy"):
            return
        source = c_input.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning(APP_TITLE, "请先选择一个视频或音频文件。")
            return
        if not concert.ai_available():
            messagebox.showinfo(APP_TITLE, "第一次使用需要先安装演唱会降噪组件。")
            return
        steps = selected_steps()
        start_at = None
        if preview:
            try:
                start_at = core.parse_time(c_preview_at.get()) or 0.0
            except ValueError as error:
                messagebox.showwarning(APP_TITLE, str(error))
                return
            duration = c_last.get("duration") or 0.0
            if duration and start_at > max(0.0, duration - 30):
                start_at = max(0.0, duration - 30)
        ffmpeg = core.find_ffmpeg()
        if not ffmpeg:
            messagebox.showerror(APP_TITLE, "找不到 ffmpeg，请重新运行 start.bat。")
            return
        remember_settings()
        concert.play_wav(None)
        set_concert_busy(True)
        c_open_button.configure(state="disabled")
        c_progress.stop(); c_progress.configure(mode="determinate", value=0)
        c_status.set("正在生成 30 秒试听……" if preview else "正在处理整段……")
        c_write("—" * 60)
        settings = current_settings()
        output_dir = c_output_dir.get().strip() or os.path.dirname(source)
        make_video = c_video.get()
        make_stems = c_stems.get()
        fast = c_fast.get()
        concert.PLAYER.close()
        log = lambda text: events.put(("concert_log", text))
        progress = lambda frac, stage="": events.put(("concert_progress", (frac, stage)))

        def worker():
            try:
                session = concert.run_ai(source, ffmpeg, steps, start_at, log=log, progress=progress, fast=fast)
                if preview:
                    result = concert.render_preview(ffmpeg, session, settings)
                else:
                    progress(0.98, "导出")
                    result = concert.export(ffmpeg, session, settings, output_dir, make_video, log, stems=make_stems)
                result["session"] = session
                events.put(("concert_done", result))
            except concert.Cancelled:
                events.put(("concert_cancelled", None))
            except Exception as error:
                events.put(("concert_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def play_adjusted():
        """按当前滑块重新混合试听（不重跑 AI，一秒左右），然后播放。"""
        session = c_session.get("preview")
        if not session or state.get("concert_busy"):
            return
        if not c_session["dirty"] and concert.PLAYER.files:
            concert.PLAYER.play("processed")
            return
        state["resume_at"] = concert.PLAYER.position() if concert.PLAYER.playing() else 0
        concert.PLAYER.close()   # 先释放文件，才能覆盖
        ffmpeg = core.find_ffmpeg()
        settings = current_settings()
        c_status.set("正在按新设置生成试听……")
        state["concert_busy"] = True

        def worker():
            try:
                events.put(("concert_rerendered", concert.render_preview(ffmpeg, session, settings)))
            except Exception as error:
                events.put(("concert_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def reexport():
        session = c_session.get("full")
        if not session or state.get("concert_busy"):
            return
        ffmpeg = core.find_ffmpeg()
        settings = current_settings()
        remember_settings()
        output_dir = c_output_dir.get().strip() or os.path.dirname(session["source"])
        make_video = c_video.get()
        make_stems = c_stems.get()
        set_concert_busy(True)
        c_status.set("正在按当前设置重新导出（不用重跑 AI）……")
        c_write("—" * 60)

        def worker():
            try:
                result = concert.export(ffmpeg, session, settings, output_dir, make_video, lambda text: events.put(("concert_log", text)),
                                        stems=make_stems)
                result["session"] = session
                events.put(("concert_done", result))
            except Exception as error:
                events.put(("concert_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_concert():
        state["batch_cancel"] = True
        concert.cancel()
        c_status.set("正在取消……")

    def batch_process():
        if state.get("concert_busy"):
            return
        if not concert.ai_available():
            messagebox.showinfo(APP_TITLE, "第一次使用需要先安装演唱会降噪组件。")
            return
        files = filedialog.askopenfilenames(filetypes=concert.MEDIA_TYPES, title="选择要批量处理的视频（可多选）")
        if not files:
            return
        ffmpeg = core.find_ffmpeg()
        steps, settings = selected_steps(), current_settings()
        remember_settings()
        output_dir = c_output_dir.get().strip()
        make_video, make_stems, fast = c_video.get(), c_stems.get(), c_fast.get()
        concert.PLAYER.close()
        set_concert_busy(True)
        state["batch_cancel"] = False
        c_progress.stop(); c_progress.configure(mode="determinate", value=0)
        c_write("—" * 60)
        total = len(files)

        def worker():
            ok, failed = 0, []
            for index, source in enumerate(files):
                if state.get("batch_cancel"):
                    break
                name = os.path.basename(source)
                events.put(("concert_log", f"[{index + 1}/{total}] {name}"))

                def progress(frac, stage="", i=index):
                    events.put(("concert_progress", ((i + frac) / total, f"第 {i + 1}/{total} 个 · {stage}")))
                try:
                    session = concert.run_ai(source, ffmpeg, steps, None, log=lambda t: events.put(("concert_log", t)),
                                             progress=progress, fast=fast)
                    result = concert.export(ffmpeg, session, settings, output_dir or os.path.dirname(source), make_video,
                                            lambda t: events.put(("concert_log", t)), stems=make_stems)
                    events.put(("concert_log", "✔ " + (result.get("video") or result["audio"])))
                    state["batch_last"] = result.get("video") or result["audio"]
                    ok += 1
                except concert.Cancelled:
                    break
                except Exception as error:
                    failed.append(name)
                    events.put(("concert_log", f"✖ {name}：{str(error)[-300:]}"))
            events.put(("concert_batch_done", (ok, failed, total)))

        threading.Thread(target=worker, daemon=True).start()

    def open_settings():
        win = tk.Toplevel(root)
        win.title("演唱会降噪 · 设置")
        win.transient(root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        temp_var = tk.StringVar(value=prefs.get("temp_dir", ""))
        ttk.Label(frame, text="中间文件放在哪（整场演唱会每小时约 3 GB，处理完同类的新文件时会自动删掉旧的）").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(frame, textvariable=temp_var, width=48).grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Button(frame, text="浏览…", command=lambda: temp_var.set(filedialog.askdirectory(parent=win) or temp_var.get())).grid(row=1, column=1, padx=6)
        ttk.Label(frame, text="留空 = 系统临时文件夹（一般在 C 盘）", foreground="#6b7686").grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(frame, text="显卡半精度加速（约快 1.5–2 倍；关掉则和旧版逐位一致）", variable=c_fast).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))

        def save():
            folder = temp_var.get().strip()
            if folder and not os.path.isdir(folder):
                messagebox.showwarning(APP_TITLE, "这个文件夹不存在。", parent=win)
                return
            prefs.update(temp_dir=folder, fast=bool(c_fast.get()))
            concert.set_temp_root(folder or None)
            concert.save_presets(presets)
            win.destroy()

        ttk.Button(frame, text="保存", style="Primary.TButton", command=save).grid(row=4, column=0, columnspan=3, sticky="e", pady=(14, 0))

    c_play_b.configure(command=play_adjusted)
    c_reexport_button.configure(command=reexport)
    c_cancel_button.configure(command=cancel_concert)
    c_batch_button.configure(command=batch_process)
    c_settings_button.configure(command=open_settings)

    c_install_button.configure(command=install_concert_clicked)
    c_preview_button.configure(command=lambda: concert_job(True))
    c_start_button.configure(command=lambda: concert_job(False))

    # ------------------------------------------------------------- 扒谱与声部音量地图
    score_tab = ttk.Frame(notebook, padding=8)
    notebook.insert(2, score_tab, text="扒谱")
    s_card = card(score_tab)
    s_card.columnconfigure(1, weight=1)
    s_card.rowconfigure(8, weight=1)
    s_input = tk.StringVar()
    s_output_dir = tk.StringVar(value=saved["outdir"])
    s_clean = tk.BooleanVar(value=True)
    s_fine = tk.BooleanVar(value=False)
    s_pdf = tk.BooleanVar(value=True)
    s_size = tk.StringVar(value=list(transcribe.SIZES)[0])
    s_status = tk.StringVar(value="选一段演奏录音：先“识别音符”，再选要哪些声部、记成什么乐器，生成总谱和分谱")
    s_state = {"session": None, "rows": [], "instruments": [], "stems": None, "last": None}

    ttk.Label(s_card, text="扒谱与声部分析", style="Card.TLabel", font=("Microsoft YaHei UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(s_card, text="AI 把录音扒成总谱和分谱（MusicXML / PDF，初稿需校对），并画出每个声部什么时候响、多响", style="Muted.Card.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(1, 6))
    ttk.Label(s_card, text="音频/视频", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
    ttk.Entry(s_card, textvariable=s_input).grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Button(s_card, text="选择…", command=lambda: s_input.set(filedialog.askopenfilename(filetypes=concert.MEDIA_TYPES) or s_input.get())).grid(row=2, column=2)
    ttk.Label(s_card, text="保存到", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4)
    ttk.Entry(s_card, textvariable=s_output_dir).grid(row=3, column=1, sticky="ew", padx=8)
    ttk.Button(s_card, text="浏览…", command=lambda: choose_directory(s_output_dir)).grid(row=3, column=2)

    s_opts = ttk.Frame(s_card, style="Card.TFrame")
    s_opts.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 2))
    ttk.Checkbutton(s_opts, text="先去观众声和底噪（现场录音推荐）", variable=s_clean).pack(side="left")
    ttk.Label(s_opts, text="精度", style="Card.TLabel").pack(side="left", padx=(14, 4))
    ttk.Combobox(s_opts, textvariable=s_size, values=list(transcribe.SIZES), state="readonly", width=24).pack(side="left")
    s_inst_label = ttk.Label(s_opts, text="乐器：自动识别", style="Muted.Card.TLabel")
    s_inst_label.pack(side="left", padx=(14, 4))

    def choose_instruments():
        win = tk.Toplevel(root)
        win.title("指定乐器（知道编制时选上，识别会更准）")
        win.transient(root)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="只勾这场演出里真的有的乐器；不勾 = 让 AI 自己判断").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        chosen = {}
        for i, name in enumerate(transcribe.GROUP_NAMES):
            var = tk.BooleanVar(value=transcribe.GROUP_NAMES[name] in s_state["instruments"])
            chosen[name] = var
            ttk.Checkbutton(frame, text=name, variable=var).grid(row=1 + i // 4, column=i % 4, sticky="w", padx=4, pady=1)

        def save():
            s_state["instruments"] = [transcribe.GROUP_NAMES[n] for n, v in chosen.items() if v.get()]
            names = [n for n, v in chosen.items() if v.get()]
            s_inst_label.configure(text="乐器：" + ("、".join(names[:4]) + ("…" if len(names) > 4 else "") if names else "自动识别"))
            win.destroy()

        ttk.Button(frame, text="确定", style="Primary.TButton", command=save).grid(row=20, column=3, sticky="e", pady=(10, 0))

    ttk.Button(s_opts, text="指定乐器…", command=choose_instruments).pack(side="left")

    s_actions = ttk.Frame(s_card, style="Card.TFrame")
    s_actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(6, 4))
    s_actions.columnconfigure(0, weight=1)
    s_detect_button = ttk.Button(s_actions, text="① 识别音符", style="Primary.TButton")
    s_detect_button.grid(row=0, column=0, sticky="ew")
    s_map_button = ttk.Button(s_actions, text="声部音量地图")
    s_map_button.grid(row=0, column=1, padx=(8, 0))
    s_cancel_button = ttk.Button(s_actions, text="取消", state="disabled", command=lambda: concert.cancel())
    s_cancel_button.grid(row=0, column=2, padx=(8, 0))

    s_mid = ttk.Frame(s_card, style="Card.TFrame")
    s_mid.grid(row=8, column=0, columnspan=3, sticky="nsew")
    s_mid.columnconfigure(1, weight=1)
    s_mid.rowconfigure(0, weight=1)
    s_list = ttk.Frame(s_mid, style="Card.TFrame")
    s_list.grid(row=0, column=0, sticky="nw")
    ttk.Label(s_list, text="识别到的声部（勾选要输出的，右边选记成什么乐器）", style="Muted.Card.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
    s_rows_frame = ttk.Frame(s_list, style="Card.TFrame")
    s_rows_frame.grid(row=1, column=0, columnspan=4, sticky="w")
    s_bottom = ttk.Frame(s_list, style="Card.TFrame")
    s_bottom.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
    ttk.Checkbutton(s_bottom, text="细致节奏（到 32 分音符）", variable=s_fine).pack(side="left")
    ttk.Checkbutton(s_bottom, text="用 MuseScore 导出 PDF", variable=s_pdf).pack(side="left", padx=(10, 0))
    s_export_button = ttk.Button(s_list, text="② 生成总谱和分谱", state="disabled")
    s_export_button.grid(row=3, column=0, sticky="w", pady=(6, 0))
    s_open_button = ttk.Button(s_list, text="打开文件夹", state="disabled", command=lambda: open_path(s_state["last"]))
    s_open_button.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
    s_map = tk.Canvas(s_mid, height=int(170 * scale), background="#ffffff", highlightthickness=1, highlightbackground="#dde3ea")
    s_map.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

    s_progress = ttk.Progressbar(s_card, maximum=100)
    s_progress.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 0))
    ttk.Label(s_card, textvariable=s_status, style="Muted.Card.TLabel").grid(row=10, column=0, columnspan=3, sticky="w", pady=2)
    s_foot = ttk.Frame(s_card, style="Card.TFrame")
    s_foot.grid(row=11, column=0, columnspan=3, sticky="ew")
    s_foot.columnconfigure(0, weight=1)
    s_ai_label = ttk.Label(s_foot, text="", style="Muted.Card.TLabel")
    s_ai_label.grid(row=0, column=0, sticky="w")
    s_token_button = ttk.Button(s_foot, text="Hugging Face 授权…")
    s_token_button.grid(row=0, column=1, padx=(8, 0))
    s_install_button = ttk.Button(s_foot, text="安装扒谱组件")
    s_install_button.grid(row=0, column=2, padx=(8, 0))

    def update_score_label():
        parts = []
        parts.append("扒谱组件：已安装" if transcribe.available() else "扒谱组件：未安装")
        parts.append("授权：已设置" if transcribe.get_token() else "授权：未设置")
        parts.append("MuseScore：已找到" if transcribe.find_musescore() else "MuseScore：未找到（只导出 MusicXML）")
        s_ai_label.configure(text=" · ".join(parts))

    update_score_label()

    def draw_map(_event=None):
        """声部音量地图：每行一个声部，横轴是时间，颜色越深越响；最上面一行标出每一秒最响的声部。"""
        cv = s_map
        cv.delete("all")
        w, h = max(cv.winfo_width(), 240), max(cv.winfo_height(), 120)
        rows = []
        if s_state["stems"]:
            rows += [(r["name"], r["levels"], "db") for r in s_state["stems"]["rows"]]
        if s_state["session"]:
            rows += [(t["name_zh"] + "（活动）", t["activity"], "act") for t in s_state["session"]["summary"]["tracks"]]
        if not rows:
            cv.create_text(w / 2, h / 2, text="声部音量地图：点“声部音量地图”（六轨分离，看各声部多响）\n或先“识别音符”（看每件乐器什么时候在演奏）",
                           fill="#8a94a3", font=("Microsoft YaHei UI", 9), justify="center")
            return
        left, top, right = 86, 16, 6
        length = max(len(r[1]) for r in rows)
        row_h = max(8, (h - top - 16) / len(rows))
        col_w = (w - left - right) / max(length, 1)
        db_values = [v for _, levels, kind in rows if kind == "db" for v in levels if v > -90]
        db_max = max(db_values) if db_values else 0
        colors = ["#f4f6f9", "#d7e6f7", "#a9cbf0", "#6fa8e6", "#3b82d6", "#1f5fb3", "#123f80"]
        for r, (name, levels, kind) in enumerate(rows):
            y = top + r * row_h
            cv.create_text(left - 4, y + row_h / 2, text=name[:10], anchor="e", fill="#4a5563", font=("Microsoft YaHei UI", 8))
            peak = max(levels) if kind == "act" and levels else 1
            step = max(1, int(1 / col_w)) if col_w < 1 else 1
            for i in range(0, len(levels), step):
                value = levels[i]
                level = (value - (db_max - 36)) / 36 if kind == "db" else value / max(peak, 1e-6)
                level = max(0.0, min(1.0, level))
                if level < 0.05:
                    continue
                color = colors[min(len(colors) - 1, int(level * (len(colors) - 1) + 0.5))] if kind == "db" else \
                    ("#fbe3c5", "#f6c28b", "#ee9a45", "#d9731a")[min(3, int(level * 3.99))]
                x = left + i * col_w
                cv.create_rectangle(x, y + 1, x + max(col_w * step, 1), y + row_h - 1, fill=color, width=0)
        # 顶部：每一秒最响的声部（只看六轨响度）
        stem_rows = [(name, levels) for name, levels, kind in rows if kind == "db"]
        if stem_rows:
            palette = ["#e05a47", "#2f6fdf", "#2eaa6a", "#9b59b6", "#e8871e", "#6b7686"]
            for i in range(length):
                best = max(range(len(stem_rows)), key=lambda k: stem_rows[k][1][i] if i < len(stem_rows[k][1]) else -999)
                x = left + i * col_w
                cv.create_rectangle(x, 2, x + max(col_w, 1), top - 4, fill=palette[best % len(palette)], width=0)
            cv.create_text(left - 4, top / 2, text="最响", anchor="e", fill="#4a5563", font=("Microsoft YaHei UI", 8))
        minutes = length / 60
        for m in range(0, int(minutes) + 1, max(1, int(minutes // 6) or 1)):
            x = left + m * 60 * col_w
            cv.create_line(x, top, x, h - 12, fill="#e6eaf0")
            cv.create_text(x, h - 6, text=f"{m}:00", fill="#8a94a3", font=("Microsoft YaHei UI", 7))

    s_map.bind("<Configure>", draw_map)

    def fill_rows(summary):
        for child in s_rows_frame.winfo_children():
            child.destroy()
        s_state["rows"] = []
        names = [n for n, _ in transcribe.TARGETS]
        for i, track in enumerate(summary["tracks"][:14]):
            include = tk.BooleanVar(value=track["group"] != "drums" and track["notes"] >= 8)
            default = next((n for n, m in transcribe.TARGETS if m == _group_target(track["group"])), "钢琴")
            target = tk.StringVar(value=default)
            ttk.Checkbutton(s_rows_frame, variable=include).grid(row=i, column=0)
            ttk.Label(s_rows_frame, text=track["name_zh"], style="Card.TLabel", width=12).grid(row=i, column=1, sticky="w")
            ttk.Label(s_rows_frame, text=f"{track['notes']} 个音 · {_note_name(track['low'])}–{_note_name(track['high'])}",
                      style="Muted.Card.TLabel", width=18).grid(row=i, column=2, sticky="w")
            ttk.Combobox(s_rows_frame, textvariable=target, values=names, state="readonly", width=16).grid(row=i, column=3, padx=(4, 0))
            s_state["rows"].append((track, include, target))
        s_export_button.configure(state="normal" if s_state["rows"] else "disabled")

    def _group_target(group):
        return transcribe.GROUP_TARGET.get(group, "Piano")

    def _note_name(midi):
        names = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
        return f"{names[midi % 12]}{midi // 12 - 1}"

    def set_score_busy(busy):
        state["score_busy"] = busy
        for button in (s_detect_button, s_map_button, s_install_button, s_token_button):
            button.configure(state="disabled" if busy else "normal")
        s_export_button.configure(state="disabled" if busy or not s_state["rows"] else "normal")
        s_cancel_button.configure(state="normal" if busy else "disabled")

    def score_progress(frac, stage=""):
        events.put(("score_progress", (frac, stage)))

    def score_log(text):
        events.put(("score_log", text))

    def start_detect():
        if state.get("score_busy"):
            return
        source = s_input.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning(APP_TITLE, "请先选择一个视频或音频文件。")
            return
        if not transcribe.available():
            messagebox.showinfo(APP_TITLE, "请先点右下角“安装扒谱组件”。")
            return
        if not transcribe.get_token():
            open_token_dialog()
            return
        ffmpeg = core.find_ffmpeg()
        set_score_busy(True)
        s_status.set("正在识别音符……")

        def worker():
            try:
                events.put(("score_detected", transcribe.run_transcription(
                    source, ffmpeg, s_size.get(), list(s_state["instruments"]), s_clean.get(), score_log, score_progress)))
            except concert.Cancelled:
                events.put(("score_error", "已取消"))
            except Exception as error:
                events.put(("score_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def start_export():
        session = s_state["session"]
        if not session or state.get("score_busy"):
            return
        parts = []
        used = set()
        for track, include, target in s_state["rows"]:
            if not include.get():
                continue
            name = target.get()
            music21_name = dict(transcribe.TARGETS).get(name, "Piano")
            label = name.split("（")[0]
            count = sum(1 for p in parts if p["label"].startswith(label))
            label = f"{label} {count + 1}" if count or label in used else label
            used.add(label)
            parts.append({"track": track["track"], "group": track["group"], "instrument": music21_name, "label": label,
                          "grand": music21_name in ("Piano", "Harp", "Organ", "Electric Piano")})
        if not parts:
            messagebox.showwarning(APP_TITLE, "请至少勾选一个声部。")
            return
        set_score_busy(True)
        s_status.set("正在生成总谱和分谱……")
        output_dir = s_output_dir.get().strip() or os.path.dirname(session["source"])

        def worker():
            try:
                events.put(("score_exported", transcribe.export_score(session, parts, output_dir, s_fine.get(), s_pdf.get(),
                                                                      score_log, score_progress)))
            except Exception as error:
                events.put(("score_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def start_map():
        if state.get("score_busy"):
            return
        source = s_input.get().strip()
        if not os.path.isfile(source):
            messagebox.showwarning(APP_TITLE, "请先选择一个视频或音频文件。")
            return
        if not concert.ai_available():
            messagebox.showinfo(APP_TITLE, "需要先安装演唱会降噪组件。")
            return
        ffmpeg = core.find_ffmpeg()
        set_score_busy(True)
        s_status.set("正在分离六个声部……")
        audio = (s_state["session"] or {}).get("audio") if (s_state["session"] or {}).get("source") == source else None

        def worker():
            try:
                events.put(("score_stems", transcribe.run_stems(source, ffmpeg, s_clean.get(), score_log, score_progress, audio)))
            except concert.Cancelled:
                events.put(("score_error", "已取消"))
            except Exception as error:
                events.put(("score_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def open_token_dialog():
        win = tk.Toplevel(root)
        win.title("Hugging Face 授权")
        win.transient(root)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        steps = ("扒谱模型 MuScriptor 需要你同意它的许可（免费，个人非商业用途），只需要做一次：\n"
                 "1. 注册并登录 huggingface.co\n"
                 "2. 打开 huggingface.co/MuScriptor/muscriptor-large ，点同意许可（Agree）\n"
                 "3. 打开 huggingface.co/settings/tokens ，新建一个 Read 类型的令牌，复制\n"
                 "4. 粘贴到下面（只保存在你这台电脑的程序文件夹里）")
        ttk.Label(frame, text=steps, justify="left").grid(row=0, column=0, columnspan=3, sticky="w")
        token_var = tk.StringVar(value=transcribe.get_token())
        ttk.Entry(frame, textvariable=token_var, width=52, show="•").grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(frame, text="打开网页", command=lambda: __import__("webbrowser").open("https://huggingface.co/MuScriptor/muscriptor-large")).grid(row=1, column=2, padx=(6, 0))

        def save():
            value = token_var.get().strip()
            if not value.startswith("hf_"):
                messagebox.showwarning(APP_TITLE, "令牌一般以 hf_ 开头，请检查一下。", parent=win)
                return
            transcribe.set_token(value)
            update_score_label()
            win.destroy()

        ttk.Button(frame, text="保存", style="Primary.TButton", command=save).grid(row=2, column=2, sticky="e")

    def install_score():
        if state.get("score_busy"):
            return
        if not concert.ai_available():
            messagebox.showinfo(APP_TITLE, "请先在“演唱会降噪”页安装 AI 组件（扒谱和它共用显卡环境）。")
            return
        set_score_busy(True)
        s_status.set("正在安装扒谱组件……")

        def worker():
            try:
                transcribe.install(score_log)
                events.put(("score_installed", None))
            except Exception as error:
                events.put(("score_error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    s_detect_button.configure(command=start_detect)
    s_export_button.configure(command=start_export)
    s_map_button.configure(command=start_map)
    s_token_button.configure(command=open_token_dialog)
    s_install_button.configure(command=install_score)

    def handle_score_event(kind, value):
        if kind == "score_progress":
            frac, stage = value
            s_progress.configure(value=frac * 100)
            s_status.set(f"{stage} · {frac * 100:.0f}%")
        elif kind == "score_log":
            if value:
                s_status.set(value)
        elif kind == "score_detected":
            set_score_busy(False)
            s_state["session"] = value
            fill_rows(value["summary"])
            set_score_busy(False)
            meta = value["summary"]
            extra = " · ".join(x for x in (f"拍号 {meta['time_signatures'][0]}" if meta.get("time_signatures") else "",
                                           f"速度约 {meta['tempo']}" if meta.get("tempo") else "") if x)
            s_status.set(f"识别到 {len(meta['tracks'])} 个声部" + (f"（{extra}）" if extra else "") + "。勾选要输出的声部，再点“② 生成总谱和分谱”")
            draw_map()
            root.bell()
        elif kind == "score_exported":
            set_score_busy(False)
            s_state["last"] = value.get("score") or value.get("folder")
            s_open_button.configure(state="normal")
            s_status.set(f"已生成：总谱 + {len(value.get('parts', []))} 份分谱" + (f"（调性 {value['key']}）" if value.get("key") else "") + "，保存在 " + value["folder"])
            root.bell()
        elif kind == "score_stems":
            set_score_busy(False)
            s_state["stems"] = value
            s_status.set("声部音量地图已生成：颜色越深越响；最上面一行是每一秒最响的声部")
            draw_map()
        elif kind == "score_installed":
            set_score_busy(False)
            update_score_label()
            s_status.set("扒谱组件安装完成")
        elif kind == "score_error":
            set_score_busy(False)
            s_progress.configure(value=0)
            s_status.set("失败：" + value.splitlines()[0][:120] if value != "已取消" else "已取消")
            if value != "已取消":
                messagebox.showerror(APP_TITLE, value)

    # ------------------------------------------------------------- 事件与快捷键
    def finish_recording(raw_path):
        config = state["record_config"]
        if not config or not os.path.isfile(raw_path):
            events.put(("record_process_error", "录音结束，但没有找到临时 WAV 文件。"))
            return
        ffmpeg = core.find_ffmpeg()
        if not ffmpeg:
            events.put(("record_process_error", "找不到 ffmpeg，无法完成录音后期处理。"))
            return
        try:
            outputs = postprocess_recording(raw_path, config["output_dir"], f"电脑录音_{time.strftime('%Y%m%d_%H%M%S')}", config["format"], ffmpeg, config["trim"], config["split"], config["normalize"], log=lambda text: events.put(("record_log", text)))
            events.put(("record_processed", outputs))
        except Exception as error:
            events.put(("record_process_error", str(error)))
        finally:
            try:
                os.remove(raw_path)
            except OSError:
                pass

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "download_log":
                    d_write(value)
                elif kind == "download_progress":
                    if value is None:
                        d_progress.configure(mode="indeterminate"); d_progress.start(12); d_status.set("正在处理音频……")
                    else:
                        d_progress.stop(); d_progress.configure(mode="determinate", value=value); d_status.set(f"正在下载…… {value:.0f}%")
                elif kind in {"download_done", "download_cancelled", "download_error"}:
                    state["download_busy"] = False; d_start_button.configure(state="normal"); d_cancel_button.configure(state="disabled"); d_progress.stop()
                    if kind == "download_done":
                        d_last["path"] = value; d_open_button.configure(state="normal"); d_status.set("完成"); d_write("✔ " + value)
                        if d_send.get() and os.path.isfile(value):
                            notebook.select(concert_tab)
                            load_concert_file(value)
                            c_write("已从“视频提取”收到：" + os.path.basename(value) + "。先点“生成试听（AI）”试试效果。")
                    elif kind == "download_cancelled":
                        d_status.set("已取消")
                    else:
                        d_status.set("失败"); d_write("✖ " + value); messagebox.showerror(APP_TITLE, value)
                elif kind == "record_log":
                    r_write(value)
                elif kind == "helper_done":
                    update_helper_label(); r_write("录音组件已就绪。")
                elif kind == "helper_error":
                    update_helper_label(); messagebox.showerror(APP_TITLE, value)
                elif kind == "record":
                    event_type = value.get("type")
                    if event_type == "ready":
                        r_status.set("正在录音 · " + value.get("format", "")); r_write("录音已开始。")
                    elif event_type == "level":
                        r_level.configure(value=max(0, min(60, float(value.get("db", -60)) + 60))); r_status.set(f"正在录音 · {value.get('seconds', 0):.1f} 秒")
                    elif event_type in {"notice", "log"}:
                        r_write(value.get("message", ""))
                    elif event_type == "paused":
                        state["paused"] = True; r_pause_button.configure(text="继续"); r_status.set("已暂停")
                    elif event_type == "resumed":
                        state["paused"] = False; r_pause_button.configure(text="暂停"); r_status.set("正在录音")
                    elif event_type == "error":
                        r_write("✖ " + value.get("message", "未知错误")); r_start_button.configure(state="normal"); messagebox.showerror(APP_TITLE, value.get("message", "录音失败"))
                    elif event_type == "stopped":
                        state["recording"] = False; state["paused"] = False; state["record_stopped"] = True; r_pause_button.configure(state="disabled"); r_stop_button.configure(state="disabled"); r_level.configure(value=0); r_status.set("正在完成后期处理……")
                        threading.Thread(target=finish_recording, args=(value.get("output", ""),), daemon=True).start()
                    elif event_type == "exit" and value.get("code", 0) != 0 and state["recording"] and not state["record_stopped"]:
                        state["recording"] = False; state["paused"] = False; r_start_button.configure(state="normal"); r_pause_button.configure(state="disabled"); r_stop_button.configure(state="disabled"); r_status.set("录音组件异常退出")
                elif kind == "record_processed":
                    r_start_button.configure(state="normal"); r_status.set(f"完成 · 共 {len(value)} 个文件"); record_last["path"] = value[0]; r_open_button.configure(state="normal"); root.bell()
                elif kind == "record_process_error":
                    r_start_button.configure(state="normal"); r_status.set("后期处理失败"); messagebox.showerror(APP_TITLE, value)
                elif kind == "concert_log":
                    c_write(value)
                elif kind == "concert_progress":
                    frac, stage = value
                    c_progress.configure(value=frac * 100); c_status.set(f"{stage} · {frac * 100:.0f}%")
                elif kind == "concert_install_done":
                    set_concert_busy(False); c_progress.stop(); c_progress.configure(mode="determinate", value=0); update_concert_label(); c_status.set("组件安装完成，可以开始了")
                elif kind == "concert_install_error":
                    set_concert_busy(False); c_progress.stop(); c_progress.configure(mode="determinate", value=0); update_concert_label(); c_status.set("组件安装失败"); c_write("✖ " + value); messagebox.showerror(APP_TITLE, value)
                elif kind == "concert_done":
                    set_concert_busy(False)
                    session = value.get("session")
                    if "preview_processed" in value:
                        c_session.update(preview=session, dirty=False)
                        c_last["preview"] = value
                        for button in (c_play_a, c_play_b, c_stop, c_hold):
                            button.configure(state="normal")
                        c_status.set("试听已生成：拖动滑块后点“▶ 调整后”；按住“按住=听原声”可在同一时间点对比")
                        concert.PLAYER.load(original=value["preview_original"], processed=value["preview_processed"])
                        concert.PLAYER.play("processed", 0)
                    else:
                        c_session.update(full=session)
                        c_reexport_button.configure(state="normal")
                        line = (session or {}).get("analysis", {}).get("timeline") or {}
                        if line.get("vocal") and c_line.get("data"):
                            c_line["data"]["vocal"] = line["vocal"]
                            draw_timeline()
                        c_last["path"] = value.get("video") or value.get("audio")
                        c_open_button.configure(state="normal"); c_status.set("完成。想换个音色？调好滑块后点“按当前设置重新导出”，几秒就好"); c_write("✔ " + c_last["path"]); root.bell()
                    draw_spectrum()
                elif kind == "concert_rerendered":
                    state["concert_busy"] = False
                    c_last["preview"] = value
                    c_session["dirty"] = False
                    c_status.set("已按新设置更新试听")
                    concert.PLAYER.load(original=value["preview_original"], processed=value["preview_processed"])
                    concert.PLAYER.play("processed", state.get("resume_at", 0))
                elif kind.startswith("score_"):
                    handle_score_event(kind, value)
                elif kind == "update_status":
                    info, silent = value
                    update_state["info"] = info
                    if info.get("update"):
                        update_button.configure(text="有新版本 · 点此更新", state="normal")
                    else:
                        update_button.configure(text="已是最新版", state="normal")
                        if not silent:
                            messagebox.showinfo(APP_TITLE, "已是最新版")
                elif kind == "update_error":
                    update_button.configure(text="检查更新", state="normal")
                    messagebox.showerror(APP_TITLE, value)
                elif kind == "update_done":
                    updater.restart()
                    root.destroy()
                    return
                elif kind == "concert_reference":
                    set_reference(value)
                    c_status.set(f"参考曲已设置：{value['name']}（自动音色校正会照着它来）")
                elif kind == "concert_timeline":
                    path, data = value
                    if path == c_input.get():
                        c_line["data"] = data
                        draw_timeline()
                elif kind == "concert_cancelled":
                    set_concert_busy(False); c_progress.configure(value=0); c_status.set("已取消"); c_write("已取消")
                elif kind == "concert_batch_done":
                    ok, failed, total = value
                    set_concert_busy(False); c_progress.configure(value=100 if ok else 0)
                    c_last["path"] = state.get("batch_last")
                    if c_last["path"]:
                        c_open_button.configure(state="normal")
                    c_status.set(f"批量处理结束：成功 {ok} 个" + (f"，失败 {len(failed)} 个" if failed else "") + (f"，未处理 {total - ok - len(failed)} 个（已取消）" if ok + len(failed) < total else ""))
                    root.bell()
                elif kind == "dropped":
                    media = [f for f in value if os.path.splitext(f)[1].lower() in concert.MEDIA_EXTENSIONS]
                    if media:
                        notebook.select(concert_tab)
                        load_concert_file(media[0])
                        if len(media) > 1:
                            c_write(f"拖进了 {len(media)} 个文件，已选第一个。要一次处理多个，请用“批量处理…”")
                elif kind == "concert_error":
                    set_concert_busy(False); state["concert_busy"] = False; c_progress.configure(value=0); c_status.set("处理失败"); c_write("✖ " + value); messagebox.showerror(APP_TITLE, value)
                elif kind == "enhance_log":
                    e_write(value)
                elif kind == "ai_done":
                    e_install_button.configure(state="normal"); e_status.set("AI 组件安装完成"); update_ai_label()
                elif kind == "ai_error":
                    e_install_button.configure(state="normal"); e_status.set("AI 组件安装失败"); messagebox.showerror(APP_TITLE, value)
                elif kind in {"enhance_done", "enhance_error"}:
                    state["enhance_busy"] = False; e_start_button.configure(state="normal")
                    if kind == "enhance_done":
                        e_last["path"] = value; e_open_button.configure(state="normal"); e_status.set("增强完成"); e_write("✔ " + value); root.bell()
                    else:
                        e_status.set("增强失败"); e_write("✖ " + value); messagebox.showerror(APP_TITLE, value)
        except queue.Empty:
            pass
        root.after(100, poll)

    def hotkey_toggle():
        if not r_hotkeys.get():
            return
        root.after(0, stop_recording if state["recording"] else start_recording)

    def hotkey_pause():
        if r_hotkeys.get():
            root.after(0, toggle_pause)

    def enable_drag_and_drop():
        """把视频拖进窗口就能选中（需要 windnd；没有的话在后台装上，下次启动生效）。"""
        if os.name != "nt":
            return
        try:
            import windnd
            windnd.hook_dropfiles(root, func=lambda paths: events.put(("dropped", [str(p) for p in paths])), force_unicode=True)
        except ImportError:
            python = Path(sys.executable).with_name("python.exe")
            try:
                subprocess.Popen([str(python if python.is_file() else sys.executable), "-m", "pip", "install",
                                  "--disable-pip-version-check", "-q", "windnd"], creationflags=NO_WINDOW)
            except OSError:
                pass
        except Exception:
            pass

    enable_drag_and_drop()
    root.after(4000, lambda: check_updates(silent=True))
    hotkeys = GlobalHotkeys(hotkey_toggle, hotkey_pause)
    hotkeys.start()

    def close_window():
        if state["recording"] and not messagebox.askyesno(APP_TITLE, "录音仍在进行。确定停止并退出吗？"):
            return
        cancel_download.set()
        recorder.terminate()
        concert.play_wav(None)
        hotkeys.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)
    poll()
    root.mainloop()


def run() -> None:
    """启动程序；pythonw 没有控制台，出错时写日志并弹窗，而不是悄悄退出。"""
    log_path = Path(__file__).with_name(".tools") / "audio_studio_error.log"
    try:
        main()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            log_path.parent.mkdir(exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + detail + "\n")
        except OSError:
            pass
        try:
            import tkinter.messagebox
            tkinter.messagebox.showerror(APP_TITLE, "Audio Studio 启动失败：\n" + detail[-1500:])
        except Exception:
            pass


if __name__ == "__main__":
    run()
