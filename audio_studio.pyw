# -*- coding: utf-8 -*-
"""Audio Studio 3：视频音频提取、WASAPI 录音和音质增强。"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

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
    ttk.Label(shell, text="Audio Studio", style="Title.TLabel").pack(anchor="w")
    ttk.Label(shell, text="提取 · 录制 · 增强，全程在本机处理", style="Subtitle.TLabel").pack(anchor="w", pady=(0, 12))
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
    ttk.Checkbutton(clip_row, text="只保存一段", variable=d_clip).pack(side="left")
    ttk.Label(clip_row, text="从", style="Card.TLabel").pack(side="left", padx=(15, 4))
    ttk.Entry(clip_row, textvariable=d_start, width=10).pack(side="left")
    ttk.Label(clip_row, text="到", style="Card.TLabel").pack(side="left", padx=(10, 4))
    ttk.Entry(clip_row, textvariable=d_end, width=10).pack(side="left")
    ttk.Label(clip_row, text="例：1:30；结束留空=到结尾", style="Muted.Card.TLabel").pack(side="left", padx=10)

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
        options = dict(url=url, outdir=d_dir.get().strip(), format=d_format.get(), quality=d_quality.get(), start=start_at, end=end_at, login=d_login.get(), cookie_file=d_cookie.get())
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

    hotkeys = GlobalHotkeys(hotkey_toggle, hotkey_pause)
    hotkeys.start()

    def close_window():
        if state["recording"] and not messagebox.askyesno(APP_TITLE, "录音仍在进行。确定停止并退出吗？"):
            return
        cancel_download.set()
        recorder.terminate()
        hotkeys.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_window)
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
