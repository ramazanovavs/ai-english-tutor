"""
IELTS Speaking with an examiner.

The examiner speaks every question (TTS); the candidate answers by voice only. The flow follows
the exam: Part 1 (4-5 min of short questions), Part 2 (cue card, 1 minute to prepare, up to
2 minutes to speak, then a rounding-off question), Part 3 (discussion where follow-up questions
depend on the candidate's answers). A session is a plain dict so that it can be stored in the
mock metadata (full mock) or in the tests table (practice of one part).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
import uuid

from app.services.openai_service import ask_json, synthesize_speech
from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)
PHRASE_BUCKET = "listening-audio"
ROUNDING_OFF_FALLBACK = "Thank you. Is that something you would like to experience again?"
END_TEXT = "Thank you. That is the end of the Speaking test."

EXAMINER_VOICE = "cedar"
EXAMINER_STYLE = ("You are an IELTS Speaking examiner: polite, neutral and clear, speaking at a natural, "
                  "moderate pace. Read exactly the given words.")

PART1_SECONDS = 270          # move on after ~4.5 minutes
PART3_SECONDS = 270
PART3_MAX_QUESTIONS = 6
ANSWER_SECONDS = {1: 45, 2: 120, 3: 75}
ROUNDING_OFF_SECONDS = 30
PREP_SECONDS = 60

PART2_INTRO = ("Now I'm going to give you a topic, and I'd like you to talk about it for one to two minutes. "
               "Before you talk, you'll have one minute to think about what you're going to say. "
               "You can make some notes if you wish. Here is your topic.")
PART2_GO = ("All right? Remember, you have one to two minutes for this, so don't worry if I stop you. "
            "I'll tell you when the time is up. Can you start speaking now, please?")


def _now() -> float:
    return time.time()


def new_session(content: dict, parts: list[int]) -> dict:
    """content: {"1": {...questions}, "2": {cue_card, bullet_points}, "3": {...questions}}"""
    return {
        "id": str(uuid.uuid4()),
        "parts": parts,
        "content": content,
        "turns": [],
        "state": {"part": parts[0], "index": 0, "part_started": None, "rounding_done": False, "part3_asked": 0},
        "done": False,
        "started_at": _now(),
    }


def _turn(session: dict, part: int, kind: str, text: str, **extra) -> dict:
    turn = {"id": f"t{len(session['turns']) + 1}", "part": part, "kind": kind, "text": text,
            "answer_seconds": extra.pop("answer_seconds", ANSWER_SECONDS.get(part, 45)),
            "transcript": None, "audio_path": None, "spoken_seconds": None, **extra}
    session["turns"].append(turn)
    return turn


def pending_turn(session: dict) -> dict | None:
    last = session["turns"][-1] if session["turns"] else None
    if last and last["kind"] != "end" and last["transcript"] is None:
        return last
    return None


def _dialogue(session: dict, part: int) -> str:
    lines = []
    for t in session["turns"]:
        if t["part"] == part and t["transcript"] is not None:
            lines.append(f"Examiner: {t['text']}\nCandidate: {t['transcript'] or '(no answer)'}")
    return "\n".join(lines[-8:])


def _adaptive_question(kind: str, session: dict) -> str:
    c3 = session["content"].get("3") or {}
    c2 = session["content"].get("2") or {}
    if kind == "rounding_off":
        instructions = ("You are an IELTS Speaking examiner finishing Part 2. Ask ONE brief rounding-off "
                        "question about the candidate's talk that can be answered in a sentence or two "
                        "(e.g. 'Do you think you will ... again?'). Return JSON {\"question\": string}.")
        context = f"Cue card: {c2.get('cue_card')}\n{_dialogue(session, 2)}"
    else:
        instructions = (
            "You are an IELTS Speaking examiner in Part 3 (two-way discussion of abstract issues). "
            "Ask the NEXT question: usually a short follow-up that makes the candidate justify, compare, "
            "speculate or evaluate what they just said; or move on to the next planned question if the "
            "topic is exhausted. One question only, at most 30 words, no feedback or praise. "
            "Return JSON {\"question\": string}.")
        context = (f"Part 2 topic: {c2.get('cue_card')}\nPlanned Part 3 questions: {c3.get('questions')}\n"
                   f"Discussion so far:\n{_dialogue(session, 3)}")
    try:
        q = str(ask_json(instructions, context).get("question") or "").strip()
        if q:
            return q
    except Exception:
        logger.warning("Adaptive question unavailable; using the planned question")
    if kind == "rounding_off":
        return ROUNDING_OFF_FALLBACK.replace("Thank you. ", "")
    planned = c3.get("questions") or ["Why do you think that is?"]
    return planned[min(max(session["state"]["part3_asked"] - 1, 0), len(planned) - 1)]


def next_turn(session: dict) -> dict:
    """Create the examiner's next turn (call only when no answer is pending)."""
    st = session["state"]
    single = len(session["parts"]) == 1
    while True:
        part = st["part"]
        if part not in session["parts"]:
            later = [p for p in session["parts"] if p > part]
            if not later:
                session["done"] = True
                return _turn(session, part, "end", END_TEXT, answer_seconds=0)
            st.update(part=later[0], index=0, part_started=None)
            continue

        if part == 1:
            qs = (session["content"].get("1") or {}).get("questions") or []
            if st["part_started"] is None:
                st["part_started"] = _now()
                intro = ("Good morning. My name is Daniel, and I'll be your examiner today. "
                         "In this first part, I'd like to ask you some questions about yourself. ")
                if qs:
                    st["index"] = 1
                    return _turn(session, 1, "question", intro + qs[0])
            if st["index"] < len(qs) and _now() - st["part_started"] < PART1_SECONDS:
                st["index"] += 1
                return _turn(session, 1, "question", qs[st["index"] - 1])
            st.update(part=2, index=0, part_started=None)
            continue

        if part == 2:
            c2 = session["content"].get("2") or {}
            if st["part_started"] is None:
                st["part_started"] = _now()
                intro = ("Good morning. " if single else "") + PART2_INTRO
                return _turn(session, 2, "cue_card", intro, cue_card=c2.get("cue_card"),
                             bullet_points=c2.get("bullet_points") or [], prep_seconds=PREP_SECONDS,
                             go_text=PART2_GO, answer_seconds=ANSWER_SECONDS[2])
            if not st["rounding_done"]:
                st["rounding_done"] = True
                return _turn(session, 2, "question", "Thank you. " + _adaptive_question("rounding_off", session),
                             answer_seconds=ROUNDING_OFF_SECONDS)
            st.update(part=3, index=0, part_started=None)
            continue

        if part == 3:
            qs = (session["content"].get("3") or {}).get("questions") or []
            if st["part_started"] is None:
                st["part_started"] = _now()
                st["part3_asked"] = 1
                lead = ("We've been talking about a topic from Part 2, and I'd like to discuss with you "
                        "one or two more general questions related to it. ") if not single else \
                       "In this part, I'd like to discuss some more general questions with you. "
                return _turn(session, 3, "question", lead + (qs[0] if qs else "What do you think about this topic?"))
            if st["part3_asked"] < PART3_MAX_QUESTIONS and _now() - st["part_started"] < PART3_SECONDS:
                st["part3_asked"] += 1
                return _turn(session, 3, "question", _adaptive_question("part3", session))
            st.update(part=4)
            continue

        session["done"] = True
        return _turn(session, part, "end", END_TEXT, answer_seconds=0)


