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


def generate(level: str = "B1", focus: str = "mixed", language: str = "en") -> dict:
    from app.agents.ielts_rubrics import language_rule, topic_text
    return ask_json(
        INSTRUCTIONS + "\n" + language_rule(language) + " This applies to explanations only.",
        f"Target CEFR: {level[:5]}\nFocus: {topic_text(focus, 120)}",
    )
