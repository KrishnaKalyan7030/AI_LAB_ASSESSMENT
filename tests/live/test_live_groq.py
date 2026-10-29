"""OPTIONAL live test against the real Groq API (uses a few free-plan tokens).

Skipped unless GROQ_API_KEY is set in .env. Run with:

    pytest -m live -v -s tests/live/test_live_groq.py
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"), override=True)

KEY = os.environ.get("GROQ_API_KEY", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not KEY or KEY == "your-groq-api-key-here",
                       reason="Set GROQ_API_KEY in .env to run the live Groq test"),
]


def _service():
    from app.services.groq_service import GroqService

    return GroqService(api_key=KEY, model=os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b",
                       timeout=90, max_retries=1)


def test_live_groq_viva_assessment():
    from app.services.ai_features import assess_viva

    question = {
        "subject": "Artificial Intelligence Laboratory",
        "experiment": "A* Search",
        "question": "What is a heuristic function in A* search, and why must it be admissible?",
        "reference_answer": ("h(n) estimates the cost from n to the goal; A* uses f = g + h. "
                             "Admissible means it never overestimates, which guarantees optimality."),
        "rubric": "1 mark each: definition, f = g + h, admissible definition, optimality, example.",
        "max_score": 5,
    }
    answer = ("A heuristic estimates how far a node is from the goal. A* adds it to the path cost "
              "so far, f(n) = g(n) + h(n). It should not overestimate so A* finds the best path.")
    result = assess_viva(_service(), question, answer)
    assert 0 <= result.data["score"] <= 5
    assert result.source == "groq"
    print("\nLive Groq result:", result.model, result.data)


def test_live_groq_invalid_key_reported_cleanly():
    from app.services.claude_service import AuthenticationFailedError
    from app.services.groq_service import GroqService

    bad = GroqService(api_key="gsk_invalidkeyfortesting000000000000000", model="openai/gpt-oss-20b",
                      timeout=30, max_retries=0)
    with pytest.raises(AuthenticationFailedError):
        bad.request_json(system="Reply in JSON.", user_content="Say hi", tool_name="noop",
                         tool_description="noop", schema={"type": "object", "properties": {"hi": {"type": "string"}}})
