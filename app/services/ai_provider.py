"""Choose the AI provider from configuration.

AI_PROVIDER=groq (default) | gemini | claude
"""
from __future__ import annotations

from .claude_service import ClaudeService
from .gemini_service import GeminiService
from .groq_service import GroqService

PROVIDERS = {"groq": GroqService, "gemini": GeminiService, "claude": ClaudeService}
DEFAULT_PROVIDER = "groq"


def provider_name(config) -> str:
    name = str(config.get("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def get_ai_service(config):
    """Return the configured provider service (all share the same interface)."""
    return PROVIDERS[provider_name(config)].from_config(config)
