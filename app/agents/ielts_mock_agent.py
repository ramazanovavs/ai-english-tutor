from __future__ import annotations

from app.services.openai_service import ask_json
from app.agents.ielts_agent import EXAM_LEVEL
from app.services.ielts_scoring import criterion, criteria_band
from app.agents.ielts_visuals import CHART_SPEC, pick_type, clean_chart


LISTENING_CONTEXTS = {
    1: "an everyday social conversation between two speakers (e.g. booking, enquiry, service, travel, accommodation)",
    2: "one speaker giving information about an everyday social setting, local facility, event or service",
    3: "an educational or training discussion between two or three speakers (e.g. students and a tutor)",
    4: "one speaker giving an academic lecture or talk for non-specialists",
}


def _clean_listening_part(data: dict, part: int) -> dict:
    speakers = [
        {"name": str(s.get("name") or f"Speaker {i + 1}").strip(),
         "gender": "male" if str(s.get("gender", "")).lower().startswith("m") else "female"}
        for i, s in enumerate(data.get("speakers") or [])
    ] or [{"name": "Speaker", "gender": "female"}]
    names = {s["name"] for s in speakers}
    script = []
    for line in data.get("script") or []:
        text = str(line.get("text") or "").strip()
        if text:
            who = str(line.get("speaker") or "").strip()
            script.append({"speaker": who if who in names else speakers[0]["name"], "text": text})
    questions = (data.get("questions") or [])[:10]
    if not script or len(questions) != 10:
        raise ValueError(f"Listening Part {part} is incomplete.")
    key = data.get("answer_key") or {}
    for q in questions:
        qid = str(q.get("id"))
        accepted = key.get(qid)
        if not accepted:
            raise ValueError(f"Listening Part {part}: no answer for question {qid}.")
        key[qid] = accepted if isinstance(accepted, list) else [accepted]
    return {
        "title": str(data.get("title") or f"Part {part}"),
        "intro": str(data.get("intro") or f"Part {part}.").strip(),
        "speakers": speakers,
        "script": script,
        "questions": questions,
        "answer_key": key,
        "explanations": data.get("explanations") or {},
        # Plain text kept for the review after the exam.
        "audio_script": "\n".join(f"{l['speaker']}: {l['text']}" for l in script),
    }


def generate_listening_part(part: int) -> dict:
    situation = LISTENING_CONTEXTS[part]
    first_q = (part - 1) * 10 + 1
    instructions = f"""
Create ONE original IELTS Listening Part {part} for a computer-delivered mock exam.
Never copy or closely imitate a published IELTS item. The text will be voiced by text-to-speech,
one voice per speaker, so write ONLY spoken words (no stage directions, no sound effects).

Return JSON with exactly:
title (string),
intro (string): what the exam narrator says before the recording, e.g.
  "Part {part}. You will hear ... First, you have some time to look at questions {first_q} to {first_q + 4}."
  (mention questions {first_q}-{first_q + 9} with the real numbers; do NOT read the questions),
speakers (array of objects with name and gender "female" or "male"),
script (array of objects with speaker and text; speaker is one of the speakers' names),
questions (array of exactly 10 objects),
answer_key (object mapping "1".."10" to an ARRAY of accepted exact answers),
explanations (object mapping "1".."10" to one short sentence, quoting where the answer is heard).

Each question object:
id (integer 1-10 within this part; the website shows them as {first_q}-{first_q + 9}),
type (one of "multiple_choice", "completion", "short_answer"),
question (string; for completion use a sentence/note/table line with "___" for the gap),
options (array: exactly 4 choices for multiple_choice; [] otherwise),
instruction (string; for completion/short answer a strict IELTS word limit such as
  "Write NO MORE THAN TWO WORDS AND/OR A NUMBER" or "Write ONE WORD ONLY").

Accepted answers must respect the word limit and be said in the recording; for multiple choice the accepted
answer is the full option text. Add spelling variants only when genuinely equivalent (e.g. "12" and "twelve").
Script length: 600-800 words, natural spoken English with realistic distractors (a speaker corrects
themselves, mentions a wrong option first, etc.). Questions follow the order of the recording.
Do not reveal answers in question wording.
"""
    last_error = None
    for _ in range(2):
        try:
            return _clean_listening_part(
                ask_json(instructions, f"Part: {part}\nSituation: {situation}\n{EXAM_LEVEL}"), part)
        except ValueError as exc:
            last_error = exc
    raise last_error


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

General Training Task 1: create a letter situation and include exactly three bullet-point requirements.
Task 2: create a clear essay prompt based on a point of view, argument, or problem.
Task 1 minimum 150 words / suggested 20 minutes.
Task 2 minimum 250 words / suggested 40 minutes.
"""
    if kind != "Academic":
        return ask_json(instructions, f"Test type: {kind}\n{EXAM_LEVEL}")
    visual = pick_type()
    instructions += (
        "\nAcademic Task 1: task1 must also contain " + CHART_SPEC +
        f"\nThe visual type MUST be \"{visual}\". task1.prompt is the exam rubric only, e.g. "
        "'The chart below shows ... Summarise the information by selecting and reporting the main features, "
        "and make comparisons where relevant.' (adapt for a process or map); never list the data in the prompt."
    )
    last = None
    for _ in range(2):
        paper = ask_json(instructions, f"Test type: {kind}\n{EXAM_LEVEL}")
        try:
            paper["task1"]["chart"] = clean_chart((paper.get("task1") or {}).get("chart"))
            return paper
        except (ValueError, TypeError) as exc:
            last = exc
    raise ValueError(f"Could not create the Task 1 visual: {last}")


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
