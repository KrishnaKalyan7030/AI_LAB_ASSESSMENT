"""Application configuration, read from environment variables.

Secrets (the Anthropic API key, the Flask secret key) are only ever read from
the environment / .env file. They are never written to templates, logs or
API responses.
"""
import os
import secrets


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


PLACEHOLDER_VALUES = {
    "your-anthropic-api-key-here",
    "replace-with-a-long-random-string",
    "replace-with-a-secret-teacher-code",
    "your-gemini-api-key-here",
    "your-groq-api-key-here",
}


def _secret(name: str) -> str:
    """Read a secret, treating the .env.example placeholders as 'not set'."""
    value = os.environ.get(name, "").strip()
    return "" if value in PLACEHOLDER_VALUES else value


def load_config() -> dict:
    """Build the configuration dictionary from the current environment."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    secret_key = _secret("FLASK_SECRET_KEY")
    generated_secret = False
    if not secret_key:
        # Sessions will not survive a restart, but the app still works safely.
        secret_key = secrets.token_hex(32)
        generated_secret = True

    return {
        "SECRET_KEY": secret_key,
        "GENERATED_SECRET_KEY": generated_secret,
        "DATABASE": os.environ.get(
            "DATABASE_PATH", os.path.join(base_dir, "instance", "ai_lab.db")
        ),
        # --- AI provider: "groq" (default), "gemini" or "claude" ------------
        "AI_PROVIDER": (os.environ.get("AI_PROVIDER", "groq").strip().lower() or "groq"),
        # --- Groq ------------------------------------------------------------
        "GROQ_API_KEY": _secret("GROQ_API_KEY"),
        "GROQ_MODEL": os.environ.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
        "GROQ_TIMEOUT_SECONDS": _float("GROQ_TIMEOUT_SECONDS", 60.0),
        "GROQ_MAX_RETRIES": _int("GROQ_MAX_RETRIES", 1),
        "GROQ_MAX_OUTPUT_TOKENS": _int("GROQ_MAX_OUTPUT_TOKENS", 4096),
        "GROQ_TRANSPORT": None,  # tests inject a fake HTTP transport here
        # --- Google Gemini ------------------------------------------------
        "GEMINI_API_KEY": _secret("GEMINI_API_KEY"),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "").strip(),
        "GEMINI_TIMEOUT_SECONDS": _float("GEMINI_TIMEOUT_SECONDS", 60.0),
        "GEMINI_MAX_RETRIES": _int("GEMINI_MAX_RETRIES", 1),
        "GEMINI_MAX_OUTPUT_TOKENS": _int("GEMINI_MAX_OUTPUT_TOKENS", 8192),
        "GEMINI_TRANSPORT": None,  # tests inject a fake HTTP transport here
        # --- Anthropic / Claude -------------------------------------------
        "ANTHROPIC_API_KEY": _secret("ANTHROPIC_API_KEY"),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "").strip(),
        "ANTHROPIC_TIMEOUT_SECONDS": _float("ANTHROPIC_TIMEOUT_SECONDS", 30.0),
        "ANTHROPIC_MAX_RETRIES": _int("ANTHROPIC_MAX_RETRIES", 2),
        "AI_MAX_OUTPUT_TOKENS": _int("AI_MAX_OUTPUT_TOKENS", 2000),
        "CLAUDE_CLIENT_FACTORY": None,  # tests inject a fake client here
        # --- Assessment ---------------------------------------------------
        "VIVA_DEFAULT_MAX_SCORE": _int("VIVA_DEFAULT_MAX_SCORE", 5),
        "ALLOW_RULE_BASED_FALLBACK": _bool("ALLOW_RULE_BASED_FALLBACK", True),
        "SIMILARITY_WARNING_THRESHOLD": _float("SIMILARITY_WARNING_THRESHOLD", 0.75),
        # --- Limits -------------------------------------------------------
        "MAX_ANSWER_CHARS": _int("MAX_ANSWER_CHARS", 4000),
        "MAX_CODE_CHARS": _int("MAX_CODE_CHARS", 12000),
        "MAX_CONTENT_LENGTH": _int("MAX_CONTENT_LENGTH", 512 * 1024),
        "AI_RATE_LIMIT_PER_MINUTE": _int("AI_RATE_LIMIT_PER_MINUTE", 10),
        "CODE_TIMEOUT_SECONDS": _float("CODE_TIMEOUT_SECONDS", 3.0),
        "MAX_GENERATED_QUESTIONS": _int("MAX_GENERATED_QUESTIONS", 10),
        # --- Registration -------------------------------------------------
        # Students can always self-register. Teachers need this code; leave it
        # empty to disable teacher self-registration.
        "TEACHER_REGISTRATION_CODE": _secret("TEACHER_REGISTRATION_CODE"),
        "REGISTRATION_LIMIT_PER_HOUR": _int("REGISTRATION_LIMIT_PER_HOUR", 10),
        # --- Security -----------------------------------------------------
        "CSRF_ENABLED": True,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "TESTING": False,
    }
