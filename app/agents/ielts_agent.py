from __future__ import annotations

from typing import Any

from app.services.openai_service import ask_json
from app.services.ielts_scoring import criterion, criteria_band
from app.agents.ielts_visuals import CHART_SPEC, pick_type, clean_chart, chart_as_text
from app.agents.ielts_verify import verify as verify_key
from app.agents.ielts_reading_types import GROUP_SPEC, clean_groups
import random
from app.agents.ielts_rubrics import (DATA_GUARD, WRITING_DESCRIPTORS, SPEAKING_DESCRIPTORS,
                                      learner_block, language_rule)

# One difficulty for every candidate, as in the real exam (no per-learner simplification).
EXAM_LEVEL = (
    "Use authentic IELTS exam difficulty for every candidate (never simplify for a learner level). "
    "Within the set, items progress from easier to harder so that the final items discriminate "
    "between bands 7, 8 and 9."
)


def generate_writing_prompt(test_type: str, task_number: int) -> dict:
    test_type = "General Training" if test_type.lower().startswith("general") else "Academic"
    task_label = f"Task {task_number}"
    instructions = """
You create original IELTS-style preparation tasks. Do not copy or closely imitate any published test item.
Return JSON with exactly these keys:
title, task_type, prompt, minimum_words, suggested_minutes, planning_questions (array of 3 strings), useful_language (array of 5 strings).
For General Training Task 1 create a letter situation with three bullet requirements.
For Task 2 create an essay prompt suitable for either test type.
"""
    request = f"Test type: {test_type}\n{task_label}\n{EXAM_LEVEL}\nGenerate one original practice task."
    if test_type == "Academic" and task_number == 1:
        kind = pick_type()
        instructions += (
            "\nAcademic Task 1: also return " + CHART_SPEC +
            f"\nThe visual type MUST be \"{kind}\". The prompt is the exam rubric only, e.g. "
            "'The chart below shows ... Summarise the information by selecting and reporting the main features, "
            "and make comparisons where relevant.' (for a process or map adapt the wording); never list the data in prompt."
        )
        last = None
        for _ in range(2):
            data = ask_json(instructions, request)
            try:
                data["chart"] = clean_chart(data.get("chart"))
                return data
            except ValueError as exc:
                last = exc
        raise last
    return ask_json(instructions, request)


def assess_writing(test_type: str, task_number: int, prompt: str, response: str, chart: dict | None = None,
                   language: str = "en") -> dict:
    criterion_key = "task_achievement" if task_number == 1 else "task_response"
    instructions = f"""
You are an IELTS preparation writing coach. This is formative AI feedback, NOT an official IELTS score.
Use the official IELTS-style four-criterion framework at a high level, but do not claim examiner status.
Return ONLY JSON with exactly these keys:
{criterion_key} (whole number 0-9),
coherence_cohesion (whole number 0-9),
lexical_resource (whole number 0-9),
grammatical_range_accuracy (whole number 0-9),
word_count (integer),
strengths (array of 3 concise strings),
improvements (array of 4 objects with criterion, issue, action),
error_examples (array of up to 5 objects with original, correction, explanation),
next_task (string),
disclaimer (string).
Each criterion is awarded in whole bands, as by IELTS examiners; use the full 0-9 range (band 9 is possible).
{WRITING_DESCRIPTORS}
{DATA_GUARD}
{language_rule(language)}
"""
    data = ask_json(
        instructions,
        f"Test type: {test_type}\nTask number: {task_number}\nMinimum words: {150 if task_number == 1 else 250}\n"
        f"Word count of the response: {len(response.split())}\nPrompt:\n{prompt}{chart_as_text(chart)}"
        f"\n\nLearner response:\n{learner_block(response)}",
    )
    keys = [criterion_key, "coherence_cohesion", "lexical_resource", "grammatical_range_accuracy"]
    for key in keys:
        data[key] = criterion(data.get(key))
    words = len(response.split())
    minimum = 150 if task_number == 1 else 250
    if words <= 20:  # IELTS rule: 20 words or fewer = band 1 in every criterion
        for key in keys:
            data[key] = 1
    elif words < minimum * 2 / 3:  # app rule: a substantially short response cannot cover the task
        data[criterion_key] = min(data[criterion_key], 5)
    data["underlength"] = words < minimum
    data["overall_band"] = criteria_band([data[k] for k in keys])  # computed here, not by the model
    data["word_count"] = words
    data["disclaimer"] = "AI practice estimate only; an official IELTS band can only be awarded through an official test process."
    return data


def generate_speaking_set(part: int) -> dict:
    part = min(3, max(1, int(part)))
    instructions = """
Create original IELTS-style Speaking preparation material; do not reproduce published test questions.
Return JSON with exactly: part, title, instructions, questions, cue_card, bullet_points.
For Part 1: questions = 8 familiar-topic questions on 2-3 topics (e.g. work/study, home town, a hobby); cue_card "" and bullet_points [].
For Part 2: cue_card = the task ("Describe a ..."), bullet_points = exactly 4 short prompts without the words "You should say" (e.g. "where you went", ..., and the last one "and explain why ..."); questions [].
For Part 3: questions = 6 abstract discussion questions related to a plausible Part 2 topic; cue_card should name that topic in a few words; bullet_points [].
"""
    return ask_json(instructions, f"Speaking Part {part}. {EXAM_LEVEL}")


