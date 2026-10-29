"""Claude API service built on the official Anthropic Python SDK.

Every AI feature in the app goes through `ClaudeService.request_json`, which:

* reads the key from ANTHROPIC_API_KEY and the model from ANTHROPIC_MODEL
  (no model identifier is hard-coded here);
* uses the Messages API with a forced tool call so Claude must answer with
  JSON that matches a declared schema;
* applies a request timeout and a bounded number of SDK retries;
* converts SDK exceptions into small, user-safe error classes;
* never logs, returns or stores the API key.

The returned JSON is *not* trusted: callers validate it with
`app.services.validators` before anything is stored.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors (safe to show to users - they never contain secrets)
# ---------------------------------------------------------------------------
class ClaudeServiceError(Exception):
    code = "api_error"
    retryable = True
    default_message = "The AI service returned an error. Please try again."

    def __init__(self, message: Optional[str] = None):
        super().__init__(message or self.default_message)
        self.user_message = message or self.default_message


class MissingCredentialsError(ClaudeServiceError):
    code = "missing_credentials"
    retryable = False
    default_message = (
        "AI features are not configured. Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL "
        "in the .env file and restart the server."
    )


class AuthenticationFailedError(ClaudeServiceError):
    code = "invalid_api_key"
    retryable = False
    default_message = (
        "The Anthropic API rejected the configured API key. Check ANTHROPIC_API_KEY in .env."
    )


class ModelNotFoundError(ClaudeServiceError):
    code = "model_not_found"
    retryable = False
    default_message = (
        "The configured model was not found. Check ANTHROPIC_MODEL in .env against the "
        "models listed in the Anthropic documentation."
    )


class RateLimitedError(ClaudeServiceError):
    code = "rate_limited"
    default_message = "The AI service is rate-limiting requests. Wait a minute and retry."


class NetworkError(ClaudeServiceError):
    code = "network_error"
    default_message = "Could not reach the AI service (network problem or timeout). Please retry."


class APIUnavailableError(ClaudeServiceError):
    code = "api_unavailable"
    default_message = "The AI service is temporarily unavailable or overloaded. Please retry."


class BadRequestError(ClaudeServiceError):
    code = "bad_request"
    retryable = False
    default_message = "The AI service could not process this request."


class InvalidAIResponseError(ClaudeServiceError):
    code = "invalid_ai_response"
    default_message = (
        "The AI returned a response that failed validation, so nothing was saved. Please retry."
    )


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------
@dataclass
class AIResult:
    data: dict
    model: str
    source: str = "claude"


def _default_client_factory(**kwargs):
    import anthropic  # imported lazily so the app can start without the SDK configured

    return anthropic.Anthropic(**kwargs)


def translate_exception(exc: Exception) -> ClaudeServiceError:
    """Map an Anthropic SDK exception to a safe ClaudeServiceError.

    Classification uses the exception's class hierarchy names and HTTP status,
    so it works with every SDK version that keeps the documented names.
    """
    if isinstance(exc, ClaudeServiceError):
        return exc
    names = {cls.__name__ for cls in type(exc).__mro__}
    status = getattr(exc, "status_code", None)

    if "AuthenticationError" in names or status == 401:
        return AuthenticationFailedError()
    if "PermissionDeniedError" in names or status == 403:
        return AuthenticationFailedError(
            "The API key does not have permission for this request or model."
        )
    if "NotFoundError" in names or status == 404:
        return ModelNotFoundError()
    if "RateLimitError" in names or status == 429:
        return RateLimitedError()
    if "APITimeoutError" in names:
        return NetworkError("The AI request timed out. Please retry.")
    if "APIConnectionError" in names:
        return NetworkError()
    if isinstance(status, int) and (status >= 500 or status == 529):
        return APIUnavailableError()
    if "BadRequestError" in names or status in (400, 413, 422):
        return BadRequestError()
    return ClaudeServiceError()


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _parse_text_json(text: str) -> dict:
    candidate = text.strip()
    fence = _JSON_FENCE.search(candidate)
    if fence:
        candidate = fence.group(1).strip()
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise InvalidAIResponseError()
        candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        raise InvalidAIResponseError() from None
    if not isinstance(data, dict):
        raise InvalidAIResponseError()
    return data


def extract_tool_input(response: Any, tool_name: str) -> dict:
    """Return the JSON object Claude produced for `tool_name`."""
    content = getattr(response, "content", None)
    if not content:
        raise InvalidAIResponseError()
    texts = []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "tool_use" and getattr(block, "name", None) == tool_name:
            data = getattr(block, "input", None)
            if isinstance(data, dict):
                return data
            raise InvalidAIResponseError()
        if btype == "text":
            texts.append(getattr(block, "text", "") or "")
    if texts:
        return _parse_text_json("\n".join(texts))
    raise InvalidAIResponseError()


class ClaudeService:
    provider = "claude"
    display_name = "Claude"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 2,
        max_tokens: int = 2000,
        client_factory: Optional[Callable[..., Any]] = None,
    ):
        self._api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self._client_factory = client_factory or _default_client_factory
        self._client = None

    @classmethod
    def from_config(cls, config) -> "ClaudeService":
        return cls(
            api_key=config.get("ANTHROPIC_API_KEY", ""),
            model=config.get("ANTHROPIC_MODEL", ""),
            timeout=float(config.get("ANTHROPIC_TIMEOUT_SECONDS", 30.0)),
            max_retries=int(config.get("ANTHROPIC_MAX_RETRIES", 2)),
            max_tokens=int(config.get("AI_MAX_OUTPUT_TOKENS", 2000)),
            client_factory=config.get("CLAUDE_CLIENT_FACTORY"),
        )

    # Never include the key in repr / debug output.
    def __repr__(self) -> str:
        return f"ClaudeService(model={self.model!r}, configured={self.is_configured})"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    def _get_client(self):
        if not self._api_key:
            raise MissingCredentialsError()
        if not self.model:
            raise MissingCredentialsError(
                "ANTHROPIC_MODEL is not set. Add a current Claude model name to .env."
            )
        if self._client is None:
            try:
                self._client = self._client_factory(
                    api_key=self._api_key, timeout=self.timeout, max_retries=self.max_retries
                )
            except ImportError:
                raise ClaudeServiceError(
                    "The 'anthropic' package is not installed. Run: pip install -r requirements.txt"
                ) from None
        return self._client

    def request_json(
        self,
        *,
        system: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        schema: dict,
        max_tokens: Optional[int] = None,
    ) -> AIResult:
        """Call the Messages API and force a JSON answer matching `schema`."""
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                tools=[{
                    "name": tool_name,
                    "description": tool_description,
                    "input_schema": schema,
                }],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except Exception as exc:  # noqa: BLE001 - every SDK error is translated
            err = translate_exception(exc)
            log.warning(
                "Claude API call failed: %s (status=%s) -> %s",
                type(exc).__name__, getattr(exc, "status_code", None), err.code,
            )
            raise err from None
        data = extract_tool_input(response, tool_name)
        model_used = getattr(response, "model", None) or self.model
        return AIResult(data=data, model=str(model_used))
