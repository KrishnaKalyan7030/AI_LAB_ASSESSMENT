"""JSON schemas sent to Claude and strict validation of what comes back.

Nothing Claude returns is stored until it passes these checks. Scores are
range-checked on the backend; out-of-range or malformed answers are rejected
(not silently clamped), so the student can retry instead of getting a wrong mark.
"""
from __future__ import annotations

import math
from typing import Any

from .claude_service import InvalidAIResponseError

SEVERITIES = ("info", "low", "medium", "high")
QUALITY_CATEGORIES = (
    "readability", "naming", "organization", "maintainability",
    "documentation", "logic", "performance", "other",
)
DIFFICULTIES = ("easy", "medium", "hard")

# ---------------------------------------------------------------------------
# Schemas (used as tool input_schema in the Messages API call)
# ---------------------------------------------------------------------------
_STR_LIST = {"type": "array", "items": {"type": "string"}}

VIVA_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "description": "Marks awarded, between 0 and max_score."},
        "max_score": {"type": "number"},
        "correct_concepts": {**_STR_LIST, "description": "Concepts the student got right."},
        "missing_concepts": {**_STR_LIST, "description": "Rubric concepts that are missing or wrong."},
        "strengths": _STR_LIST,
        "feedback": {"type": "string", "description": "Explanation of the score."},
        "suggestion": {"type": "string", "description": "How the student can improve."},
    },
    "required": ["score", "max_score", "correct_concepts", "missing_concepts",
                 "strengths", "feedback", "suggestion"],
}

PROGRAMMING_SCHEMA = {
    "type": "object",
    "properties": {
        "code_explanation": {"type": "string"},
        "failed_test_explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"test": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["test", "explanation"],
            },
        },
        "likely_logical_errors": _STR_LIST,
        "improvement_suggestions": _STR_LIST,
        "encouragement": {"type": "string"},
    },
    "required": ["code_explanation", "failed_test_explanations", "likely_logical_errors",
                 "improvement_suggestions", "encouragement"],
}

QUALITY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(QUALITY_CATEGORIES)},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "line": {"type": ["integer", "null"]},
                    "title": {"type": "string"},
                    "explanation": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["category", "severity", "title", "explanation", "recommendation"],
            },
        },
    },
    "required": ["summary", "findings"],
}

QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "reference_answer": {"type": "string"},
                    "rubric": {"type": "string", "description": "Marking scheme with marks per point."},
                    "key_concepts": _STR_LIST,
                },
                "required": ["question", "reference_answer", "rubric", "key_concepts"],
            },
        }
    },
    "required": ["questions"],
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "strong_topics": _STR_LIST,
        "weak_topics": _STR_LIST,
        "revision_topics": _STR_LIST,
        "study_suggestions": _STR_LIST,
    },
    "required": ["overview", "strong_topics", "weak_topics", "revision_topics", "study_suggestions"],
}


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------
def _fail(reason: str):
    raise InvalidAIResponseError(
        f"The AI response failed validation ({reason}), so nothing was saved. Please retry."
    )


def _str(data: dict, key: str, max_len: int = 2000, required: bool = True) -> str:
    value = data.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        _fail(f"'{key}' must be text")
    value = value.strip()
    if required and not value:
        _fail(f"'{key}' is empty")
    return value[:max_len]


def _str_list(data: dict, key: str, max_items: int = 15, max_len: int = 300) -> list:
    value = data.get(key)
    if not isinstance(value, list):
        _fail(f"'{key}' must be a list")
    out = []
    for item in value:
        if not isinstance(item, str):
            _fail(f"'{key}' must contain only text")
        item = item.strip()
        if item:
            out.append(item[:max_len])
    return out[:max_items]


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"'{key}' must be a number")
    if not math.isfinite(value):
        _fail(f"'{key}' must be finite")
    return float(value)


def _require_dict(data: Any) -> dict:
    if not isinstance(data, dict):
        _fail("expected a JSON object")
    return data


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
def validate_viva(data: Any, max_score: float) -> dict:
    data = _require_dict(data)
    score = _number(data.get("score"), "score")
    returned_max = _number(data.get("max_score"), "max_score")
    if abs(returned_max - float(max_score)) > 1e-9:
        _fail(f"max_score {returned_max:g} does not match the question's {max_score:g}")
    if score < 0 or score > max_score:
        _fail(f"score {score:g} is outside 0..{max_score:g}")
    score = round(score * 2) / 2  # half-mark granularity
    return {
        "score": score,
        "max_score": float(max_score),
        "correct_concepts": _str_list(data, "correct_concepts"),
        "missing_concepts": _str_list(data, "missing_concepts"),
        "strengths": _str_list(data, "strengths"),
        "feedback": _str(data, "feedback"),
        "suggestion": _str(data, "suggestion"),
    }


def validate_programming(data: Any) -> dict:
    data = _require_dict(data)
    failed = data.get("failed_test_explanations")
    if not isinstance(failed, list):
        _fail("'failed_test_explanations' must be a list")
    cleaned_failed = []
    for item in failed[:20]:
        if not isinstance(item, dict):
            _fail("each failed test explanation must be an object")
        cleaned_failed.append({
            "test": _str(item, "test", 200),
            "explanation": _str(item, "explanation", 1500),
        })
    return {
        "code_explanation": _str(data, "code_explanation", 3000),
        "failed_test_explanations": cleaned_failed,
        "likely_logical_errors": _str_list(data, "likely_logical_errors", 10, 600),
        "improvement_suggestions": _str_list(data, "improvement_suggestions", 10, 600),
        "encouragement": _str(data, "encouragement", 600, required=False),
    }


def validate_quality(data: Any, max_line: int) -> dict:
    data = _require_dict(data)
    findings = data.get("findings")
    if not isinstance(findings, list):
        _fail("'findings' must be a list")
    cleaned = []
    for item in findings[:25]:
        if not isinstance(item, dict):
            _fail("each finding must be an object")
        severity = item.get("severity")
        if severity not in SEVERITIES:
            _fail(f"unknown severity {severity!r}")
        category = item.get("category")
        if category not in QUALITY_CATEGORIES:
            category = "other"
        line = item.get("line")
        if line is not None:
            if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= max_line:
                line = None  # a wrong line number is not worth rejecting the report
        cleaned.append({
            "category": category,
            "severity": severity,
            "line": line,
            "title": _str(item, "title", 200),
            "explanation": _str(item, "explanation", 1200),
            "recommendation": _str(item, "recommendation", 1200),
        })
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    cleaned.sort(key=lambda f: order[f["severity"]])
    return {"summary": _str(data, "summary", 1500), "findings": cleaned}


def validate_questions(data: Any, expected_count: int) -> list:
    data = _require_dict(data)
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        _fail("'questions' must be a non-empty list")
    cleaned = []
    for item in questions[:expected_count]:
        if not isinstance(item, dict):
            _fail("each question must be an object")
        cleaned.append({
            "question": _str(item, "question", 1000),
            "reference_answer": _str(item, "reference_answer", 3000),
            "rubric": _str(item, "rubric", 2000),
            "key_concepts": _str_list(item, "key_concepts", 10, 80),
        })
    return cleaned


def validate_summary(data: Any) -> dict:
    data = _require_dict(data)
    return {
        "overview": _str(data, "overview", 2000),
        "strong_topics": _str_list(data, "strong_topics", 10, 200),
        "weak_topics": _str_list(data, "weak_topics", 10, 200),
        "revision_topics": _str_list(data, "revision_topics", 10, 200),
        "study_suggestions": _str_list(data, "study_suggestions", 10, 500),
    }