def record_answer(session: dict, turn_id: str, transcript: str | None, audio_path: str | None,
                  seconds: float | None, pending: bool = False) -> None:
    for t in session["turns"]:
        if t["id"] == turn_id:
            t["transcript"] = transcript or ""
            t["audio_path"] = audio_path
            t["spoken_seconds"] = seconds
            t["transcript_pending"] = pending  # transcription service unavailable: done later
            return
    raise KeyError("Unknown turn")


_tts_cache: dict[str, str] = {}
_stored_phrases: set[str] = set()


def _phrase_path(text: str) -> str:
    return f"examiner/{hashlib.sha1(text.encode()).hexdigest()}.mp3"


def _store_phrase(text: str, audio: bytes) -> None:
    path = _phrase_path(text)
    if path in _stored_phrases:
        return
    try:
        admin_db().storage.from_(PHRASE_BUCKET).upload(path, audio, {"content-type": "audio/mpeg", "upsert": "true"})
        _stored_phrases.add(path)
    except Exception:
        logger.exception("Could not store examiner phrase")


def speech_b64(text: str) -> str:
    """Examiner audio: TTS, stored for reuse; if TTS is unavailable, the stored copy is used."""
    if text not in _tts_cache:
        try:
            audio = synthesize_speech(text, voice=EXAMINER_VOICE, instructions=EXAMINER_STYLE)
            threading.Thread(target=_store_phrase, args=(text, audio), daemon=True).start()
        except Exception:
            audio = admin_db().storage.from_(PHRASE_BUCKET).download(_phrase_path(text))  # raises if missing
        _tts_cache[text] = base64.b64encode(audio).decode()
        if len(_tts_cache) > 300:
            _tts_cache.pop(next(iter(_tts_cache)))
    return _tts_cache[text]


