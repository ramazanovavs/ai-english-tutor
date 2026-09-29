from app.services.openai_service import ask_json

INSTRUCTIONS = """
Create an adaptive English mini-test.
Return JSON with exactly:
title (string),
level (string),
questions (array of exactly 8 objects).
Each question object must have:
id (integer 1..8),
skill (grammar or vocabulary or reading),
question (string),
options (array of 4 strings),
answer_index (integer 0..3),
explanation (string).

Do not put the correct answer in the wording of the question.
"""


def generate(level: str = "B1", focus: str = "mixed") -> dict:
    return ask_json(
        INSTRUCTIONS,
        f"Target CEFR: {level}\nFocus: {focus}",
    )
