"""Database helpers shared by routes and the seed script."""
from __future__ import annotations

import json

from .services.code_runner import RunReport, run_tests


def get_ai_service(app_config):
    """The configured AI provider (Claude by default, or Gemini)."""
    from .services.ai_provider import get_ai_service as _get

    return _get(app_config)


get_claude_service = get_ai_service  # backwards-compatible name


# --- Viva ------------------------------------------------------------------
def store_viva_result(db, attempt, result: dict, source: str, model: str | None) -> None:
    """Persist a validated viva assessment: score row + qualitative feedback row."""
    db.execute("DELETE FROM viva_scores WHERE attempt_id = ?", (attempt["id"],))
    db.execute("DELETE FROM ai_feedback WHERE kind = 'viva' AND ref_id = ?", (attempt["id"],))
    db.execute(
        "INSERT INTO viva_scores (attempt_id, score, max_score, source, model) VALUES (?, ?, ?, ?, ?)",
        (attempt["id"], result["score"], result["max_score"], source, model),
    )
    feedback = {k: v for k, v in result.items() if k not in ("score", "max_score")}
    db.execute(
        "INSERT INTO ai_feedback (student_id, kind, ref_id, payload_json, source, model) "
        "VALUES (?, 'viva', ?, ?, ?, ?)",
        (attempt["student_id"], attempt["id"], json.dumps(feedback), source, model),
    )
    db.execute("UPDATE viva_attempts SET status = 'evaluated', last_error = NULL WHERE id = ?",
               (attempt["id"],))
    db.commit()


def mark_viva_failed(db, attempt_id: int, message: str) -> None:
    db.execute("UPDATE viva_attempts SET status = 'ai_failed', last_error = ? WHERE id = ?",
               (message[:500], attempt_id))
    db.commit()


# --- Programming -------------------------------------------------------------
def create_submission(db, assignment, student_id: int, code: str, timeout: float) -> tuple[int, RunReport]:
    tests = db.execute(
        "SELECT * FROM test_cases WHERE assignment_id = ? ORDER BY id", (assignment["id"],)
    ).fetchall()
    report = run_tests(code, assignment["function_name"], tests, timeout=timeout)
    cur = db.execute(
        "INSERT INTO submissions (assignment_id, student_id, code, static_errors) VALUES (?, ?, ?, ?)",
        (assignment["id"], student_id, code,
         json.dumps(report.static_errors) if report.static_errors else None),
    )
    submission_id = cur.lastrowid
    for o in report.outcomes:
        db.execute(
            "INSERT INTO test_results (submission_id, test_case_id, passed, actual_json, error, stdout, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (submission_id, o.test_case_id, int(o.passed),
             json.dumps(o.actual) if o.error is None else None, o.error, o.stdout, o.duration_ms),
        )
    db.commit()
    return submission_id, report


def submission_test_rows(db, submission_id: int):
    return db.execute(
        """SELECT tr.*, tc.description, tc.args_json, tc.expected_json, tc.is_hidden
           FROM test_results tr JOIN test_cases tc ON tc.id = tr.test_case_id
           WHERE tr.submission_id = ? ORDER BY tc.id""",
        (submission_id,),
    ).fetchall()


def test_report_for_ai(rows) -> list:
    """Test-engine results in the shape sent to Claude (hidden test inputs withheld)."""
    report = []
    for i, r in enumerate(rows, 1):
        entry = {"test": f"Test {i}" + (" (hidden)" if r["is_hidden"] else ""),
                 "passed": bool(r["passed"])}
        if not r["is_hidden"]:
            entry.update({"arguments": json.loads(r["args_json"]),
                          "expected": json.loads(r["expected_json"]),
                          "actual": json.loads(r["actual_json"]) if r["actual_json"] else None})
        if r["error"]:
            entry["error"] = r["error"]
        report.append(entry)
    return report


def save_feedback(db, student_id: int, kind: str, ref_id, payload: dict, source: str, model) -> int:
    cur = db.execute(
        "INSERT INTO ai_feedback (student_id, kind, ref_id, payload_json, source, model) VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, kind, ref_id, json.dumps(payload), source, model),
    )
    db.commit()
    return cur.lastrowid


def save_quality_report(db, submission_id: int, payload: dict, source: str, model) -> int:
    cur = db.execute(
        "INSERT INTO code_quality_reports (submission_id, payload_json, source, model) VALUES (?, ?, ?, ?)",
        (submission_id, json.dumps(payload), source, model),
    )
    db.commit()
    return cur.lastrowid
