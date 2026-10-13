#!/usr/bin/env python3
"""拍一张粘虫卡 → 切块推理 → 把计数写进 outbox/，然后退出。

由 systemd timer 在 06:00–20:00 的每个整点和半点启动。
当天第一次在 12:00–12:29 拍成功的那张额外生成缩略图，并在记录里带上检测框。
温湿度、电压、剩余空间不在这里写，由 uploader 上传前补全。
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from common import Config, load_config, write_json_atomic
from detect import detect, load_model
from images import prune_images

log = logging.getLogger("capture")

PHOTO_TIMEOUT_S = 120
THUMB_LONG_SIDE = 1280
THUMB_MAX_BYTES = 500_000


def take_photo(cfg: Config, out_path: Path, run=subprocess.run) -> bool:
    cmd = [
        "rpicam-still", "-n", "--immediate",
        "--mode", cfg.camera_mode,
        "--lens-position", f"{cfg.lens_position:.2f}",
        "-o", str(out_path),
    ]
    try:
        proc = run(cmd, timeout=PHOTO_TIMEOUT_S, capture_output=True)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("rpicam-still failed: %s", e)
        return False
    if proc.returncode != 0:
        log.error("rpicam-still exited %s: %s", proc.returncode, (proc.stderr or b"")[-500:])
        return False
    try:
        return Path(out_path).stat().st_size > 0
    except OSError:
        return False


def is_noon_slot(now: datetime, thumbs_dir: Path) -> bool:
    if not (now.hour == 12 and now.minute < 30):
        return False
    return not any(Path(thumbs_dir).glob(f"{now:%Y%m%d}_*.jpg"))


def make_thumbnail(img, out_path: Path, long_side: int = THUMB_LONG_SIDE,
                   max_bytes: int = THUMB_MAX_BYTES) -> None:
    """缩到长边 long_side；JPEG 质量从 85 往下降，直到不超过 max_bytes。

    质量降到 35 还超，就再把尺寸缩小到 0.8 倍重试（真实粘虫卡一般用不到这一步）。
    """
    h, w = img.shape[:2]
    side = long_side
    while True:
        scale = min(1.0, side / max(h, w))
        small = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        for quality in range(85, 34, -10):
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok and len(buf) <= max_bytes:
                tmp = Path(str(out_path) + ".tmp")
                tmp.write_bytes(buf.tobytes())
                tmp.replace(out_path)
                return
        if side < 200:
            raise ValueError("cannot fit thumbnail under max_bytes")
        side = int(side * 0.8)


def capture_once(cfg: Config, now: datetime, model, run=subprocess.run) -> Optional[Path]:
    for d in (cfg.outbox_dir, cfg.thumbs_dir, cfg.images_dir):
        d.mkdir(parents=True, exist_ok=True)
    prune_images(cfg.images_dir, cfg.disk_path, cfg.max_image_pct, cfg.state_dir, now)

    stamp = now.strftime("%Y%m%d_%H%M%S")
    photo = cfg.images_dir / f"{stamp}.jpg"
    if not take_photo(cfg, photo, run=run):
        return None
    img = cv2.imread(str(photo))
    if img is None:
        log.error("cannot read photo %s", photo)
        return None

    t0 = time.monotonic()
    try:
        boxes = detect(model, img)
    except Exception:
        log.exception("inference failed on %s", photo)
        return None
    infer_ms = int((time.monotonic() - t0) * 1000)

    record = {
        "device_id": cfg.device_id,
        "acquired_at": now.isoformat(timespec="seconds"),
        "count_male_swd": len(boxes),
        "model_version": cfg.model_version,
        "infer_ms": infer_ms,
    }
    if is_noon_slot(now, cfg.thumbs_dir):
        thumb = f"{stamp}.jpg"
        try:
            make_thumbnail(img, cfg.thumbs_dir / thumb)
            h, w = img.shape[:2]
            record.update({
                "thumb": thumb,
                "boxes": [[round(v, 1) for v in b[:4]] + [round(b[4], 3)] for b in boxes],
                "img_w": w,
                "img_h": h,
            })
        except Exception:
            log.exception("thumbnail failed, writing count only")

    out = cfg.outbox_dir / f"{cfg.device_id}_{stamp}.json"
    write_json_atomic(out, record)
    log.info("captured %s: %d male SWD in %d ms", stamp, len(boxes), infer_ms)
    return out


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture one sticky-card photo and count male SWD")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.ini"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    model = load_model(cfg.model_path)
    return 0 if capture_once(cfg, datetime.now().astimezone(), model) else 1


if __name__ == "__main__":
    sys.exit(main())
