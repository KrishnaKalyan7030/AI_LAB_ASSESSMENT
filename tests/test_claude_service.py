"""Claude service: configuration, credentials, error translation (all mocked)."""
import pytest

from app.services import claude_service as cs
from app.services.claude_service import ClaudeService, translate_exception
from tests import fakes
from tests.conftest import Client, TEST_API_KEY, good_viva

SCHEMA = {"type": "object", "properties": {}}


def _call(service):
    return service.request_json(system="s", user_content="u", tool_name="t",
                                tool_description="d", schema=SCHEMA)


def test_missing_api_key_raises_before_creating_client():
    fake = fakes.FakeClaude()
    service = ClaudeService(api_key="", model="m", client_factory=fake.factory)
    assert not service.is_configured
    with pytest.raises(cs.MissingCredentialsError):
        _call(service)
    assert fake.factory_kwargs == [] and fake.calls == []


def test_missing_model_raises():
    service = ClaudeService(api_key="key-123456789", model="", client_factory=fakes.FakeClaude().factory)
    with pytest.raises(cs.MissingCredentialsError, match="ANTHROPIC_MODEL"):
        _call(service)


def test_missing_credentials_in_app_shows_clear_error(make_app, fake_claude, query):
    app = make_app(AI_PROVIDER="claude", ANTHROPIC_API_KEY="")
    c = Client(app.test_client())
    c.login("student1")
    resp = c.post("/student/viva/1", {"answer": "A heuristic estimates remaining cost."})
    attempt_id = resp.headers["Location"].split("/")[-1]
    page = c.get(f"/viva/attempts/{attempt_id}").data.decode()
    assert "AI features are not configured" in page
    assert "AI not configured" in page
    assert fake_claude.calls == []
    assert query("SELECT COUNT(*) AS n FROM viva_scores WHERE attempt_id = ?", (attempt_id,))[0]["n"] == 0


def test_timeout_and_retries_passed_to_sdk_client():
    fake = fakes.FakeClaude()
    fake.set("t", {"ok": True})
    service = ClaudeService(api_key="key-123456789", model="m", timeout=12.5, max_retries=1,
                            client_factory=fake.factory)
    _call(service)
    assert fake.factory_kwargs == [{"api_key": "key-123456789", "timeout": 12.5, "max_retries": 1}]


def test_model_read_from_config_not_hardcoded():
    source = open(cs.__file__, encoding="utf-8").read()
    assert "claude-" not in source  # no model identifier hard-coded in the service
    service = ClaudeService.from_config({"ANTHROPIC_API_KEY": "k" * 12, "ANTHROPIC_MODEL": "my-model"})
    assert service.model == "my-model"


@pytest.mark.parametrize("exc, expected", [
    (fakes.AuthenticationError("bad key"), cs.AuthenticationFailedError),
    (fakes.PermissionDeniedError("no"), cs.AuthenticationFailedError),
    (fakes.NotFoundError("model"), cs.ModelNotFoundError),
    (fakes.RateLimitError("slow down"), cs.RateLimitedError),
    (fakes.APITimeoutError("timeout"), cs.NetworkError),
    (fakes.APIConnectionError("dns"), cs.NetworkError),
    (fakes.InternalServerError("500"), cs.APIUnavailableError),
    (fakes.OverloadedError("529"), cs.APIUnavailableError),
    (ValueError("something else"), cs.ClaudeServiceError),
])
def test_sdk_errors_translated(exc, expected):
    err = translate_exception(exc)
    assert type(err) is expected
    assert TEST_API_KEY not in err.user_message


def test_timeout_message_mentions_timeout():
    assert "timed out" in translate_exception(fakes.APITimeoutError("t")).user_message


def test_errors_raised_from_request_json():
    fake = fakes.FakeClaude()
    fake.set("t", fakes.RateLimitError("429"))
    service = ClaudeService(api_key="key-123456789", model="m", client_factory=fake.factory)
    with pytest.raises(cs.RateLimitedError) as info:
        _call(service)
    assert info.value.retryable is True


def test_invalid_key_shown_without_retry_loop(claude_student, fake_claude):
    student = claude_student
    fake_claude.set("record_viva_assessment", fakes.AuthenticationError("401"))
    resp = student.post("/student/viva/1", {"answer": "Some answer about heuristics."})
    page = student.get(resp.headers["Location"]).data.decode()
    assert "rejected the configured API key" in page


def test_network_failure_shown(claude_student, fake_claude):
    student = claude_student
    fake_claude.set("record_viva_assessment", fakes.APIConnectionError("connection refused"))
    resp = student.post("/student/viva/1", {"answer": "Some answer about heuristics."})
    page = student.get(resp.headers["Location"]).data.decode()
    assert "Could not reach the AI service" in page


def test_missing_tool_block_rejected():
    from types import SimpleNamespace

    fake = fakes.FakeClaude()
    fake.set("t", SimpleNamespace(content=[], model="m"))
    service = ClaudeService(api_key="key-123456789", model="m", client_factory=fake.factory)
    with pytest.raises(cs.InvalidAIResponseError):
        _call(service)


def test_repr_does_not_contain_key():
    service = ClaudeService(api_key=TEST_API_KEY, model="m")
    assert TEST_API_KEY not in repr(service) and TEST_API_KEY not in str(vars(service).get("model"))


def test_viva_with_good_response_uses_service(claude_student, fake_claude):
    fake_claude.set("record_viva_assessment", good_viva())
    claude_student.post("/student/viva/1", {"answer": "answer text here"})
    assert len(fake_claude.calls) == 1
    assert fake_claude.factory_kwargs  # the Anthropic client path was used


# --- Checks against the REAL SDK classes (run where `anthropic` is installed) --------
def test_real_sdk_exceptions_translate():
    anthropic = pytest.importorskip("anthropic")
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status_error(cls, code):
        return cls("error", response=httpx.Response(code, request=request), body=None)

    assert isinstance(translate_exception(status_error(anthropic.RateLimitError, 429)), cs.RateLimitedError)
    assert isinstance(translate_exception(status_error(anthropic.AuthenticationError, 401)), cs.AuthenticationFailedError)
    assert isinstance(translate_exception(status_error(anthropic.NotFoundError, 404)), cs.ModelNotFoundError)
    assert isinstance(translate_exception(status_error(anthropic.InternalServerError, 500)), cs.APIUnavailableError)
    assert isinstance(translate_exception(anthropic.APITimeoutError(request=request)), cs.NetworkError)
    assert isinstance(translate_exception(anthropic.APIConnectionError(request=request)), cs.NetworkError)


def test_real_sdk_client_constructed_with_timeout():
    anthropic = pytest.importorskip("anthropic")
    client = cs._default_client_factory(api_key="sk-ant-placeholder-000000", timeout=7.0, max_retries=1)
    assert isinstance(client, anthropic.Anthropic)
    assert client.max_retries == 1


def test_env_example_placeholder_key_treated_as_missing(monkeypatch):
    from app.config import load_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "your-anthropic-api-key-here")
    monkeypatch.setenv("ANTHROPIC_MODEL", "some-model")
    config = load_config()
    assert config["ANTHROPIC_API_KEY"] == ""
    assert not ClaudeService.from_config(config).is_configured
