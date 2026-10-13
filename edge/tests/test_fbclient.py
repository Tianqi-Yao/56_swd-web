import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from fbclient import AuthError, ClientError, FirebaseClient, NetworkError

SIGNIN = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH = "https://securetoken.googleapis.com/v1/token"
DB = "https://proj-default-rtdb.firebaseio.com"
READING = f"{DB}/readings/MSU1/2026-07-15/120003.json"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def add_signin(token="ID1", expires="3600"):
    responses.add(responses.POST, SIGNIN,
                  json={"idToken": token, "refreshToken": "R1", "expiresIn": expires})


def qs(call):
    return parse_qs(urlparse(call.request.url).query)


@responses.activate
def test_first_request_signs_in_and_caches_token(cfg):
    add_signin()
    responses.add(responses.PUT, READING, json={})
    FirebaseClient(cfg, clock=Clock()).put("readings/MSU1/2026-07-15/120003", {"a": 1})
    signin, put = responses.calls
    assert json.loads(signin.request.body) == {
        "email": "msu1@devices.test", "password": "pw", "returnSecureToken": True}
    assert qs(signin)["key"] == ["KEY"]
    assert qs(put)["auth"] == ["ID1"]
    assert json.loads(put.request.body) == {"a": 1}
    cached = json.loads((cfg.state_dir / "token.json").read_text())
    assert cached["id_token"] == "ID1" and cached["refresh_token"] == "R1"
    assert cached["expires_at"] == 1_000_000.0 + 3600


@responses.activate
def test_reuses_cached_token(cfg):
    clock = Clock()
    add_signin()
    responses.add(responses.PUT, READING, json={})
    FirebaseClient(cfg, clock=clock).put("readings/MSU1/2026-07-15/120003", {})
    FirebaseClient(cfg, clock=clock).put("readings/MSU1/2026-07-15/120003", {})
    assert [c.request.method for c in responses.calls] == ["POST", "PUT", "PUT"]


@responses.activate
def test_refreshes_when_expiring(cfg):
    clock = Clock()
    add_signin()
    responses.add(responses.POST, REFRESH,
                  json={"id_token": "ID2", "refresh_token": "R2", "expires_in": "3600"})
    responses.add(responses.PUT, READING, json={})
    client = FirebaseClient(cfg, clock=clock)
    client.put("readings/MSU1/2026-07-15/120003", {})
    clock.t += 3600 - 60
    client.put("readings/MSU1/2026-07-15/120003", {})
    refresh = responses.calls[2]
    assert refresh.request.url.startswith(REFRESH)
    assert parse_qs(refresh.request.body) == {"grant_type": ["refresh_token"], "refresh_token": ["R1"]}
    assert qs(responses.calls[3])["auth"] == ["ID2"]


@responses.activate
def test_failed_refresh_falls_back_to_signin(cfg):
    clock = Clock()
    add_signin()
    responses.add(responses.POST, REFRESH, status=400, json={"error": {"message": "TOKEN_EXPIRED"}})
    add_signin(token="ID3")
    responses.add(responses.PUT, READING, json={})
    client = FirebaseClient(cfg, clock=clock)
    client.put("readings/MSU1/2026-07-15/120003", {})
    clock.t += 7200
    client.put("readings/MSU1/2026-07-15/120003", {})
    assert qs(responses.calls[-1])["auth"] == ["ID3"]


@responses.activate
def test_bad_password_raises_auth_error(cfg):
    responses.add(responses.POST, SIGNIN, status=400,
                  json={"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
    with pytest.raises(AuthError):
        FirebaseClient(cfg, clock=Clock()).put("x", {})


@responses.activate
def test_put_5xx_raises_network_error(cfg):
    add_signin()
    responses.add(responses.PUT, READING, status=503)
    with pytest.raises(NetworkError):
        FirebaseClient(cfg, clock=Clock()).put("readings/MSU1/2026-07-15/120003", {})


@responses.activate
def test_connection_error_raises_network_error(cfg):
    add_signin()
    responses.add(responses.PUT, READING, body=requests.ConnectionError("down"))
    with pytest.raises(NetworkError):
        FirebaseClient(cfg, clock=Clock()).put("readings/MSU1/2026-07-15/120003", {})


@responses.activate
def test_signin_connection_error_raises_network_error(cfg):
    responses.add(responses.POST, SIGNIN, body=requests.ConnectionError("down"))
    with pytest.raises(NetworkError):
        FirebaseClient(cfg, clock=Clock()).put("x", {})


@responses.activate
def test_put_401_raises_auth_error(cfg):
    add_signin()
    responses.add(responses.PUT, READING, status=401, json={"error": "Permission denied"})
    with pytest.raises(AuthError):
        FirebaseClient(cfg, clock=Clock()).put("readings/MSU1/2026-07-15/120003", {})


@responses.activate
def test_put_400_raises_client_error(cfg):
    add_signin()
    responses.add(responses.PUT, READING, status=400, json={"error": "Invalid data"})
    with pytest.raises(ClientError) as e:
        FirebaseClient(cfg, clock=Clock()).put("readings/MSU1/2026-07-15/120003", {})
    assert e.value.status == 400


@responses.activate
def test_patch_uses_patch_method(cfg):
    add_signin()
    responses.add(responses.PATCH, f"{DB}/heartbeat/MSU1.json", json={})
    FirebaseClient(cfg, clock=Clock()).patch("heartbeat/MSU1", {"x": 1})
    assert responses.calls[1].request.method == "PATCH"


@responses.activate
def test_upload_jpeg_url_and_headers(cfg):
    add_signin()
    responses.add(responses.POST, "https://firebasestorage.googleapis.com/v0/b/proj.appspot.com/o", json={})
    FirebaseClient(cfg, clock=Clock()).upload_jpeg("thumbs/MSU1/2026-07-15.jpg", b"\xff\xd8jpeg")
    up = responses.calls[1].request
    assert "name=thumbs%2FMSU1%2F2026-07-15.jpg" in up.url
    assert up.headers["Authorization"] == "Firebase ID1"
    assert up.headers["Content-Type"] == "image/jpeg"
    assert up.body == b"\xff\xd8jpeg"


@responses.activate
def test_emulator_endpoints(make_cfg):
    # Emulator 把 database.rules.json 加载到 <project>-default-rtdb，namespace 要和 db_url 一致
    cfg = make_cfg(emulator_host="127.0.0.1", db_url="https://demo-swd-web-default-rtdb.firebaseio.com")
    responses.add(responses.POST,
                  "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
                  json={"idToken": "E1", "refreshToken": "R", "expiresIn": "3600"})
    responses.add(responses.PUT, "http://127.0.0.1:9000/heartbeat/MSU1.json", json={})
    responses.add(responses.POST, "http://127.0.0.1:9199/v0/b/proj.appspot.com/o", json={})
    client = FirebaseClient(cfg, clock=Clock())
    client.put("heartbeat/MSU1", {})
    client.upload_jpeg("thumbs/MSU1/2026-07-15.jpg", b"x")
    assert qs(responses.calls[1]) == {"ns": ["demo-swd-web-default-rtdb"], "auth": ["E1"]}
