"""start.bat 调用：第一次运行时自动准备录音组件和 AI 组件，之后秒过。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import audio_processing
import recording

HERE = Path(__file__).resolve().parent
SHORTCUT_FLAG = HERE / ".tools" / "desktop_shortcut_v2"


def ensure_desktop_shortcut() -> None:
    """第一次运行时在桌面放一个“Audio Studio”图标（之后用户删掉也不会再加回来）。"""
    if os.name != "nt" or SHORTCUT_FLAG.exists():
        return
    script = (
        "$ErrorActionPreference='Stop';"
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Audio Studio.lnk'));"
        # 直接启动程序本身（不经过 start.bat），双击后不会有黑色窗口
        f"$s.TargetPath='{HERE / '.venv' / 'Scripts' / 'pythonw.exe'}';"
        f"$s.Arguments='\"{HERE / 'audio_studio.pyw'}\"';"
        f"$s.WorkingDirectory='{HERE}';"
        "$s.WindowStyle=1;"
        "$s.IconLocation=\"$env:WINDIR\\System32\\SndVol.exe,0\";"
        "$s.Description='Audio Studio：视频提取 · 电脑录音 · AI 音质增强';"
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, creationflags=0x08000000,
    )
    SHORTCUT_FLAG.parent.mkdir(parents=True, exist_ok=True)
    (SHORTCUT_FLAG.parent / "desktop_shortcut.log").write_text(
        f"rc={result.returncode}\n{result.stdout}\n{result.stderr}", encoding="utf-8")
    if result.returncode == 0:
        SHORTCUT_FLAG.write_text("ok", encoding="utf-8")
        print("已在桌面创建“Audio Studio”图标，以后双击它就能打开。")


def main() -> int:
    problems = []
    if not recording.helper_available():
        try:
            recording.install_helper(print)
        except Exception as error:  # 不影响打开程序，界面里还能重试
            problems.append(f"录音组件：{error}")
    if not audio_processing.ai_available():
        try:
            audio_processing.install_ai(print)
        except Exception as error:
            problems.append(f"AI 组件：{error}")
    try:
        ensure_desktop_shortcut()
    except Exception:
        pass
    for problem in problems:
        print("[!] " + problem)
    if problems:
        print("以上组件稍后可以在程序界面里点按钮重试，其他功能不受影响。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
