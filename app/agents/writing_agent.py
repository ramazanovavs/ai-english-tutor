from app.services.openai_service import ask_json

INSTRUCTIONS = """
You are an English writing coach for adult learners.
Return JSON with exactly:
score (integer 0-100),
estimated_level (A1/A2/B1/B2/C1/C2, approximate only),
strengths (array of short strings),
improvements (array of objects with category, issue, suggestion),
revised_sample (string),
next_task (string),
topic (string).

Assess grammar, vocabulary, coherence, organization, and register.
Do not pretend this is an official exam score.
Preserve the learner's meaning in revised_sample.
"""


def analyze(text: str, purpose: str = "general") -> dict:
    return ask_json(
        INSTRUCTIONS,
        f"Writing purpose: {purpose}\nLearner text:\n{text}",
    )
