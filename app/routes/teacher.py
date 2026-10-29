"""Teacher pages: assignments, question bank, AI question generation,
similarity checks and per-student results."""
from __future__ import annotations

import itertools
import json
import re

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, url_for)

from ..auth import role_required
from ..db import get_db
from ..repository import get_claude_service
from ..security import ai_rate_limited
from ..services import ai_features
from ..services.claude_service import ClaudeServiceError
from ..services.similarity import HUMAN_REVIEW_NOTE, compare
from ..services.validators import DIFFICULTIES
from .common import generate_summary, latest_summary, parse_payload

bp = Blueprint("teacher", __name__, url_prefix="/teacher")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")


@bp.route("/dashboard")
@role_required("teacher")
def dashboard():
    db = get_db()
    assignments = db.execute(
        """SELECT a.*, (SELECT COUNT(*) FROM submissions s WHERE s.assignment_id = a.id) AS submission_count,
                  (SELECT COUNT(*) FROM similarity_comparisons c WHERE c.assignment_id = a.id
                     AND c.similarity >= ?) AS flagged
           FROM assignments a ORDER BY a.id""",
        (current_app.config["SIMILARITY_WARNING_THRESHOLD"],),
    ).fetchall()
    counts = db.execute(
        "SELECT status, COUNT(*) AS n FROM viva_questions GROUP BY status").fetchall()
    students = db.execute(
        """SELECT u.id, u.full_name, u.username,
                  (SELECT COUNT(*) FROM viva_attempts a WHERE a.student_id = u.id) AS attempts,
                  (SELECT ROUND(AVG(100.0 * s.score / s.max_score), 1) FROM viva_attempts a
                     JOIN viva_scores s ON s.attempt_id = a.id WHERE a.student_id = u.id) AS viva_avg,
                  (SELECT COUNT(*) FROM submissions s WHERE s.student_id = u.id) AS submissions
           FROM users u WHERE u.role = 'student' ORDER BY u.full_name"""
    ).fetchall()
    return render_template("teacher/dashboard.html", assignments=assignments,
                           counts={r["status"]: r["n"] for r in counts}, students=students)


