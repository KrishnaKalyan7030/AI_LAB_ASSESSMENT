"""Objective test engine for student programs.

Student code is NEVER executed inside the Flask process. Each test case runs
in a fresh child Python process (`python -I -S`, isolated mode) with:

* static AST checks that block dangerous imports, builtins and private
  attribute access before anything runs;
* restricted builtins and an import allow-list inside the child;
* OS resource limits (CPU time, file size, memory where the OS supports it);
* a wall-clock timeout, a temporary empty working directory and a minimal
  environment.

This is a best-effort restricted environment suitable for a lab mini
project. It is not a hardened security sandbox; for production use run the
child process in a container or VM (see README "Known limitations").
"""
from __future__ import annotations

import ast
import json
import math
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Optional

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_harness.py")

ALLOWED_IMPORTS = {
    "math", "itertools", "collections", "functools", "heapq", "bisect",
    "string", "random", "statistics", "copy", "queue", "typing", "dataclasses", "re",
}
BANNED_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "memoryview", "help",
    "dir", "type", "__builtins__", "__loader__", "__spec__",
}
BANNED_ATTRIBUTES = {
    "format", "format_map", "mro", "gi_frame", "gi_code", "cr_frame", "ag_frame",
    "f_globals", "f_locals", "f_builtins", "f_back", "tb_frame", "tb_next", "co_code",
    "system", "popen", "modules",
}
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024


@dataclass
class TestOutcome:
    test_case_id: Optional[int]
    passed: bool
    actual: Any = None
    error: Optional[str] = None
    stdout: str = ""
    duration_ms: int = 0


@dataclass
class RunReport:
    static_errors: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)


def static_check(code: str) -> list:
    """Return a list of human-readable reasons the code may not run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]
    errors = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    errors.append(f"Line {line}: importing '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                errors.append(f"Line {line}: importing from '{node.module}' is not allowed")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            errors.append(f"Line {line}: use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Name) and node.id.startswith("__"):
            errors.append(f"Line {line}: dunder names are not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                errors.append(f"Line {line}: access to private attribute '{node.attr}' is not allowed")
            elif node.attr in BANNED_ATTRIBUTES:
                errors.append(f"Line {line}: use of '.{node.attr}' is not allowed")
        elif isinstance(node, ast.AsyncFunctionDef):
            errors.append(f"Line {line}: async functions are not supported")
    # keep the list short and unique
    seen, unique = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique[:20]


def values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-9)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            values_equal(a, e) for a, e in zip(actual, expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            values_equal(actual[k], expected[k]) for k in actual)
    return actual == expected


def run_single(code: str, function_name: str, args: list, timeout: float = 3.0) -> TestOutcome:
    nonce = "@@RESULT-" + secrets.token_hex(16) + "@@"
    job = json.dumps({
        "code": code, "function": function_name, "args": args, "nonce": nonce,
        "cpu_seconds": max(1, math.ceil(timeout)), "memory_bytes": MEMORY_LIMIT_BYTES,
    })
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="labrun-") as workdir:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-S", HARNESS],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=workdir, env=env, text=True, start_new_session=True,
            )
        except OSError as exc:
            return TestOutcome(None, False, error=f"Could not start the test runner: {exc}")
        try:
            out, err = proc.communicate(job, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                proc.kill()
            proc.communicate()
            return TestOutcome(None, False, error=f"Timed out after {timeout:g}s (possible infinite loop)",
                               duration_ms=int((time.monotonic() - start) * 1000))
    duration = int((time.monotonic() - start) * 1000)
    for line in reversed(out.splitlines()):
        if line.startswith(nonce):
            data = json.loads(line[len(nonce):])
            return TestOutcome(None, bool(data.get("ok")), actual=data.get("actual"),
                               error=data.get("error"), stdout=(data.get("stdout") or "")[:4000],
                               duration_ms=duration)
    if proc.returncode is not None and proc.returncode < 0:
        sig = -proc.returncode
        reason = "CPU time limit exceeded" if sig in (getattr(signal, "SIGXCPU", 24), signal.SIGKILL) \
            else f"terminated by signal {sig}"
        return TestOutcome(None, False, error=f"Program {reason}", duration_ms=duration)
    tail = (err or "").strip().splitlines()[-1:] or ["no output"]
    return TestOutcome(None, False, error=f"Runner error: {tail[0][:300]}", duration_ms=duration)


def run_tests(code: str, function_name: str, test_cases, timeout: float = 3.0) -> RunReport:
    """Run every test case. `test_cases` rows need id, args_json and expected_json."""
    report = RunReport(static_errors=static_check(code))
    if report.static_errors:
        return report
    for tc in test_cases:
        args = json.loads(tc["args_json"])
        expected = json.loads(tc["expected_json"])
        outcome = run_single(code, function_name, args, timeout)
        outcome.test_case_id = tc["id"]
        if outcome.passed:  # "ok" means it ran; now compare
            outcome.passed = values_equal(outcome.actual, expected)
        report.outcomes.append(outcome)
    return report
