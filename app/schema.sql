-- AI Laboratory Assessment System - SQLite schema
-- Numeric scores are stored in their own tables, separate from the
-- qualitative (AI or rule-based) feedback text.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS similarity_comparisons;
DROP TABLE IF EXISTS code_quality_reports;
DROP TABLE IF EXISTS ai_feedback;
DROP TABLE IF EXISTS test_results;
DROP TABLE IF EXISTS submissions;
DROP TABLE IF EXISTS viva_scores;
DROP TABLE IF EXISTS viva_attempts;
DROP TABLE IF EXISTS viva_questions;
DROP TABLE IF EXISTS test_cases;
DROP TABLE IF EXISTS assignments;
DROP TABLE IF EXISTS users;

-- Users and roles -------------------------------------------------------
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('student', 'teacher')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Assignments and test cases ---------------------------------------------
CREATE TABLE assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    experiment    TEXT NOT NULL,
    description   TEXT NOT NULL,
    function_name TEXT NOT NULL,
    starter_code  TEXT NOT NULL DEFAULT '',
    created_by    INTEGER NOT NULL REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE test_cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    description   TEXT NOT NULL DEFAULT '',
    args_json     TEXT NOT NULL,      -- JSON list of positional arguments
    expected_json TEXT NOT NULL,      -- JSON expected return value
    is_hidden     INTEGER NOT NULL DEFAULT 0
);

-- Viva questions and rubrics ----------------------------------------------
CREATE TABLE viva_questions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subject          TEXT NOT NULL,
    experiment       TEXT NOT NULL,
    difficulty       TEXT NOT NULL DEFAULT 'medium'
                     CHECK (difficulty IN ('easy', 'medium', 'hard')),
    question         TEXT NOT NULL,
    reference_answer TEXT NOT NULL,
    rubric           TEXT NOT NULL,
    key_concepts     TEXT NOT NULL DEFAULT '',   -- comma separated, used by the non-AI fallback
    max_score        INTEGER NOT NULL CHECK (max_score BETWEEN 1 AND 100),
    status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    ai_generated     INTEGER NOT NULL DEFAULT 0,
    created_by       INTEGER NOT NULL REFERENCES users(id),
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Student submissions -----------------------------------------------------
CREATE TABLE viva_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id),
    question_id INTEGER NOT NULL REFERENCES viva_questions(id) ON DELETE CASCADE,
    answer      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'evaluated', 'ai_failed')),
    last_error  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id    INTEGER NOT NULL REFERENCES users(id),
    code          TEXT NOT NULL,
    static_errors TEXT,              -- JSON list, set when code was rejected before running
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Scores (numeric) ----------------------------------------------------------
CREATE TABLE viva_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL UNIQUE REFERENCES viva_attempts(id) ON DELETE CASCADE,
    score      REAL NOT NULL,
    max_score  REAL NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('groq', 'claude', 'gemini', 'rule_based')),
    model      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (score >= 0 AND score <= max_score)
);

-- Objective test results (from the code-execution engine, never from AI) ---
CREATE TABLE test_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    test_case_id  INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    passed        INTEGER NOT NULL,
    actual_json   TEXT,
    error         TEXT,
    stdout        TEXT,
    duration_ms   INTEGER NOT NULL DEFAULT 0
);

-- Qualitative feedback (AI or rule-based, clearly labelled) ----------------
CREATE TABLE ai_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id   INTEGER NOT NULL REFERENCES users(id),
    kind         TEXT NOT NULL CHECK (kind IN ('viva', 'programming', 'summary')),
    ref_id       INTEGER,            -- viva_attempts.id / submissions.id / NULL
    payload_json TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('groq', 'claude', 'gemini', 'rule_based')),
    model        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE code_quality_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    payload_json  TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('groq', 'claude', 'gemini', 'rule_based')),
    model         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Similarity comparisons (token based, never AI) ---------------------------
CREATE TABLE similarity_comparisons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    submission_a  INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    submission_b  INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    similarity    REAL NOT NULL,
    details_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_attempts_student ON viva_attempts(student_id);
CREATE INDEX idx_submissions_student ON submissions(student_id);
CREATE INDEX idx_feedback_student ON ai_feedback(student_id, kind);
