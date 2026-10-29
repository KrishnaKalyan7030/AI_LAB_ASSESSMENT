"""Student and teacher authentication."""


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Log in" in resp.data


def test_student_login_redirects_to_student_dashboard(client):
    resp = client.login("student1")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/student/dashboard")
    page = client.get("/student/dashboard")
    assert page.status_code == 200
    assert b"Rahul Patil" in page.data


def test_teacher_login_redirects_to_teacher_dashboard(client):
    resp = client.login("teacher1")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/teacher/dashboard")
    assert client.get("/teacher/dashboard").status_code == 200


def test_wrong_password_rejected(client):
    resp = client.login("student1", "wrong-password")
    assert resp.status_code == 401
    assert b"Invalid username or password" in resp.data
    assert client.get("/student/dashboard").status_code == 302


def test_unknown_user_rejected(client):
    assert client.login("nobody", "whatever").status_code == 401


def test_logout_clears_session(student):
    assert student.get("/student/dashboard").status_code == 200
    student.logout()
    resp = student.get("/student/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anonymous_user_redirected_to_login(client):
    for url in ("/student/dashboard", "/teacher/dashboard", "/submissions/1", "/viva/attempts/1"):
        resp = client.get(url)
        assert resp.status_code == 302, url
        assert "/login" in resp.headers["Location"]


def test_post_without_csrf_token_rejected(app):
    raw = app.test_client()
    resp = raw.post("/login", data={"username": "student1", "password": "Student@123"})
    assert resp.status_code == 400


def test_open_redirect_blocked(client):
    resp = client.post("/login?next=//evil.example.com",
                       {"username": "student1", "password": "Student@123"})
    assert "evil.example.com" not in resp.headers["Location"]


def test_passwords_are_hashed(query):
    rows = query("SELECT password_hash FROM users")
    assert rows and all(not r["password_hash"].endswith("@123") for r in rows)
    assert all(("pbkdf2" in r["password_hash"]) or ("scrypt" in r["password_hash"]) for r in rows)
