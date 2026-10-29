"""Secrets never reach clients or logs; AI endpoints are rate limited."""
import logging
import os

from app.security import redact
from tests.conftest import Client, TEST_API_KEY, good_viva
from tests.fakes import AuthenticationError

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _all_pages(student, teacher):
    pages = []
    for url in ("/login", "/api/health"):
        pages.append(Client(student.c.application.test_client()).get(url))
    for url in ("/student/dashboard", "/student/viva/1", "/student/assignments/1", "/submissions/1",
                "/viva/attempts/1", "/student/summary", "/api/me/results", "/api/attempts/1",
                "/does-not-exist", "/teacher/dashboard"):
        pages.append(student.get(url))
    for url in ("/teacher/dashboard", "/teacher/questions", "/teacher/questions/generate",
                "/teacher/assignments/1", "/teacher/students/2", "/teacher/questions/1/edit",
                "/submissions/2", "/api/health"):
        pages.append(teacher.get(url))
    return pages


def test_api_key_never_returned_to_clients(student, teacher, fake_claude):
    fake_claude.set("record_viva_assessment", good_viva())
    student.post("/student/viva/1", {"answer": "heuristic answer"})
    for resp in _all_pages(student, teacher):
        body = resp.get_data(as_text=True)
        assert TEST_API_KEY not in body
        assert "sk-ant-" not in body
        assert TEST_API_KEY not in str(resp.headers)


def test_health_endpoint_reports_only_configuration_state(client):
    data = client.get("/api/health").get_json()
    assert set(data) == {"status", "ai_provider", "ai_configured", "model"}
    assert data["ai_configured"] is True


def test_api_key_not_written_to_logs(student, fake_claude, caplog):
    fake_claude.set("record_viva_assessment", AuthenticationError(f"invalid x-api-key {TEST_API_KEY}"))
    with caplog.at_level(logging.DEBUG):
        student.post("/student/viva/1", {"answer": "answer"})
        logging.getLogger("test").warning("debug dump: key=%s", TEST_API_KEY)
    assert caplog.records
    assert TEST_API_KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_redact_patterns():
    assert redact(f"key {TEST_API_KEY} end") == "key [REDACTED] end"
    assert "sk-ant-" not in redact("Authorization: sk-ant-api03-abcdefghijklmnop")


def test_env_example_has_placeholders_only():
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    assert "ANTHROPIC_API_KEY=your-anthropic-api-key-here" in text
    assert "sk-ant-" not in text


def test_gitignore_excludes_secrets_and_database():
    lines = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().splitlines()
    assert ".env" in lines and "instance/" in lines and "*.db" in lines


def test_no_api_key_in_frontend_assets():
    for folder in ("app/static", "app/templates"):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
            for name in files:
                text = open(os.path.join(dirpath, name), encoding="utf-8").read()
                assert "ANTHROPIC_API_KEY" not in text or name.endswith(".html")
                assert "sk-ant-" not in text


def test_security_headers(client):
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_ai_endpoints_rate_limited(make_app, fake_claude):
    app = make_app(AI_RATE_LIMIT_PER_MINUTE=2)
    c = Client(app.test_client())
    c.login("student1")
    fake_claude.set("record_viva_assessment", good_viva())
    codes = [c.post("/student/viva/1", {"answer": "answer"}).status_code for _ in range(3)]
    assert codes == [302, 302, 429]
    assert len(fake_claude.calls) == 2
    # Viewing pages is not limited
    assert c.get("/student/dashboard").status_code == 200


def test_rate_limit_is_per_user(make_app, fake_claude):
    app = make_app(AI_RATE_LIMIT_PER_MINUTE=1)
    fake_claude.set("record_viva_assessment", good_viva())
    a, b = Client(app.test_client()), Client(app.test_client())
    a.login("student1")
    b.login("student2")
    assert a.post("/student/viva/1", {"answer": "x"}).status_code == 302
    assert a.post("/student/viva/1", {"answer": "x"}).status_code == 429
    assert b.post("/student/viva/1", {"answer": "x"}).status_code == 302


def test_request_body_size_limited(make_app):
    app = make_app(MAX_CONTENT_LENGTH=1024)
    c = Client(app.test_client())
    c.login("student1")
    assert c.post("/student/assignments/1", {"code": "x" * 5000}).status_code == 413