def assess_speaking_transcript(part: int, prompt: str, transcript: str, language: str = "en") -> dict:
    instructions = """
You are an IELTS Speaking preparation coach reviewing a transcript only.
Return ONLY JSON with exactly:
fluency_coherence (whole number 0-9),
lexical_resource (whole number 0-9),
grammatical_range_accuracy (whole number 0-9),
pronunciation (null),
strengths (array of 3 strings),
improvements (array of 4 objects with criterion, issue, action),
better_phrasing (array of up to 5 objects with original, improved),
follow_up_question (string),
disclaimer (string).
Because you only have a transcript, DO NOT score pronunciation. Criteria are whole bands; use the full 0-9 range.
""" + SPEAKING_DESCRIPTORS + DATA_GUARD + "\n" + language_rule(language)
    data = ask_json(instructions, f"Part: {part}\nPrompt:\n{prompt}\n\nTranscript:\n{learner_block(transcript)}")
    keys = ["fluency_coherence", "lexical_resource", "grammatical_range_accuracy"]
    for key in keys:
        data[key] = criterion(data.get(key))
    data["provisional_language_band"] = criteria_band([data[k] for k in keys])
    data["pronunciation"] = None
    data["disclaimer"] = "Transcript-only practice estimate. Pronunciation is not assessed here, so this is not a complete or official IELTS Speaking band."
    return data


FOCUS_LABELS = {
    "multiple_choice": "multiple choice", "true_false_not_given": "TRUE / FALSE / NOT GIVEN",
    "yes_no_not_given": "YES / NO / NOT GIVEN", "matching": "matching information",
    "completion": "sentence / note completion", "short_answer": "short-answer (NO MORE THAN THREE WORDS)",
}
GROUP_TYPES = ("matching_headings", "summary_completion", "diagram_labelling")


def generate_reading_practice(test_type: str, focus: str | None = None) -> dict:
    instructions = """
Create an original IELTS-style Reading practice set for study, not an official or copied test.
Return JSON with exactly: title, passage, questions, answer_key, explanations, groups.
passage: 650-850 words for Academic; 500-700 words for General Training.
questions: exactly 10 objects with id (1-10), type, question, options (array; empty if not MCQ), instruction.
Use a varied mix of multiple choice, true/false/not given, matching-style, and short-answer where practical in plain text.
answer_key: object mapping string question ids to exact answers. explanations: object mapping ids to concise explanations.
""" + GROUP_SPEC
    kind = "General Training" if test_type.lower().startswith("general") else "Academic"
    special = random.choice(["matching_headings", "summary_completion", "diagram_labelling"] if kind == "Academic"
                            else ["matching_headings", "summary_completion"])
    request = f"IELTS {kind}. Include one \"{special}\" group of 4-6 questions; the rest standard. {EXAM_LEVEL}"
    if focus in GROUP_TYPES:  # targeted drill of a weak question type
        special = focus
        request = (f"IELTS {kind}. Targeted drill: include one \"{focus}\" group of 7 questions; "
                   f"the remaining 3 standard. {EXAM_LEVEL}")
    elif focus in FOCUS_LABELS:
        request = (f"IELTS {kind}. Targeted drill: at least 7 of the 10 questions must be of type \"{focus}\" "
                   f"({FOCUS_LABELS[focus]}); also include one \"{special}\" group of 3 questions. {EXAM_LEVEL}")
    data = None
    for attempt in range(2):
        data = ask_json(instructions, request)
        try:
            data = clean_groups(data, special if attempt == 0 else None)
            break
        except ValueError:
            data["groups"] = []
    return verify_key(data, "passage", "passage")


def generate_listening_practice(focus: str | None = None) -> dict:
    instructions = """
Create an original IELTS-style Listening practice set. It will be converted to text-to-speech.
Return JSON with exactly: title, audio_script, questions, answer_key, explanations.
audio_script: a natural English conversation or monologue, about 300-420 words, suitable for text-to-speech. Do not label answers inside the script.
questions: exactly 10 objects with id (1-10), type, question, options (array; use 4 options for MCQ and [] otherwise).
answer_key: object mapping string ids to exact answers. explanations: object mapping ids to concise explanations.
Do not copy published IELTS material.
"""
    request = EXAM_LEVEL
    if focus in ("multiple_choice", "completion", "short_answer"):
        request += (f"\nTargeted drill: at least 7 of the 10 questions must be of type \"{focus}\" "
                    f"({FOCUS_LABELS[focus]}).")
    return verify_key(ask_json(instructions, request), "audio_script", "transcript")
