from app.services.openai_service import ask_json

SET_INSTRUCTIONS = """
You are an English vocabulary trainer.

Return JSON with exactly:
topic (string),
items (array of exactly 10 objects),
tip (string).

Each item must contain exactly:
word (string),
part_of_speech (string),
definition (string),
example (string),
collocation (string).

Requirements:
- Target the requested CEFR level.
- Use practical contemporary English.
- Prefer useful words, phrases, phrasal verbs, and collocations rather than trivial items.
- Avoid duplicates and near-duplicates.
- Definitions must be learner-friendly and written in English.
- Examples must be natural and clearly show the meaning.
"""


PRACTICE_INSTRUCTIONS = """
You create a vocabulary practice test from a supplied word list.

Return JSON with exactly:
title (string),
questions (array of exactly 10 objects).

Use exactly this mix and order:
- questions 1-4: type "meaning"
- questions 5-7: type "context"
- questions 8-10: type "recall"

Every question object must contain:
id (integer 1-10),
type ("meaning", "context", or "recall"),
word (the target vocabulary item),
question (string),
options (array),
answer (string),
explanation (string).

Rules:
- Meaning: exactly 4 definition options; answer is the full correct option text.
- Context: exactly 4 word/phrase options; answer is the full correct option text.
- Recall: options must be []; ask the learner to type the target word/phrase; answer is the exact target word/phrase.
- Test only vocabulary supplied in the source list.
- Do not make the correct option conspicuously longer or more detailed.
- Keep all distractors plausible but objectively wrong.
- Do not reveal the answer in the question wording.
- Match the requested CEFR level.
"""


def create_set(topic: str, level: str = "B1") -> dict:
    return ask_json(
        SET_INSTRUCTIONS,
        f"Topic: {topic}\nCEFR target: {level}",
    )


def create_practice(topic: str, level: str, items: list[dict]) -> dict:
    source = [
        {
            "word": x.get("word"),
            "part_of_speech": x.get("part_of_speech"),
            "definition": x.get("definition"),
            "example": x.get("example"),
            "collocation": x.get("collocation"),
        }
        for x in items
        if x.get("word")
    ]
    return ask_json(
        PRACTICE_INSTRUCTIONS,
        f"Topic: {topic}\nCEFR target: {level}\nVocabulary source:\n{source}",
    )
