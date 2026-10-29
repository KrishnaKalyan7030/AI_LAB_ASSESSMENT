"""Create or update .env safely (used by setup.sh).

    python scripts/configure_env.py init          # create .env from .env.example + secret key
    python scripts/configure_env.py has-groq-key  # exit code 0 if a real GROQ_API_KEY is set
    python scripts/configure_env.py set-groq-key  # read a key from stdin and save it

The key is never printed.
"""
import os
import re
import secrets
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV = os.path.join(ROOT, ".env")
EXAMPLE = os.path.join(ROOT, ".env.example")
PLACEHOLDERS = {"", "your-groq-api-key-here", "replace-with-a-long-random-string"}


def read_lines():
    with open(ENV, encoding="utf-8") as fh:
        return fh.read().splitlines()


def get(name):
    for line in read_lines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def set_value(name, value):
    lines = read_lines()
    for i, line in enumerate(lines):
        if line.startswith(name + "="):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    with open(ENV, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(ENV, 0o600)  # only your user can read the secrets file
    except OSError:
        pass


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "init":
        if not os.path.exists(ENV):
            with open(EXAMPLE, encoding="utf-8") as src:
                content = src.read()
            with open(ENV, "w", encoding="utf-8") as dst:
                dst.write(content)
            print("Created .env from .env.example")
        if get("FLASK_SECRET_KEY") in PLACEHOLDERS:
            set_value("FLASK_SECRET_KEY", secrets.token_hex(32))
            print("Generated a FLASK_SECRET_KEY")
    elif command == "has-groq-key":
        sys.exit(0 if get("GROQ_API_KEY") not in PLACEHOLDERS else 1)
    elif command == "set-groq-key":
        key = sys.stdin.readline().strip().strip('"').strip("'")
        if not key:
            sys.exit(1)
        if not re.fullmatch(r"gsk_[A-Za-z0-9]{20,}", key):
            print("Warning: Groq keys normally start with gsk_ - saved anyway, please double-check.")
        set_value("GROQ_API_KEY", key)
        print("Saved GROQ_API_KEY to .env")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
