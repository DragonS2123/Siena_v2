"""HTTPS client for Relay's temporary attachment broker (G:\\SienaRelay\\app\\
attachments\\routes.py — read-only reference). Used by the remote chat/tts
bridge to download phone-uploaded images and upload synthesized audio for a
specific Device. Never trusts a client-declared filename/Content-Type on the
way out; never keeps downloaded bytes longer than one processing pass (the
caller is responsible for deleting temp files and calling delete_attachment()
once it has consumed the content).

Uses `requests` (already a project dependency) wrapped in asyncio.to_thread —
consistent with how every other blocking call in this backend is bridged into
the async event loop (see api/server.py, ocr/vision/translator services).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests

DEFAULT_TIMEOUT_SECONDS = 30
_WS_TO_HTTP_SCHEME = {"ws": "http", "wss": "https"}


class AttachmentClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def relay_http_base_url(relay_ws_url: str) -> str:
    """wss://relay.sienaai.ru -> https://relay.sienaai.ru ; ws://127.0.0.1:PORT
    -> http://127.0.0.1:PORT (used by local fake-Relay integration tests)."""
    parts = urlsplit(relay_ws_url)
    http_scheme = _WS_TO_HTTP_SCHEME.get(parts.scheme)
    if http_scheme is None:
        raise ValueError(f"unsupported relay URL scheme: {parts.scheme!r}")
    return urlunsplit((http_scheme, parts.netloc, "", "", "")).rstrip("/")


class AttachmentClient:
    def __init__(
        self,
        *,
        http_base_url_provider: Callable[[], str],
        credentials_provider: Callable[[], tuple[str, str] | None],
        verify_tls: bool = True,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._http_base_url_provider = http_base_url_provider
        self._credentials_provider = credentials_provider
        self._verify_tls = verify_tls
        self._timeout_seconds = timeout_seconds

    def _gateway_token(self) -> str:
        credentials = self._credentials_provider()
        if credentials is None:
            raise AttachmentClientError("gateway credentials are not configured")
        _gateway_id, token = credentials
        return token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._gateway_token()}"}

    async def download_device_attachment(self, attachment_id: str) -> bytes:
        """Downloads an attachment the Device uploaded for us. Only the
        Gateway that owns the receiving Device can succeed here (Relay
        enforces direction-based ownership server-side)."""
        base_url = self._http_base_url_provider()
        headers = self._auth_headers()

        def _do_request() -> bytes:
            response = requests.get(
                f"{base_url}/v1/attachments/{attachment_id}",
                headers=headers,
                timeout=self._timeout_seconds,
                verify=self._verify_tls,
            )
            if response.status_code != 200:
                raise AttachmentClientError(
                    f"attachment download failed ({response.status_code})",
                    status_code=response.status_code,
                )
            return response.content

        return await asyncio.to_thread(_do_request)

    async def upload_gateway_attachment(
        self, device_id: str, *, data: bytes, content_type: str,
    ) -> dict[str, Any]:
        """Uploads Gateway-synthesized audio (TTS) for a specific Device."""
        base_url = self._http_base_url_provider()
        headers = {**self._auth_headers(), "Content-Type": content_type}

        def _do_request() -> dict[str, Any]:
            response = requests.post(
                f"{base_url}/v1/attachments/gateway/{device_id}",
                headers=headers,
                data=data,
                timeout=self._timeout_seconds,
                verify=self._verify_tls,
            )
            if response.status_code != 200:
                raise AttachmentClientError(
                    f"attachment upload failed ({response.status_code})",
                    status_code=response.status_code,
                )
            return response.json()

        return await asyncio.to_thread(_do_request)

    async def delete_attachment(self, attachment_id: str) -> None:
        """Best-effort cleanup — callers should not fail the whole operation
        just because the delete call itself failed (Relay's own TTL sweep is
        the safety net)."""
        base_url = self._http_base_url_provider()
        headers = self._auth_headers()

        def _do_request() -> None:
            try:
                requests.delete(
                    f"{base_url}/v1/attachments/{attachment_id}",
                    headers=headers,
                    timeout=self._timeout_seconds,
                    verify=self._verify_tls,
                )
            except requests.RequestException:
                pass

        await asyncio.to_thread(_do_request)
