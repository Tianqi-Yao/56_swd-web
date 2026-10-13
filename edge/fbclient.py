"""Firebase REST 客户端：Email/Password 登录、token 刷新、RTDB 写入、Storage 上传。

只依赖 requests。所有错误归成三类，由调用方决定怎么处理：
  NetworkError  连不上、超时、5xx           → 整轮停下，下次重试
  AuthError     401/403、登录失败           → 整轮停下，文件不动
  ClientError   其他 4xx                    → 这条记录有问题
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional
from urllib.parse import quote, urlparse

import requests

from common import Config

TIMEOUT = 30
REFRESH_MARGIN = 300  # token 剩不到 5 分钟就刷新


class NetworkError(Exception):
    pass


class AuthError(Exception):
    pass


class ClientError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class FirebaseClient:
    def __init__(self, cfg: Config, session: Optional[requests.Session] = None,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.clock = clock
        self.token_path = cfg.state_dir / "token.json"
        self._token: Optional[dict] = None

        host = cfg.emulator_host
        if host:
            self.auth_base = f"http://{host}:9099/identitytoolkit.googleapis.com/v1"
            self.refresh_url = f"http://{host}:9099/securetoken.googleapis.com/v1/token"
            self.db_base = f"http://{host}:9000"
            # Emulator 按 namespace 区分数据库，规则加载在 <project>-default-rtdb 上，
            # 所以 namespace 取 db_url 主机名的第一段，跟云端实例名一致
            self.db_ns = (urlparse(cfg.db_url).hostname or "").split(".")[0]
            self.storage_base = f"http://{host}:9199/v0/b/{cfg.bucket}/o"
        else:
            self.auth_base = "https://identitytoolkit.googleapis.com/v1"
            self.refresh_url = "https://securetoken.googleapis.com/v1/token"
            self.db_base = cfg.db_url
            self.storage_base = f"https://firebasestorage.googleapis.com/v0/b/{cfg.bucket}/o"

    # ---------- 公共接口 ----------

    def put(self, path: str, data: dict) -> None:
        self._db("PUT", path, data)

    def patch(self, path: str, data: dict) -> None:
        self._db("PATCH", path, data)

    def upload_jpeg(self, storage_path: str, data: bytes) -> None:
        token = self._id_token()
        # Storage 要求对象名里的 / 也编码成 %2F
        url = f"{self.storage_base}?name={quote(storage_path, safe='')}"
        self._request(
            "POST", url,
            headers={"Authorization": f"Firebase {token}", "Content-Type": "image/jpeg"},
            data=data,
        )

    # ---------- 内部 ----------

    def _db(self, method: str, path: str, data: dict) -> None:
        params = {"auth": self._id_token()}
        if self.cfg.emulator_host:
            params = {"ns": self.db_ns, **params}
        url = f"{self.db_base}/{path.strip('/')}.json"
        self._request(method, url, params=params, data=json.dumps(data))

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        try:
            r = self.session.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise NetworkError(str(e)) from e
        if r.status_code >= 500:
            raise NetworkError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code in (401, 403):
            raise AuthError(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code >= 400:
            raise ClientError(r.status_code, r.text[:200])
        return r

    def _id_token(self) -> str:
        if self._token is None:
            self._token = self._load_cached()
        now = self.clock()
        if self._token is None:
            self._sign_in()
        elif now >= self._token["expires_at"] - REFRESH_MARGIN:
            try:
                self._refresh()
            except (AuthError, ClientError):
                self._sign_in()
        return self._token["id_token"]

    def _sign_in(self) -> None:
        try:
            r = self._request(
                "POST", f"{self.auth_base}/accounts:signInWithPassword",
                params={"key": self.cfg.api_key},
                json={"email": self.cfg.email, "password": self.cfg.password,
                      "returnSecureToken": True},
            )
        except ClientError as e:
            # 密码错、账号不存在：属于配置问题，不能当成坏记录
            raise AuthError(f"sign-in failed: {e}") from e
        body = r.json()
        self._save(body["idToken"], body["refreshToken"], body["expiresIn"])

    def _refresh(self) -> None:
        r = self._request(
            "POST", self.refresh_url,
            params={"key": self.cfg.api_key},
            data={"grant_type": "refresh_token", "refresh_token": self._token["refresh_token"]},
        )
        body = r.json()
        self._save(body["id_token"], body["refresh_token"], body["expires_in"])

    def _save(self, id_token: str, refresh_token: str, expires_in) -> None:
        self._token = {
            "id_token": id_token,
            "refresh_token": refresh_token,
            "expires_at": self.clock() + int(expires_in),
        }
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.token_path.with_name(self.token_path.name + ".tmp")
        tmp.write_text(json.dumps(self._token))
        os.replace(tmp, self.token_path)

    def _load_cached(self) -> Optional[dict]:
        try:
            tok = json.loads(self.token_path.read_text())
            if {"id_token", "refresh_token", "expires_at"} <= tok.keys():
                return tok
        except (OSError, ValueError, AttributeError):
            pass
        return None
