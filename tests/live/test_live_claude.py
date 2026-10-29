"""OPTIONAL live test against the real Anthropic API.

Not run by default (it costs a few API tokens). After configuring .env:

    pytest -m live -v

It is also skipped unless ANTHROPIC_API_KEY and ANTHROPIC_MODEL are set.
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("ANTHROPIC_MODEL")),
        reason="Set ANTHROPIC_API_KEY and ANTHROPIC_MODEL in .env to run the live test",
    ),
]


def _service():
    from app.services.claude_service import ClaudeService

    return ClaudeService(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model=os.environ["ANTHROPIC_MODEL"],
        timeout=60,
        max_retries=2,
    )


def test_live_viva_assessment_returns_valid_score():
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
    assert result.data["max_score"] == 5
    assert result.data["feedback"]
    print("\nLive result:", result.model, result.data)


def test_live_invalid_key_is_reported_cleanly():
    from app.services.claude_service import AuthenticationFailedError, ClaudeService

    bad = ClaudeService(api_key="sk-ant-invalid-key-for-testing-000000",
                        model=os.environ["ANTHROPIC_MODEL"], timeout=30, max_retries=0)
    with pytest.raises(AuthenticationFailedError):
        bad.request_json(system="Reply.", user_content="Hi", tool_name="noop",
                         tool_description="noop", schema={"type": "object", "properties": {}})
