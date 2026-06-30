"""Detection signals that score text on a 0.0 (human) - 1.0 (AI) scale."""

import os
import re
import statistics

from groq import Groq
from collections import Counter

GROQ_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are an AI authorship classifier. "
    "Analyze the following text and return only a single float between 0.0 and 1.0 "
    "representing the likelihood it was AI-generated. "
    "0.0 = human-authored, 1.0 = AI-generated. "
    "Return the number only, no other text."
)

FUNCTION_WORDS = FUNCTION_WORDS = [
    "i", "you", "he", "she", "it", "we", "they",
    "his", "her", "their", "this", "that", "these", "those",
    "is", "was", "were", "have", "had", "will", "would", "could", "should",
    "and", "but", "or", "so",
    "because", "although", "if", "when",
    "of", "to", "in", "with", "for",
]

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
    """Average five surface-level heuristics into a single 0.0 (human) – 1.0 (AI) score.

    Heuristics that lack sufficient data return None and are excluded from the average
    so they don't dilute the signal with a meaningless neutral value.
    """
    raw_scores = [
        _sentence_length_variance(text),
        _type_token_ratio(text),
        _punctuation_density(text),
        _paragraph_length_consistency(text),
        _function_word_frequency(text),
    ]
    scores = [s for s in raw_scores if s is not None]
    if not scores:
        return 0.5
    return round(sum(scores) / len(scores), 2)


def _sentence_length_variance(text):
    """Low variance in sentence length → AI tendency → higher score."""
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if len(sentences) < 2:
        return None

    lengths = [len(s.split()) for s in sentences]
    std_dev = statistics.stdev(lengths)

    # AI writes uniform sentences (low std dev → score near 1.0).
    # Humans vary their rhythm (high std dev → score near 0.0).
    # Cap at std dev = 20 words to keep the score in 0.0–1.0.
    return 1.0 - min(std_dev / 20.0, 1.0)


def _type_token_ratio(text):
    """Lower vocabulary diversity (TTR) → AI tendency → higher score."""
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if not words:
        return None

    ttr = len(set(words)) / len(words)

    # AI reuses words more (low TTR → score near 1.0).
    # Humans draw on a wider vocabulary (high TTR → score near 0.0).
    return 1.0 - ttr


def _punctuation_density(text):
    """Sparse punctuation → AI tendency → higher score."""
    if not text:
        return None

    punct_chars = set('.,;:!?\'"()[]{}…—–')
    density = sum(1 for c in text if c in punct_chars) / len(text)

    # AI uses conservative punctuation (low density → score near 1.0).
    # Humans use expressive, varied punctuation (high density → score near 0.0).
    # Cap at 10 % density to keep the score in 0.0–1.0.
    return 1.0 - min(density / 0.10, 1.0)


def _paragraph_length_consistency(text):
    """Even paragraph lengths → AI tendency → higher score."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) < 2:
        # Fall back to single newlines if there are no blank lines.
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    if len(paragraphs) < 2:
        return None

    lengths = [len(p.split()) for p in paragraphs]
    std_dev = statistics.stdev(lengths)

    # AI produces evenly balanced paragraphs (low std dev → score near 1.0).
    # Humans write to idea length, not word count (high std dev → score near 0.0).
    # Cap at std dev = 50 words to keep the score in 0.0–1.0.
    return 1.0 - min(std_dev / 50.0, 1.0)


def _function_word_frequency(text):
    """Uniform distribution of function words → AI tendency → higher score."""
    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    if not words:
        return None

    total = len(words)
    # Rate of each function word per 100 words.
    word_counts = Counter(words)
    rates = [word_counts[fw] / total * 100 for fw in FUNCTION_WORDS]

    if sum(rates) == 0 or len(rates) < 2:
        return None

    mean = sum(rates) / len(rates)
    std_dev = statistics.stdev(rates)
    cv = std_dev / mean  # coefficient of variation: 0 = perfectly uniform

    # AI distributes function words uniformly (low CV → score near 1.0).
    # Humans use them unevenly depending on style (high CV → score near 0.0).
    # Cap at CV = 2.0 to keep the score in 0.0–1.0.
    return 1.0 - min(cv / 2.0, 1.0)
