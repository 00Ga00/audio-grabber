"""检查电脑配置（显卡、显存、内存、CPU），按配置决定哪些 AI 选项可用、默认选什么，并估计处理时间。

档位：
  strong  NVIDIA 显卡、显存 ≥ 8 GB、内存 ≥ 16 GB：全部可用
  medium  NVIDIA 显卡、显存 4–8 GB 或内存 < 16 GB：全部可用，但一次只放一个模型；重的选项默认关
  weak    没有 NVIDIA 显卡（或显存 < 4 GB）：只用 CPU，锁住特别慢的选项
设置里可以“解除限制”，自己决定。
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".tools" / "hardware.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TIER_NAMES = {"strong": "高配（显卡加速，全部可用）", "medium": "中配（显卡加速，显存较小）", "weak": "低配（没有可用的 NVIDIA 显卡，用 CPU）"}

# 每一步大约要“音频时长的几倍”（RTF）；按档位粗估，实际跑过之后用实测值校正
RTF = {
    "strong": {"crowd": 0.04, "denoise": 0.04, "restore": 0.06, "split": 0.04, "dereverb": 0.04, "stems": 0.08,
               "score_medium": 0.10, "score_large": 0.20, "score_beam": 0.60},
    "medium": {"crowd": 0.10, "denoise": 0.10, "restore": 0.15, "split": 0.10, "dereverb": 0.10, "stems": 0.20,
               "score_medium": 0.25, "score_large": 0.50, "score_beam": 1.50},
    "weak": {"crowd": 1.2, "denoise": 1.2, "restore": 4.0, "split": 1.2, "dereverb": 1.2, "stems": 2.5,
             "score_medium": 2.0, "score_large": 5.0, "score_beam": 15.0},
}


def _nvidia() -> dict | None:
    exes = ["nvidia-smi", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")]
    for exe in exes:
        try:
            out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, creationflags=NO_WINDOW, timeout=15)
        except Exception:
            continue
        if out.returncode == 0 and out.stdout.strip():
            best = None
            for line in out.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    try:
                        vram = int(float(parts[1]))
                    except ValueError:
                        vram = 0
                    if not best or vram > best["vram_mb"]:
                        best = {"name": parts[0], "vram_mb": vram, "driver": parts[2] if len(parts) > 2 else ""}
            if best:
                return best
    return None


def _other_gpus() -> list[str]:
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []


def _memory_gb() -> float:
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullTotalPhys / 2 ** 30
        return 0.0
    try:
        text = Path("/proc/meminfo").read_text()
        return int(re.search(r"MemTotal:\s+(\d+)", text).group(1)) / 2 ** 20
    except Exception:
        return 0.0


def _cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except Exception:
            pass
    return platform.processor() or "CPU"


def tier_of(info: dict) -> str:
    gpu = info.get("nvidia")
    if not gpu or gpu.get("vram_mb", 0) < 3800:
        return "weak"
    if gpu["vram_mb"] >= 7600 and info.get("ram_gb", 0) >= 15:
        return "strong"
    return "medium"


def detect(use_cache: bool = False) -> dict:
    """检查配置（约 1 秒）；结果存一份，下次启动先用旧结果、后台再刷新。"""
    if use_cache and CACHE.is_file():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    nvidia = _nvidia()
    info = {"nvidia": nvidia, "gpus": [] if nvidia else _other_gpus(), "ram_gb": round(_memory_gb(), 1),
            "cpu": _cpu_name(), "cores": os.cpu_count() or 1, "time": time.time()}
    info["tier"] = tier_of(info)
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return info


def describe(info: dict) -> str:
    gpu = info.get("nvidia")
    gpu_text = f"{gpu['name']}（显存 {gpu['vram_mb'] / 1024:.0f} GB）" if gpu else ("、".join(info.get("gpus") or []) or "没有独立显卡") + "（不能用于 AI 加速）"
    return f"显卡：{gpu_text} · 内存：{info.get('ram_gb', 0):.0f} GB · CPU：{info.get('cpu', '')}（{info.get('cores', 1)} 线程）"


# ---------------------------------------------------------------- 按档位决定选项

# 被锁住的选项：档位 → {选项: 原因}
LOCKS = {
    "weak": {
        "restore": "音质修复在 CPU 上极慢（约为音频时长的 4 倍），需要 NVIDIA 显卡",
        "dereverb": "去混响需要 NVIDIA 显卡（CPU 上太慢）",
        "stems": "声部音量地图需要 NVIDIA 显卡（CPU 上太慢）",
        "score_large": "大模型需要 NVIDIA 显卡；CPU 上用中模型",
        "score_beam": "束搜索需要 NVIDIA 显卡",
    },
    "medium": {
        "score_beam": "束搜索需要 8 GB 以上显存",
    },
    "strong": {},
}

LOCK_NAMES = {"restore": "音质修复", "dereverb": "去混响", "stems": "声部音量地图", "score_large": "扒谱大模型", "score_beam": "束搜索"}

# 各档位的默认勾选
DEFAULTS = {
    "strong": {"crowd": True, "denoise": True, "restore": True, "split": True, "dereverb": False},
    "medium": {"crowd": True, "denoise": True, "restore": True, "split": True, "dereverb": False},
    "weak": {"crowd": True, "denoise": True, "restore": False, "split": False, "dereverb": False},
}


def locks(info: dict, unlocked: bool = False) -> dict:
    return {} if unlocked else dict(LOCKS.get(info.get("tier", "weak"), {}))


def score_sizes(labels: list[str], info: dict, unlocked: bool = False) -> list[str]:
    """扒谱“精度”下拉框里可选的项（labels 的顺序：最准、较准、快速）。"""
    lock = locks(info, unlocked)
    allowed = []
    for label in labels:
        if "束搜索" in label and "score_beam" in lock:
            continue
        if "大模型" in label and "score_large" in lock:
            continue
        allowed.append(label)
    return allowed or labels[-1:]


def default_score_size(labels: list[str], info: dict) -> str:
    """中模型和大模型听感差别不大，默认用中模型（快、凉快）；想要更准可以自己改。"""
    return next((label for label in labels if "中模型" in label), labels[-1])


def low_vram(info: dict) -> bool:
    return info.get("tier") == "medium"


def estimate(info: dict, keys: list[str], seconds: float, measured: dict | None = None) -> float:
    """估计处理时间（秒）。measured：以前实测的 {键: RTF}，有就优先用。"""
    table = dict(RTF.get(info.get("tier", "weak"), RTF["weak"]))
    table.update(measured or {})
    return sum(table.get(key, 0.1) for key in keys) * seconds + 15   # +15 秒：加载模型等固定开销


def fmt_minutes(sec: float) -> str:
    if sec < 90:
        return f"约 {max(sec, 10):.0f} 秒"
    if sec < 5400:
        return f"约 {sec / 60:.0f} 分钟"
    return f"约 {sec / 3600:.1f} 小时"
