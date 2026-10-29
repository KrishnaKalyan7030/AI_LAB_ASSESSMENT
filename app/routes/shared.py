"""Pages used by both roles, with ownership checks (viva results, submissions)."""
from __future__ import annotations

import json

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, url_for

from ..auth import login_required
from ..db import get_db
from ..repository import (get_claude_service, save_feedback, save_quality_report,
                          store_viva_result, submission_test_rows, test_report_for_ai)
from ..security import ai_rate_limited
from ..services import ai_features
from ..services.claude_service import ClaudeServiceError
from ..services.fallbacks import code_quality_rules, viva_keyword_estimate
from .common import (evaluate_attempt_with_ai, is_teacher, load_attempt_or_404,
                     load_submission_or_404)

bp = Blueprint("shared", __name__)


# --- Viva attempts ------------------------------------------------------------
@bp.route("/viva/attempts/<int:attempt_id>")
@login_required
def view_attempt(attempt_id):
    attempt = load_attempt_or_404(attempt_id)
    db = get_db()
    score = db.execute("SELECT * FROM viva_scores WHERE attempt_id = ?", (attempt_id,)).fetchone()
    feedback = db.execute(
        "SELECT * FROM ai_feedback WHERE kind = 'viva' AND ref_id = ? ORDER BY id DESC LIMIT 1",
        (attempt_id,),
    ).fetchone()
    return render_template("viva_result.html", attempt=attempt, score=score, feedback=feedback,
                           payload=json.loads(feedback["payload_json"]) if feedback else None)


@bp.route("/viva/attempts/<int:attempt_id>/retry", methods=["POST"])
@login_required
@ai_rate_limited
def retry_attempt(attempt_id):
    attempt = load_attempt_or_404(attempt_id)
    existing = get_db().execute("SELECT source FROM viva_scores WHERE attempt_id = ?",
                                (attempt_id,)).fetchone()
    if existing is not None and existing["source"] != "rule_based" and not is_teacher():
        flash("This answer has already been assessed by AI.", "info")
    else:
        evaluate_attempt_with_ai(attempt)
    return redirect(url_for("shared.view_attempt", attempt_id=attempt_id))


@bp.route("/viva/attempts/<int:attempt_id>/fallback", methods=["POST"])
@login_required
def fallback_attempt(attempt_id):
    attempt = load_attempt_or_404(attempt_id)
    if not current_app.config.get("ALLOW_RULE_BASED_FALLBACK", True):
        abort(403)
    if attempt["status"] != "ai_failed":
        flash("The rule-based estimate is only available when the AI assessment failed.", "warning")
        return redirect(url_for("shared.view_attempt", attempt_id=attempt_id))
    question = get_db().execute("SELECT * FROM viva_questions WHERE id = ?",
                                (attempt["question_id"],)).fetchone()
    result = viva_keyword_estimate(question, attempt["answer"])
    store_viva_result(get_db(), attempt, result, "rule_based", None)
    flash("A rule-based (non-AI) estimate was recorded. You can still retry the AI assessment later.", "info")
    return redirect(url_for("shared.view_attempt", attempt_id=attempt_id))


# --- Programming submissions ---------------------------------------------------
@bp.route("/submissions/<int:submission_id>")
@login_required
def view_submission(submission_id):
    sub = load_submission_or_404(submission_id)
    db = get_db()
    rows = submission_test_rows(db, submission_id)
    assist = db.execute(
        "SELECT * FROM ai_feedback WHERE kind = 'programming' AND ref_id = ? ORDER BY id DESC LIMIT 1",
        (submission_id,),
    ).fetchone()
    quality = db.execute(
        "SELECT * FROM code_quality_reports WHERE submission_id = ? ORDER BY id DESC LIMIT 1",
        (submission_id,),
    ).fetchone()
    return render_template(
        "submission.html", sub=sub, rows=rows,
        static_errors=json.loads(sub["static_errors"]) if sub["static_errors"] else [],
        assist=assist, assist_payload=json.loads(assist["payload_json"]) if assist else None,
        quality=quality, quality_payload=json.loads(quality["payload_json"]) if quality else None,
        show_hidden=is_teacher(),
    )


@bp.route("/submissions/<int:submission_id>/assist", methods=["POST"])
@login_required
@ai_rate_limited
def programming_assist(submission_id):
    sub = load_submission_or_404(submission_id)
    db = get_db()
    if sub["static_errors"]:
        flash("Fix the reported safety/syntax errors and resubmit before asking for AI help.", "warning")
        return redirect(url_for("shared.view_submission", submission_id=submission_id))
    assignment = db.execute("SELECT * FROM assignments WHERE id = ?", (sub["assignment_id"],)).fetchone()
    report = test_report_for_ai(submission_test_rows(db, submission_id))
    service = get_claude_service(current_app.config)
    try:
        result = ai_features.explain_programming(service, assignment, sub["code"], report)
    except ClaudeServiceError as err:
        flash(err.user_message, "danger")
        return redirect(url_for("shared.view_submission", submission_id=submission_id))
    save_feedback(db, sub["student_id"], "programming", submission_id, result.data, result.source, result.model)
    flash(f"{service.display_name}'s explanation is ready below. Test results are unchanged - they come from the test engine.", "success")
    return redirect(url_for("shared.view_submission", submission_id=submission_id) + "#assist")


@bp.route("/submissions/<int:submission_id>/quality", methods=["POST"])
@login_required
@ai_rate_limited
def quality_review(submission_id):
    sub = load_submission_or_404(submission_id)
    db = get_db()
    assignment = db.execute("SELECT * FROM assignments WHERE id = ?", (sub["assignment_id"],)).fetchone()
    service = get_claude_service(current_app.config)
    try:
        result = ai_features.review_code_quality(service, assignment, sub["code"])
    except ClaudeServiceError as err:
        flash(err.user_message, "danger")
        return redirect(url_for("shared.view_submission", submission_id=submission_id))
    save_quality_report(db, submission_id, result.data, result.source, result.model)
    flash(f"Code-quality review generated by {service.display_name}.", "success")
    return redirect(url_for("shared.view_submission", submission_id=submission_id) + "#quality")


@bp.route("/submissions/<int:submission_id>/quality/rules", methods=["POST"])
@login_required
def quality_rules(submission_id):
    sub = load_submission_or_404(submission_id)
    if not current_app.config.get("ALLOW_RULE_BASED_FALLBACK", True):
        abort(403)
    save_quality_report(get_db(), submission_id, code_quality_rules(sub["code"]), "rule_based", None)
    flash("Rule-based (non-AI) quality checks completed.", "info")
    return redirect(url_for("shared.view_submission", submission_id=submission_id) + "#quality")
