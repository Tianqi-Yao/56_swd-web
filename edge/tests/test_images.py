from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

import images
from images import prune_images

NOW = datetime(2026, 7, 15, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
Usage = namedtuple("Usage", "total used free")


@pytest.fixture(autouse=True)
def disk_1000_bytes(monkeypatch):
    monkeypatch.setattr(images.shutil, "disk_usage", lambda p: Usage(1000, 0, 0))


def make_images(d, n=5, size=200):
    names = [f"202607{10 + i:02d}_120000.jpg" for i in range(n)]
    for name in names:
        (d / name).write_bytes(b"x" * size)
    return names


def test_prune_deletes_oldest_until_under_limit(cfg):
    names = make_images(cfg.images_dir)
    deleted = prune_images(cfg.images_dir, cfg.disk_path, 60.0, cfg.state_dir, NOW)
    assert [p.name for p in deleted] == names[:2]
    assert sorted(p.name for p in cfg.images_dir.iterdir()) == names[2:]
    assert (cfg.state_dir / "last_prune.txt").read_text() == NOW.isoformat()


def test_prune_noop_under_limit(cfg):
    make_images(cfg.images_dir, n=3)
    assert prune_images(cfg.images_dir, cfg.disk_path, 60.0, cfg.state_dir, NOW) == []
    assert not (cfg.state_dir / "last_prune.txt").exists()


def test_prune_missing_dir(cfg, tmp_path):
    assert prune_images(tmp_path / "nope", cfg.disk_path, 60.0, cfg.state_dir, NOW) == []
