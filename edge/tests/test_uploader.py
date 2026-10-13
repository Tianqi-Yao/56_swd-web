import json
import os
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

import images
from fbclient import AuthError, ClientError, NetworkError
from images import image_status
from uploader import run_once

CDT = timezone(timedelta(hours=-5))
NOW = datetime(2026, 7, 15, 13, 0, 0, tzinfo=CDT)
Usage = namedtuple("Usage", "total used free")


class FakeClient:
    """记录所有调用；fail_on={调用序号(从1开始): 异常} 用来模拟出错。"""

    def __init__(self, fail_on=None, fail_upload=None, existing=None, fail_get=None):
        self.calls = []
        self.fail_on = fail_on or {}
        self.fail_upload = fail_upload
        self.existing = existing or {}  # 数据库里已有的节点 {path: data}
        self.fail_get = fail_get
        self.gets = []

    def _call(self, kind, path, data):
        self.calls.append((kind, path, data))
        err = self.fail_on.get(len(self.calls))
        if err:
            raise err

    def put(self, path, data):
        self._call("put", path, data)

    def patch(self, path, data):
        self._call("patch", path, data)

    def get(self, path):
        self.gets.append(path)
        if self.fail_get:
            raise self.fail_get
        return self.existing.get(path)

    def upload_jpeg(self, storage_path, data):
        if self.fail_upload:
            self.calls.append(("upload", storage_path, len(data)))
            raise self.fail_upload
        self._call("upload", storage_path, len(data))

    def puts(self, prefix="readings/"):
        return [(p, d) for k, p, d in self.calls if k == "put" and p.startswith(prefix)]


@pytest.fixture(autouse=True)
def fake_disk(monkeypatch):
    monkeypatch.setattr(images.shutil, "disk_usage", lambda p: Usage(10_000, 0, 5_000))


def record(hms="120003", **kw):
    r = {
        "device_id": "MSU1",
        "acquired_at": f"2026-07-15T{hms[:2]}:{hms[2:4]}:{hms[4:]}-05:00",
        "count_male_swd": 3,
        "model_version": "test-model",
        "infer_ms": 840,
    }
    r.update(kw)
    return r


def put_outbox(cfg, name, content, age_s=120):
    p = cfg.outbox_dir / name
    p.write_text(content if isinstance(content, str) else json.dumps(content))
    t = NOW.timestamp() - age_s
    os.utime(p, (t, t))
    return p


def sent_files(cfg):
    return sorted(p.name for p in cfg.sent_dir.rglob("*.json"))


def test_normal_upload_moves_to_sent(cfg):
    put_outbox(cfg, "MSU1_20260715_120003.json", record())
    client = FakeClient()
    result = run_once(cfg, client, NOW)
    (path, data), = client.puts()
    assert path == "readings/MSU1/2026-07-15/120003"
    assert data["serverTime"] == {".sv": "timestamp"}
    assert data["count_male_swd"] == 3
    assert data["temp_c"] == 28.4 and data["rh"] == 61.2
    assert "thumb" not in data
    assert (cfg.sent_dir / "2026-07-15" / "MSU1_20260715_120003.json").exists()
    assert list(cfg.outbox_dir.iterdir()) == []
    assert result == {"sent": 1, "bad": 0, "skipped": 0, "stopped": None}


def test_existing_sensor_values_not_overwritten(cfg):
    put_outbox(cfg, "a.json", record(temp_c=20.0))
    client = FakeClient()
    run_once(cfg, client, NOW)
    assert client.puts()[0][1]["temp_c"] == 20.0


def test_none_fields_omitted(cfg):
    # fixtures 里 7 月 15 日 12:00 前后 15 分钟内有 0.09 V；换到 09:00 就没有电压
    put_outbox(cfg, "a.json", record(hms="090000", battery_v=None))
    client = FakeClient()
    run_once(cfg, client, NOW)
    data = client.puts()[0][1]
    assert "battery_v" not in data and "temp_c" not in data
    assert all(v is not None for v in data.values())


def noon_record(cfg, size=1000):
    (cfg.thumbs_dir / "20260715_120003.jpg").write_bytes(b"\xff" * size)
    return record(thumb="20260715_120003.jpg", boxes=[[1.0, 2.0, 3.0, 4.0, 0.9]],
                  img_w=9248, img_h=6944)


