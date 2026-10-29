"""Authorization: roles and ownership of results."""
from tests.conftest import user_id


def test_student_cannot_open_teacher_pages(student):
    for url in ("/teacher/dashboard", "/teacher/questions", "/teacher/assignments/new",
                "/teacher/assignments/1", "/teacher/students/2", "/teacher/questions/generate"):
        assert student.get(url).status_code == 403, url


def test_student_cannot_post_teacher_actions(student, query):
    assert student.post("/teacher/questions/6/publish").status_code == 403
    assert query("SELECT status FROM viva_questions WHERE id = 6")[0]["status"] == "draft"
    assert student.post("/teacher/assignments/1/similarity").status_code == 403


def test_teacher_cannot_use_student_only_pages(teacher):
    assert teacher.get("/student/dashboard").status_code == 403
    assert teacher.post("/student/viva/1", {"answer": "x"}).status_code == 403


def test_student_cannot_view_another_students_attempt(student2, query):
    attempt = query("SELECT a.id FROM viva_attempts a JOIN users u ON u.id = a.student_id "
                    "WHERE u.username = 'student1'")[0]["id"]
    assert student2.get(f"/viva/attempts/{attempt}").status_code == 404
    assert student2.get(f"/api/attempts/{attempt}").status_code == 404
    assert student2.post(f"/viva/attempts/{attempt}/retry").status_code == 404
    assert student2.post(f"/viva/attempts/{attempt}/fallback").status_code == 404


def test_student_cannot_view_or_trigger_ai_on_another_students_submission(student2, query, fake_claude):
    sub = query("SELECT s.id FROM submissions s JOIN users u ON u.id = s.student_id "
                "WHERE u.username = 'student1' LIMIT 1")[0]["id"]
    assert student2.get(f"/submissions/{sub}").status_code == 404
    assert student2.post(f"/submissions/{sub}/assist").status_code == 404
    assert student2.post(f"/submissions/{sub}/quality").status_code == 404
    assert fake_claude.calls == []  # no AI call was made for the unauthorised request


def test_owner_and_teacher_can_view(student, teacher, query):
    attempt = query("SELECT a.id FROM viva_attempts a JOIN users u ON u.id = a.student_id "
                    "WHERE u.username = 'student1'")[0]["id"]
    assert student.get(f"/viva/attempts/{attempt}").status_code == 200
    assert teacher.get(f"/viva/attempts/{attempt}").status_code == 200
    assert teacher.get("/submissions/2").status_code == 200


def test_students_cannot_see_draft_questions(student, query):
    draft = query("SELECT id FROM viva_questions WHERE status = 'draft'")[0]["id"]
    assert student.get(f"/student/viva/{draft}").status_code == 404
    assert student.post(f"/student/viva/{draft}", {"answer": "anything"}).status_code == 404
    assert b"minimum-remaining-values" not in student.get("/student/dashboard").data


def test_api_results_only_include_own_data(student, query):
    data = student.get("/api/me/results").get_json()
    own = user_id(query, "student1")
    assert data["user"]["id"] == own
    own_subs = {r["id"] for r in query("SELECT id FROM submissions WHERE student_id = ?", (own,))}
    assert {s["submission_id"] for s in data["submissions"]} == own_subs


def test_reference_answer_hidden_from_students(student, query):
    attempt = query("SELECT a.id FROM viva_attempts a JOIN users u ON u.id = a.student_id "
                    "WHERE u.username = 'student1'")[0]["id"]
    page = student.get(f"/viva/attempts/{attempt}").data.decode()
    assert "Reference answer" not in page
    assert "1 mark: defines h(n)" not in page


def test_hidden_test_inputs_hidden_from_students(student, teacher):
    student_page = student.get("/submissions/1").data.decode()
    teacher_page = teacher.get("/submissions/1").data.decode()
    assert "Hidden test" in student_page
    assert '&#34;U&#34;' not in student_page and '"U"' not in student_page
    assert "&#34;U&#34;" in teacher_page or '"U"' in teacher_page
