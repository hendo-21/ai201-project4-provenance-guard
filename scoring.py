"""Combines signal scores into a confidence value and maps it to a label."""


def combine_scores(s1_score, s2_score, word_count):
    """Combines signal scores using a weighted average, then dampens toward 0.5 for short texts."""
    if word_count < 150:
        damping_factor = word_count / 150
        s2_score = 0.5 + (s2_score - 0.5) * damping_factor

    confidence = round(0.7 * s1_score + 0.3 * s2_score, 2)
    return confidence


def generate_label(confidence):
    """Maps a confidence score to a human-readable transparency label."""
    if confidence < 0.20:
        return "✅ Likely Human-Written · high confidence"
    elif confidence < 0.40:
        return "✅ Likely Human-Written · moderate confidence"
    elif confidence < 0.70:
        return "⚠️ Authorship Uncertain"
    elif confidence < 0.85:
        return "🤖 Likely AI-Generated · moderate confidence"
    else:
        return "🤖 Likely AI-Generated · high confidence"
