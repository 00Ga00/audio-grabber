"""一键更新：对比 GitHub 上的最新版本，下载并覆盖程序文件（不动 AI 环境、模型、设置和你的文件）。"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "00Ga00/audio-grabber"
BRANCH = "main"
HERE = Path(__file__).resolve().parent
VERSION_FILE = HERE / ".tools" / "version.txt"
KEEP = {".venv", ".venv-ai", ".tools", ".git", "_测试文件", "__pycache__"}   # 更新时绝不覆盖/删除
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _get(url: str, timeout: int = 15, accept: str | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AudioStudio-updater", **({"Accept": accept} if accept else {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def is_git_checkout() -> bool:
    return (HERE / ".git").exists()


def local_version() -> str | None:
    """当前版本（提交号）：git 仓库读 HEAD，ZIP 版读上次更新时记下的版本。"""
    if is_git_checkout():
        try:
            head = (HERE / ".git" / "HEAD").read_text().strip()
            if head.startswith("ref: "):
                ref = head[5:]
                path = HERE / ".git" / ref
                if path.is_file():
                    return path.read_text().strip()
                packed = (HERE / ".git" / "packed-refs").read_text()
                for line in packed.splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0]
            return head
        except OSError:
            return None
    try:
        return VERSION_FILE.read_text().strip() or None
    except OSError:
        return None


def latest_version() -> str:
    return _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", accept="application/vnd.github.sha").decode().strip()


def check() -> dict:
    """{"latest": 提交号, "local": 提交号或 None, "update": 有没有新版}"""
    latest = latest_version()
    local = local_version()
    return {"latest": latest, "local": local, "update": local != latest}


def apply(log=lambda _m: None) -> str:
    """下载最新版并覆盖程序文件。返回新版本号。"""
    if is_git_checkout():
        raise RuntimeError("这个文件夹是 git 仓库：请在 GitHub Desktop 里点 “Fetch origin” → “Pull origin” 更新。")
    latest = latest_version()
    log("正在下载新版本……")
    data = _get(f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}", timeout=120)
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1 or not (roots[0] / "audio_studio.pyw").is_file():
            raise RuntimeError("下载的新版本不完整，没有更新。")
        source = roots[0]
        log("正在替换程序文件……")
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            if relative.parts[0] in KEEP or item.is_dir():
                continue
            target = HERE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(latest)
    python = HERE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.is_file() and (HERE / "requirements.txt").is_file():
        log("正在更新依赖……")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                        str(HERE / "requirements.txt")], creationflags=NO_WINDOW, timeout=900)
    log("更新完成。")
    return latest


def restart() -> None:
    """用同一个 Python 重新打开程序。"""
    subprocess.Popen([sys.executable, str(HERE / "audio_studio.pyw")], cwd=str(HERE))
