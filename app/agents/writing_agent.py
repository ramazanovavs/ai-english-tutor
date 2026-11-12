from app.services.openai_service import ask_json

INSTRUCTIONS = """
You are an English writing coach for adult learners.
Return JSON with exactly:
score (integer 0-100),
estimated_level (exactly one of: A1, A2, B1, B2, C1, C2 — no ranges or extra words),
strengths (array of short strings),
improvements (array of objects with category, issue, suggestion),
revised_sample (string),
errors (array of up to 6 objects with original, correction, rule, explanation — real language errors quoted
  from the text: "original" is the exact wrong fragment, "correction" its corrected form),
next_task (string),
topic (string).

Assess grammar, vocabulary, coherence, organization, and register.
Do not pretend this is an official exam score.
Preserve the learner's meaning in revised_sample.
"""


def analyze(text: str, purpose: str = "general", language: str = "en") -> dict:
    from app.agents.ielts_rubrics import language_rule
    return ask_json(
        INSTRUCTIONS + "\n" + language_rule(language) + " This applies to strengths, issues, suggestions, "
        "explanations and next_task; revised_sample and corrections stay in English.",
        f"Writing purpose: {purpose}\nLearner text:\n{text}",
    )
