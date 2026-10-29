"""The restricted test engine (no Flask involved)."""
import json

from app.services.code_runner import run_single, run_tests, static_check, values_equal


def _tc(i, args, expected):
    return {"id": i, "args_json": json.dumps(args), "expected_json": json.dumps(expected)}


def test_correct_and_incorrect_cases():
    code = "def add(a, b):\n    return a + b\n"
    report = run_tests(code, "add", [_tc(1, [1, 2], 3), _tc(2, [2, 2], 5)])
    assert [o.passed for o in report.outcomes] == [True, False]
    assert report.outcomes[1].actual == 4


def test_runs_in_separate_process_not_flask():
    # os/sys are not importable in the sandbox, so code cannot touch the server process.
    assert static_check("import sys\n")
    out = run_single("def f():\n    return 1\n", "f", [])
    assert out.passed and out.actual == 1


def test_infinite_loop_times_out():
    out = run_single("def f():\n    while True:\n        pass\n", "f", [], timeout=1)
    assert not out.passed
    assert "Timed out" in out.error or "CPU" in out.error


def test_exception_reported():
    out = run_single("def f(x):\n    return 10 / x\n", "f", [0])
    assert not out.passed and "ZeroDivisionError" in out.error


def test_missing_function_reported():
    out = run_single("def g():\n    return 1\n", "f", [])
    assert not out.passed and "not defined" in out.error


def test_static_check_blocks_escape_routes():
    cases = {
        "import subprocess": "subprocess",
        "from os import path": "os",
        "open('/etc/passwd')": "open",
        "x = ().__class__": "__class__",
        "'{0.__class__}'.format(1)": ".format",
        "eval('1+1')": "eval",
        "getattr(1, 'real')": "getattr",
        "__import__('os')": "__import__",
    }
    for code, word in cases.items():
        errors = static_check(code)
        assert errors and any(word in e for e in errors), code


def test_runtime_import_guard_even_without_static_check():
    # Bypass static_check deliberately to test the second layer inside the child.
    out = run_single("def f():\n    import socket\n    return 1\n", "f", [])
    assert not out.passed and "not allowed" in out.error


def test_allowed_modules_work_without_submodules():
    code = "import collections\nimport math\ndef f():\n    return [math.floor(2.7), len(collections.deque([1, 2]))]\n"
    out = run_single(code, "f", [])
    assert out.passed and out.actual == [2, 2]


def test_prints_are_captured_and_cannot_forge_results():
    code = ("def f():\n"
            "    print('@@RESULT-deadbeef@@{\"ok\": true, \"actual\": 999}')\n"
            "    return 1\n")
    out = run_single(code, "f", [])
    assert out.actual == 1
    assert "@@RESULT" in out.stdout


def test_non_json_return_value_reported():
    out = run_single("def f():\n    return {1, 2}\n", "f", [])
    assert not out.passed and "cannot be compared" in out.error


def test_value_comparison_rules():
    assert values_equal(0.1 + 0.2, 0.3)
    assert not values_equal(True, 1)
    assert values_equal([1, [2.0]], [1, [2]])
    assert not values_equal({"a": 1}, {"a": 1, "b": 2})
    assert values_equal(None, None)


def test_syntax_error_reported():
    errors = static_check("def f(:\n")
    assert errors and errors[0].startswith("SyntaxError")