# --- Assignments ------------------------------------------------------------------
def _parse_tests(raw: str) -> tuple[list, list]:
    tests, errors = [], []
    for n, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError as exc:
            errors.append(f"Test line {n}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(item, dict) or "args" not in item or "expected" not in item:
            errors.append(f"Test line {n}: needs \"args\" and \"expected\" keys")
            continue
        if not isinstance(item["args"], list):
            errors.append(f"Test line {n}: \"args\" must be a JSON list of arguments")
            continue
        tests.append({
            "args": item["args"], "expected": item["expected"],
            "hidden": bool(item.get("hidden", False)),
            "description": str(item.get("description", ""))[:200],
        })
    if not tests and not errors:
        errors.append("Add at least one test case.")
    return tests, errors


@bp.route("/assignments/new", methods=["GET", "POST"])
@role_required("teacher")
def new_assignment():
    form = request.form
    if request.method == "POST":
        errors = []
        title, experiment = form.get("title", "").strip(), form.get("experiment", "").strip()
        description, function_name = form.get("description", "").strip(), form.get("function_name", "").strip()
        starter = form.get("starter_code", "").replace("\r\n", "\n")
        if not title or not experiment or not description:
            errors.append("Title, experiment and description are required.")
        if not IDENTIFIER.match(function_name):
            errors.append("Function name must be a valid Python identifier.")
        if len(starter) > current_app.config["MAX_CODE_CHARS"]:
            errors.append("Starter code is too long.")
        tests, test_errors = _parse_tests(form.get("tests", ""))
        errors += test_errors
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("teacher/assignment_form.html", form=form), 400
        db = get_db()
        cur = db.execute(
            "INSERT INTO assignments (title, experiment, description, function_name, starter_code, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title[:200], experiment[:200], description[:5000], function_name, starter, g.user["id"]),
        )
        for t in tests:
            db.execute(
                "INSERT INTO test_cases (assignment_id, description, args_json, expected_json, is_hidden) "
                "VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, t["description"], json.dumps(t["args"]), json.dumps(t["expected"]), int(t["hidden"])),
            )
        db.commit()
        flash(f"Assignment created with {len(tests)} test case(s).", "success")
        return redirect(url_for("teacher.assignment_detail", assignment_id=cur.lastrowid))
    return render_template("teacher/assignment_form.html", form={})


@bp.route("/assignments/<int:assignment_id>")
@role_required("teacher")
def assignment_detail(assignment_id):
    db = get_db()
    asg = db.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    if asg is None:
        abort(404)
    tests = db.execute("SELECT * FROM test_cases WHERE assignment_id = ? ORDER BY id", (assignment_id,)).fetchall()
    submissions = db.execute(
        """SELECT s.id, s.created_at, s.static_errors, u.full_name,
                  COUNT(tr.id) AS total, COALESCE(SUM(tr.passed), 0) AS passed
           FROM submissions s JOIN users u ON u.id = s.student_id
           LEFT JOIN test_results tr ON tr.submission_id = s.id
           WHERE s.assignment_id = ? GROUP BY s.id ORDER BY s.id DESC""",
        (assignment_id,),
    ).fetchall()
    comparisons = db.execute(
        """SELECT c.*, ua.full_name AS name_a, ub.full_name AS name_b
           FROM similarity_comparisons c
           JOIN submissions sa ON sa.id = c.submission_a JOIN users ua ON ua.id = sa.student_id
           JOIN submissions sb ON sb.id = c.submission_b JOIN users ub ON ub.id = sb.student_id
           WHERE c.assignment_id = ? ORDER BY c.similarity DESC""",
        (assignment_id,),
    ).fetchall()
    return render_template("teacher/assignment_detail.html", assignment=asg, tests=tests,
                           submissions=submissions, comparisons=comparisons,
                           threshold=current_app.config["SIMILARITY_WARNING_THRESHOLD"],
                           review_note=HUMAN_REVIEW_NOTE)


@bp.route("/assignments/<int:assignment_id>/similarity", methods=["POST"])
@role_required("teacher")
def run_similarity(assignment_id):
    db = get_db()
    if db.execute("SELECT 1 FROM assignments WHERE id = ?", (assignment_id,)).fetchone() is None:
        abort(404)
    latest = db.execute(
        """SELECT s.* FROM submissions s
           WHERE s.assignment_id = ? AND s.id = (SELECT MAX(id) FROM submissions s2
                 WHERE s2.assignment_id = s.assignment_id AND s2.student_id = s.student_id)
           ORDER BY s.id""",
        (assignment_id,),
    ).fetchall()
    threshold = current_app.config["SIMILARITY_WARNING_THRESHOLD"]
    db.execute("DELETE FROM similarity_comparisons WHERE assignment_id = ?", (assignment_id,))
    flagged = 0
    for a, b in itertools.combinations(latest, 2):
        result = compare(a["code"], b["code"], threshold)
        flagged += result.flagged
        db.execute(
            "INSERT INTO similarity_comparisons (assignment_id, submission_a, submission_b, similarity, details_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (assignment_id, a["id"], b["id"], result.similarity, json.dumps({
                "sequence_ratio": result.sequence_ratio,
                "fingerprint_overlap": result.fingerprint_overlap,
                "matching_sections": result.matching_sections,
            })),
        )
    db.commit()
    pairs = len(latest) * (len(latest) - 1) // 2
    flash(f"Compared {pairs} pair(s) of latest submissions; {flagged} above the "
          f"{threshold:.0%} warning threshold. Similar code requires human review.",
          "warning" if flagged else "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id) + "#similarity")


@bp.route("/similarity/<int:comparison_id>")
@role_required("teacher")
def similarity_detail(comparison_id):
    db = get_db()
    row = db.execute(
        """SELECT c.*, a.title, sa.code AS code_a, sb.code AS code_b,
                  ua.full_name AS name_a, ub.full_name AS name_b
           FROM similarity_comparisons c JOIN assignments a ON a.id = c.assignment_id
           JOIN submissions sa ON sa.id = c.submission_a JOIN users ua ON ua.id = sa.student_id
           JOIN submissions sb ON sb.id = c.submission_b JOIN users ub ON ub.id = sb.student_id
           WHERE c.id = ?""",
        (comparison_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return render_template("teacher/similarity.html", c=row, details=json.loads(row["details_json"]),
                           threshold=current_app.config["SIMILARITY_WARNING_THRESHOLD"],
                           review_note=HUMAN_REVIEW_NOTE)


# --- Question bank ------------------------------------------------------------------
@bp.route("/questions")
@role_required("teacher")
def questions():
    status = request.args.get("status", "")
    sql = "SELECT * FROM viva_questions"
    params = ()
    if status in ("draft", "published"):
        sql += " WHERE status = ?"
        params = (status,)
    rows = get_db().execute(sql + " ORDER BY status, experiment, id", params).fetchall()
    return render_template("teacher/questions.html", questions=rows, status=status)


def _question_form_values(form, default_max: int) -> tuple[dict, list]:
    errors = []
    values = {k: form.get(k, "").strip() for k in
              ("subject", "experiment", "question", "reference_answer", "rubric", "key_concepts", "difficulty")}
    for key in ("subject", "experiment", "question", "reference_answer", "rubric"):
        if not values[key]:
            errors.append(f"{key.replace('_', ' ').capitalize()} is required.")
    if values["difficulty"] not in DIFFICULTIES:
        errors.append("Choose a valid difficulty.")
    try:
        values["max_score"] = int(form.get("max_score", default_max))
        if not 1 <= values["max_score"] <= 100:
            raise ValueError
    except ValueError:
        errors.append("Maximum score must be a whole number from 1 to 100.")
        values["max_score"] = default_max
    limits = {"question": 1000, "reference_answer": 4000, "rubric": 3000, "key_concepts": 500,
              "subject": 200, "experiment": 200}
    for key, n in limits.items():
        values[key] = values[key][:n]
    return values, errors


@bp.route("/questions/new", methods=["GET", "POST"])
@bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@role_required("teacher")
def edit_question(question_id=None):
    db = get_db()
    default_max = current_app.config["VIVA_DEFAULT_MAX_SCORE"]
    question = None
    if question_id is not None:
        question = db.execute("SELECT * FROM viva_questions WHERE id = ?", (question_id,)).fetchone()
        if question is None:
            abort(404)
    if request.method == "POST":
        values, errors = _question_form_values(request.form, default_max)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("teacher/question_edit.html", q=values, question_id=question_id), 400
        cols = ("subject", "experiment", "difficulty", "question", "reference_answer", "rubric",
                "key_concepts", "max_score")
        if question is None:
            cur = db.execute(
                f"INSERT INTO viva_questions ({', '.join(cols)}, status, created_by) "
                f"VALUES ({', '.join('?' * len(cols))}, 'draft', ?)",
                (*[values[c] for c in cols], g.user["id"]),
            )
            question_id = cur.lastrowid
        else:
            db.execute(f"UPDATE viva_questions SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       (*[values[c] for c in cols], question_id))
        db.commit()
        flash("Question saved.", "success")
        if request.form.get("publish") == "1":
            return _set_status(question_id, "published")
        return redirect(url_for("teacher.edit_question", question_id=question_id))
    q = dict(question) if question else {"subject": "Artificial Intelligence Laboratory",
                                          "difficulty": "medium", "max_score": default_max}
    return render_template("teacher/question_edit.html", q=q, question_id=question_id)


def _set_status(question_id: int, status: str):
    db = get_db()
    if db.execute("SELECT 1 FROM viva_questions WHERE id = ?", (question_id,)).fetchone() is None:
        abort(404)
    db.execute("UPDATE viva_questions SET status = ? WHERE id = ?", (status, question_id))
    db.commit()
    flash("Question published - students can now answer it." if status == "published"
          else "Question moved back to drafts.", "success")
    return redirect(url_for("teacher.questions", status=status))


@bp.route("/questions/<int:question_id>/publish", methods=["POST"])
@role_required("teacher")
def publish_question(question_id):
    return _set_status(question_id, "published")


@bp.route("/questions/<int:question_id>/unpublish", methods=["POST"])
@role_required("teacher")
def unpublish_question(question_id):
    return _set_status(question_id, "draft")


@bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@role_required("teacher")
def delete_question(question_id):
    db = get_db()
    db.execute("DELETE FROM viva_questions WHERE id = ? AND status = 'draft'", (question_id,))
    db.commit()
    flash("Draft deleted (published questions cannot be deleted - unpublish first).", "info")
    return redirect(url_for("teacher.questions", status="draft"))


@bp.route("/questions/generate", methods=["GET"])
@role_required("teacher")
def generate_form():
    return render_template("teacher/generate.html", form={},
                           max_count=current_app.config["MAX_GENERATED_QUESTIONS"],
                           default_max=current_app.config["VIVA_DEFAULT_MAX_SCORE"])


@bp.route("/questions/generate", methods=["POST"])
@role_required("teacher")
@ai_rate_limited
def generate_questions():
    form = request.form
    max_count = current_app.config["MAX_GENERATED_QUESTIONS"]
    default_max = current_app.config["VIVA_DEFAULT_MAX_SCORE"]
    subject, experiment = form.get("subject", "").strip()[:200], form.get("experiment", "").strip()[:200]
    difficulty = form.get("difficulty", "medium")
    errors = []
    try:
        count = int(form.get("count", 3))
        marks = int(form.get("max_score", default_max))
    except ValueError:
        count, marks = 0, 0
    if not subject or not experiment:
        errors.append("Subject and experiment title are required.")
    if difficulty not in DIFFICULTIES:
        errors.append("Choose a valid difficulty.")
    if not 1 <= count <= max_count:
        errors.append(f"Number of questions must be between 1 and {max_count}.")
    if not 1 <= marks <= 20:
        errors.append("Marks per question must be between 1 and 20.")
    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("teacher/generate.html", form=form, max_count=max_count,
                               default_max=default_max), 400
    service = get_claude_service(current_app.config)
    try:
        result = ai_features.generate_questions(service, subject, experiment, difficulty, count, marks)
    except ClaudeServiceError as err:
        flash(err.user_message, "danger")
        return render_template("teacher/generate.html", form=form, max_count=max_count,
                               default_max=default_max), 502
    db = get_db()
    for q in result.data["questions"]:
        db.execute(
            "INSERT INTO viva_questions (subject, experiment, difficulty, question, reference_answer, rubric, "
            "key_concepts, max_score, status, ai_generated, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?)",
            (subject, experiment, difficulty, q["question"], q["reference_answer"], q["rubric"],
             ", ".join(q["key_concepts"]), marks, g.user["id"]),
        )
    db.commit()
    flash(f"{service.display_name} generated {len(result.data['questions'])} draft question(s). Review and edit each one "
          "before publishing - students cannot see drafts.", "success")
    return redirect(url_for("teacher.questions", status="draft"))


# --- Students ---------------------------------------------------------------------
def _student_or_404(student_id: int):
    student = get_db().execute(
        "SELECT id, username, full_name, role FROM users WHERE id = ? AND role = 'student'", (student_id,)
    ).fetchone()
    if student is None:
        abort(404)
    return student


@bp.route("/students/<int:student_id>")
@role_required("teacher")
def student_detail(student_id):
    student = _student_or_404(student_id)
    db = get_db()
    attempts = db.execute(
        """SELECT a.id, a.status, a.created_at, q.question, q.experiment, s.score, s.max_score, s.source
           FROM viva_attempts a JOIN viva_questions q ON q.id = a.question_id
           LEFT JOIN viva_scores s ON s.attempt_id = a.id
           WHERE a.student_id = ? ORDER BY a.id DESC""", (student_id,)).fetchall()
    submissions = db.execute(
        """SELECT s.id, s.created_at, a.title, COUNT(tr.id) AS total, COALESCE(SUM(tr.passed), 0) AS passed
           FROM submissions s JOIN assignments a ON a.id = s.assignment_id
           LEFT JOIN test_results tr ON tr.submission_id = s.id
           WHERE s.student_id = ? GROUP BY s.id ORDER BY s.id DESC""", (student_id,)).fetchall()
    return render_template("teacher/student_detail.html", student=student,
                           attempts=attempts, submissions=submissions)


@bp.route("/students/<int:student_id>/summary", methods=["GET"])
@role_required("teacher")
def student_summary(student_id):
    student = _student_or_404(student_id)
    row = latest_summary(student_id)
    return render_template("summary.html", student=student, summary=row, payload=parse_payload(row),
                           action=url_for("teacher.student_summary_generate", student_id=student_id),
                           rules_action=url_for("teacher.student_summary_rules", student_id=student_id))


@bp.route("/students/<int:student_id>/summary", methods=["POST"])
@role_required("teacher")
@ai_rate_limited
def student_summary_generate(student_id):
    _student_or_404(student_id)
    generate_summary(student_id)
    return redirect(url_for("teacher.student_summary", student_id=student_id))


@bp.route("/students/<int:student_id>/summary/rules", methods=["POST"])
@role_required("teacher")
def student_summary_rules(student_id):
    _student_or_404(student_id)
    generate_summary(student_id, use_rules=True)
    return redirect(url_for("teacher.student_summary", student_id=student_id))
