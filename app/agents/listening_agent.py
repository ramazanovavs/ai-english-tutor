"""General English Listening practice: script + 10 questions generated for a CEFR level."""

import re

from app.services.openai_service import ask_json

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
FORMATS = {"dialogue", "monologue", "announcement"}
TFNG_OPTIONS = ["True", "False", "Not Given"]

LEVEL_GUIDE = {
    "A1": ("80-100", "very short simple sentences, the most common everyday words, present simple"),
    "A2": ("100-130", "short simple sentences, everyday vocabulary, basic past and future forms"),
    "B1": ("150-200", "clear standard English, familiar topics, some linking words"),
    "B2": ("230-300", "natural speech, wider vocabulary, opinions and reasons, some idioms"),
    "C1": ("280-350", "natural fast speech, implicit meaning, precise and idiomatic vocabulary"),
    "C2": ("320-380", "fully natural speech, nuance, implied attitude, sophisticated vocabulary"),
}

FORMAT_GUIDE = {
    "dialogue": "a natural conversation between exactly 2 speakers (one female, one male) taking turns",
    "monologue": "one speaker talking (e.g. a short talk, story, podcast segment or explanation)",
    "announcement": "one speaker making a public announcement or recorded message "
                    "(e.g. at a station, airport, university, museum or on a phone line)",
}

INSTRUCTIONS = """
You create original General English listening practice for adult learners.
The script will be converted to speech, so write only words that are spoken
(no stage directions, no sound effects, no brackets).

Return JSON with exactly these keys:
- title: short title
- speakers: array of {name, gender} where gender is "female" or "male"
- script: array of {speaker, text}; speaker must be one of the speakers' names
- questions: array of exactly 10 objects in this order:
  * questions 1-2: type "gist" - multiple choice about the main idea/purpose,
    4 options, exactly one correct
  * questions 3-5: type "detail" - multiple choice about specific information,
    4 options, exactly one correct
  * questions 6-7: type "tfng" - a statement about the recording;
    answer is "True", "False" or "Not Given"
  * questions 8-10: type "gap" - a sentence from or close to the script with ONE gap
    written as "___"; the answer is 1-3 words that are said word-for-word in the script
  Each question object has: id (1-10), type, question, options (array; empty for "gap";
  ["True","False","Not Given"] for "tfng"), answer (must exactly equal one option for
  choice questions), accepted (array of other acceptable spellings for "gap", may be empty),
  evidence (an EXACT, word-for-word fragment of 3-15 words copied from one script line that
  proves the answer; empty string only for a "Not Given" answer), explanation (one short
  sentence in simple English).

Rules: questions must be answerable only by listening; do not reveal answers in other
questions; vary the position of the correct option; keep everything at the requested level.
"""


def _norm(text: str) -> str:
    text = re.sub(r"[^\w\s']", " ", str(text or "").lower())
    return " ".join(text.split())


def _clean(data: dict, fmt: str) -> dict:
    speakers = [
        {"name": str(s.get("name") or f"Speaker {i + 1}").strip(),
         "gender": "male" if str(s.get("gender", "")).lower().startswith("m") else "female"}
        for i, s in enumerate(data.get("speakers") or [])
    ] or [{"name": "Speaker", "gender": "female"}]
    names = {s["name"] for s in speakers}

    script = []
    for line in data.get("script") or []:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        speaker = str(line.get("speaker") or "").strip()
        if speaker not in names:
            speaker = speakers[0]["name"]
        script.append({"speaker": speaker, "text": text})
    if not script:
        raise ValueError("The AI returned an empty listening script.")
    if fmt == "dialogue" and len({l["speaker"] for l in script}) < 2:
        raise ValueError("The AI returned a dialogue with only one speaker.")

    full_text = _norm(" ".join(l["text"] for l in script))
    questions = []
    for i, q in enumerate((data.get("questions") or [])[:10], start=1):
        qtype = str(q.get("type") or "").lower()
        if qtype not in {"gist", "detail", "tfng", "gap"}:
            qtype = "detail"
        answer = str(q.get("answer") or "").strip()
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        if qtype == "tfng":
            options = TFNG_OPTIONS
            match = [o for o in options if _norm(o) == _norm(answer)]
            if not match:
                raise ValueError(f"Question {i} has an invalid True/False/Not Given answer.")
            answer = match[0]
        elif qtype == "gap":
            options = []
            if not answer or "___" not in str(q.get("question") or ""):
                raise ValueError(f"Gap question {i} is incomplete.")
        else:
            if answer not in options:
                match = [o for o in options if _norm(o) == _norm(answer)]
                if not match:
                    raise ValueError(f"Question {i}: the answer is not among the options.")
                answer = match[0]
        evidence = str(q.get("evidence") or "").strip()
        if evidence and _norm(evidence) not in full_text:
            evidence = ""  # not an exact quote: better no highlight than a wrong one
        questions.append({
            "id": i,
            "type": qtype,
            "question": str(q.get("question") or "").strip(),
            "options": options,
            "answer": answer,
            "accepted": [str(a) for a in (q.get("accepted") or []) if str(a).strip()],
            "evidence": evidence,
            "explanation": str(q.get("explanation") or "").strip(),
        })
    if len(questions) != 10:
        raise ValueError("The AI did not generate 10 questions.")

    return {
        "title": str(data.get("title") or "Listening practice").strip(),
        "speakers": speakers,
        "script": script,
        "questions": questions,
    }


def generate(level: str, topic: str, fmt: str) -> dict:
    level = level if level in CEFR_LEVELS else "B1"
    fmt = fmt if fmt in FORMATS else "dialogue"
    words, language = LEVEL_GUIDE[level]
    request = (
        f"CEFR level: {level}\n"
        f"Topic: {topic or 'everyday life'}\n"
        f"Format: {FORMAT_GUIDE[fmt]}\n"
        f"Script length: {words} words in total\n"
        f"Language: {language}"
    )
    last_error = None
    for _ in range(2):  # one retry if the model breaks the structure
        try:
            return _clean(ask_json(INSTRUCTIONS, request), fmt)
        except ValueError as exc:
            last_error = exc
    raise last_error


def normalize_answer(text: str) -> str:
    return _norm(text)


def is_correct(question: dict, given: str) -> bool:
    given_n = _norm(given)
    if not given_n:
        return False
    if question.get("type") == "gap":
        variants = [question.get("answer", "")] + list(question.get("accepted") or [])
        # Ignore a leading article, which learners often add or drop.
        strip_article = lambda s: re.sub(r"^(a|an|the) ", "", s)
        return any(strip_article(given_n) == strip_article(_norm(v)) for v in variants if v)
    return given_n == _norm(question.get("answer", ""))
