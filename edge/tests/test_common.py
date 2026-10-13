import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import common
from common import load_config, local_naive, parse_acquired_at, write_json_atomic

EDGE_DIR = Path(__file__).resolve().parent.parent


def test_load_config_reads_example():
    cfg = load_config(EDGE_DIR / "config.example.ini")
    assert cfg.device_id == "MSU1"
    assert cfg.max_image_pct == 60.0
    assert cfg.sent_keep_days == 30
    assert isinstance(cfg.outbox_dir, Path)
    assert cfg.emulator_host is None
    assert isinstance(cfg.lens_position, float)


def test_load_config_emulator_host(tmp_path):
    text = (EDGE_DIR / "config.example.ini").read_text()
    text = text.replace("emulator_host =", "emulator_host = 127.0.0.1")
    p = tmp_path / "c.ini"
    p.write_text(text)
    assert load_config(p).emulator_host == "127.0.0.1"


def test_parse_acquired_at_with_offset():
    dt = parse_acquired_at("2026-07-15T12:00:03-05:00")
    assert dt.utcoffset() == timedelta(hours=-5)


def test_parse_acquired_at_rejects_naive():
    with pytest.raises(ValueError):
        parse_acquired_at("2026-07-15T12:00:03")


def test_parse_acquired_at_rejects_garbage():
    with pytest.raises(ValueError):
        parse_acquired_at("72:41:18")


def test_parse_acquired_at_rejects_non_string():
    with pytest.raises(ValueError):
        parse_acquired_at(12345)


def test_local_naive_keeps_wall_clock():
    dt = parse_acquired_at("2026-07-15T12:00:03-05:00")
    assert local_naive(dt) == datetime(2026, 7, 15, 12, 0, 3)


def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "a.json"
    write_json_atomic(p, {"x": 1})
    assert json.loads(p.read_text()) == {"x": 1}
    assert list(tmp_path.iterdir()) == [p]


def test_write_json_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    def broken_dump(obj, f, **kw):
        f.write('{"x": ')
        raise OSError("disk full")

    monkeypatch.setattr(common.json, "dump", broken_dump)
    with pytest.raises(OSError):
        write_json_atomic(tmp_path / "a.json", {"x": 1})
    assert list(tmp_path.glob("*.json")) == []
