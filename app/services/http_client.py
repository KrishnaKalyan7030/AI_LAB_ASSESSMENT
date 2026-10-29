"""Tiny JSON-over-HTTPS client built on the Python standard library.

Used by the Groq and Gemini services so no extra package is required.
* Uses certifi's CA bundle when available (python.org builds on macOS ship
  without system certificates, which otherwise causes SSL errors).
* Sends a descriptive User-Agent (some API gateways reject the default
  "Python-urllib" agent).
* Returns (status_code, parsed_json_or_None) for HTTP responses, including
  error responses; raises NetworkError for timeouts / connection problems.
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any

from .claude_service import NetworkError

USER_AGENT = "AI-Lab-Assessment/2.0 (Flask; +python-urllib)"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi is in requirements.txt
        return ssl.create_default_context()


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except (ValueError, UnicodeDecodeError):
        return None


def post_json(url: str, headers: dict, body: dict, timeout: float):
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **headers}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read())
    except (socket.timeout, TimeoutError):
        raise NetworkError("The AI request timed out. Please retry.") from None
    except ssl.SSLError:
        raise NetworkError(
            "Secure connection to the AI service failed (SSL). Run: pip install --upgrade certifi"
        ) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise NetworkError("The AI request timed out. Please retry.") from None
        if isinstance(reason, ssl.SSLError):
            raise NetworkError(
                "Secure connection to the AI service failed (SSL). Run: pip install --upgrade certifi"
            ) from None
        raise NetworkError(
            "Could not reach the AI service. Check your internet connection and retry.") from None
