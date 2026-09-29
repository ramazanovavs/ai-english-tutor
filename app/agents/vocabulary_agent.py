from app.services.openai_service import ask_json

INSTRUCTIONS = """
You are an English vocabulary trainer.
Return JSON with exactly:
topic (string),
items (array of 8 objects: word, part_of_speech, definition, example, collocation),
quiz (array of 5 objects: question, options (4 strings), answer),
tip (string).

Target the requested CEFR level. Use practical contemporary English.
"""


def create_set(topic: str, level: str = "B1") -> dict:
    return ask_json(
        INSTRUCTIONS,
        f"Topic: {topic}\nCEFR target: {level}",
    )
