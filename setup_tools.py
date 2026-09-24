"""start.bat 调用：第一次运行时自动准备录音组件和 AI 组件，之后秒过。"""

from __future__ import annotations

import sys

import audio_processing
import recording


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
