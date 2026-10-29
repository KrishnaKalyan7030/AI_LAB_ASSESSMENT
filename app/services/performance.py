"""Aggregate one student's stored results into statistics.

Only rows belonging to `student_id` are read, so a summary can never include
another student's data. The statistics (not raw answers or names) are what
gets sent to Claude for Feature E.
"""
from __future__ import annotations

from collections import defaultdict


def collect_student_stats(db, student_id: int) -> dict:
    viva_rows = db.execute(
        """
        SELECT q.experiment AS topic, s.score, s.max_score, s.source, a.created_at
        FROM viva_attempts a
        JOIN viva_scores s ON s.attempt_id = a.id
        JOIN viva_questions q ON q.id = a.question_id
        WHERE a.student_id = ?
        ORDER BY a.created_at
        """,
        (student_id,),
    ).fetchall()

    prog_rows = db.execute(
        """
        SELECT asg.experiment AS topic, asg.title, sub.id AS submission_id, sub.created_at,
               COUNT(tr.id) AS total, COALESCE(SUM(tr.passed), 0) AS passed
        FROM submissions sub
        JOIN assignments asg ON asg.id = sub.assignment_id
        LEFT JOIN test_results tr ON tr.submission_id = sub.id
        WHERE sub.student_id = ?
        GROUP BY sub.id
        ORDER BY sub.created_at
        """,
        (student_id,),
    ).fetchall()

    # Each assessment counts equally: a viva attempt's percentage, and the BEST
    # submission's pass percentage for each assignment.
    topic_percents = defaultdict(list)
    viva = []
    for r in viva_rows:
        pct = round(100 * r["score"] / r["max_score"], 1)
        topic_percents[r["topic"]].append(pct)
        viva.append({"topic": r["topic"], "percent": pct, "scored_by": r["source"]})

    best = {}
    programming = []
    for r in prog_rows:
        total = r["total"] or 0
        pct = round(100 * r["passed"] / total, 1) if total else 0.0
        programming.append({"topic": r["topic"], "assignment": r["title"],
                            "tests_passed": r["passed"], "tests_total": total, "percent": pct})
        if r["title"] not in best or pct > best[r["title"]][1]:
            best[r["title"]] = (r["topic"], pct)
    for topic, pct in best.values():
        topic_percents[topic].append(pct)

    topics = [
        {"topic": topic, "percent": round(sum(p) / len(p), 1), "assessments": len(p)}
        for topic, p in sorted(topic_percents.items())
    ]
    all_pcts = [p for values in topic_percents.values() for p in values]
    return {
        "assessment_count": len(viva) + len(programming),
        "overall_percent": round(sum(all_pcts) / len(all_pcts), 1) if all_pcts else None,
        "topics": topics,
        "viva_attempts": viva[-30:],
        "programming_submissions": programming[-30:],
    }
