"""Assignment creation, student submission and objective test results."""
import json

CORRECT_MANHATTAN = "def manhattan(a, b):\n    return abs(a[0] - b[0]) + abs(a[1] - b[1])\n"
WRONG_MANHATTAN = "def manhattan(a, b):\n    return (a[0] - b[0]) + (a[1] - b[1])\n"


def _new_assignment(teacher, **overrides):
    data = {
        "title": "Square numbers",
        "experiment": "Python warm-up",
        "description": "Return n squared.",
        "function_name": "square",
        "starter_code": "def square(n):\n    return 0\n",
        "tests": "\n".join([
            json.dumps({"args": [3], "expected": 9, "description": "three"}),
            json.dumps({"args": [-4], "expected": 16}),
            json.dumps({"args": [0], "expected": 0, "hidden": True}),
        ]),
    }
    data.update(overrides)
    return teacher.post("/teacher/assignments/new", data)


def test_teacher_creates_assignment_with_tests(teacher, query):
    resp = _new_assignment(teacher)
    assert resp.status_code == 302
    asg = query("SELECT * FROM assignments WHERE title = 'Square numbers'")[0]
    tests = query("SELECT * FROM test_cases WHERE assignment_id = ? ORDER BY id", (asg["id"],))
    assert [json.loads(t["args_json"]) for t in tests] == [[3], [-4], [0]]
    assert [t["is_hidden"] for t in tests] == [0, 0, 1]


def test_invalid_test_json_rejected(teacher, query):
    resp = _new_assignment(teacher, tests='{"args": [1], "expected": }')
    assert resp.status_code == 400
    assert b"invalid JSON" in resp.data
    assert query("SELECT COUNT(*) AS n FROM assignments WHERE title = 'Square numbers'")[0]["n"] == 0


def test_invalid_function_name_rejected(teacher):
    resp = _new_assignment(teacher, function_name="not valid!")
    assert resp.status_code == 400


def test_args_must_be_list(teacher):
    resp = _new_assignment(teacher, tests='{"args": 3, "expected": 9}')
    assert resp.status_code == 400


def test_student_submits_correct_program(student, query):
    resp = student.post("/student/assignments/2", {"code": CORRECT_MANHATTAN})
    assert resp.status_code == 302
    sub_id = int(resp.headers["Location"].rstrip("/").split("/")[-1])
    results = query("SELECT passed FROM test_results WHERE submission_id = ?", (sub_id,))
    assert len(results) == 4 and all(r["passed"] == 1 for r in results)
    page = student.get(f"/submissions/{sub_id}").data.decode()
    assert "4/4 tests passed" in page


def test_student_submits_incorrect_program(student, query):
    resp = student.post("/student/assignments/2", {"code": WRONG_MANHATTAN})
    sub_id = int(resp.headers["Location"].split("/")[-1])
    results = query("SELECT tr.passed, tr.actual_json, tc.expected_json FROM test_results tr "
                    "JOIN test_cases tc ON tc.id = tr.test_case_id WHERE submission_id = ? ORDER BY tc.id",
                    (sub_id,))
    passed = [r["passed"] for r in results]
    assert passed == [1, 0, 0, 0]  # only the "same cell" case passes without abs()
    assert json.loads(results[1]["actual_json"]) == -7


def test_runtime_error_recorded(student, query):
    resp = student.post("/student/assignments/2", {"code": "def manhattan(a, b):\n    return a[5]\n"})
    sub_id = int(resp.headers["Location"].split("/")[-1])
    errors = query("SELECT error FROM test_results WHERE submission_id = ?", (sub_id,))
    assert all("IndexError" in r["error"] for r in errors)


def test_dangerous_code_rejected_before_running(student, query):
    code = "import os\ndef manhattan(a, b):\n    os.system('echo hacked')\n    return 0\n"
    resp = student.post("/student/assignments/2", {"code": code})
    sub_id = int(resp.headers["Location"].split("/")[-1])
    sub = query("SELECT static_errors FROM submissions WHERE id = ?", (sub_id,))[0]
    assert "os" in sub["static_errors"]
    assert query("SELECT COUNT(*) AS n FROM test_results WHERE submission_id = ?", (sub_id,))[0]["n"] == 0


def test_code_size_limit(make_app):
    from tests.conftest import Client

    app = make_app(MAX_CODE_CHARS=100)
    c = Client(app.test_client())
    c.login("student1")
    resp = c.post("/student/assignments/2", {"code": "# x\n" * 100})
    assert resp.status_code == 400
    assert b"limited to 100 characters" in resp.data


def test_empty_code_rejected(student):
    assert student.post("/student/assignments/2", {"code": "   "}).status_code == 400


def test_seeded_submissions_have_real_results(query):
    rows = query("""SELECT u.username, SUM(tr.passed) AS passed, COUNT(*) AS total
                    FROM submissions s JOIN users u ON u.id = s.student_id
                    JOIN test_results tr ON tr.submission_id = s.id
                    WHERE s.assignment_id = 1 GROUP BY s.id ORDER BY u.username""")
    by_user = {r["username"]: (r["passed"], r["total"]) for r in rows}
    assert by_user["student1"] == (4, 4)
    assert by_user["student2"] == (4, 4)
    assert by_user["student3"][0] < 4  # DFS-style attempt fails BFS ordering
