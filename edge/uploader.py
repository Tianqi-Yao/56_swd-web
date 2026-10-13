#!/usr/bin/env python3
"""把 outbox/ 里的计数记录上传到 Firebase，然后写心跳。

由 systemd timer 每 5 分钟启动一次，扫一遍 outbox 就退出。
  成功           → sent/YYYY-MM-DD/
  记录本身有问题 → bad/
  断网/5xx/401/403 → 整轮停下，文件留在 outbox/ 下次重试
只依赖标准库和 requests。
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from common import Config, load_config, local_naive, parse_acquired_at, unique_path, write_json_atomic
from fbclient import AuthError, ClientError, FirebaseClient, NetworkError
from images import image_status
from sensors import free_gb, read_battery, read_temp_rh

log = logging.getLogger("uploader")

MIN_AGE_S = 60
MAX_THUMB_BYTES = 500 * 1024
LOCAL_ONLY = ("thumb", "boxes", "img_w", "img_h")
SERVER_TIME = {".sv": "timestamp"}


class BadRecord(Exception):
    pass


def _validate(path: Path, cfg: Config) -> tuple:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadRecord(f"unreadable JSON: {e}")
    if not isinstance(rec, dict):
        raise BadRecord("not a JSON object")
    missing = [k for k in ("device_id", "acquired_at", "count_male_swd") if k not in rec]
    if missing:
        raise BadRecord(f"missing {missing}")
    if rec["device_id"] != cfg.device_id:
        raise BadRecord(f"device_id {rec['device_id']!r} != {cfg.device_id!r}")
    n = rec["count_male_swd"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise BadRecord(f"count_male_swd {n!r} is not a non-negative integer")
    try:
        at = parse_acquired_at(rec["acquired_at"])
    except ValueError as e:
        raise BadRecord(str(e))
    return rec, local_naive(at)


def _fill_sensors(rec: dict, cfg: Config, at: datetime) -> None:
    if rec.get("temp_c") is None or rec.get("rh") is None:
        temp, rh = read_temp_rh(cfg.sensor_log_dir, at)
        if rec.get("temp_c") is None:
            rec["temp_c"] = temp
        if rec.get("rh") is None:
            rec["rh"] = rh
    if rec.get("battery_v") is None:
        rec["battery_v"] = read_battery(cfg.sensor_log_dir, at)
    if rec.get("free_gb") is None:
        try:
            rec["free_gb"] = free_gb(cfg.disk_path)
        except OSError:
            rec["free_gb"] = None


def _upload_thumb(rec: dict, cfg: Config, client: FirebaseClient, day: str) -> None:
    """缩略图出问题只记日志，不影响计数上传；断网和认证错误往外抛。"""
    src = cfg.thumbs_dir / str(rec["thumb"])
    try:
        data = src.read_bytes()
    except OSError as e:
        log.warning("thumbnail %s unreadable, uploading count only: %s", src, e)
        return
    if len(data) >= MAX_THUMB_BYTES:
        log.warning("thumbnail %s is %d bytes (limit %d), uploading count only",
                    src, len(data), MAX_THUMB_BYTES)
        return
    storage_path = f"thumbs/{cfg.device_id}/{day}.jpg"
    try:
        client.upload_jpeg(storage_path, data)
        client.put(f"thumbs/{cfg.device_id}/{day}", {
            "storagePath": storage_path,
            "acquiredAt": rec["acquired_at"],
            "boxes": rec.get("boxes") or [],
            "imgW": rec.get("img_w"),
            "imgH": rec.get("img_h"),
        })
    except ClientError as e:
        log.warning("thumbnail upload rejected, uploading count only: %s", e)


def _ensure_record_id(rec: dict, path: Path) -> None:
    """给记录一个唯一 ID 并写回 outbox 文件，这样断网重试时还能认出是同一条。"""
    if not rec.get("record_id"):
        rec["record_id"] = uuid.uuid4().hex
        write_json_atomic(path, rec)


def _reading_key(client: FirebaseClient, base: str, record_id: str) -> str:
    """时钟故障会让不同记录落在同一个时间点：被别的记录占了就依次试 _1、_2……"""
    key, i = base, 0
    while True:
        existing = client.get(key)
        if not existing or existing.get("record_id") == record_id:
            return key
        i += 1
        key = f"{base}_{i}"


def _move(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    src.replace(unique_path(dest_dir / src.name))


def _prune_sent(cfg: Config, now: datetime) -> None:
    cutoff = now.date() - timedelta(days=cfg.sent_keep_days)
    try:
        subdirs = [d for d in cfg.sent_dir.iterdir() if d.is_dir()]
    except OSError:
        return
    for d in subdirs:
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def run_once(cfg: Config, client: FirebaseClient, now: datetime) -> dict:
    result = {"sent": 0, "bad": 0, "skipped": 0, "stopped": None}
    last_reading_at: Optional[str] = None

    for path in sorted(cfg.outbox_dir.glob("*.json")):
        try:
            age = now.timestamp() - path.stat().st_mtime
            if 0 <= age < MIN_AGE_S:  # 负数说明时钟往回跳过，不能当成刚写的
                result["skipped"] += 1
                continue
            rec, at = _validate(path, cfg)
        except BadRecord as e:
            log.error("bad record %s: %s", path.name, e)
            _move(path, cfg.bad_dir)
            result["bad"] += 1
            continue
        except OSError:
            continue  # 文件在扫描期间被挪走了

        day, hms = at.strftime("%Y-%m-%d"), at.strftime("%H%M%S")
        try:
            _ensure_record_id(rec, path)
        except OSError:
            continue  # 文件在扫描期间被挪走了，或磁盘写不进去，下次再试
        try:
            _fill_sensors(rec, cfg, at)
        except Exception:
            log.exception("sensor fill failed for %s, uploading without sensor values", path.name)
        try:
            if rec.get("thumb"):
                _upload_thumb(rec, cfg, client, day)
            reading = {k: v for k, v in rec.items() if k not in LOCAL_ONLY and v is not None}
            reading["serverTime"] = SERVER_TIME
            key = _reading_key(client, f"readings/{cfg.device_id}/{day}/{hms}", rec["record_id"])
            client.put(key, reading)
        except NetworkError as e:
            log.warning("network error, stopping this run: %s", e)
            result["stopped"] = "network"
            break
        except AuthError as e:
            log.error("auth error, stopping this run (check config / devices mapping): %s", e)
            result["stopped"] = "auth"
            break
        except ClientError as e:
            log.error("reading %s rejected: %s", path.name, e)
            _move(path, cfg.bad_dir)
            result["bad"] += 1
            continue

        _move(path, cfg.sent_dir / now.strftime("%Y-%m-%d"))
        result["sent"] += 1
        last_reading_at = rec["acquired_at"]

    heartbeat = {"deviceTime": now.isoformat(timespec="seconds"), "serverTime": SERVER_TIME}
    if last_reading_at:
        heartbeat["lastReadingAt"] = last_reading_at
    try:
        heartbeat.update(image_status(cfg.images_dir, cfg.disk_path, cfg.state_dir, now))
    except OSError as e:
        log.warning("image status unavailable: %s", e)
    try:
        client.patch(f"heartbeat/{cfg.device_id}", heartbeat)
    except (NetworkError, AuthError, ClientError) as e:
        log.warning("heartbeat failed: %s", e)

    _prune_sent(cfg, now)
    log.info("run done: %s", result)
    return result


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Upload SWD counts from outbox/ to Firebase")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.ini"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    run_once(cfg, FirebaseClient(cfg), datetime.now().astimezone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
