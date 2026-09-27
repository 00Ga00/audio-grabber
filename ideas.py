"""灵感本：每个灵感一个文件夹（录音 / 和弦进行、自动扒出来的谱、idea.json 记着名字、日期、调、速度、和弦、标签）。"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path


def new_idea(root: str, name: str, kind: str = "recording") -> Path:
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "灵感"
    folder = base / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe[:40]}"
    folder.mkdir()
    info = {"name": name, "kind": kind, "created": time.strftime("%Y-%m-%d %H:%M"), "tags": [], "key": "", "tempo": 0,
            "chords": []}
    (folder / "idea.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


def read(folder: Path) -> dict:
    try:
        info = json.loads((Path(folder) / "idea.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {"name": Path(folder).name}
    info["folder"] = str(folder)
    return info


def update(folder: Path, **fields) -> dict:
    info = read(folder)
    info.pop("folder", None)
    info.update(fields)
    (Path(folder) / "idea.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def list_ideas(root: str) -> list[dict]:
    base = Path(root)
    if not base.is_dir():
        return []
    items = [read(p) for p in base.iterdir() if (p / "idea.json").is_file()]
    return sorted(items, key=lambda i: i.get("created", ""), reverse=True)


def keep_result(folder: Path, result: dict) -> dict:
    """扒谱的临时文件夹下次会被清掉：把练习要用的音频和 result.json 复制进灵感的文件夹，路径改过来。"""
    data = Path(folder) / "_练习数据"
    data.mkdir(exist_ok=True)
    files = dict(result.get("files", {}))
    for key in ("mix", "guitar", "backing", "guitar1", "guitar2"):
        if files.get(key) and Path(files[key]).is_file():
            target = data / Path(files[key]).name
            shutil.copyfile(files[key], target)
            files[key] = str(target)
    result = dict(result, files=files, folder=str(data))
    (data / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def load_result(folder: Path) -> dict | None:
    path = Path(folder) / "_练习数据" / "result.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def remove(folder: Path, trash_root: str) -> Path:
    """删除 = 挪进灵感本里的“已删除”文件夹（后悔了还能找回来）。"""
    trash = Path(trash_root) / "已删除"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / Path(folder).name
    shutil.move(str(folder), str(target))
    return target
