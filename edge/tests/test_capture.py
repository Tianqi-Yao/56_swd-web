import json
import subprocess
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from capture import capture_once, is_noon_slot, make_thumbnail, take_photo
from fakes import FakeModel

CDT = timezone(timedelta(hours=-5))


def at(h, m, s=0):
    return datetime(2026, 7, 15, h, m, s, tzinfo=CDT)


def synthetic_card(w=2000, h=1500):
    """三个白方块各落在不同的图块里，FakeModel 会报 3 只。"""
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    for x in (100, 800, 1300):
        img[100:140, x:x + 40] = 255
    return img


class FakeRun:
    def __init__(self, returncode=0, write=True, exc=None):
        self.returncode, self.write, self.exc = returncode, write, exc
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append((cmd, kw))
        if self.exc:
            raise self.exc
        if self.write:
            cv2.imwrite(cmd[cmd.index("-o") + 1], synthetic_card())
        return subprocess.CompletedProcess(cmd, self.returncode, b"", b"")


def outbox_records(cfg):
    return {p.name: json.loads(p.read_text()) for p in cfg.outbox_dir.glob("*.json")}


def test_take_photo_command(cfg, tmp_path):
    run = FakeRun()
    assert take_photo(cfg, tmp_path / "a.jpg", run=run) is True
    cmd, kw = run.cmds[0]
    assert cmd[0] == "rpicam-still"
    assert cmd[cmd.index("--lens-position") + 1] == "3.50"
    assert cmd[cmd.index("--mode") + 1] == "9248:6944:12:P"
    assert "--immediate" in cmd and "-n" in cmd
    assert kw["timeout"] == 120


@pytest.mark.parametrize("run", [
    FakeRun(returncode=1),
    FakeRun(write=False),
    FakeRun(exc=subprocess.TimeoutExpired("rpicam-still", 120)),
    FakeRun(exc=FileNotFoundError("rpicam-still")),
])
def test_take_photo_failure_returns_false(cfg, tmp_path, run):
    assert take_photo(cfg, tmp_path / "a.jpg", run=run) is False


def test_take_photo_empty_file_is_failure(cfg, tmp_path):
    def run(cmd, **kw):
        open(cmd[cmd.index("-o") + 1], "wb").close()
        return subprocess.CompletedProcess(cmd, 0)
    assert take_photo(cfg, tmp_path / "a.jpg", run=run) is False


def test_capture_writes_outbox_record(cfg):
    out = capture_once(cfg, at(9, 30), FakeModel(), run=FakeRun())
    assert out == cfg.outbox_dir / "MSU1_20260715_093000.json"
    (name, rec), = outbox_records(cfg).items()
    assert rec["device_id"] == "MSU1"
    assert rec["acquired_at"] == "2026-07-15T09:30:00-05:00"
    assert rec["count_male_swd"] == 3
    assert rec["model_version"] == "test-model"
    assert isinstance(rec["infer_ms"], int)
    assert not {"thumb", "boxes", "temp_c", "battery_v"} & rec.keys()
    assert (cfg.images_dir / "20260715_093000.jpg").exists()


def test_noon_capture_adds_thumb_and_boxes(cfg):
    capture_once(cfg, at(12, 0, 5), FakeModel(), run=FakeRun())
    rec = outbox_records(cfg)["MSU1_20260715_120005.json"]
    assert rec["thumb"] == "20260715_120005.jpg"
    assert len(rec["boxes"]) == 3 and rec["boxes"][0][:4] == [100.0, 100.0, 140.0, 140.0]
    assert (rec["img_w"], rec["img_h"]) == (2000, 1500)
    thumb = cv2.imread(str(cfg.thumbs_dir / rec["thumb"]))
    assert max(thumb.shape[:2]) == 1280


def test_second_noon_capture_same_day_no_thumb(cfg):
    capture_once(cfg, at(12, 0), FakeModel(), run=FakeRun())
    capture_once(cfg, at(12, 20), FakeModel(), run=FakeRun())
    assert "thumb" not in outbox_records(cfg)["MSU1_20260715_122000.json"]


@pytest.mark.parametrize("h,m,expected", [(11, 59, False), (12, 0, True), (12, 29, True), (12, 30, False)])
def test_noon_slot_bounds(cfg, h, m, expected):
    assert is_noon_slot(at(h, m), cfg.thumbs_dir) is expected


def test_noon_slot_false_when_thumb_exists_today(cfg):
    (cfg.thumbs_dir / "20260715_120000.jpg").write_bytes(b"x")
    assert is_noon_slot(at(12, 10), cfg.thumbs_dir) is False


def test_thumbnail_under_500kb(tmp_path):
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, size=(6944, 9248, 3), dtype=np.uint8)
    out = tmp_path / "t.jpg"
    make_thumbnail(noise, out)
    assert out.stat().st_size <= 500_000
    assert not list(tmp_path.glob("*.tmp*"))


def test_photo_failure_writes_nothing(cfg):
    assert capture_once(cfg, at(9, 30), FakeModel(), run=FakeRun(returncode=1)) is None
    assert outbox_records(cfg) == {}


def test_inference_exception_writes_nothing(cfg):
    assert capture_once(cfg, at(12, 0), FakeModel(fail=True), run=FakeRun()) is None
    assert outbox_records(cfg) == {}
    assert list(cfg.thumbs_dir.iterdir()) == []


def test_capture_prunes_before_shooting(cfg, monkeypatch):
    calls = []
    import capture
    monkeypatch.setattr(capture, "prune_images", lambda *a: calls.append(a) or [])
    capture_once(cfg, at(9, 30), FakeModel(), run=FakeRun())
    assert calls and calls[0][2] == 60.0


def test_same_timestamp_twice_keeps_both_records_and_photos(cfg):
    # RTC 每次开机都回到同一时刻时，同名文件不能互相覆盖
    capture_once(cfg, at(9, 30), FakeModel(), run=FakeRun())
    capture_once(cfg, at(9, 30), FakeModel(), run=FakeRun())
    assert len(list(cfg.outbox_dir.glob("*.json"))) == 2
    assert len(list(cfg.images_dir.glob("*.jpg"))) == 2
