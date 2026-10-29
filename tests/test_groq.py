"""Groq provider (the default) plus start-up and whole-app smoke checks."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys

import pytest

from app.services import claude_service as cs
from app.services.groq_service import API_URL, GroqService, strict_schema
from app.services.validators import QUALITY_SCHEMA, VIVA_SCHEMA
from tests.conftest import TEST_GROQ_KEY, Client, good_viva

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def ok(content, model="openai/gpt-oss-120b", finish="stop"):
    if not isinstance(content, str):
        content = json.dumps(content)
    return 200, {"id": "x", "model": model,
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                              "finish_reason": finish}]}


def err(status, message="error", code=None, type_="invalid_request_error"):
    e = {"message": message, "type": type_}
    if code:
        e["code"] = code
    return status, {"error": e}


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(json.dumps(body))})
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, BaseException):
            raise item
        return item


def svc(transport, model="openai/gpt-oss-120b", **kw):
    return GroqService(api_key=TEST_GROQ_KEY, model=model, transport=transport, sleep=lambda s: None, **kw)


def call(service, schema=None):
    return service.request_json(system="sys", user_content="hello", tool_name="record_x",
                                tool_description="Record it.", schema=schema or VIVA_SCHEMA)


# --- request format -----------------------------------------------------------------
def test_request_format_for_gpt_oss():
    t = Transport(ok({"a": 1}))
    result = call(svc(t))
    sent = t.calls[0]
    assert result.data == {"a": 1} and result.source == "groq"
    assert sent["url"] == API_URL == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {TEST_GROQ_KEY}"
    assert TEST_GROQ_KEY not in sent["url"]
    body = sent["body"]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"][0]["role"] == "system" and "JSON" in body["messages"][0]["content"]
    assert body["messages"][1] == {"role": "user", "content": "hello"}
    rf = body["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "record_x"
    assert body["reasoning_effort"] == "low" and body["include_reasoning"] is False
    assert 0 < body["max_completion_tokens"] <= 8192


def test_json_mode_for_models_without_structured_outputs():
    t = Transport(ok({"a": 1}))
    call(svc(t, model="llama-3.3-70b-versatile"))
    body = t.calls[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body and "include_reasoning" not in body


def test_strict_schema_conversion():
    s = strict_schema(QUALITY_SCHEMA)
    assert s["additionalProperties"] is False and set(s["required"]) == {"summary", "findings"}
    item = s["properties"]["findings"]["items"]
    assert item["additionalProperties"] is False
    assert "line" in item["required"] and item["properties"]["line"]["type"] == ["integer", "null"]
    assert "description" not in json.dumps(strict_schema(VIVA_SCHEMA))
    assert "additionalProperties" not in json.dumps(QUALITY_SCHEMA)  # original untouched


def test_schema_rejection_falls_back_to_json_mode():
    t = Transport(err(400, "response_format json_schema is invalid", "json_validate_failed"), ok({"b": 2}))
    assert call(svc(t)).data == {"b": 2}
    assert t.calls[0]["body"]["response_format"]["type"] == "json_schema"
    assert t.calls[1]["body"]["response_format"] == {"type": "json_object"}


def test_fenced_json_and_bad_content():
    assert call(svc(Transport(ok('```json\n{"c": 3}\n```')))).data == {"c": 3}
    with pytest.raises(cs.InvalidAIResponseError):
        call(svc(Transport(ok("I think 4/5"))))
    with pytest.raises(cs.InvalidAIResponseError, match="ran out"):
        call(svc(Transport(ok("", finish="length"))))
    with pytest.raises(cs.InvalidAIResponseError):
        call(svc(Transport((200, {"choices": []}))))


# --- errors -----------------------------------------------------------------------
@pytest.mark.parametrize("response, expected, text", [
    (err(401, "Invalid API Key", "invalid_api_key"), cs.AuthenticationFailedError, "GROQ_API_KEY"),
    (err(403, "Forbidden"), cs.AuthenticationFailedError, "Groq"),
    (err(404, "The model `x` does not exist", "model_not_found"), cs.ModelNotFoundError, "GROQ_MODEL"),
    (err(400, "The model `x` has been decommissioned", "model_decommissioned"), cs.ModelNotFoundError, "retired"),
    (err(413, "Request too large for model"), cs.BadRequestError, "too large"),
    (err(429, "Rate limit reached", "rate_limit_exceeded"), cs.RateLimitedError, "rate limit"),
    (err(400, "messages: invalid"), cs.BadRequestError, "Groq"),
])
def test_errors_translated(response, expected, text):
    t = Transport(response)
    with pytest.raises(expected) as info:
        call(svc(t))
    assert text in info.value.user_message
    assert TEST_GROQ_KEY not in info.value.user_message
    assert len(t.calls) == 1


def test_server_error_retried_once():
    t = Transport(err(503, "unavailable"), ok({"d": 4}))
    assert call(svc(t)).data == {"d": 4} and len(t.calls) == 2
    t2 = Transport(err(500, "boom"))
    with pytest.raises(cs.APIUnavailableError):
        call(svc(t2, max_retries=1))
    assert len(t2.calls) == 2


def test_missing_key_message():
    with pytest.raises(cs.MissingCredentialsError, match="GROQ_API_KEY"):
        call(GroqService(api_key="", model="m", transport=Transport(ok({}))))


def test_model_has_a_sensible_default(monkeypatch):
    from app.config import load_config

    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a" * 40)
    cfg = load_config()
    assert cfg["AI_PROVIDER"] == "groq" and cfg["GROQ_MODEL"] == "openai/gpt-oss-120b"
    assert GroqService.from_config(cfg).is_configured


def test_placeholder_key_is_not_configured(monkeypatch):
    from app.config import load_config

    monkeypatch.setenv("GROQ_API_KEY", "your-groq-api-key-here")
    assert not GroqService.from_config(load_config()).is_configured


# --- through the app -----------------------------------------------------------------
def test_app_uses_groq_by_default(student, fake_claude, query):
    fake_claude.set("record_viva_assessment", good_viva(4, 5))
    page = student.get("/student/dashboard").get_data(as_text=True)
    assert "Groq connected" in page
    resp = student.post("/student/viva/1", {"answer": "A heuristic estimates remaining cost."})
    result = student.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Groq AI" in result and "assessed by Groq" in result
    assert fake_claude.factory_kwargs == []  # the Anthropic client was never created


def test_groq_key_never_exposed(student, teacher, fake_claude, caplog):
    fake_claude.set("record_viva_assessment", good_viva())
    with caplog.at_level(logging.DEBUG):
        student.post("/student/viva/1", {"answer": "answer"})
        logging.getLogger("test").warning("leak attempt %s", TEST_GROQ_KEY)
    for client, urls in ((student, ["/student/dashboard", "/viva/attempts/1", "/api/health", "/api/me/results"]),
                         (teacher, ["/teacher/dashboard", "/teacher/questions/generate", "/api/health"])):
        for url in urls:
            body = client.get(url).get_data(as_text=True)
            assert TEST_GROQ_KEY not in body and "gsk_" not in body
    assert TEST_GROQ_KEY not in caplog.text and "[REDACTED]" in caplog.text


# --- every page renders without errors --------------------------------------------------
def _crawl(client, start_urls, max_pages=80):
    seen, queue, problems = set(), list(start_urls), []
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        resp = client.get(url)
        if resp.status_code >= 500 or resp.status_code not in (200, 302, 403, 404):
            problems.append((url, resp.status_code))
            continue
        if resp.status_code == 200 and resp.mimetype == "text/html":
            for href in re.findall(r'href="(/[^"#?]*)', resp.get_data(as_text=True)):
                if not href.startswith("/static") and href not in seen:
                    queue.append(href)
    return seen, problems


def test_every_student_page_renders(student):
    seen, problems = _crawl(student, ["/student/dashboard", "/student/summary"])
    assert not problems, problems
    assert len(seen) > 8


def test_every_teacher_page_renders(teacher):
    teacher.post("/teacher/assignments/1/similarity")
    seen, problems = _crawl(teacher, ["/teacher/dashboard", "/teacher/questions", "/teacher/questions/new",
                                      "/teacher/assignments/new", "/teacher/students/2/summary"])
    assert not problems, problems
    assert any(u.startswith("/teacher/similarity/") for u in seen)


def test_public_pages_render(client):
    for url in ("/login", "/register", "/does-not-exist"):
        assert client.get(url).status_code in (200, 404)
    assert b"Go to my dashboard" in client.get("/does-not-exist").data


# --- start-up helpers --------------------------------------------------------------------
def test_database_created_automatically(tmp_path):
    from app import create_app
    from app.db import ensure_database

    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fresh.db")})
    with app.app_context():
        assert ensure_database() == "created"
        assert ensure_database() == "ok"


def test_configure_env_script(tmp_path):
    import shutil

    work = tmp_path / "proj"
    (work / "scripts").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "scripts", "configure_env.py"), work / "scripts")
    shutil.copy(os.path.join(ROOT, ".env.example"), work / ".env.example")
    script = str(work / "scripts" / "configure_env.py")

    def run(*args, stdin=None):
        return subprocess.run([sys.executable, script, *args], input=stdin, capture_output=True, text=True)

    assert run("init").returncode == 0
    env_text = (work / ".env").read_text()
    secret = re.search(r"^FLASK_SECRET_KEY=(\w+)$", env_text, re.M).group(1)
    assert len(secret) == 64
    assert run("has-groq-key").returncode == 1
    result = run("set-groq-key", stdin="gsk_" + "b" * 40 + "\n")
    assert result.returncode == 0 and "gsk_" not in result.stdout  # key never printed
    assert run("has-groq-key").returncode == 0
    run("init")  # running again keeps the existing secret
    assert secret in (work / ".env").read_text()


def test_shell_scripts_are_valid():
    for name in ("setup.sh", "start.sh"):
        assert subprocess.run(["bash", "-n", os.path.join(ROOT, name)]).returncode == 0
