"""Child-process harness that runs ONE student function call.

This file is executed as a separate Python process (`python -I -S`) by
code_runner.py; it is never imported by the Flask server. It:

1. reads the job (code, function name, arguments, nonce) from stdin;
2. executes the student's code with a reduced set of builtins and an import
   guard that only allows a few pure-computation modules (exposed without
   their sub-modules or private attributes);
3. captures anything the student prints;
4. writes one result line prefixed with a random nonce the student cannot see.
"""
import io
import json
import sys
import types

ALLOWED_MODULES = {
    "math", "itertools", "collections", "functools", "heapq", "bisect",
    "string", "random", "statistics", "copy", "queue", "typing", "dataclasses", "re",
}
SAFE_BUILTIN_NAMES = [
    "abs", "all", "any", "bool", "chr", "dict", "divmod", "enumerate", "filter", "float",
    "frozenset", "hash", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
    "max", "min", "next", "ord", "pow", "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "zip", "object", "super", "property",
    "staticmethod", "classmethod", "hasattr", "callable", "bin", "hex", "oct", "id",
    "Exception", "ValueError", "KeyError", "IndexError", "TypeError", "ZeroDivisionError",
    "StopIteration", "RuntimeError", "ArithmeticError", "AssertionError",
    "NotImplementedError", "LookupError", "OverflowError", "RecursionError",
    "NotImplemented", "Ellipsis", "None", "True", "False", "__build_class__",
]
MAX_PRINT_CHARS = 4000


def _safe_module(name):
    module = __import__(name)
    ns = types.SimpleNamespace()
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        value = getattr(module, attr)
        if isinstance(value, types.ModuleType):
            continue
        setattr(ns, attr, value)
    return ns


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or name not in ALLOWED_MODULES:
        raise ImportError(f"Importing '{name}' is not allowed in the lab sandbox")
    return _safe_module(name)


def _apply_limits(cpu_seconds, memory_bytes):
    """OS resource limits for this child process (applied before student code runs)."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    for limit, value in (
        (resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1)),
        (resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024)),
        (resource.RLIMIT_AS, (memory_bytes, memory_bytes)),  # not enforced on macOS
    ):
        try:
            resource.setrlimit(limit, value)
        except (ValueError, OSError):
            pass


def main():
    job = json.loads(sys.stdin.read())
    _apply_limits(int(job.get("cpu_seconds", 3)), int(job.get("memory_bytes", 256 * 1024 * 1024)))
    nonce = job["nonce"]
    real_stdout = sys.stdout
    captured = io.StringIO()
    printed = [0]

    def safe_print(*args, sep=" ", end="\n", **_ignored):
        text = sep.join(str(a) for a in args) + end
        if printed[0] < MAX_PRINT_CHARS:
            captured.write(text[: MAX_PRINT_CHARS - printed[0]])
        printed[0] += len(text)

    import builtins

    safe_builtins = {n: getattr(builtins, n) for n in SAFE_BUILTIN_NAMES if hasattr(builtins, n)}
    safe_builtins["print"] = safe_print
    safe_builtins["__import__"] = _guarded_import
    student_globals = {"__builtins__": safe_builtins, "__name__": "student_solution"}

    result = {"ok": False, "actual": None, "error": None}
    sys.stdout = captured
    try:
        exec(compile(job["code"], "<student_code>", "exec"), student_globals)
        func = student_globals.get(job["function"])
        if not callable(func):
            raise NameError(f"Function '{job['function']}' was not defined")
        value = func(*job["args"])
        try:
            result["actual"] = json.loads(json.dumps(value))
        except (TypeError, ValueError):
            raise TypeError(
                f"Return value of type {type(value).__name__} cannot be compared "
                "(return numbers, strings, lists, dicts, booleans or None)"
            ) from None
        result["ok"] = True
    except RecursionError:
        result["error"] = "RecursionError: maximum recursion depth exceeded"
    except MemoryError:
        result["error"] = "MemoryError: the program used too much memory"
    except BaseException as exc:  # noqa: BLE001 - report every student error
        result["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        sys.stdout = real_stdout
    result["stdout"] = captured.getvalue()
    real_stdout.write("\n" + nonce + json.dumps(result) + "\n")
    real_stdout.flush()


if __name__ == "__main__":
    main()
