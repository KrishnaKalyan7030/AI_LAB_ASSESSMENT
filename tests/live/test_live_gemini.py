"""OPTIONAL live test against the real Google Gemini API.

Skipped unless GEMINI_API_KEY and GEMINI_MODEL are set in .env. Run with:

    pytest -m live -v -s tests/live/test_live_gemini.py
"""
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL")),
        reason="Set GEMINI_API_KEY and GEMINI_MODEL in .env to run the live Gemini test",
    ),
]


def test_live_gemini_viva_assessment():
    from app.services.ai_features import assess_viva
    from app.services.gemini_service import GeminiService

    service = GeminiService(api_key=os.environ["GEMINI_API_KEY"], model=os.environ["GEMINI_MODEL"],
                            timeout=90, max_retries=1)
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
    result = assess_viva(service, question, answer)
    assert 0 <= result.data["score"] <= 5
    assert result.source == "gemini"
    print("\nLive Gemini result:", result.model, result.data)
