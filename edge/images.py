"""原图目录的占用统计和滚动删除。

原图文件名是 YYYYMMDD_HHMMSS.jpg，按文件名排序就是按时间排序。
只用标准库，uploader 也会 import 这里。
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

ROLLING_WINDOW = timedelta(hours=24)
LAST_PRUNE = "last_prune.txt"


def _image_files(images_dir: Path) -> List[Path]:
    try:
        return sorted(p for p in Path(images_dir).iterdir() if p.is_file() and p.suffix == ".jpg")
    except OSError:
        return []


def _taken_at(p: Path) -> Optional[datetime]:
    try:
        return datetime.strptime(p.stem, "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def image_status(images_dir: Path, disk_path: Path, state_dir: Path, now: datetime) -> dict:
    files = _image_files(images_dir)
    used = sum(p.stat().st_size for p in files)
    total = shutil.disk_usage(disk_path).total
    oldest = next((t for t in map(_taken_at, files) if t is not None), None)

    rolling = False
    try:
        last = datetime.fromisoformat((Path(state_dir) / LAST_PRUNE).read_text().strip())
        rolling = now - last < ROLLING_WINDOW
    except (OSError, ValueError, TypeError):
        pass

    return {
        "imgUsedPct": round(used / total * 100, 1) if total else 0.0,
        "oldestImageAt": oldest.isoformat() if oldest else None,
        "imgRolling": rolling,
    }
