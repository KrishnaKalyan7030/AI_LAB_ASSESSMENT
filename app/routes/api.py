"""Small JSON API. Responses never contain secrets or other students' data."""
from __future__ import annotations

import json

from flask import Blueprint, current_app, g

from ..auth import login_required
from ..db import get_db
from ..repository import get_claude_service
from .common import load_attempt_or_404

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/health")
def health():
    service = get_claude_service(current_app.config)
    # Only report WHETHER AI is configured; never the key itself.
    return {"status": "ok", "ai_provider": service.provider,
            "ai_configured": service.is_configured, "model": service.model or None}


@bp.route("/me/results")
@login_required
def my_results():
    db = get_db()
    uid = g.user["id"]
    viva = db.execute(
        """SELECT a.id AS attempt_id, q.experiment, a.status, s.score, s.max_score, s.source, s.model
           FROM viva_attempts a JOIN viva_questions q ON q.id = a.question_id
           LEFT JOIN viva_scores s ON s.attempt_id = a.id
           WHERE a.student_id = ? ORDER BY a.id""", (uid,)).fetchall()
    subs = db.execute(
        """SELECT s.id AS submission_id, a.title, COUNT(tr.id) AS tests_total,
                  COALESCE(SUM(tr.passed), 0) AS tests_passed
           FROM submissions s JOIN assignments a ON a.id = s.assignment_id
           LEFT JOIN test_results tr ON tr.submission_id = s.id
           WHERE s.student_id = ? GROUP BY s.id ORDER BY s.id""", (uid,)).fetchall()
    return {"user": {"id": uid, "username": g.user["username"], "role": g.user["role"]},
            "viva": [dict(r) for r in viva], "submissions": [dict(r) for r in subs]}


@bp.route("/attempts/<int:attempt_id>")
@login_required
def attempt(attempt_id):
    row = load_attempt_or_404(attempt_id)
    db = get_db()
    score = db.execute("SELECT score, max_score, source, model FROM viva_scores WHERE attempt_id = ?",
                       (attempt_id,)).fetchone()
    fb = db.execute("SELECT payload_json, source, model FROM ai_feedback WHERE kind = 'viva' AND ref_id = ? "
                    "ORDER BY id DESC LIMIT 1", (attempt_id,)).fetchone()
    return {
        "attempt_id": row["id"], "student_id": row["student_id"], "status": row["status"],
        "question": row["question"], "answer": row["answer"],
        "score": dict(score) if score else None,
        "feedback": {"source": fb["source"], "model": fb["model"], **json.loads(fb["payload_json"])} if fb else None,
        "error": row["last_error"],
    }