def fixed_phrases(content: dict, parts: list[int]) -> list[str]:
    """Every examiner line that can be said without the AI (used to pre-voice bank exams)."""
    single = len(parts) == 1
    q1 = (content.get("1") or {}).get("questions") or []
    q3 = (content.get("3") or {}).get("questions") or []
    out = [END_TEXT]
    if 1 in parts and q1:
        out.append("Good morning. My name is Daniel, and I'll be your examiner today. "
                   "In this first part, I'd like to ask you some questions about yourself. " + q1[0])
        out += q1[1:]
    if 2 in parts:
        out += [("Good morning. " if single else "") + PART2_INTRO, PART2_GO, ROUNDING_OFF_FALLBACK]
    if 3 in parts:
        lead = ("We've been talking about a topic from Part 2, and I'd like to discuss with you "
                "one or two more general questions related to it. ") if not single else \
               "In this part, I'd like to discuss some more general questions with you. "
        out.append(lead + (q3[0] if q3 else "What do you think about this topic?"))
        out += q3[1:] + ["Why do you think that is?"]
    return out


def prevoice(content: dict, parts: list[int]) -> None:
    """Voice and store the fixed examiner lines in the background."""
    def job():
        for text in fixed_phrases(content, parts):
            try:
                _store_phrase(text, synthesize_speech(text, voice=EXAMINER_VOICE, instructions=EXAMINER_STYLE))
            except Exception:
                logger.exception("Could not pre-voice an examiner line")
                return
    threading.Thread(target=job, daemon=True).start()


def public_turn(turn: dict) -> dict:
    """What the browser receives: the examiner's audio, not the question text (as in the exam)."""
    out = {k: turn.get(k) for k in ("id", "part", "kind", "answer_seconds", "prep_seconds",
                                    "cue_card", "bullet_points")}
    out["audio"] = speech_b64(turn["text"])
    if turn.get("go_text"):
        out["go_audio"] = speech_b64(turn["go_text"])
    return out


def transcripts_by_part(session: dict) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    for t in session["turns"]:
        if t["kind"] == "end":
            continue
        out.setdefault(str(t["part"]), []).append(
            f"Examiner: {t['text']}\nCandidate: {t['transcript'] if t['transcript'] else '(no answer)'}")
    return {k: "\n".join(v) for k, v in out.items()}
