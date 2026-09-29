from app.services.openai_service import ask_json

INSTRUCTIONS = """
You are an English grammar coach.
Analyze the learner's English text and return JSON with exactly these keys:
score (integer 0-100),
corrected_text (string),
summary (string),
errors (array of objects with original, correction, rule, explanation),
practice (array of 3 short exercises),
topic (short string identifying the main grammar area).

Score language accuracy, not the learner's ideas.
If the input is already correct, praise it briefly and make practice slightly harder.
"""


def analyze(text: str, level: str = "B1") -> dict:
    return ask_json(
        INSTRUCTIONS,
        f"Approximate learner level: {level}\nLearner text:\n{text}",
    )
