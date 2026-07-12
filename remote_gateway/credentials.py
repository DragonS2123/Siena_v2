"""Encrypted local storage for the Home Gateway credentials.

The gateway token is protected with Windows DPAPI (CryptProtectData /
CryptUnprotectData via ctypes — no new dependency), bound to the current
Windows user. Only the encrypted blob ever touches disk; the plaintext token
exists in memory only for the moments it's actually needed (building the
Authorization header right before a connect).

File location (outside the repo on purpose):
    %LOCALAPPDATA%\\Siena_v2\\remote_gateway\\credentials.json
containing: schema_version, gateway_id (plain — it's an identifier, not a
secret), encrypted_token (base64 of the DPAPI blob), created_at, updated_at.

The protector is an injectable interface so unit tests run on a fake
(tests never touch DPAPI or real credentials).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Protocol

SCHEMA_VERSION = 1


def default_credentials_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Siena_v2" / "remote_gateway" / "credentials.json"


class TokenProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class DpapiProtector:
    """Windows DPAPI, current-user scope, via ctypes. CRYPTPROTECT_UI_FORBIDDEN
    keeps it non-interactive (this runs inside a backend service)."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def __init__(self) -> None:
        if sys.platform != "win32":  # pragma: no cover — project is Windows-only
            raise RuntimeError("DpapiProtector requires Windows (DPAPI)")

    def _blob_roundtrip(self, data: bytes, *, protect: bool) -> bytes:
        import ctypes
        import ctypes.wintypes as wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()

        func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = func(
            ctypes.byref(blob_in),
            None,  # description
            None,  # optional entropy
            None,  # reserved
            None,  # prompt struct
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed (winerror {ctypes.GetLastError()})")

        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def protect(self, plaintext: bytes) -> bytes:
        return self._blob_roundtrip(plaintext, protect=True)

    def unprotect(self, blob: bytes) -> bytes:
        return self._blob_roundtrip(blob, protect=False)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CredentialsStore:
    def __init__(self, path: Path | None = None, protector: TokenProtector | None = None):
        self._path = path or default_credentials_path()
        self._protector = protector or DpapiProtector()

    @property
    def path(self) -> Path:
        return self._path

    def _read_raw(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def is_configured(self) -> bool:
        data = self._read_raw()
        return bool(data and data.get("gateway_id") and data.get("encrypted_token"))

    def gateway_id(self) -> str | None:
        """Plain identifier — safe to read without touching the token."""
        data = self._read_raw()
        return data.get("gateway_id") if data else None

    def save(self, gateway_id: str, token: str) -> None:
        """Encrypts the token and writes the credentials file atomically.
        The plaintext token is only held for the duration of this call."""
        encrypted = self._protector.protect(token.encode("utf-8"))
        existing = self._read_raw() or {}
        payload = {
            "schema_version": SCHEMA_VERSION,
            "gateway_id": gateway_id,
            "encrypted_token": base64.b64encode(encrypted).decode("ascii"),
            "created_at": existing.get("created_at") or _now_iso(),
            "updated_at": _now_iso(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def decrypt_token(self) -> str | None:
        """Decrypts the token on demand — call right before connecting and
        do not keep extra copies around. Returns None when not configured
        or when the blob can't be decrypted (e.g. different Windows user)."""
        data = self._read_raw()
        if not data or not data.get("encrypted_token"):
            return None
        try:
            blob = base64.b64decode(data["encrypted_token"])
            return self._protector.unprotect(blob).decode("utf-8")
        except Exception:
            return None

    def clear(self) -> bool:
        """Deletes the local encrypted credentials file. Does NOT revoke the
        gateway on the Relay — the server-side registration stays valid
        until revoked there."""
        if self._path.exists():
            self._path.unlink()
            return True
        return False
