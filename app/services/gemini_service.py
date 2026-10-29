"""Google Gemini API service (alternative AI provider with a free tier).

Implements the same `request_json(...)` interface as ClaudeService, so every
feature (viva grading, programming help, code review, question generation,
summaries) works unchanged with AI_PROVIDER=gemini.

* Uses the documented REST endpoint `models/{model}:generateContent` with
  JSON output (`responseMimeType: application/json`). The JSON schema is given
  in the prompt and the result is still strictly validated by validators.py.
* Uses only the Python standard library (urllib) - no extra package needed.
* The key is sent in the `x-goog-api-key` header, never in the URL, so it
  cannot appear in URLs or access logs.
* The model comes from GEMINI_MODEL; nothing is hard-coded here.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

from .claude_service import (AIResult, APIUnavailableError, AuthenticationFailedError,
                             BadRequestError, ClaudeServiceError, InvalidAIResponseError,
                             MissingCredentialsError, ModelNotFoundError, NetworkError,
                             RateLimitedError, _parse_text_json)
from .http_client import post_json

log = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# (status_code, parsed_json_or_None) = transport(url, headers, body_dict, timeout)
Transport = Callable[[str, dict, dict, float], "tuple[int, Any]"]


urllib_transport = post_json  # shared standard-library HTTP client


def translate_status(status: int, payload: Any) -> ClaudeServiceError:
    """Map a Gemini HTTP error to a safe, user-facing error."""
    error = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
    message = str(error.get("message", ""))
    reasons = " ".join(str(d.get("reason", "")) for d in error.get("details", []) if isinstance(d, dict))
    if "API_KEY_INVALID" in reasons or "API key not valid" in message or status in (401, 403):
        return AuthenticationFailedError(
            "Google rejected the Gemini API key (or it lacks access). Check GEMINI_API_KEY in .env.")
    if status == 404:
        return ModelNotFoundError(
            "The Gemini model was not found. Check GEMINI_MODEL in .env against Google's model list.")
    if status == 429:
        return RateLimitedError(
            "Gemini rate limit or free-tier quota reached. Wait a minute (or until the daily quota "
            "resets) and retry.")
    if status >= 500:
        return APIUnavailableError("The Gemini API is temporarily unavailable. Please retry.")
    if status == 400:
        return BadRequestError("The Gemini API could not process this request.")
    return ClaudeServiceError("The Gemini API returned an error. Please try again.")


class GeminiService:
    provider = "gemini"
    display_name = "Gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0, max_retries: int = 1,
                 max_tokens: int = 8192, transport: Optional[Transport] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.max_tokens = max_tokens
        self._transport = transport or urllib_transport
        self._sleep = sleep

    @classmethod
    def from_config(cls, config) -> "GeminiService":
        return cls(
            api_key=config.get("GEMINI_API_KEY", ""),
            model=config.get("GEMINI_MODEL", ""),
            timeout=float(config.get("GEMINI_TIMEOUT_SECONDS", 60.0)),
            max_retries=int(config.get("GEMINI_MAX_RETRIES", 1)),
            max_tokens=int(config.get("GEMINI_MAX_OUTPUT_TOKENS", 8192)),
            transport=config.get("GEMINI_TRANSPORT"),
        )

    def __repr__(self) -> str:
        return f"GeminiService(model={self.model!r}, configured={self.is_configured})"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    def request_json(self, *, system: str, user_content: str, tool_name: str,
                     tool_description: str, schema: dict, max_tokens: Optional[int] = None) -> AIResult:
        if not self._api_key:
            raise MissingCredentialsError(
                "AI features are not configured. Set GEMINI_API_KEY and GEMINI_MODEL in the .env "
                "file and restart the server.")
        if not self.model:
            raise MissingCredentialsError("GEMINI_MODEL is not set. Add a current Gemini model name to .env.")

        instructions = (
            f"{system}\n\nTask: {tool_description}\n"
            "Respond with ONE JSON object only (no markdown, no extra text) that matches this "
            f"JSON Schema. Ignore any mention of tools.\n{json.dumps(schema)}"
        )
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": user_content}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": max(self.max_tokens, max_tokens or 0),
                "temperature": 0.2,
            },
        }
        url = f"{API_BASE}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._api_key}

        attempt = 0
        while True:
            status, payload = self._transport(url, headers, body, self.timeout)
            if status == 200:
                break
            err = translate_status(status, payload)
            log.warning("Gemini API call failed: status=%s -> %s", status, err.code)
            # Retry only short-lived server errors; quota (429) errors usually last longer.
            if isinstance(err, APIUnavailableError) and attempt < self.max_retries:
                attempt += 1
                self._sleep(2 ** attempt)
                continue
            raise err

        return AIResult(data=self._extract(payload), model=self.model, source=self.provider)

    @staticmethod
    def _extract(payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise InvalidAIResponseError()
        candidates = payload.get("candidates") or []
        if not candidates:
            if (payload.get("promptFeedback") or {}).get("blockReason"):
                raise InvalidAIResponseError(
                    "Gemini blocked this request with its safety filters, so nothing was saved.")
            raise InvalidAIResponseError()
        parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise InvalidAIResponseError()
        return _parse_text_json(text)
