"""Feature E: performance summaries built only from the student's own data."""
import json

from app.db import get_db
from app.services.performance import collect_student_stats
from tests.conftest import assert_rate_limited_message, good_viva, user_id
from tests.fakes import RateLimitError

TOOL = "record_performance_summary"
SUMMARY = {
    "overview": "Solid progress in search algorithms.",
    "strong_topics": ["Uninformed Search (BFS/DFS)"],
    "weak_topics": ["A* Search"],
    "revision_topics": ["A* Search"],
    "study_suggestions": ["Explain admissibility with an example."],
}


def test_stats_use_only_own_results(app, query):
    with app.app_context():
        s1 = collect_student_stats(get_db(), user_id(query, "student1"))
        s3 = collect_student_stats(get_db(), user_id(query, "student3"))
    # student1: 1 viva attempt + 2 submissions (BFS, Manhattan), all tests pass
    assert s1["assessment_count"] == 3
    assert {t["topic"] for t in s1["topics"]} == {"A* Search", "Uninformed Search (BFS/DFS)"}
    # student3 only has a failing BFS submission and no viva attempts
    assert s3["assessment_count"] == 1 and s3["viva_attempts"] == []
    assert s3["overall_percent"] < 100


def test_ai_summary_generated_and_stored(student, fake_claude, query):
    fake_claude.set(TOOL, SUMMARY)
    student.post("/student/summary")
    row = query("SELECT * FROM ai_feedback WHERE kind = 'summary'")[0]
    assert row["student_id"] == user_id(query, "student1") and row["source"] == "groq"
    prompt = fake_claude.last_prompt()
    assert "Rahul" not in prompt and "Sneha" not in prompt and "Arjun" not in prompt
    assert "<performance_data>" in prompt
    page = student.get("/student/summary").data.decode()
    assert "Explain admissibility" in page and "Groq AI" in page


def test_summary_prompt_excludes_other_students(student, fake_claude, query):
    fake_claude.set(TOOL, SUMMARY)
    student.post("/student/summary")
    prompt = fake_claude.last_prompt()
    data = json.loads(prompt.split("<performance_data>")[1].split("</performance_data>")[0])
    own_subs = query("SELECT COUNT(*) AS n FROM submissions WHERE student_id = ?",
                     (user_id(query, "student1"),))[0]["n"]
    assert len(data["programming_submissions"]) == own_subs


def test_rule_based_summary_labelled(student, query):
    student.post("/student/summary/rules")
    row = query("SELECT * FROM ai_feedback WHERE kind = 'summary'")[0]
    assert row["source"] == "rule_based"
    assert "NOT an AI assessment" in json.loads(row["payload_json"])["overview"]


def test_summary_with_no_data(app, fake_claude):
    from tests.conftest import Client

    c = Client(app.test_client())
    c.login("student2")
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM submissions WHERE student_id = (SELECT id FROM users WHERE username='student2')")
        db.commit()
    resp = c.post("/student/summary", follow_redirects=True)
    assert b"no assessed results yet" in resp.data
    assert fake_claude.calls == []


def test_summary_api_error(student, fake_claude, query):
    fake_claude.set(TOOL, RateLimitError("429"))
    resp = student.post("/student/summary", follow_redirects=True)
    assert_rate_limited_message(resp.get_data(as_text=True))
    assert query("SELECT COUNT(*) AS n FROM ai_feedback WHERE kind = 'summary'")[0]["n"] == 0


def test_teacher_can_generate_for_student(teacher, fake_claude, query):
    fake_claude.set(TOOL, SUMMARY)
    sid = user_id(query, "student3")
    teacher.post(f"/teacher/students/{sid}/summary")
    assert query("SELECT student_id FROM ai_feedback WHERE kind = 'summary'")[0]["student_id"] == sid
    assert b"Solid progress" in teacher.get(f"/teacher/students/{sid}/summary").data


def test_student_cannot_request_other_students_summary(student, query):
    sid = user_id(query, "student2")
    assert student.get(f"/teacher/students/{sid}/summary").status_code == 403
    assert student.post(f"/teacher/students/{sid}/summary").status_code == 403


def test_summary_reflects_new_scores(student, fake_claude, app, query):
    fake_claude.set("record_viva_assessment", good_viva(1, 5))
    student.post("/student/viva/3", {"answer": "alpha beta"})
    with app.app_context():
        stats = collect_student_stats(get_db(), user_id(query, "student1"))
    topics = {t["topic"]: t["percent"] for t in stats["topics"]}
    assert topics["Minimax and Alpha-Beta Pruning"] == 20.0
