"""Central username/password authentication and scoped R2 credential retrieval.

Users never receive permanent Cloudflare credentials. Refresh tokens are
protected by the Windows user credential store, not by the configuration JSON.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")
SERVICE = "CodeCrafter.AutoBackup.Session"
LOGIN_DOMAIN = "users.autobackup.example.com"


class AuthError(Exception):
    pass


def public_config() -> dict:
    location = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = location / "config" / "client.json"
    if not path.exists():
        raise AuthError("Client configuration is missing. Ask your backup administrator for a configured installer.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise AuthError("Client configuration cannot be read.") from exc
    url = str(value.get("supabase_url", "")).rstrip("/")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or
            not parsed.hostname.endswith(".supabase.co") or
            parsed.path or parsed.query or parsed.fragment or parsed.username):
        raise AuthError("Installer requires a valid HTTPS Supabase project URL.")
    key = value.get("supabase_publishable_key", "")
    if not isinstance(key, str) or len(key) < 20 or "REPLACE" in key:
        raise AuthError("The installer has not been configured with a Supabase publishable key.")
    return {"url": url, "key": key}


def request(url: str, body: dict, headers: dict | None = None, timeout: int = 45) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = Request(url, data=payload,
                  headers={"Content-Type": "application/json", **(headers or {})},
                  method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            value = json.loads(response.read(1000000))
            if not isinstance(value, dict):
                raise AuthError("Unexpected authentication response.")
            return value
    except HTTPError as exc:
        # Do not surface Supabase debug responses containing unexpected secrets.
        if exc.code in (400, 401, 403, 422):
            raise AuthError("Sign-in failed or the session is no longer authorized.") from exc
        if exc.code == 429:
            raise AuthError("Too many requests. Try signing in later.") from exc
        raise AuthError(f"Authentication service returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise AuthError("Could not connect to the authentication service.") from exc


def _auth_endpoint(cfg: dict, grant_type: str) -> str:
    return f"{cfg['url']}/auth/v1/token?grant_type={grant_type}"


def _headers(cfg: dict) -> dict:
    return {"apikey": cfg["key"]}


def _broker(cfg: dict, token: str, action: str, **data) -> dict:
    return request(f"{cfg['url']}/functions/v1/r2-broker",
                   {"action": action, **data},
                   {**_headers(cfg), "Authorization": "Bearer " + token}, timeout=90)


def _stored_key(cfg: dict, user_id: str) -> str:
    return f"{cfg['url']}/{user_id}"


def sign_in(config: dict, username: str, password: str) -> dict:
    import keyring
    public = public_config()
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username) or not password:
        raise AuthError("Enter a valid username and password.")
    session = request(
        _auth_endpoint(public, "password"),
        {"email": f"{username}@{LOGIN_DOMAIN}", "password": password},
        _headers(public))
    user = session.get("user", {})
    user_id = user.get("id", "")
    token = session.get("access_token")
    refresh = session.get("refresh_token")
    if not isinstance(user_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", user_id) or not token or not refresh:
        raise AuthError("Authentication returned an incomplete session.")
    if user.get("app_metadata", {}).get("autobackup_enabled") is not True:
        raise AuthError("This account is not enabled for AutoBackup.")
    if config.get("user_id") != user_id:
        device_id = str(uuid.uuid4())
    else:
        device_id = config.get("device_id") or str(uuid.uuid4())
    device_name = (os.getenv("COMPUTERNAME") or "Windows computer")[:80]
    _broker(public, token, "register_device", device_id=device_id, device_name=device_name)
    keyring.set_password(SERVICE, _stored_key(public, user_id), refresh)
    config["username"] = username
    config["user_id"] = user_id
    config["device_id"] = device_id
    return config


def access_token(config: dict) -> str:
    """Refresh every operation; rotated refresh token is committed immediately."""
    import keyring
    public = public_config()
    user_id = config.get("user_id")
    if not user_id:
        raise AuthError("Please sign in to AutoBackup.")
    key = _stored_key(public, user_id)
    refresh = keyring.get_password(SERVICE, key)
    if not refresh:
        raise AuthError("Session is missing. Sign in again.")
    result = request(_auth_endpoint(public, "refresh_token"),
                     {"refresh_token": refresh}, _headers(public))
    token = result.get("access_token")
    new_refresh = result.get("refresh_token")
    if not token or not new_refresh or result.get("user", {}).get("id") != user_id:
        raise AuthError("Session was not renewed. Sign in again.")
    keyring.set_password(SERVICE, key, new_refresh)
    return token


def list_devices(config: dict) -> list[dict]:
    result = _broker(public_config(), access_token(config), "list_devices")
    devices = result.get("devices", [])
    if not isinstance(devices, list):
        raise AuthError("Invalid device list.")
    return devices


def grant(config: dict, device_id: str | None = None) -> dict:
    device_id = device_id or config.get("device_id")
    if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", device_id):
        raise AuthError("Invalid device ID.")
    value = _broker(public_config(), access_token(config), "grant", device_id=device_id)
    required = ("repository", "access_key_id", "secret_access_key",
                "session_token", "password")
    if not all(isinstance(value.get(field), str) and value[field] for field in required):
        raise AuthError("The backup service returned incomplete R2 credentials.")
    if not value["repository"].startswith("s3:https://"):
        raise AuthError("Invalid storage repository URL.")
    return value


def sign_out(config: dict) -> dict:
    import keyring
    if config.get("user_id"):
        public = public_config()
        try:
            keyring.delete_password(SERVICE, _stored_key(public, config["user_id"]))
        except keyring.errors.PasswordDeleteError:
            pass
    config["user_id"] = ""
    config["username"] = ""
    config["device_id"] = ""
    return config
