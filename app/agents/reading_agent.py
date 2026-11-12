"""General English Reading practice: a text written at a CEFR level with 8 questions and a glossary."""

from __future__ import annotations

from app.services.openai_service import ask_json
from app.agents.ielts_rubrics import language_rule

WORDS = {"A1": "100-150", "A2": "150-220", "B1": "250-350", "B2": "350-450", "C1": "450-600", "C2": "550-700"}
GENRES = {"article": "a magazine article", "story": "a short story", "email": "a personal or work email",
          "blog": "a blog post", "notice": "notices and adverts", "report": "a short report", "interview": "an interview"}

INSTRUCTIONS = """
Write an original English reading text for learners and questions on it.
Return JSON with exactly: title, passage, questions, answer_key, explanations, glossary.
passage: {words} words, written exactly at CEFR {level} ({genre}); paragraphs separated by blank lines.
questions: exactly 8 objects {{id (1-8), type, question, options, instruction}} in text order where possible:
 - 3 of type "multiple_choice" (options: 4 strings);
 - 3 of type "true_false_not_given" (options exactly ["TRUE", "FALSE", "NOT GIVEN"]); for A1-A2 use "true_false" with options ["TRUE", "FALSE"];
 - 2 of type "short_answer" (options [], instruction "Write NO MORE THAN THREE WORDS from the text").
Questions check gist, detail, vocabulary in context and (from B1) inference and the writer's opinion.
answer_key: id -> array of accepted answers (exact option text for choice questions).
explanations: id -> one sentence quoting the line of the text that gives the answer.
glossary: 6-8 objects {{word, part_of_speech, definition, example, collocation}} — useful words from the text at {level}.
{lang} (Applies to explanations and glossary definitions only; the text, questions and examples are in English.)
"""


def generate(level: str, genre: str = "article", topic: str = "", language: str = "en") -> dict:
    prompt = INSTRUCTIONS.format(level=level, words=WORDS.get(level, "250-350"), genre=GENRES.get(genre, genre),
                                 lang=language_rule(language))
    from app.agents.ielts_rubrics import TOPIC_GUARD, topic_text
    data = ask_json(prompt + "\n" + TOPIC_GUARD,
                    f"CEFR {level}. Topic: {topic_text(topic) or 'any interesting everyday or academic topic'}")
    qs = [q for q in data.get("questions") or [] if q.get("question")]
    if len(qs) < 5 or not data.get("passage"):
        raise ValueError("The reading set is incomplete.")
    data["questions"] = qs[:8]
    return data
