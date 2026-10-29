from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.db import get_db, init_db  # noqa: E402
from app.security import CSRF_SESSION_KEY  # noqa: E402
from tests.fakes import FakeClaude, GroqAdapter  # noqa: E402

TEST_API_KEY = "sk-ant-api03-TESTKEY-do-not-leak-0123456789abcdef"
TEST_GROQ_KEY = "gsk_TESTKEYdoNotLeak0123456789abcdefABCDEF"
TEST_MODEL = "openai/gpt-oss-test"          # Groq model used by the default test app
CLAUDE_TEST_MODEL = "claude-test-model"


def base_config(db_path: str, fake: FakeClaude | None = None, **overrides) -> dict:
    cfg = {
        "TESTING": True,
        "DATABASE": db_path,
        "SECRET_KEY": "test-secret-key-for-sessions",
        # Default provider in tests is Groq (as in the real app), driven by the
        # FakeClaude answers through GroqAdapter - no network, no API key needed.
        "AI_PROVIDER": "groq",
        "GROQ_API_KEY": TEST_GROQ_KEY,
        "GROQ_MODEL": TEST_MODEL,
        "GROQ_TRANSPORT": GroqAdapter(fake) if fake else None,
        "ANTHROPIC_API_KEY": TEST_API_KEY,
        "ANTHROPIC_MODEL": CLAUDE_TEST_MODEL,
        "CLAUDE_CLIENT_FACTORY": fake.factory if fake else None,
        "AI_RATE_LIMIT_PER_MINUTE": 1000,
        "CODE_TIMEOUT_SECONDS": 5,
        "ALLOW_RULE_BASED_FALLBACK": True,
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture(scope="session")
def seeded_db_template(tmp_path_factory):
    """Seed once (seeding runs real test-engine subprocesses) and copy per test."""
    path = str(tmp_path_factory.mktemp("template") / "seed.db")
    app = create_app(base_config(path))
    with app.app_context():
        init_db(seed=True)
    return path


@pytest.fixture
def fake_claude():
    return FakeClaude()


@pytest.fixture
def make_app(tmp_path, seeded_db_template, fake_claude):
    def _make(**overrides):
        db_path = str(tmp_path / "test.db")
        if not os.path.exists(db_path):
            shutil.copy(seeded_db_template, db_path)
        return create_app(base_config(db_path, fake_claude, **overrides))

    return _make


@pytest.fixture
def app(make_app):
    return make_app()


class Client:
    """Test client wrapper that adds the CSRF token to POSTs."""

    def __init__(self, flask_client):
        self.c = flask_client

    def _token(self):
        with self.c.session_transaction() as sess:
            if not sess.get(CSRF_SESSION_KEY):
                sess[CSRF_SESSION_KEY] = "test-csrf-token"
            return sess[CSRF_SESSION_KEY]

    def get(self, *args, **kwargs):
        return self.c.get(*args, **kwargs)

    def post(self, url, data=None, **kwargs):
        data = dict(data or {})
        data.setdefault("csrf_token", self._token())
        return self.c.post(url, data=data, **kwargs)

    def login(self, username, password=None):
        password = password or ("Teacher@123" if username.startswith("teacher") else "Student@123")
        return self.post("/login", {"username": username, "password": password})

    def logout(self):
        return self.post("/logout")


@pytest.fixture
def client(app):
    return Client(app.test_client())


@pytest.fixture
def student(app):
    c = Client(app.test_client())
    c.login("student1")
    return c


@pytest.fixture
def student2(app):
    c = Client(app.test_client())
    c.login("student2")
    return c


@pytest.fixture
def teacher(app):
    c = Client(app.test_client())
    c.login("teacher1")
    return c


@pytest.fixture
def claude_student(make_app):
    """A logged-in student on an app configured with AI_PROVIDER=claude."""
    c = Client(make_app(AI_PROVIDER="claude").test_client())
    c.login("student1")
    return c


def assert_rate_limited_message(page: str) -> None:
    low = page.lower()
    assert "rate limit" in low or "rate-limiting" in low, "expected a rate-limit message"


@pytest.fixture
def query(app):
    def _q(sql, params=()):
        with app.app_context():
            return [dict(r) for r in get_db().execute(sql, params).fetchall()]

    return _q


def user_id(query, username):
    return query("SELECT id FROM users WHERE username = ?", (username,))[0]["id"]


def good_viva(score=4, max_score=5):
    return {
        "score": score,
        "max_score": max_score,
        "correct_concepts": ["Heuristic function"],
        "missing_concepts": ["Admissibility"],
        "strengths": ["Clear definition of h(n)"],
        "feedback": "The answer is mostly correct but needs more detail.",
        "suggestion": "Explain how the heuristic estimates the remaining cost.",
    }
