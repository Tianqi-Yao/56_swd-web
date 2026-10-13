"""在本地 Firebase Emulator 上跑真实的上传流程。用 scripts/e2e.sh 运行。"""
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
import requests

from fbclient import AuthError, FirebaseClient
from uploader import run_once

pytestmark = [
    pytest.mark.emulator,
    pytest.mark.skipif(not os.environ.get("FIREBASE_DATABASE_EMULATOR_HOST"),
                       reason="needs Firebase emulators (scripts/e2e.sh)"),
]

HOST = "127.0.0.1"
NS = "demo-swd-web-default-rtdb"
OWNER = {"Authorization": "Bearer owner"}
NOW = datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone(timedelta(hours=-5)))


def db_url(path):
    return f"http://{HOST}:9000/{path}.json?ns={NS}"


def owner_get(path):
    r = requests.get(db_url(path), headers=OWNER, timeout=10)
    r.raise_for_status()
    return r.json()


@pytest.fixture
def device_cfg(make_cfg, tmp_path):
    email = f"msu1-{os.getpid()}-{tmp_path.name}@devices.test"
    r = requests.post(
        f"http://{HOST}:9099/identitytoolkit.googleapis.com/v1/accounts:signUp?key=fake",
        json={"email": email, "password": "secret1", "returnSecureToken": True}, timeout=10)
    r.raise_for_status()
    uid = r.json()["localId"]
    requests.delete(db_url(""), headers=OWNER, timeout=10).raise_for_status()
    requests.put(db_url(f"devices/{uid}"), headers=OWNER, json="MSU1", timeout=10).raise_for_status()
    return make_cfg(email=email, password="secret1", emulator_host=HOST,
                    db_url=f"https://{NS}.firebaseio.com", bucket="demo-swd-web.appspot.com")


def put_outbox(cfg, hms, **kw):
    rec = {"device_id": "MSU1", "acquired_at": f"2026-07-15T{hms[:2]}:{hms[2:4]}:{hms[4:]}-05:00",
           "count_male_swd": 2, "model_version": "test-model", "infer_ms": 800, **kw}
    p = cfg.outbox_dir / f"MSU1_20260715_{hms}.json"
    p.write_text(json.dumps(rec))
    t = NOW.timestamp() - 120
    os.utime(p, (t, t))


def test_e2e_upload_then_offline_then_recover(device_cfg):
    cfg = device_cfg
    (cfg.thumbs_dir / "20260715_120003.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 2000)
    put_outbox(cfg, "113003")
    put_outbox(cfg, "120003", thumb="20260715_120003.jpg",
               boxes=[[1.0, 2.0, 3.0, 4.0, 0.9]], img_w=9248, img_h=6944)
    put_outbox(cfg, "123003")

    result = run_once(cfg, FirebaseClient(cfg), NOW)
    assert result == {"sent": 3, "bad": 0, "skipped": 0, "stopped": None}

    readings = owner_get("readings/MSU1/2026-07-15")
    assert sorted(readings) == ["113003", "120003", "123003"]
    assert isinstance(readings["120003"]["serverTime"], int)
    assert "thumb" not in readings["120003"]
    thumb = owner_get("thumbs/MSU1/2026-07-15")
    assert thumb["storagePath"] == "thumbs/MSU1/2026-07-15.jpg" and thumb["imgW"] == 9248
    hb = owner_get("heartbeat/MSU1")
    assert isinstance(hb["serverTime"], int)
    assert hb["lastReadingAt"] == "2026-07-15T12:30:03-05:00"
    obj = requests.get(
        f"http://{HOST}:9199/v0/b/{cfg.bucket}/o/{quote('thumbs/MSU1/2026-07-15.jpg', safe='')}",
        headers=OWNER, timeout=10)
    assert obj.status_code == 200 and obj.json()["contentType"] == "image/jpeg"

    # 断网：主机名解析不了，文件留在 outbox
    put_outbox(cfg, "130003")
    offline = replace(cfg, emulator_host="offline.invalid")
    result = run_once(offline, FirebaseClient(offline), NOW)
    assert result["stopped"] == "network"
    assert (cfg.outbox_dir / "MSU1_20260715_130003.json").exists()

    # 恢复后补传
    result = run_once(cfg, FirebaseClient(cfg), NOW)
    assert result["sent"] == 1
    assert "130003" in owner_get("readings/MSU1/2026-07-15")
    assert list(cfg.outbox_dir.iterdir()) == []


def test_e2e_rules_block_other_device(device_cfg):
    """确认 Emulator 对 uploader 用的 namespace 真的执行了 database.rules.json。"""
    with pytest.raises(AuthError):
        FirebaseClient(device_cfg).put("readings/HSH1/2026-07-15/120003", {
            "device_id": "HSH1", "acquired_at": "2026-07-15T12:00:03-05:00", "count_male_swd": 1})


def test_e2e_duplicate_timestamp_keeps_both(device_cfg):
    """时钟故障：两条不同的记录落在同一时间点，第二条存到 _1；同一条重传仍覆盖原键。"""
    cfg = device_cfg
    put_outbox(cfg, "120003", count_male_swd=2)
    run_once(cfg, FirebaseClient(cfg), NOW)
    first = owner_get("readings/MSU1/2026-07-15/120003")

    put_outbox(cfg, "120003", count_male_swd=7)
    run_once(cfg, FirebaseClient(cfg), NOW)

    readings = owner_get("readings/MSU1/2026-07-15")
    assert sorted(readings) == ["120003", "120003_1"]
    assert readings["120003"]["record_id"] == first["record_id"]
    assert readings["120003"]["count_male_swd"] == 2
    assert readings["120003_1"]["count_male_swd"] == 7