def test_noon_thumb_uploaded_then_reading(cfg):
    put_outbox(cfg, "a.json", noon_record(cfg))
    client = FakeClient()
    run_once(cfg, client, NOW)
    kinds = [(k, p) for k, p, _ in client.calls if k != "patch"]
    assert kinds == [
        ("upload", "thumbs/MSU1/2026-07-15.jpg"),
        ("put", "thumbs/MSU1/2026-07-15"),
        ("put", "readings/MSU1/2026-07-15/120003"),
    ]
    thumb = client.calls[1][2]
    assert thumb == {"storagePath": "thumbs/MSU1/2026-07-15.jpg",
                     "acquiredAt": "2026-07-15T12:00:03-05:00",
                     "boxes": [[1.0, 2.0, 3.0, 4.0, 0.9]], "imgW": 9248, "imgH": 6944}
    reading = client.calls[2][2]
    assert not {"thumb", "boxes", "img_w", "img_h"} & reading.keys()


def test_thumb_missing_file_still_uploads_reading(cfg):
    put_outbox(cfg, "a.json", record(thumb="nope.jpg", boxes=[]))
    client = FakeClient()
    result = run_once(cfg, client, NOW)
    assert [p for p, _ in client.puts("")] == ["readings/MSU1/2026-07-15/120003"]
    assert result["sent"] == 1


def test_thumb_too_big_still_uploads_reading(cfg):
    put_outbox(cfg, "a.json", noon_record(cfg, size=600 * 1024))
    client = FakeClient()
    run_once(cfg, client, NOW)
    assert not [c for c in client.calls if c[0] == "upload"]
    assert len(client.puts()) == 1


def test_thumb_client_error_still_uploads_reading(cfg):
    put_outbox(cfg, "a.json", noon_record(cfg))
    client = FakeClient(fail_upload=ClientError(400))
    result = run_once(cfg, client, NOW)
    assert len(client.puts()) == 1 and not client.puts("thumbs/")
    assert result["sent"] == 1


def test_thumb_network_error_stops(cfg):
    put_outbox(cfg, "a.json", noon_record(cfg))
    client = FakeClient(fail_upload=NetworkError("down"))
    result = run_once(cfg, client, NOW)
    assert result["stopped"] == "network"
    assert (cfg.outbox_dir / "a.json").exists()


def test_network_error_stops_and_keeps_files(cfg):
    for i, hms in enumerate(["120003", "123003", "130003"]):
        put_outbox(cfg, f"MSU1_20260715_{hms}.json", record(hms=hms))
    client = FakeClient(fail_on={2: NetworkError("down")})
    result = run_once(cfg, client, NOW)
    assert result["stopped"] == "network" and result["sent"] == 1
    assert sent_files(cfg) == ["MSU1_20260715_120003.json"]
    assert sorted(p.name for p in cfg.outbox_dir.iterdir()) == [
        "MSU1_20260715_123003.json", "MSU1_20260715_130003.json"]
    assert client.calls[-1][0] == "patch"  # 心跳照写


def test_auth_error_stops_and_keeps_all_files(cfg):
    put_outbox(cfg, "a.json", record(hms="120003"))
    put_outbox(cfg, "b.json", record(hms="123003"))
    client = FakeClient(fail_on={1: AuthError("401")})
    result = run_once(cfg, client, NOW)
    assert result["stopped"] == "auth"
    assert list(cfg.bad_dir.iterdir()) == []
    assert len(list(cfg.outbox_dir.iterdir())) == 2


def test_reading_client_error_moves_to_bad(cfg):
    put_outbox(cfg, "a.json", record(hms="120003"))
    put_outbox(cfg, "b.json", record(hms="123003"))
    client = FakeClient(fail_on={1: ClientError(400)})
    result = run_once(cfg, client, NOW)
    assert (cfg.bad_dir / "a.json").exists()
    assert sent_files(cfg) == ["b.json"]
    assert result["bad"] == 1 and result["sent"] == 1


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps(["a list"]),
    json.dumps({"device_id": "MSU1", "acquired_at": "2026-07-15T12:00:03-05:00"}),
    json.dumps(record(count_male_swd=-1)),
    json.dumps(record(count_male_swd=1.5)),
    json.dumps(record(count_male_swd=True)),
])
def test_bad_records_to_bad_and_continue(cfg, content):
    put_outbox(cfg, "a.json", content)
    put_outbox(cfg, "b.json", record())
    client = FakeClient()
    result = run_once(cfg, client, NOW)
    assert (cfg.bad_dir / "a.json").exists()
    assert sent_files(cfg) == ["b.json"]
    assert result["bad"] == 1


