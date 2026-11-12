from __future__ import annotations

from app.services.openai_service import ask_json
from app.agents.ielts_agent import EXAM_LEVEL
from app.services.ielts_scoring import criterion, criteria_band


def generate_listening_part(part: int) -> dict:
    contexts = {
        1: "an everyday social conversation between two speakers, such as booking, services, travel, or accommodation",
        2: "one speaker giving information about an everyday social setting, local facility, event, or service",
        3: "an educational or training discussion between two or three speakers",
        4: "one speaker giving an academic-style talk or lecture for non-specialists",
    }
    instructions = """
Create ONE original IELTS-style Listening mock-test part for exam practice.
Never copy or closely imitate a published IELTS item.

Return JSON with exactly:
title (string),
audio_script (string),
questions (array of exactly 10 objects),
answer_key (object),
explanations (object).

Each question object:
id (integer 1-10),
type (one of "multiple_choice", "completion", "short_answer"),
question (string),
options (array: exactly 4 choices for multiple_choice; [] otherwise),
instruction (string; for completion/short answer give a clear word limit such as "NO MORE THAN TWO WORDS").

answer_key maps "1"..."10" to an array of accepted exact answers.
For multiple choice, accepted answer should be the full option text.
For completion/short answer, provide sensible spelling variants only when genuinely equivalent.

Make the recording natural and long enough for serious practice: about 430-520 words.
Questions must follow the order of information in the recording.
Do not reveal answers in question wording.
"""
    return ask_json(
        instructions,
        f"Part: {part}\nSituation: {contexts[part]}\n{EXAM_LEVEL}",
    )


def generate_reading_section(test_type: str, section: int) -> dict:
    kind = "General Training" if test_type.lower().startswith("general") else "Academic"
    counts = {1: 13, 2: 13, 3: 14}
    if kind == "Academic":
        section_instruction = (
            "Create one continuous passage of about 750-900 words for a non-specialist educated audience. "
            "Across all sections the style should vary; Section 3 should be the most conceptually demanding."
        )
    else:
        if section == 1:
            section_instruction = (
                "Create 2-3 short everyday texts/notices/advertisements totalling about 700-800 words. "
                "Separate them clearly with headings."
            )
        elif section == 2:
            section_instruction = (
                "Create 2 workplace-related texts (policies, training, job information) totalling about 700-800 words."
            )
        else:
            section_instruction = (
                "Create one longer general-interest descriptive or argumentative text of about 750-850 words."
            )

    instructions = f"""
Create ONE original IELTS {kind} Reading mock-test section. Never copy published IELTS material.
{section_instruction}

Return JSON with exactly:
title (string),
passage (string),
questions (array of exactly {counts[section]} objects),
answer_key (object),
explanations (object).

Question objects:
id (integer starting at 1 within this section),
type (one of "multiple_choice", "true_false_not_given", "yes_no_not_given",
      "matching", "completion", "short_answer"),
question (string),
options (array; 4 options for multiple choice, useful lettered/text options for matching, [] for text entry),
instruction (string).

answer_key maps each question id string to an array of accepted exact answers.
Use a realistic mix of IELTS-style question types that work in plain text.
For text-entry questions, state a strict word limit in instruction.
Questions and keys must be unambiguous from the passage.
"""
    return ask_json(
        instructions,
        f"Test type: {kind}\nReading section: {section}\n{EXAM_LEVEL}",
    )


def generate_full_writing(test_type: str) -> dict:
    kind = "General Training" if test_type.lower().startswith("general") else "Academic"
    instructions = """
Create a complete original IELTS-style Writing mock paper with Task 1 and Task 2.
Do not copy published IELTS prompts.

Return JSON with exactly:
task1 (object), task2 (object).

Each task object must contain:
title, prompt, minimum_words, suggested_minutes.

Academic Task 1: provide all chart/table/process/map information textually so it can be answered without an image.
General Training Task 1: create a letter situation and include exactly three bullet-point requirements.
Task 2: create a clear essay prompt based on a point of view, argument, or problem.
Task 1 minimum 150 words / suggested 20 minutes.
Task 2 minimum 250 words / suggested 40 minutes.
"""
    return ask_json(instructions, f"Test type: {kind}\n{EXAM_LEVEL}")


def generate_full_speaking() -> dict:
    instructions = """
Create a complete original IELTS-style Speaking mock interview.
Do not reproduce published IELTS questions.

Return JSON with exactly:
part1, part2, part3.

part1: object with title, instructions, questions (array of 8 familiar-topic questions), suggested_minutes=5.
part2: object with title, instructions, cue_card (string), bullet_points (array of exactly 4 strings),
       preparation_seconds=60, speaking_seconds=120.
part3: object with title, instructions, questions (array of 6 abstract discussion questions linked to Part 2),
       suggested_minutes=5.

Make the progression natural and coherent.
"""
    return ask_json(instructions, EXAM_LEVEL)


def assess_full_speaking(parts: dict) -> dict:
    instructions = """
You are an IELTS Speaking preparation coach assessing a complete mock interview from TRANSCRIPTS ONLY.
This is formative AI feedback, not an official score.

Return JSON with exactly:
fluency_coherence (whole number 0-9),
lexical_resource (whole number 0-9),
grammatical_range_accuracy (whole number 0-9),
pronunciation (null),
strengths (array of 4 concise strings),
improvements (array of 4 objects with criterion, issue, action),
part_feedback (object with keys part1, part2, part3),
next_steps (array of 3 strings),
disclaimer (string).

Do NOT score pronunciation because no acoustic evidence is available.
Criteria are whole bands; use the full 0-9 range.
"""
    data = ask_json(instructions, f"Mock speaking data:\n{parts}")
    keys = ["fluency_coherence", "lexical_resource", "grammatical_range_accuracy"]
    for key in keys:
        data[key] = criterion(data.get(key))
    data["pronunciation"] = None
    data["provisional_language_band"] = criteria_band([data[k] for k in keys])
    return data
