from app.services.openai_service import ask_json

INSTRUCTIONS = """
You are an English grammar coach.
Analyze the learner's English text and return JSON with exactly these keys:
score (integer 0-100),
estimated_level (one of A1/A2/B1/B2/C1/C2; approximate evidence from this sample only),
corrected_text (string),
summary (string),
errors (array of objects with original, correction, rule, explanation),
practice (array of 3 short exercises),
topic (short string identifying the main grammar area).

Score language accuracy, not the learner's ideas.
estimated_level must reflect the grammatical complexity and control actually demonstrated
in this sample; do not derive it mechanically from score.
If the input is already correct, praise it briefly and make practice slightly harder.
"""


def analyze(text: str, level: str = "not assessed") -> dict:
    return ask_json(
        INSTRUCTIONS,
        f"Current overall learner profile: {level}\nLearner text:\n{text}",
    )
