"""Windows WASAPI 录音辅助模块。"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path


NO_WINDOW = 0x08000000 if os.name == "nt" else 0
HELPER_SOURCE = Path(__file__).resolve().parent / "recorder-helper"


def helper_path() -> Path:
    return Path(__file__).resolve().parent / ".tools" / "recorder-helper" / "AudioRecorderHelper.exe"


def _sources() -> list[Path]:
    return sorted(HELPER_SOURCE.glob("*.cs"))


def helper_available() -> bool:
    """录音组件存在，且比源码新（源码更新后会自动重新编译）。"""
    exe = helper_path()
    if not exe.is_file():
        return False
    newest = max((source.stat().st_mtime for source in _sources()), default=0)
    return exe.stat().st_mtime >= newest


def find_csc() -> Path | None:
    """Windows 自带的 C# 编译器（.NET Framework 4.x，Windows 10/11 默认就有）。"""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for folder in ("Framework64", "Framework"):
        candidate = Path(windir) / "Microsoft.NET" / folder / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    return None


def install_helper(log=lambda _message: None) -> Path:
    """用系统自带的编译器把 recorder-helper 里的源码编译成录音组件，不需要联网。"""
    csc = find_csc()
    if csc is None:
        raise RuntimeError("找不到 Windows 自带的 .NET Framework 编译器（csc.exe）。请确认系统是 Windows 10 或 11。")
    sources = _sources()
    if not sources:
        raise RuntimeError("缺少 recorder-helper 源码文件，请重新下载完整的项目。")
    target = helper_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    log("正在准备录音组件……")
    command = [str(csc), "/nologo", "/target:exe", "/optimize+", "/platform:anycpu", f"/out:{target}"] + [str(source) for source in sources]
    result = subprocess.run(command, capture_output=True, text=True, encoding="mbcs" if os.name == "nt" else "utf-8", errors="replace", creationflags=NO_WINDOW)
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError("录音组件编译失败：\n" + (result.stdout + result.stderr).strip()[-1500:])
    os.utime(target, None)
    log("录音组件已就绪。")
    return target


def list_visible_processes() -> list[dict]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    results = {}
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        executable = ""
        if handle:
            try:
                size = wintypes.DWORD(32768)
                path_buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                    executable = os.path.basename(path_buffer.value)
            finally:
                kernel32.CloseHandle(handle)
        if executable and executable.lower() not in {"explorer.exe", "applicationframehost.exe"}:
            results.setdefault(process_id.value, {"pid": process_id.value, "name": executable, "title": title_buffer.value})
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return sorted(results.values(), key=lambda item: (item["name"].lower(), item["title"].lower()))


class RecordingController:
    def __init__(self, event_callback):
        self.event_callback = event_callback
        self.process: subprocess.Popen | None = None
        self.reader_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, mode: str, pid: int | None, output_wav: str, timer_seconds: float, silence_seconds: float, threshold_db: float) -> None:
        if self.running:
            raise RuntimeError("录音已经在进行。")
        if not helper_available():
            raise RuntimeError("尚未安装录音组件。")
        command = [
            str(helper_path()),
            "--mode",
            mode,
            "--output",
            output_wav,
            "--timer",
            str(timer_seconds),
            "--silence-stop",
            str(silence_seconds),
            "--threshold-db",
            str(threshold_db),
        ]
        if mode in ("process", "exclude"):
            command += ["--pid", str(pid or 0)]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=NO_WINDOW,
        )

        def read_output():
            assert self.process and self.process.stdout
            for line in self.process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except ValueError:
                    event = {"type": "log", "message": text}
                self.event_callback(event)
            code = self.process.wait()
            self.event_callback({"type": "exit", "code": code})

        self.reader_thread = threading.Thread(target=read_output, daemon=True)
        self.reader_thread.start()

    def command(self, value: str) -> None:
        if self.running and self.process and self.process.stdin:
            self.process.stdin.write(value + "\n")
            self.process.stdin.flush()

    def pause(self) -> None:
        self.command("pause")

    def resume(self) -> None:
        self.command("resume")

    def stop(self) -> None:
        self.command("stop")

    def terminate(self) -> None:
        if self.running and self.process:
            self.process.terminate()


class GlobalHotkeys:
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, on_toggle, on_pause):
        self.on_toggle = on_toggle
        self.on_pause = on_pause
        self.thread = None
        self.thread_id = None

    def start(self) -> None:
        if os.name != "nt" or self.thread:
            return

        def loop():
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            self.thread_id = kernel32.GetCurrentThreadId()
            registered_one = user32.RegisterHotKey(None, 1, self.MOD_CONTROL | self.MOD_ALT, ord("R"))
            registered_two = user32.RegisterHotKey(None, 2, self.MOD_CONTROL | self.MOD_ALT, ord("P"))
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.WM_HOTKEY:
                    (self.on_toggle if message.wParam == 1 else self.on_pause)()
            if registered_one:
                user32.UnregisterHotKey(None, 1)
            if registered_two:
                user32.UnregisterHotKey(None, 2)

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.thread_id and os.name == "nt":
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)