def test_device_id_mismatch_to_bad(cfg):
    put_outbox(cfg, "a.json", record(device_id="HSH1"))
    run_once(cfg, FakeClient(), NOW)
    assert (cfg.bad_dir / "a.json").exists()


def test_naive_acquired_at_to_bad(cfg):
    put_outbox(cfg, "a.json", record(acquired_at="2026-07-15T12:00:03"))
    run_once(cfg, FakeClient(), NOW)
    assert (cfg.bad_dir / "a.json").exists()


def test_garbage_acquired_at_to_bad(cfg):
    put_outbox(cfg, "a.json", record(acquired_at="2026-06-17T52:00:10-05:00"))
    run_once(cfg, FakeClient(), NOW)
    assert (cfg.bad_dir / "a.json").exists()


def test_recent_file_skipped(cfg):
    put_outbox(cfg, "a.json", record(), age_s=10)
    client = FakeClient()
    result = run_once(cfg, client, NOW)
    assert result["skipped"] == 1 and not client.puts()
    assert (cfg.outbox_dir / "a.json").exists()


def test_tmp_files_ignored(cfg):
    put_outbox(cfg, "a.json.tmp", record())
    client = FakeClient()
    run_once(cfg, client, NOW)
    assert not client.puts()
    assert (cfg.outbox_dir / "a.json.tmp").exists()


def test_rtc_fault_time_uploaded_unchanged(cfg):
    put_outbox(cfg, "a.json", record(acquired_at="2000-01-01T03:12:00-06:00"))
    client = FakeClient()
    run_once(cfg, client, NOW)
    (path, data), = client.puts()
    assert path == "readings/MSU1/2000-01-01/031200"
    assert data["acquired_at"] == "2000-01-01T03:12:00-06:00"


def test_backlog_processed_in_filename_order(cfg):
    names = []
    for i in range(40):
        t = datetime(2026, 7, 13, 6, 0) + timedelta(minutes=30 * i)
        name = f"MSU1_{t:%Y%m%d_%H%M%S}.json"
        names.append(name)
        put_outbox(cfg, name, record(acquired_at=t.strftime("%Y-%m-%dT%H:%M:%S-05:00")))
    client = FakeClient()
    run_once(cfg, client, NOW)
    expected = [f"readings/MSU1/{n[5:9]}-{n[9:11]}-{n[11:13]}/{n[14:20]}" for n in sorted(names)]
    assert [p for p, _ in client.puts()] == expected
    assert len(sent_files(cfg)) == 40


def test_heartbeat_after_upload(cfg):
    put_outbox(cfg, "a.json", record())
    client = FakeClient()
    run_once(cfg, client, NOW)
    kind, path, hb = client.calls[-1]
    assert (kind, path) == ("patch", "heartbeat/MSU1")
    assert hb["deviceTime"] == "2026-07-15T13:00:00-05:00"
    assert hb["serverTime"] == {".sv": "timestamp"}
    assert hb["lastReadingAt"] == "2026-07-15T12:00:03-05:00"


def test_heartbeat_written_when_outbox_empty(cfg):
    client = FakeClient()
    run_once(cfg, client, NOW)
    (kind, path, hb), = client.calls
    assert kind == "patch" and "lastReadingAt" not in hb
    assert hb["imgUsedPct"] == 0.0


def test_heartbeat_failure_does_not_raise(cfg):
    client = FakeClient(fail_on={1: NetworkError("down")})
    assert run_once(cfg, client, NOW)["stopped"] is None


def test_image_status_rolling_flag(cfg):
    (cfg.images_dir / "20260701_060000.jpg").write_bytes(b"x" * 1500)
    (cfg.images_dir / "20260710_120000.jpg").write_bytes(b"x" * 500)
    (cfg.state_dir / "last_prune.txt").write_text((NOW - timedelta(hours=2)).isoformat())
    st = image_status(cfg.images_dir, cfg.disk_path, cfg.state_dir, NOW)
    assert st == {"imgUsedPct": 20.0, "oldestImageAt": "2026-07-01T06:00:00", "imgRolling": True}
    (cfg.state_dir / "last_prune.txt").write_text((NOW - timedelta(hours=30)).isoformat())
    assert image_status(cfg.images_dir, cfg.disk_path, cfg.state_dir, NOW)["imgRolling"] is False


