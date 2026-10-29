"""Test doubles for the Anthropic SDK - no network, no API credits."""
from __future__ import annotations

from types import SimpleNamespace


def tool_response(tool_name: str, data, model: str = "claude-test-model"):
    block = SimpleNamespace(type="tool_use", name=tool_name, input=data, id="toolu_test")
    return SimpleNamespace(content=[block], model=model, stop_reason="tool_use")


def text_response(text: str, model: str = "claude-test-model"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], model=model,
                           stop_reason="end_turn")


class FakeClaude:
    """Stands in for `anthropic.Anthropic`. Configure answers per tool name.

    A configured value can be a dict (returned as tool input), a ready-made
    response object, an Exception instance (raised), or a callable taking the
    request kwargs.
    """

    def __init__(self):
        self.responses = {}
        self.calls = []
        self.factory_kwargs = []

    # anthropic.Anthropic(**kwargs)
    def factory(self, **kwargs):
        self.factory_kwargs.append(kwargs)
        return self

    @property
    def messages(self):
        return self

    def set(self, tool_name: str, value):
        self.responses[tool_name] = value

    def create(self, **kwargs):
        self.calls.append(kwargs)
        name = kwargs["tool_choice"]["name"]
        value = self.responses.get(name)
        if callable(value) and not isinstance(value, type):
            value = value(kwargs)
        if isinstance(value, BaseException):
            raise value
        if value is None:
            raise AssertionError(f"No fake response configured for tool {name!r}")
        if isinstance(value, dict):
            return tool_response(name, value)
        return value

    def last_prompt(self) -> str:
        return self.calls[-1]["messages"][0]["content"]


# --- Exceptions named like the SDK's ------------------------------------------
class APIError(Exception):
    pass


class APIStatusError(APIError):
    status_code = 500


class AuthenticationError(APIStatusError):
    status_code = 401


class PermissionDeniedError(APIStatusError):
    status_code = 403


class NotFoundError(APIStatusError):
    status_code = 404


class RateLimitError(APIStatusError):
    status_code = 429


class InternalServerError(APIStatusError):
    status_code = 500


class OverloadedError(APIStatusError):
    status_code = 529


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class GroqAdapter:
    """Lets FakeClaude's per-tool answers drive the Groq provider.

    It plays the role of the HTTP transport used by GroqService: it reads the
    tool (schema) name from the request, records a Claude-style call on the
    FakeClaude so existing assertions keep working, and returns a Groq
    chat-completions payload (or an HTTP error / network error).
    """

    def __init__(self, fake: "FakeClaude"):
        self.fake = fake
        self.bodies = []

    def __call__(self, url, headers, body, timeout):
        import json as _json

        from app.services.claude_service import NetworkError

        self.bodies.append({"url": url, "headers": headers, "body": body})
        name = (body.get("response_format") or {}).get("json_schema", {}).get("name")
        if not name:
            raise AssertionError("GroqAdapter needs json_schema mode (use a gpt-oss test model)")
        call = {
            "model": body["model"],
            "system": body["messages"][0]["content"],
            "messages": [{"role": "user", "content": body["messages"][1]["content"]}],
            "tool_choice": {"type": "tool", "name": name},
        }
        self.fake.calls.append(call)
        value = self.fake.responses.get(name)
        if callable(value) and not isinstance(value, type):
            value = value(call)
        if isinstance(value, BaseException):
            status = getattr(value, "status_code", None)
            names = {c.__name__ for c in type(value).__mro__}
            if status:
                return status, {"error": {"message": str(value), "type": "api_error"}}
            if "APITimeoutError" in names:
                raise NetworkError("The AI request timed out. Please retry.")
            if "APIConnectionError" in names:
                raise NetworkError("Could not reach the AI service. Check your internet connection and retry.")
            raise value
        if value is None:
            raise AssertionError(f"No fake response configured for tool {name!r}")
        if isinstance(value, dict):
            content = _json.dumps(value)
        else:  # tool_response / text_response objects
            content = ""
            for block in getattr(value, "content", []) or []:
                if getattr(block, "type", None) == "tool_use":
                    content = _json.dumps(block.input)
                elif getattr(block, "type", None) == "text":
                    content += block.text
        return 200, {"id": "chatcmpl-test", "object": "chat.completion", "model": body["model"],
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                                  "finish_reason": "stop"}]}
