"""
Learning from results: a Band 9 model answer, the learner's own text rewritten one band higher,
and the words / grammar points worth moving into Vocabulary and Grammar Coach.
"""

from __future__ import annotations

import math

from app.services.openai_service import ask_json
from app.agents.ielts_visuals import chart_as_text
from app.agents.ielts_rubrics import DATA_GUARD, learner_block, language_rule

CEFR = ("A1", "A2", "B1", "B2", "C1", "C2")


def target_band(current: float | None) -> int:
    """One whole band above the current estimate (6.5 -> 7, 6.0 -> 7), never above 9."""
    if current is None:
        return 7
    return int(min(9, max(5, math.floor(float(current)) + 1)))


def improve(kind: str, prompt: str, text: str, band: float | None, language: str = "en",
            test_type: str = "Academic", task_number: int = 2, chart: dict | None = None) -> dict:
    kind = "speaking" if kind == "speaking" else "writing"
    target = target_band(band)
    if kind == "writing":
        minimum = 150 if task_number == 1 else 250
        what = (f"IELTS {test_type} Writing Task {task_number}. The model answer must be {minimum + 20}-{minimum + 60} words, "
                "formal and complete for the task.")
    else:
        what = ("IELTS Speaking. The model answer is natural spoken English (contractions, discourse markers), "
                "about 45 seconds per Part 1/3 question or 2 minutes for a Part 2 cue card; answer every question asked.")
    instructions = f"""
You are an IELTS teacher. {what}
Return ONLY JSON with exactly these keys:
model_answer (string: an original Band 9 answer to the same task),
model_notes (array of 3-4 strings: what makes it Band 9 — refer to the official criteria),
upgraded (string: the LEARNER'S OWN answer rewritten to about Band {target}; keep their ideas, structure and
  most of their words; change only what is needed to reach Band {target}; do not add new arguments),
changes (array of up to 8 objects with original, improved, why — the most important edits in "upgraded"),
vocabulary (array of up to 6 objects with word, part_of_speech, definition, example, collocation — useful words
  or collocations from the model or upgraded answer that the learner did not use or used wrongly),
grammar_focus (array of up to 3 objects with topic, rule, example_from_text — grammar points where the learner
  made errors; example_from_text quotes the learner's sentence),
estimated_cefr (one of A1, A2, B1, B2, C1, C2 — the learner's level shown in the text).
The model answer, "upgraded", vocabulary words and examples are in English.
{DATA_GUARD}
{language_rule(language)} (This applies to model_notes, "why", definitions and rules only.)
"""
    data = ask_json(instructions, f"Task:\n{prompt[:4000]}{chart_as_text(chart) if chart else ''}\n\nCurrent estimate: {band}\nTarget: Band {target}\n\n"
                                  f"Learner answer:\n{learner_block(text[:6000])}")
    data["target_band"] = target
    data["kind"] = kind
    level = str(data.get("estimated_cefr") or "").upper()[:2]
    data["estimated_cefr"] = level if level in CEFR else None
    for key in ("model_notes", "changes", "vocabulary", "grammar_focus"):
        if not isinstance(data.get(key), list):
            data[key] = []
    data["vocabulary"] = [v for v in data["vocabulary"] if isinstance(v, dict) and str(v.get("word") or "").strip()][:6]
    return data
