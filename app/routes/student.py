"""Student pages: dashboard, viva answers, programming submissions, summary."""
from __future__ import annotations

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)

from ..auth import role_required
from ..db import get_db
from ..repository import create_submission
from ..security import ai_rate_limited
from .common import evaluate_attempt_with_ai, generate_summary, latest_summary, parse_payload

bp = Blueprint("student", __name__, url_prefix="/student")


@bp.route("/dashboard")
@role_required("student")
def dashboard():
    db = get_db()
    uid = g.user["id"]
    questions = db.execute(
        """SELECT q.*,
              (SELECT s.score FROM viva_attempts a JOIN viva_scores s ON s.attempt_id = a.id
                WHERE a.question_id = q.id AND a.student_id = ? ORDER BY a.id DESC LIMIT 1) AS last_score
           FROM viva_questions q WHERE q.status = 'published' ORDER BY q.experiment, q.id""",
        (uid,),
    ).fetchall()
    assignments = db.execute(
        """SELECT a.*,
              (SELECT COUNT(*) FROM test_cases t WHERE t.assignment_id = a.id) AS test_count,
              (SELECT MAX(p) FROM (SELECT SUM(tr.passed) AS p FROM submissions s
                   JOIN test_results tr ON tr.submission_id = s.id
                   WHERE s.assignment_id = a.id AND s.student_id = ? GROUP BY s.id)) AS best_passed
           FROM assignments a ORDER BY a.id""",
        (uid,),
    ).fetchall()
    attempts = db.execute(
        """SELECT a.id, a.status, a.created_at, q.question, q.experiment,
                  s.score, s.max_score, s.source
           FROM viva_attempts a JOIN viva_questions q ON q.id = a.question_id
           LEFT JOIN viva_scores s ON s.attempt_id = a.id
           WHERE a.student_id = ? ORDER BY a.id DESC LIMIT 15""",
        (uid,),
    ).fetchall()
    submissions = db.execute(
        """SELECT s.id, s.created_at, s.static_errors, a.title,
                  COUNT(tr.id) AS total, COALESCE(SUM(tr.passed), 0) AS passed
           FROM submissions s JOIN assignments a ON a.id = s.assignment_id
           LEFT JOIN test_results tr ON tr.submission_id = s.id
           WHERE s.student_id = ? GROUP BY s.id ORDER BY s.id DESC LIMIT 15""",
        (uid,),
    ).fetchall()
    return render_template("student/dashboard.html", questions=questions, assignments=assignments,
                           attempts=attempts, submissions=submissions)


def _published_question(question_id: int):
    q = get_db().execute(
        "SELECT * FROM viva_questions WHERE id = ? AND status = 'published'", (question_id,)
    ).fetchone()
    if q is None:
        abort(404)
    return q


@bp.route("/viva/<int:question_id>", methods=["GET"])
@role_required("student")
def viva_question(question_id):
    return render_template("student/viva_question.html", question=_published_question(question_id),
                           max_chars=current_app.config["MAX_ANSWER_CHARS"])


@bp.route("/viva/<int:question_id>", methods=["POST"])
@role_required("student")
@ai_rate_limited
def submit_viva(question_id):
    question = _published_question(question_id)
    answer = request.form.get("answer", "").strip()
    limit = current_app.config["MAX_ANSWER_CHARS"]
    if not answer:
        flash("Please write an answer before submitting.", "warning")
        return render_template("student/viva_question.html", question=question, max_chars=limit), 400
    if len(answer) > limit:
        flash(f"Answers are limited to {limit} characters (yours has {len(answer)}).", "warning")
        return render_template("student/viva_question.html", question=question,
                               max_chars=limit, answer=answer), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO viva_attempts (student_id, question_id, answer) VALUES (?, ?, ?)",
        (g.user["id"], question["id"], answer),
    )
    db.commit()
    attempt = db.execute("SELECT * FROM viva_attempts WHERE id = ?", (cur.lastrowid,)).fetchone()
    evaluate_attempt_with_ai(attempt)
    return redirect(url_for("shared.view_attempt", attempt_id=attempt["id"]))


@bp.route("/assignments/<int:assignment_id>", methods=["GET", "POST"])
@role_required("student")
def assignment(assignment_id):
    db = get_db()
    asg = db.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if asg is None:
        abort(404)
    visible_tests = db.execute(
        "SELECT * FROM test_cases WHERE assignment_id = ? AND is_hidden = 0 ORDER BY id", (asg["id"],)
    ).fetchall()
    hidden_count = db.execute(
        "SELECT COUNT(*) FROM test_cases WHERE assignment_id = ? AND is_hidden = 1", (asg["id"],)
    ).fetchone()[0]
    limit = current_app.config["MAX_CODE_CHARS"]
    code = asg["starter_code"]
    if request.method == "POST":
        code = request.form.get("code", "").replace("\r\n", "\n")
        if not code.strip():
            flash("Please enter your code.", "warning")
        elif len(code) > limit:
            flash(f"Source code is limited to {limit} characters (yours has {len(code)}).", "warning")
        else:
            submission_id, report = create_submission(
                db, asg, g.user["id"], code, current_app.config["CODE_TIMEOUT_SECONDS"])
            if report.static_errors:
                flash("Your code was not run because it failed the safety/syntax checks.", "danger")
            else:
                flash(f"Tests passed: {report.passed_count} of {len(report.outcomes)}.",
                      "success" if report.passed_count == len(report.outcomes) else "warning")
            return redirect(url_for("shared.view_submission", submission_id=submission_id))
        return render_template("student/assignment.html", assignment=asg, tests=visible_tests,
                               hidden_count=hidden_count, code=code, max_chars=limit), 400
    return render_template("student/assignment.html", assignment=asg, tests=visible_tests,
                           hidden_count=hidden_count, code=code, max_chars=limit)


@bp.route("/summary", methods=["GET"])
@role_required("student")
def summary():
    row = latest_summary(g.user["id"])
    return render_template("summary.html", student=g.user, summary=row,
                           payload=parse_payload(row), action=url_for("student.generate"),
                           rules_action=url_for("student.generate_rules"))


@bp.route("/summary", methods=["POST"])
@role_required("student")
@ai_rate_limited
def generate():
    generate_summary(g.user["id"])
    return redirect(url_for("student.summary"))


@bp.route("/summary/rules", methods=["POST"])
@role_required("student")
def generate_rules():
    generate_summary(g.user["id"], use_rules=True)
    return redirect(url_for("student.summary"))
