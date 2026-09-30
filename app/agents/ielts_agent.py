from __future__ import annotations

from typing import Any

from app.services.openai_service import ask_json


def _band(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    v = max(0.0, min(9.0, v))
    return round(v * 2) / 2


def generate_writing_prompt(test_type: str, task_number: int, target_band: float = 6.5) -> dict:
    test_type = "General Training" if test_type.lower().startswith("general") else "Academic"
    task_label = f"Task {task_number}"
    instructions = """
You create original IELTS-style preparation tasks. Do not copy or closely imitate any published test item.
Return JSON with exactly these keys:
title, task_type, prompt, minimum_words, suggested_minutes, planning_questions (array of 3 strings), useful_language (array of 5 strings).
For Academic Task 1 create a text-described chart/table/process/map scenario that can be answered without an image.
For General Training Task 1 create a letter situation with three bullet requirements.
For Task 2 create an essay prompt suitable for either test type.
"""
    return ask_json(
        instructions,
        f"Test type: {test_type}\n{task_label}\nTarget learner band: {target_band}\nGenerate one original practice task.",
    )


def assess_writing(test_type: str, task_number: int, prompt: str, response: str) -> dict:
    criterion = "task_achievement" if task_number == 1 else "task_response"
    instructions = f"""
You are an IELTS preparation writing coach. This is formative AI feedback, NOT an official IELTS score.
Use the official IELTS-style four-criterion framework at a high level, but do not claim examiner status.
Return ONLY JSON with exactly these keys:
{criterion} (number 0-9 in 0.5 steps),
coherence_cohesion (number 0-9 in 0.5 steps),
lexical_resource (number 0-9 in 0.5 steps),
grammatical_range_accuracy (number 0-9 in 0.5 steps),
overall_band (number 0-9 in 0.5 steps),
word_count (integer),
strengths (array of 3 concise strings),
improvements (array of 4 objects with criterion, issue, action),
error_examples (array of up to 5 objects with original, correction, explanation),
next_task (string),
disclaimer (string).
The overall_band is the arithmetic mean of the four criteria rounded to the nearest half band.
Task 1 should normally be at least 150 words; Task 2 at least 250 words. Treat length as evidence/coverage, not a mechanical score penalty.
"""
    data = ask_json(
        instructions,
        f"Test type: {test_type}\nTask number: {task_number}\nPrompt:\n{prompt}\n\nLearner response:\n{response}",
    )
    keys = [criterion, "coherence_cohesion", "lexical_resource", "grammatical_range_accuracy", "overall_band"]
    for key in keys:
        data[key] = _band(data.get(key))
    data["disclaimer"] = "AI practice estimate only; an official IELTS band can only be awarded through an official test process."
    return data


def generate_speaking_set(part: int, target_band: float = 6.5) -> dict:
    part = min(3, max(1, int(part)))
    instructions = """
Create original IELTS-style Speaking preparation material; do not reproduce published test questions.
Return JSON with exactly: part, title, instructions, questions, preparation_seconds, speaking_seconds.
questions must be an array of strings. For Part 1 give 5 familiar-topic questions. For Part 2 give one cue-card style prompt with 4 bullet prompts. For Part 3 give 5 abstract follow-up questions related to a plausible Part 2 topic.
"""
    return ask_json(instructions, f"Speaking Part {part}; target learner band {target_band}.")


def assess_speaking_transcript(part: int, prompt: str, transcript: str) -> dict:
    instructions = """
You are an IELTS Speaking preparation coach reviewing a transcript only.
Return ONLY JSON with exactly:
fluency_coherence (0-9 in 0.5 steps),
lexical_resource (0-9 in 0.5 steps),
grammatical_range_accuracy (0-9 in 0.5 steps),
pronunciation (null),
provisional_language_band (0-9 in 0.5 steps),
strengths (array of 3 strings),
improvements (array of 4 objects with criterion, issue, action),
better_phrasing (array of up to 5 objects with original, improved),
follow_up_question (string),
disclaimer (string).
Because you only have a transcript, DO NOT score pronunciation and DO NOT call provisional_language_band an official Speaking band. Average only the three text-observable criteria for the provisional language band.
"""
    data = ask_json(instructions, f"Part: {part}\nPrompt:\n{prompt}\n\nTranscript:\n{transcript}")
    for key in ["fluency_coherence", "lexical_resource", "grammatical_range_accuracy", "provisional_language_band"]:
        data[key] = _band(data.get(key))
    data["pronunciation"] = None
    data["disclaimer"] = "Transcript-only practice estimate. Pronunciation is not assessed here, so this is not a complete or official IELTS Speaking band."
    return data


def generate_reading_practice(test_type: str, target_band: float = 6.5) -> dict:
    instructions = """
Create an original IELTS-style Reading practice set for study, not an official or copied test.
Return JSON with exactly: title, passage, questions, answer_key, explanations.
passage: 650-850 words for Academic; 500-700 words for General Training.
questions: exactly 10 objects with id (1-10), type, question, options (array; empty if not MCQ).
Use a varied mix of multiple choice, true/false/not given, matching-style, and short-answer where practical in plain text.
answer_key: object mapping string question ids to exact answers. explanations: object mapping ids to concise explanations.
"""
    kind = "General Training" if test_type.lower().startswith("general") else "Academic"
    return ask_json(instructions, f"IELTS {kind}; target band {target_band}.")


def generate_listening_practice(target_band: float = 6.5) -> dict:
    instructions = """
Create an original IELTS-style Listening practice set. It will be converted to text-to-speech.
Return JSON with exactly: title, audio_script, questions, answer_key, explanations.
audio_script: a natural English conversation or monologue, about 300-420 words, suitable for text-to-speech. Do not label answers inside the script.
questions: exactly 10 objects with id (1-10), type, question, options (array; use 4 options for MCQ and [] otherwise).
answer_key: object mapping string ids to exact answers. explanations: object mapping ids to concise explanations.
Do not copy published IELTS material.
"""
    return ask_json(instructions, f"Target learner band: {target_band}.")
