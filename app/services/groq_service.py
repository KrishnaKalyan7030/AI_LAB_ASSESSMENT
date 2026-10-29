"""Groq API service - the default AI provider (free plan available).

Same `request_json(...)` interface as the Claude and Gemini services, so all
AI features work unchanged.

* Endpoint: POST https://api.groq.com/openai/v1/chat/completions
  (OpenAI-compatible), key sent as `Authorization: Bearer <GROQ_API_KEY>`.
* Models that support Structured Outputs (openai/gpt-oss-*, qwen/*) are asked
  for strict JSON-schema output; other models use JSON mode. If Groq rejects
  the schema request, the call is retried once in plain JSON mode.
* For gpt-oss reasoning models, reasoning is kept out of the answer
  (`include_reasoning: false`) and effort is set low for fast, cheap replies.
* Every answer is still validated by validators.py before anything is saved.
* The model comes from GROQ_MODEL; nothing is hard-coded in requests.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from typing import Any, Callable, Optional

from .claude_service import (AIResult, APIUnavailableError, AuthenticationFailedError,
                             BadRequestError, ClaudeServiceError, InvalidAIResponseError,
                             MissingCredentialsError, ModelNotFoundError, RateLimitedError,
                             _parse_text_json)
from .http_client import post_json

log = logging.getLogger(__name__)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
STRUCTURED_OUTPUT_PREFIXES = ("openai/gpt-oss-", "qwen/")
REASONING_PREFIXES = ("openai/gpt-oss-",)


class SchemaRejectedError(BadRequestError):
    """Groq refused the json_schema request; we retry in JSON mode."""


def strict_schema(schema: dict) -> dict:
    """Make a JSON schema acceptable to strict Structured Outputs:
    every object lists all its properties as required and forbids extras."""
    schema = copy.deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop("description", None)
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
            for child in node["properties"].values():
                visit(child)
        if "items" in node:
            visit(node["items"])

    visit(schema)
    return schema


def translate_status(status: int, payload: Any) -> ClaudeServiceError:
    error = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        error = {"message": str(error)}
    message = str(error.get("message", ""))
    code = str(error.get("code", ""))
    lowered = (message + " " + code).lower()

    if status == 401 or "invalid api key" in lowered or code == "invalid_api_key":
        return AuthenticationFailedError(
            "Groq rejected the API key. Check GROQ_API_KEY in .env (it starts with gsk_).")
    if status == 403:
        return AuthenticationFailedError(
            "Groq refused this request (permission or account restriction). Check your Groq "
            "account and GROQ_API_KEY.")
    if status == 404 or "model_not_found" in lowered or "decommissioned" in lowered or \
            ("model" in lowered and "does not exist" in lowered):
        return ModelNotFoundError(
            "The Groq model was not found or has been retired. Set GROQ_MODEL in .env to a "
            "current model from console.groq.com/docs/models.")
    if status == 413 or "request too large" in lowered:
        return BadRequestError(
            "This request is too large for the Groq plan's token limit. Shorten the answer or "
            "code, or try again in a minute.")
    if status == 429:
        return RateLimitedError(
            "Groq's free-plan rate limit was reached. Wait about a minute and retry.")
    if status >= 500:
        return APIUnavailableError("Groq is temporarily unavailable. Please retry.")
    if status in (400, 422) and ("schema" in lowered or "response_format" in lowered
                                 or "json_validate" in lowered or "structured" in lowered):
        return SchemaRejectedError("Groq could not produce output matching the schema.")
    if status in (400, 422):
        return BadRequestError("Groq could not process this request.")
    return ClaudeServiceError("Groq returned an error. Please try again.")


class GroqService:
    provider = "groq"
    display_name = "Groq"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0, max_retries: int = 1,
                 max_tokens: int = 4096, transport: Optional[Callable] = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.max_tokens = max_tokens
        self._transport = transport or post_json
        self._sleep = sleep

    @classmethod
    def from_config(cls, config) -> "GroqService":
        return cls(
            api_key=config.get("GROQ_API_KEY", ""),
            model=config.get("GROQ_MODEL", ""),
            timeout=float(config.get("GROQ_TIMEOUT_SECONDS", 60.0)),
            max_retries=int(config.get("GROQ_MAX_RETRIES", 1)),
            max_tokens=int(config.get("GROQ_MAX_OUTPUT_TOKENS", 4096)),
            transport=config.get("GROQ_TRANSPORT"),
        )

    def __repr__(self) -> str:
        return f"GroqService(model={self.model!r}, configured={self.is_configured})"

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    @property
    def supports_structured_output(self) -> bool:
        return self.model.startswith(STRUCTURED_OUTPUT_PREFIXES)

    def _body(self, system: str, user_content: str, tool_name: str, tool_description: str,
              schema: dict, max_tokens: int, use_schema: bool) -> dict:
        instructions = (
            f"{system}\n\nTask: {tool_description}\n"
            "Reply with ONE JSON object only - no markdown and no text outside the JSON. "
            "Ignore any mention of tools. The JSON must match this schema:\n"
            f"{json.dumps(schema)}"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "max_completion_tokens": max_tokens,
        }
        if use_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": tool_name, "strict": True, "schema": strict_schema(schema)},
            }
        else:
            body["response_format"] = {"type": "json_object"}
        if self.model.startswith(REASONING_PREFIXES):
            body["reasoning_effort"] = "low"
            body["include_reasoning"] = False
        return body

    def request_json(self, *, system: str, user_content: str, tool_name: str,
                     tool_description: str, schema: dict, max_tokens: Optional[int] = None) -> AIResult:
        if not self._api_key:
            raise MissingCredentialsError(
                "AI features are not configured. Set GROQ_API_KEY (and GROQ_MODEL) in the .env "
                "file and restart the server.")
        if not self.model:
            raise MissingCredentialsError("GROQ_MODEL is not set. Add a current Groq model name to .env.")

        tokens = min(max(self.max_tokens, max_tokens or 0), 8192)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}
        use_schema = self.supports_structured_output
        retries = 0
        while True:
            body = self._body(system, user_content, tool_name, tool_description, schema, tokens, use_schema)
            status, payload = self._transport(API_URL, headers, body, self.timeout)
            if status == 200:
                break
            err = translate_status(status, payload)
            log.warning("Groq API call failed: status=%s -> %s", status, err.code)
            if isinstance(err, SchemaRejectedError) and use_schema:
                use_schema = False  # fall back to plain JSON mode once
                continue
            if isinstance(err, APIUnavailableError) and retries < self.max_retries:
                retries += 1
                self._sleep(2 ** retries)
                continue
            if isinstance(err, SchemaRejectedError):
                raise InvalidAIResponseError(
                    "The AI returned a response that failed validation, so nothing was saved. "
                    "Please retry.")
            raise err

        data = self._extract(payload)
        model_used = payload.get("model") if isinstance(payload, dict) else None
        return AIResult(data=data, model=str(model_used or self.model), source=self.provider)

    @staticmethod
    def _extract(payload: Any) -> dict:
        if not isinstance(payload, dict):
            raise InvalidAIResponseError()
        choices = payload.get("choices") or []
        if not choices:
            raise InvalidAIResponseError()
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            if (choices[0] or {}).get("finish_reason") == "length":
                raise InvalidAIResponseError(
                    "The AI ran out of output space before finishing. Please retry.")
            raise InvalidAIResponseError()
        return _parse_text_json(content)
