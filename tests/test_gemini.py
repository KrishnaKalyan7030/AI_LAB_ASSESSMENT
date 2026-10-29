"""Gemini provider (AI_PROVIDER=gemini) - all HTTP calls are faked or local."""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import claude_service as cs
from app.services.ai_provider import get_ai_service
from app.services.claude_service import ClaudeService
from app.services.gemini_service import GeminiService, urllib_transport
from app.services.groq_service import GroqService
from tests.conftest import Client, good_viva

GEMINI_KEY = "AIzaSyTESTKEY-do-not-leak-0123456789abcd"
GEMINI_MODEL = "gemini-test-flash"


def ok(data, thought: str | None = None):
    parts = [{"text": "thinking...", "thought": True}] if thought else []
    parts.append({"text": json.dumps(data) if not isinstance(data, str) else data})
    return 200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}


def error(status, message="error", reason=None, api_status="INVALID_ARGUMENT"):
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}] if reason else []
    return status, {"error": {"code": status, "message": message, "status": api_status, "details": details}}


class FakeTransport:
    """Replaces the HTTP call. Give it a list of (status, payload) or exceptions."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def last_user_text(self):
        return self.calls[-1]["body"]["contents"][0]["parts"][0]["text"]


def gemini_service(transport, **kw):
    return GeminiService(api_key=GEMINI_KEY, model=GEMINI_MODEL, transport=transport,
                         sleep=lambda s: None, **kw)


def call(service):
    return service.request_json(system="sys", user_content="hello", tool_name="t",
                                tool_description="Record it.", schema={"type": "object"})


@pytest.fixture
def gemini_app(make_app):
    def _make(transport, **overrides):
        cfg = dict(AI_PROVIDER="gemini", GEMINI_API_KEY=GEMINI_KEY, GEMINI_MODEL=GEMINI_MODEL,
                   GEMINI_TRANSPORT=transport)
        cfg.update(overrides)
        return make_app(**cfg)
    return _make


def _student(app, username="student1"):
    c = Client(app.test_client())
    c.login(username)
    return c


# --- provider selection -----------------------------------------------------------
def test_provider_switch():
    assert isinstance(get_ai_service({"AI_PROVIDER": "gemini"}), GeminiService)
    assert isinstance(get_ai_service({"AI_PROVIDER": "claude"}), ClaudeService)
    assert isinstance(get_ai_service({}), GroqService)                 # default is Groq
    assert isinstance(get_ai_service({"AI_PROVIDER": "unknown"}), GroqService)


def test_placeholder_gemini_key_treated_as_missing(monkeypatch):
    from app.config import load_config

    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key-here")
    monkeypatch.setenv("GEMINI_MODEL", "x")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert not get_ai_service(load_config()).is_configured


# --- request format ---------------------------------------------------------------
def test_request_uses_generate_content_header_key_and_json_mode():
    t = FakeTransport(ok({"a": 1}))
    result = call(gemini_service(t))
    sent = t.calls[0]
    assert result.data == {"a": 1} and result.source == "gemini" and result.model == GEMINI_MODEL
    assert sent["url"].endswith(f"/models/{GEMINI_MODEL}:generateContent")
    assert sent["headers"]["x-goog-api-key"] == GEMINI_KEY
    assert GEMINI_KEY not in sent["url"]                      # key never in the URL
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert '"type": "object"' in sent["body"]["systemInstruction"]["parts"][0]["text"]
    assert sent["body"]["contents"][0]["parts"][0]["text"] == "hello"


def test_thought_parts_ignored_and_fenced_json_accepted():
    assert call(gemini_service(FakeTransport(ok({"x": 2}, thought="hmm")))).data == {"x": 2}
    assert call(gemini_service(FakeTransport(ok('```json\n{"y": 3}\n```')))).data == {"y": 3}


def test_missing_key_and_model():
    with pytest.raises(cs.MissingCredentialsError, match="GEMINI_API_KEY"):
        call(GeminiService(api_key="", model="m", transport=FakeTransport(ok({}))))
    with pytest.raises(cs.MissingCredentialsError, match="GEMINI_MODEL"):
        call(GeminiService(api_key="k" * 20, model="", transport=FakeTransport(ok({}))))


# --- error handling -----------------------------------------------------------------
@pytest.mark.parametrize("response, expected", [
    (error(400, "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"), cs.AuthenticationFailedError),
    (error(403, "Permission denied", api_status="PERMISSION_DENIED"), cs.AuthenticationFailedError),
    (error(404, "models/x is not found", api_status="NOT_FOUND"), cs.ModelNotFoundError),
    (error(429, "Resource exhausted", api_status="RESOURCE_EXHAUSTED"), cs.RateLimitedError),
    (error(400, "Invalid JSON payload"), cs.BadRequestError),
])
def test_http_errors_translated(response, expected):
    t = FakeTransport(response)
    with pytest.raises(expected) as info:
        call(gemini_service(t))
    assert GEMINI_KEY not in info.value.user_message
    assert len(t.calls) == 1  # no retry for these


def test_server_error_retried_once_then_succeeds():
    t = FakeTransport(error(503, "overloaded", api_status="UNAVAILABLE"), ok({"z": 1}))
    assert call(gemini_service(t)).data == {"z": 1}
    assert len(t.calls) == 2


def test_server_error_gives_up_after_retries():
    t = FakeTransport(error(500, "boom"))
    with pytest.raises(cs.APIUnavailableError):
        call(gemini_service(t, max_retries=1))
    assert len(t.calls) == 2


def test_blocked_and_empty_responses_rejected():
    with pytest.raises(cs.InvalidAIResponseError, match="safety"):
        call(gemini_service(FakeTransport((200, {"promptFeedback": {"blockReason": "SAFETY"}}))))
    with pytest.raises(cs.InvalidAIResponseError):
        call(gemini_service(FakeTransport((200, {"candidates": [{"content": {"parts": []}}]}))))
    with pytest.raises(cs.InvalidAIResponseError):
        call(gemini_service(FakeTransport(ok("not json at all"))))


# --- full app with Gemini ------------------------------------------------------------
def test_viva_graded_by_gemini_and_labelled(gemini_app, query, fake_claude):
    t = FakeTransport(ok(good_viva(3, 5)))
    c = _student(gemini_app(t))
    dash = c.get("/student/dashboard").get_data(as_text=True)
    assert "Gemini connected" in dash
    resp = c.post("/student/viva/1", {"answer": "A heuristic estimates the remaining cost."})
    attempt = resp.headers["Location"].split("/")[-1]
    score = query("SELECT * FROM viva_scores WHERE attempt_id = ?", (attempt,))[0]
    assert (score["score"], score["source"], score["model"]) == (3.0, "gemini", GEMINI_MODEL)
    page = c.get(f"/viva/attempts/{attempt}").get_data(as_text=True)
    assert "Gemini AI" in page and "Groq AI" not in page
    assert "A heuristic estimates the remaining cost." in t.last_user_text
    assert fake_claude.calls == []  # Claude is not called when Gemini is selected


def test_gemini_scores_still_validated(gemini_app, query):
    c = _student(gemini_app(FakeTransport(ok(good_viva(9, 5)))))
    resp = c.post("/student/viva/1", {"answer": "answer"})
    attempt = resp.headers["Location"].split("/")[-1]
    assert query("SELECT status FROM viva_attempts WHERE id = ?", (attempt,))[0]["status"] == "ai_failed"
    assert query("SELECT COUNT(*) AS n FROM viva_scores WHERE attempt_id = ?", (attempt,))[0]["n"] == 0


def test_gemini_quota_error_shown_with_retry(gemini_app):
    c = _student(gemini_app(FakeTransport(error(429, "quota", api_status="RESOURCE_EXHAUSTED"))))
    resp = c.post("/student/viva/1", {"answer": "answer"})
    page = c.get(resp.headers["Location"]).get_data(as_text=True)
    assert "free-tier quota" in page and "Retry AI assessment" in page


def test_all_features_work_with_gemini(gemini_app, query):
    responses = {
        "programming": {"code_explanation": "Uses a queue.", "failed_test_explanations": [],
                        "likely_logical_errors": [], "improvement_suggestions": ["Add a docstring."],
                        "encouragement": "Nice."},
        "quality": {"summary": "Clean.", "findings": [{"category": "naming", "severity": "low", "line": 1,
                    "title": "t", "explanation": "e", "recommendation": "r"}]},
        "questions": {"questions": [{"question": "What is BFS?", "reference_answer": "Level order search.",
                                     "rubric": "5 marks", "key_concepts": ["queue"]}]},
        "summary": {"overview": "Good.", "strong_topics": ["Search"], "weak_topics": [],
                    "revision_topics": [], "study_suggestions": ["Practise A*."]},
    }

    def transport(url, headers, body, timeout):
        task = body["systemInstruction"]["parts"][0]["text"]
        if "program and its test results" in task:
            return ok(responses["programming"])
        if "code-quality review" in task:
            return ok(responses["quality"])
        if "viva questions" in task:
            return ok(responses["questions"])
        return ok(responses["summary"])

    app = gemini_app(transport)
    student, teacher = _student(app), _student(app, "teacher1")
    student.post("/submissions/1/assist")
    student.post("/submissions/1/quality")
    student.post("/student/summary")
    teacher.post("/teacher/questions/generate", {"subject": "AI", "experiment": "BFS", "difficulty": "easy",
                                                 "count": "1", "max_score": "5"})
    assert query("SELECT source FROM ai_feedback WHERE kind = 'programming'")[0]["source"] == "gemini"
    assert query("SELECT source FROM code_quality_reports")[0]["source"] == "gemini"
    assert query("SELECT source FROM ai_feedback WHERE kind = 'summary'")[0]["source"] == "gemini"
    assert query("SELECT COUNT(*) AS n FROM viva_questions WHERE question = 'What is BFS?' "
                 "AND status = 'draft'")[0]["n"] == 1
    assert "Gemini AI" in student.get("/submissions/1").get_data(as_text=True)


def test_gemini_key_never_exposed(gemini_app, caplog):
    t = FakeTransport(error(400, f"API key not valid: {GEMINI_KEY}", "API_KEY_INVALID"))
    app = gemini_app(t)
    c = _student(app)
    with caplog.at_level(logging.DEBUG):
        c.post("/student/viva/1", {"answer": "answer"})
        logging.getLogger("test").warning("dump %s", GEMINI_KEY)
    for url in ("/student/dashboard", "/viva/attempts/1", "/api/health", "/api/me/results", "/login"):
        body = c.get(url).get_data(as_text=True)
        assert GEMINI_KEY not in body and "AIza" not in body
    health = c.get("/api/health").get_json()
    assert health["ai_provider"] == "gemini" and health["ai_configured"] is True
    assert GEMINI_KEY not in caplog.text and "[REDACTED]" in caplog.text


def test_gemini_missing_key_in_app(gemini_app):
    c = _student(gemini_app(FakeTransport(ok({})), GEMINI_API_KEY=""))
    resp = c.post("/student/viva/1", {"answer": "answer"})
    page = c.get(resp.headers["Location"]).get_data(as_text=True)
    assert "GEMINI_API_KEY" in page and "AI not configured" in page


# --- the real HTTP transport against a local server ------------------------------------
class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        _Handler.seen = {"path": self.path, "key": self.headers.get("x-goog-api-key"), "body": body}
        status, payload = _Handler.reply
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_urllib_transport_success_and_http_error(local_server):
    _Handler.reply = ok({"k": 1})
    status, payload = urllib_transport(local_server + "/v1beta/models/m:generateContent",
                                       {"Content-Type": "application/json", "x-goog-api-key": "abc"},
                                       {"hello": 1}, 5)
    assert status == 200 and payload["candidates"][0]["content"]["parts"][0]["text"] == '{"k": 1}'
    assert _Handler.seen["key"] == "abc" and _Handler.seen["body"] == {"hello": 1}

    _Handler.reply = error(429, "quota")
    status, payload = urllib_transport(local_server + "/x", {"Content-Type": "application/json"}, {}, 5)
    assert status == 429 and payload["error"]["code"] == 429


def test_urllib_transport_network_failure():
    with pytest.raises(cs.NetworkError):
        urllib_transport("http://127.0.0.1:9/unreachable", {}, {}, 2)
