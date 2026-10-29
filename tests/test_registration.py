"""User registration (students freely, teachers only with a registration code)."""
from tests.conftest import Client

GOOD = {
    "full_name": "Priya  Shinde",
    "username": "priya_s",
    "role": "student",
    "password": "Secure123",
    "confirm_password": "Secure123",
}


def _register(client, **overrides):
    data = {**GOOD, **overrides}
    return client.post("/register", data)


def test_register_page_renders_and_login_links_to_it(client):
    page = client.get("/register")
    assert page.status_code == 200 and b"Create an account" in page.data
    assert b'href="/register"' in client.get("/login").data


def test_login_page_has_no_sample_credentials_or_footer(client):
    html = client.get("/login").get_data(as_text=True)
    assert "Sample accounts" not in html
    assert "Teacher@123" not in html and "Student@123" not in html
    assert "AI-generated feedback is a learning aid" not in html


def test_footer_still_shown_inside_the_app(student):
    assert b"AI-generated feedback is a learning aid" in student.get("/student/dashboard").data


def test_student_registers_and_is_logged_in(client, query):
    resp = _register(client)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/student/dashboard")
    user = query("SELECT * FROM users WHERE username = 'priya_s'")[0]
    assert user["role"] == "student"
    assert user["full_name"] == "Priya Shinde"  # extra spaces collapsed
    assert user["password_hash"] != "Secure123"
    dash = client.get("/student/dashboard")
    assert dash.status_code == 200 and b"Priya Shinde" in dash.data


def test_registered_user_can_log_in_later(client):
    _register(client)
    client.logout()
    resp = client.login("priya_s", "Secure123")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/student/dashboard")


def test_new_student_sees_only_own_empty_results(client):
    _register(client)
    data = client.get("/api/me/results").get_json()
    assert data["viva"] == [] and data["submissions"] == []
    assert client.get("/viva/attempts/1").status_code == 404  # student1's attempt


def test_duplicate_username_rejected_case_insensitive(client, query):
    resp = _register(client, username="Student1")
    assert resp.status_code == 400
    assert b"already taken" in resp.data
    assert query("SELECT COUNT(*) AS n FROM users WHERE lower(username) = 'student1'")[0]["n"] == 1


def test_password_rules(client, query):
    cases = [
        {"password": "short1", "confirm_password": "short1"},
        {"password": "onlyletters", "confirm_password": "onlyletters"},
        {"password": "12345678", "confirm_password": "12345678"},
        {"password": "Secure123", "confirm_password": "Secure124"},
        {"username": "same1234", "password": "same1234", "confirm_password": "same1234"},
    ]
    for overrides in cases:
        assert _register(client, **overrides).status_code == 400, overrides
    assert query("SELECT COUNT(*) AS n FROM users WHERE username IN ('priya_s', 'same1234')")[0]["n"] == 0


def test_invalid_username_and_name_rejected(client):
    assert _register(client, username="ab").status_code == 400
    assert _register(client, username="bad name!").status_code == 400
    assert _register(client, full_name=" ").status_code == 400


def test_teacher_registration_disabled_without_code(client, query):
    resp = _register(client, role="teacher", teacher_code="anything")
    assert resp.status_code == 400
    assert b"Teacher self-registration is disabled" in resp.data
    assert query("SELECT COUNT(*) AS n FROM users WHERE username = 'priya_s'")[0]["n"] == 0


def test_teacher_registration_requires_correct_code(make_app, query):
    app = make_app(TEACHER_REGISTRATION_CODE="LAB-2026-secret")
    c = Client(app.test_client())
    assert b"teacher-code-group" in c.get("/register").data
    assert _register(c, role="teacher", teacher_code="wrong").status_code == 400
    resp = _register(c, role="teacher", teacher_code="LAB-2026-secret")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/teacher/dashboard")
    assert query("SELECT role FROM users WHERE username = 'priya_s'")[0]["role"] == "teacher"


def test_role_tampering_rejected(client, query):
    assert _register(client, role="admin").status_code == 400
    assert query("SELECT COUNT(*) AS n FROM users WHERE username = 'priya_s'")[0]["n"] == 0


def test_registration_requires_csrf(app):
    raw = app.test_client()
    assert raw.post("/register", data=GOOD).status_code == 400


def test_logged_in_user_redirected_away_from_register(student):
    assert student.get("/register").status_code == 302


def test_registration_rate_limited(make_app):
    app = make_app(REGISTRATION_LIMIT_PER_HOUR=2)
    c = Client(app.test_client())
    codes = []
    for i in range(3):
        codes.append(_register(c, username=f"user{i}x").status_code)
        c.logout()
    assert codes == [302, 302, 429]


def test_sample_accounts_still_work(client):
    assert client.login("teacher1").status_code == 302
