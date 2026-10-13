"""capture.py 和 uploader.py 共用的配置加载、时间解析和文件写入。

只用标准库，兼容 Python 3.9+。
"""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class Config:
    device_id: str
    email: str
    password: str
    api_key: str
    db_url: str
    bucket: str
    emulator_host: Optional[str]
    outbox_dir: Path
    sent_dir: Path
    bad_dir: Path
    thumbs_dir: Path
    state_dir: Path
    images_dir: Path
    sensor_log_dir: Path
    disk_path: Path
    camera_mode: str
    lens_position: float
    model_path: str
    model_version: str
    max_image_pct: float = 60.0
    sent_keep_days: int = 30


def load_config(path: str | Path) -> Config:
    cp = configparser.ConfigParser()
    with open(path, encoding="utf-8") as f:
        cp.read_file(f)
    fb = cp["firebase"]
    paths = cp["paths"]

    def p(key: str) -> Path:
        return Path(paths[key]).expanduser()

    return Config(
        device_id=cp["device"]["device_id"].strip(),
        email=fb["email"].strip(),
        password=fb["password"],
        api_key=fb["api_key"].strip(),
        db_url=fb["db_url"].strip().rstrip("/"),
        bucket=fb["bucket"].strip(),
        emulator_host=fb.get("emulator_host", "").strip() or None,
        outbox_dir=p("outbox"),
        sent_dir=p("sent"),
        bad_dir=p("bad"),
        thumbs_dir=p("thumbs"),
        state_dir=p("state"),
        images_dir=p("images"),
        sensor_log_dir=p("sensor_logs"),
        disk_path=p("disk"),
        camera_mode=cp["camera"]["mode"].strip(),
        lens_position=cp["camera"].getfloat("lens_position"),
        model_path=str(Path(cp["model"]["path"]).expanduser()),
        model_version=cp["model"]["version"].strip(),
        max_image_pct=cp["storage"].getfloat("max_image_pct", 60.0),
        sent_keep_days=cp["storage"].getint("sent_keep_days", 30),
    )


def parse_acquired_at(s: str) -> datetime:
    """解析带时区偏移的 ISO8601。没有偏移或格式不对时抛 ValueError。"""
    if not isinstance(s, str):
        raise ValueError(f"acquired_at is not a string: {s!r}")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"acquired_at has no UTC offset: {s!r}")
    return dt


def local_naive(dt: datetime) -> datetime:
    """去掉时区，保留设备本地的钟面时间。"""
    return dt.replace(tzinfo=None)


def write_json_atomic(path: Path, obj: dict) -> None:
    """先写 <name>.tmp 再改名，读的一方永远看不到写了一半的 .json。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
