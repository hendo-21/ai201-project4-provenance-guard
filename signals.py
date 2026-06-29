"""Detection signals that score text on a 0.0 (human) - 1.0 (AI) scale."""

import os

from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are an AI authorship classifier. "
    "Analyze the following text and return only a single float between 0.0 and 1.0 "
    "representing the likelihood it was AI-generated. "
    "0.0 = human-authored, 1.0 = AI-generated. "
    "Return the number only, no other text."
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


def signal_1_llm(text):
    """Ask the LLM to rate AI-likeness; fall back to 0.5 if the response can't be parsed."""
    response = _get_client().chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    )
    raw = response.choices[0].message.content.strip()   # type: ignore

    try:
        score = float(raw)
    except ValueError:
        return 0.5

    if score < 0.0 or score > 1.0:
        return 0.5

    return score


def signal_2_stylometric(text):
    """Placeholder for the stylometric heuristics signal (Milestone 4)."""
    return 0.5