def test_image_status_empty_and_missing_dir(cfg, tmp_path):
    st = image_status(tmp_path / "nope", cfg.disk_path, cfg.state_dir, NOW)
    assert st == {"imgUsedPct": 0.0, "oldestImageAt": None, "imgRolling": False}


def test_old_sent_dirs_pruned(cfg):
    old = cfg.sent_dir / (NOW.date() - timedelta(days=31)).isoformat()
    keep = cfg.sent_dir / (NOW.date() - timedelta(days=29)).isoformat()
    for d in (old, keep):
        d.mkdir()
        (d / "x.json").write_text("{}")
    run_once(cfg, FakeClient(), NOW)
    assert not old.exists() and keep.exists()


def test_file_from_future_mtime_not_skipped(cfg):
    # 时钟往回跳后，之前写的文件 mtime 比 now 还晚，不能被当成"刚写的"一直跳过
    put_outbox(cfg, "a.json", record(), age_s=-86400)
    result = run_once(cfg, FakeClient(), NOW)
    assert result["skipped"] == 0 and result["sent"] == 1


def test_unexpected_sensor_error_does_not_block_queue(cfg, monkeypatch):
    import uploader
    def boom(*a):
        raise RuntimeError("weird log")
    monkeypatch.setattr(uploader, "read_temp_rh", boom)
    put_outbox(cfg, "a.json", record())
    client = FakeClient()
    result = run_once(cfg, client, NOW)
    assert result["sent"] == 1
    assert "temp_c" not in client.puts()[0][1]


def test_sent_name_collision_keeps_both(cfg):
    put_outbox(cfg, "a.json", record(hms="120003"))
    run_once(cfg, FakeClient(), NOW)
    put_outbox(cfg, "a.json", record(hms="123003"))
    run_once(cfg, FakeClient(), NOW)
    assert len(list((cfg.sent_dir / "2026-07-15").iterdir())) == 2


# ---------- 重复时间戳（时钟故障）----------

R = "readings/MSU1/2026-07-15/120003"


def test_record_id_added_and_uploaded(cfg):
    put_outbox(cfg, "a.json", record())
    client = FakeClient()
    run_once(cfg, client, NOW)
    rid = client.puts()[0][1]["record_id"]
    assert isinstance(rid, str) and len(rid) == 32
    saved = json.loads((cfg.sent_dir / "2026-07-15" / "a.json").read_text())
    assert saved["record_id"] == rid


def test_duplicate_timestamp_gets_suffix(cfg):
    put_outbox(cfg, "a.json", record())
    client = FakeClient(existing={R: {"record_id": "other", "count_male_swd": 5}})
    run_once(cfg, client, NOW)
    assert [p for p, _ in client.puts()] == [R + "_1"]


def test_suffix_skips_taken_keys(cfg):
    put_outbox(cfg, "a.json", record())
    client = FakeClient(existing={R: {"record_id": "x"}, R + "_1": {"record_id": "y"}})
    run_once(cfg, client, NOW)
    assert [p for p, _ in client.puts()] == [R + "_2"]


def test_retry_after_network_error_reuses_same_key(cfg):
    """第一次 PUT 其实成功了但没收到响应：重试时认出是自己，覆盖同一个键，不生成后缀。"""
    put_outbox(cfg, "a.json", record())
    first = FakeClient(fail_on={1: NetworkError("timeout")})
    run_once(cfg, first, NOW)
    (path, data), = first.puts()
    assert path == R and (cfg.outbox_dir / "a.json").exists()

    second = FakeClient(existing={R: data})
    run_once(cfg, second, NOW)
    assert [(p, d["record_id"]) for p, d in second.puts()] == [(R, data["record_id"])]


def test_existing_record_id_kept(cfg):
    put_outbox(cfg, "a.json", record(record_id="fromcapture"))
    client = FakeClient(existing={R: {"record_id": "fromcapture"}})
    run_once(cfg, client, NOW)
    assert [(p, d["record_id"]) for p, d in client.puts()] == [(R, "fromcapture")]


def test_lookup_network_error_stops(cfg):
    put_outbox(cfg, "a.json", record())
    client = FakeClient(fail_get=NetworkError("down"))
    result = run_once(cfg, client, NOW)
    assert result["stopped"] == "network" and client.puts() == []
    assert (cfg.outbox_dir / "a.json").exists()
