from dataclasses import replace
from pathlib import Path

import pytest

from common import Config

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def cfg(tmp_path) -> Config:
    """所有目录都在 tmp_path 下的测试配置；传感器日志指向手写 fixtures。"""
    dirs = {}
    for name in ["outbox", "sent", "bad", "thumbs", "state", "images"]:
        d = tmp_path / name
        d.mkdir()
        dirs[name] = d
    return Config(
        device_id="MSU1",
        email="msu1@devices.test",
        password="pw",
        api_key="KEY",
        db_url="https://proj-default-rtdb.firebaseio.com",
        bucket="proj.appspot.com",
        emulator_host=None,
        outbox_dir=dirs["outbox"],
        sent_dir=dirs["sent"],
        bad_dir=dirs["bad"],
        thumbs_dir=dirs["thumbs"],
        state_dir=dirs["state"],
        images_dir=dirs["images"],
        sensor_log_dir=FIXTURES / "logs",
        disk_path=tmp_path,
        camera_mode="9248:6944:12:P",
        lens_position=3.5,
        model_path="model",
        model_version="test-model",
    )


@pytest.fixture
def make_cfg(cfg):
    def _make(**kw) -> Config:
        return replace(cfg, **kw)
    return _make
