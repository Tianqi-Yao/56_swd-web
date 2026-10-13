"""从 Pi 本地的温湿度和电压日志里，取离采集时间最近的一条。

日志格式（无表头，时间是设备本地时间）：
  YYYY-MM-DD.csv                         date,time,temp,rh       每分钟一条
  voltage.csv / voltage_YYYY-MM.csv      date,time,V,percent     每 10 分钟一条
读不到就返回 None，绝不编数值；电压原样返回，不做过滤。
"""
from __future__ import annotations

import csv
import math
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

TEMP_RH_WINDOW = timedelta(minutes=5)
BATTERY_WINDOW = timedelta(minutes=15)


def _to_float(s: str) -> Optional[float]:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _rows(path: Path) -> Iterable[Tuple[datetime, List[str]]]:
    """逐行产出 (时间, 剩余列)。文件不存在、时间解析不了的行都跳过。"""
    try:
        f = open(path, newline="", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            try:
                t = datetime.strptime(f"{row[0].strip()} {row[1].strip()}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            yield t, row[2:]


def _nearest(paths: Iterable[Path], at: datetime, window: timedelta, ncols: int):
    best = None
    best_gap = None
    for path in paths:
        for t, cols in _rows(path):
            gap = abs(t - at)
            if gap > window or len(cols) < ncols:
                continue
            values = [_to_float(c) for c in cols[:ncols]]
            if any(v is None for v in values):
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = values, gap
    return best


def read_temp_rh(log_dir: Path, at: datetime) -> Tuple[Optional[float], Optional[float]]:
    days = {(at - TEMP_RH_WINDOW).date(), (at + TEMP_RH_WINDOW).date()}
    paths = [Path(log_dir) / f"{d.isoformat()}.csv" for d in sorted(days)]
    found = _nearest(paths, at, TEMP_RH_WINDOW, 2)
    return (found[0], found[1]) if found else (None, None)


def read_battery(log_dir: Path, at: datetime) -> Optional[float]:
    months = sorted({(at - BATTERY_WINDOW).strftime("%Y-%m"), (at + BATTERY_WINDOW).strftime("%Y-%m")})
    log_dir = Path(log_dir)
    paths = [log_dir / "voltage.csv"] + [log_dir / f"voltage_{m}.csv" for m in months]
    found = _nearest(paths, at, BATTERY_WINDOW, 1)
    return found[0] if found else None


def free_gb(disk_path: Path) -> float:
    return round(shutil.disk_usage(disk_path).free / 1024 ** 3, 1)
