import json
import logging
import re

from typing import Literal
from datetime import datetime, timezone
from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.supabase_service import require_user, admin_db
from app.services.student_model import (
    ensure_profile,
    get_student_context,
    record_activity,
)
from app.agents.tutor_agent import chat as tutor_chat, chat_stream as tutor_chat_stream
from app.agents.grammar_agent import analyze as grammar_analyze
from app.agents.writing_agent import analyze as writing_analyze
from app.agents.vocabulary_agent import create_set, create_practice
from app.agents import listening_agent
from app.services import listening_audio, listening_bank
from app.config import get_settings
from app.agents.assessment_agent import generate as generate_test
from app.services.openai_service import ask_text, ask_json, transcribe_audio, synthesize_speech
from app.services.ielts_scoring import overall_band as ielts_overall_band, writing_band as ielts_writing_band
from app.services import ielts_exam
import threading
from app.agents.ielts_agent import (
    generate_writing_prompt as ielts_generate_writing_prompt,
    assess_writing as ielts_assess_writing,
    generate_speaking_set as ielts_generate_speaking_set,
    assess_speaking_transcript as ielts_assess_speaking,
    generate_reading_practice as ielts_generate_reading,
    generate_listening_practice as ielts_generate_listening,
)
from app.services.ielts_service import (
    ensure_ielts_profile, update_ielts_profile, get_ielts_dashboard,
    save_writing_attempt as save_ielts_writing,
    save_speaking_attempt as save_ielts_speaking,
    save_objective_attempt as save_ielts_objective,
)

from app.agents.ielts_mock_agent import (
    generate_listening_part as mock_generate_listening_part,
    generate_reading_section as mock_generate_reading_section,
    generate_full_writing as mock_generate_full_writing,
    generate_full_speaking as mock_generate_full_speaking,
    assess_full_speaking as mock_assess_full_speaking,
)
from app.services.ielts_mock_service import (
    start_mock as mock_start,
    get_mock as mock_get,
    get_section as mock_get_section,
    save_generated_section as mock_save_generated_section,
    start_section as mock_start_section,
    complete_section as mock_complete_section,
    update_mock_band as mock_update_band,
    set_mock_metadata as mock_set_metadata,
    finish_mock as mock_finish,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class TextPayload(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ChatPayload(TextPayload):
    mode: str = "tutor"


class WritingPayload(TextPayload):
    purpose: str = "general"


class VocabularyPayload(BaseModel):
    topic: str = Field(min_length=1, max_length=100)
    level: str = "B1"


class VocabularyPracticePayload(BaseModel):
    topic: str = Field(min_length=1, max_length=100)
    level: str = "B1"
    items: list[dict]


class VocabularyPracticeSubmission(BaseModel):
    test_id: str
    answers: dict[str, str]


class TestPayload(BaseModel):
    level: str = "B1"
    focus: str = "mixed"


class TestSubmission(BaseModel):
    title: str
    level: str
    questions: list[dict]
    answers: dict[str, int]


class SpeakingPayload(TextPayload):
    scenario: str = "general conversation"


class TTSPayload(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = None


@router.get("/me")
def me(user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    context = get_student_context(user["id"])
    context["email"] = user.get("email")
    context["is_admin"] = _is_admin(user)
    return context


@router.post("/chat")
def chat(payload: ChatPayload, user=Depends(require_user)):
    """Non-streaming fallback endpoint."""
    ensure_profile(user["id"], user.get("email"))
    answer = tutor_chat(user["id"], payload.text)

    db = admin_db()
    db.table("messages").insert([
        {"user_id": user["id"], "role": "user", "content": payload.text},
        {"user_id": user["id"], "role": "assistant", "content": answer},
    ]).execute()
    return {"answer": answer}


@router.post("/chat/stream")
def chat_stream(payload: ChatPayload, user=Depends(require_user)):
    """Stream tutor text to the browser using Server-Sent Events."""
    ensure_profile(user["id"], user.get("email"))
    user_id = user["id"]
    user_text = payload.text

    # Save the learner message before generation starts.
    admin_db().table("messages").insert({
        "user_id": user_id,
        "role": "user",
        "content": user_text,
    }).execute()

    def event_stream():
        chunks: list[str] = []
        try:
            for delta in tutor_chat_stream(user_id, user_text):
                chunks.append(delta)
                payload_json = json.dumps(
                    {"type": "delta", "text": delta},
                    ensure_ascii=False,
                )
                yield f"data: {payload_json}\n\n"

            answer = "".join(chunks).strip()
            if answer:
                admin_db().table("messages").insert({
                    "user_id": user_id,
                    "role": "assistant",
                    "content": answer,
                }).execute()

            yield 'data: {"type":"done"}\n\n'
        except Exception as exc:
            # Keep internal details in Render logs, but send a safe message to the UI.
            print(f"Chat stream error: {type(exc).__name__}: {exc}", flush=True)
            error_json = json.dumps(
                {
                    "type": "error",
                    "message": "The tutor could not complete the response. Please try again.",
                },
                ensure_ascii=False,
            )
            yield f"data: {error_json}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/grammar")
def grammar(payload: TextPayload, user=Depends(require_user)):
    context = get_student_context(user["id"])
    level = context.get("profile", {}).get("cefr_level") or "not assessed"
    result = grammar_analyze(payload.text, level)
    score = int(result.get("score", 0))
    estimated_level = result.get("estimated_level")
    record_activity(
        user["id"],
        "grammar",
        result.get("topic", "Grammar"),
        score,
        result,
        tested_level=estimated_level,
        evidence_source="ai_estimated_free_text",
    )
    return result


_CEFR_RE = re.compile(r"\b(A1|A2|B1|B2|C1|C2)\b", re.I)


def _clean_level(value) -> str | None:
    """The model sometimes returns 'A2 (approximate)' or 'A1-A2'; the DB accepts only A1..C2."""
    match = _CEFR_RE.search(str(value or ""))
    return match.group(1).upper() if match else None


def _clean_score(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


@router.post("/writing")
def writing(payload: WritingPayload, user=Depends(require_user)):
    try:
        result = writing_analyze(payload.text, payload.purpose)
    except Exception as exc:
        logger.exception("Writing analysis failed")
        raise HTTPException(status_code=502, detail=f"AI review failed: {exc}")

    score = _clean_score(result.get("score"))
    estimated_level = _clean_level(result.get("estimated_level"))
    result["score"] = score
    result["estimated_level"] = estimated_level or result.get("estimated_level")

    # Saving must never hide the feedback from the learner.
    try:
        ensure_profile(user["id"], user.get("email"))
        admin_db().table("writing_attempts").insert({
            "user_id": user["id"],
            "purpose": payload.purpose,
            "original_text": payload.text,
            "revised_text": result.get("revised_sample"),
            "score": score,
            "estimated_level": estimated_level,
            "feedback": result,
        }).execute()
    except Exception:
        logger.exception("Could not save writing attempt")

    try:
        record_activity(
            user["id"],
            "writing",
            str(result.get("topic") or payload.purpose),
            score,
            result,
            tested_level=estimated_level,
            evidence_source="ai_estimated_writing_sample",
        )
    except Exception:
        logger.exception("Could not record writing activity")

    return result


@router.post("/vocabulary")
def vocabulary(payload: VocabularyPayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    result = create_set(payload.topic, payload.level)

    record_activity(
        user["id"],
        "vocabulary",
        payload.topic,
        None,
        {"level": payload.level, "activity": "generated_set"},
    )

    db = admin_db()
    for item in result.get("items") or []:
        word = (item.get("word") or "").strip()
        if not word:
            continue
        db.table("vocabulary_words").upsert(
            {
                "user_id": user["id"],
                "word": word,
                "part_of_speech": item.get("part_of_speech"),
                "definition": item.get("definition"),
                "example_sentence": item.get("example"),
                "collocation": item.get("collocation"),
                "topic": payload.topic,
                "cefr_level": payload.level,
                "status": "new",
            },
            on_conflict="user_id,word",
        ).execute()

    return result


@router.post("/vocabulary/practice")
def vocabulary_practice(payload: VocabularyPracticePayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    if len(payload.items) < 4:
        raise HTTPException(status_code=400, detail="Generate a vocabulary set first.")

    result = create_practice(payload.topic, payload.level, payload.items)
    questions = result.get("questions") or []
    if len(questions) != 10:
        raise HTTPException(status_code=502, detail="The AI did not generate a complete 10-question practice set.")

    db = admin_db()
    test_row = db.table("tests").insert(
        {
            "user_id": user["id"],
            "title": result.get("title") or f"{payload.topic} vocabulary practice",
            "test_type": "vocabulary_practice",
            "focus": "vocabulary",
            "level": payload.level,
            "questions": questions,
        }
    ).execute()
    test_id = test_row.data[0]["id"]

    public_questions = [
        {
            "id": q.get("id"),
            "type": q.get("type"),
            "question": q.get("question"),
            "options": q.get("options") or [],
        }
        for q in questions
    ]

    return {
        "test_id": test_id,
        "title": result.get("title") or f"{payload.topic} vocabulary practice",
        "topic": payload.topic,
        "level": payload.level,
        "questions": public_questions,
    }


def _vocab_normalize(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


@router.post("/vocabulary/practice/submit")
def vocabulary_practice_submit(payload: VocabularyPracticeSubmission, user=Depends(require_user)):
    db = admin_db()

    test_row = (
        db.table("tests")
        .select("*")
        .eq("id", payload.test_id)
        .eq("user_id", user["id"])
        .eq("focus", "vocabulary")
        .limit(1)
        .execute()
    )
    if not test_row.data:
        raise HTTPException(status_code=404, detail="Vocabulary practice not found.")

    test = test_row.data[0]
    questions = test.get("questions") or []
    correct = 0
    details = []
    word_stats: dict[str, dict[str, int]] = {}

    for q in questions:
        qid = str(q.get("id"))
        given = payload.answers.get(qid, "")
        expected = str(q.get("answer") or "")
        ok = _vocab_normalize(given) == _vocab_normalize(expected)
        correct += int(ok)

        word = str(q.get("word") or "").strip()
        if word:
            stats = word_stats.setdefault(word, {"correct": 0, "wrong": 0})
            stats["correct" if ok else "wrong"] += 1

        details.append(
            {
                "id": q.get("id"),
                "type": q.get("type"),
                "question": q.get("question"),
                "answer": given,
                "correct_answer": expected,
                "correct": ok,
                "explanation": q.get("explanation") or "",
                "word": word,
            }
        )

    total = len(questions)
    score = round(correct / max(total, 1) * 100)

    attempt_row = db.table("test_attempts").insert(
        {
            "user_id": user["id"],
            "test_id": payload.test_id,
            "title": test.get("title"),
            "level": test.get("level"),
            "focus": "vocabulary",
            "score": score,
            "correct_count": correct,
            "total_count": total,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"practice_type": "vocabulary"},
        }
    ).execute()
    attempt_id = attempt_row.data[0]["id"]

    answer_rows = [
        {
            "attempt_id": attempt_id,
            "question_number": int(d["id"]),
            "skill": d.get("word") or "vocabulary",
            "question": d.get("question") or "",
            "selected_answer": d.get("answer"),
            "correct_answer": d.get("correct_answer"),
            "is_correct": d.get("correct"),
            "explanation": d.get("explanation"),
        }
        for d in details
    ]
    if answer_rows:
        db.table("test_answers").insert(answer_rows).execute()

    for word, stats in word_stats.items():
        row = (
            db.table("vocabulary_words")
            .select("*")
            .eq("user_id", user["id"])
            .eq("word", word)
            .limit(1)
            .execute()
        )
        if not row.data:
            continue

        item = row.data[0]
        new_correct = int(item.get("correct_count") or 0) + stats["correct"]
        new_wrong = int(item.get("wrong_count") or 0) + stats["wrong"]
        total_reviews = new_correct + new_wrong
        accuracy = new_correct / max(total_reviews, 1)

        if new_correct >= 3 and accuracy >= 0.75:
            status = "mastered"
        elif new_correct >= 1:
            status = "review"
        else:
            status = "learning"

        db.table("vocabulary_words").update(
            {
                "correct_count": new_correct,
                "wrong_count": new_wrong,
                "repetitions": int(item.get("repetitions") or 0) + 1,
                "status": status,
                "last_reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", item["id"]).execute()

    record_activity(
        user["id"],
        "vocabulary",
        test.get("title") or "Vocabulary practice",
        score,
        {
            "level": test.get("level"),
            "correct": correct,
            "total": total,
            "attempt_id": attempt_id,
        },
        tested_level=test.get("level"),
        evidence_source="explicit_cefr_vocabulary_practice",
    )

    return {
        "score": score,
        "correct": correct,
        "total": total,
        "details": details,
    }


# ---------------------------------------------------------------- Listening (General English)

class ListeningGeneratePayload(BaseModel):
    level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] = "B1"
    topic: str = Field(default="", max_length=100)
    format: Literal["dialogue", "monologue", "announcement"] = "dialogue"
    use_backup: bool = False  # learner asked for a backup test after an audio error


class ListeningSubmitPayload(BaseModel):
    test_id: str
    answers: dict[str, str]


def _load_listening_test(test_id: str, user_id: str) -> dict | None:
    rows = (
        admin_db().table("tests")
        .select("*")
        .eq("id", test_id)
        .eq("user_id", user_id)
        .eq("focus", "listening")
        .limit(1)
        .execute()
    ).data
    if not rows:
        return None
    content = rows[0].get("questions") or {}
    return content if isinstance(content, dict) else None


def _listening_public(test_id: str, test: dict, backup: bool, notice: str = "") -> dict:
    return {
        "test_id": test_id,
        "title": test["title"],
        "level": test.get("level"),
        "format": test.get("format"),
        "max_plays": listening_audio.MAX_PLAYS,
        "backup": backup,
        "notice": notice,
        "questions": [
            {"id": q["id"], "type": q["type"], "question": q["question"], "options": q["options"]}
            for q in test["questions"]
        ],
    }


def _listening_from_bank(payload: ListeningGeneratePayload, user: dict, reason: str) -> dict:
    try:
        row = listening_bank.pick(payload.level, payload.format, user["id"])
    except Exception:
        logger.exception("Backup bank is unavailable")
        row = None
    if not row:
        raise HTTPException(
            status_code=503,
            detail=f"{reason} There are no unused backup tests for level {payload.level} yet. Please try again later.",
        )
    try:
        audio = listening_bank.download(row["audio_path"])
    except Exception as exc:
        logger.exception("Could not download backup audio")
        raise HTTPException(status_code=503, detail=f"{reason} The backup audio could not be loaded: {exc}")

    test = dict(row["content"])
    test.update(level=row["level"], format=row.get("format") or test.get("format"),
                bank_id=str(row["id"]), audio_path=row["audio_path"])
    db_row = admin_db().table("tests").insert({
        "user_id": user["id"],
        "title": test["title"],
        "test_type": "listening_bank",
        "focus": "listening",
        "level": test["level"],
        "questions": test,
    }).execute()
    test_id = db_row.data[0]["id"]
    listening_audio.start(test_id, user["id"], test, audio=audio)
    listening_bank.mark_used(row)
    return _listening_public(test_id, test, True, f"{reason} This is a ready-made backup test for your level.")


@router.post("/listening/generate")
def listening_generate(payload: ListeningGeneratePayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    if payload.use_backup:
        return _listening_from_bank(payload, user, "The AI audio service is unavailable right now.")

    try:
        test = listening_agent.generate(payload.level, payload.topic.strip(), payload.format)
    except Exception:
        logger.exception("Listening generation failed; using the backup bank")
        return _listening_from_bank(payload, user, "The AI service is unavailable right now.")

    test["level"] = payload.level
    test["format"] = payload.format
    test["topic"] = payload.topic.strip()

    row = admin_db().table("tests").insert({
        "user_id": user["id"],
        "title": test["title"],
        "test_type": "listening_practice",
        "focus": "listening",
        "level": payload.level,
        "questions": test,  # full content incl. script and key; never sent before checking
    }).execute()
    test_id = row.data[0]["id"]

    # Voice in the background; a successful recording may also refill the backup bank.
    listening_audio.start(
        test_id, user["id"], test,
        on_ready=lambda audio: listening_bank.save_if_needed(test, audio),
    )
    return _listening_public(test_id, test, False)


@router.get("/listening/{test_id}/audio")
def listening_audio_play(test_id: str, user=Depends(require_user)):
    try:
        audio, plays_left = listening_audio.get_for_play(
            test_id, user["id"],
            lambda: _load_listening_test(test_id, user["id"]),
            # After a server restart a backup test reloads its MP3 from Storage, not from TTS.
            lambda t: (lambda: listening_bank.download(t["audio_path"])) if t.get("audio_path") else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    headers = {"Cache-Control": "no-store"}
    if plays_left is not None:
        headers["X-Plays-Left"] = str(plays_left)
    return StreamingResponse(BytesIO(audio), media_type="audio/mpeg", headers=headers)


@router.post("/listening/submit")
def listening_submit(payload: ListeningSubmitPayload, user=Depends(require_user)):
    test = _load_listening_test(payload.test_id, user["id"])
    if not test:
        raise HTTPException(status_code=404, detail="Listening practice not found.")

    details, correct = [], 0
    for q in test.get("questions") or []:
        given = str(payload.answers.get(str(q["id"]), "")).strip()
        ok = listening_agent.is_correct(q, given)
        correct += int(ok)
        details.append({
            "id": q["id"],
            "type": q["type"],
            "question": q["question"],
            "answer": given,
            "correct_answer": q["answer"],
            "correct": ok,
            "evidence": q.get("evidence", ""),
            "explanation": q.get("explanation", ""),
        })

    total = len(details)
    score = round(correct / max(total, 1) * 100)
    level = test.get("level")
    plays = listening_audio.plays_used(payload.test_id)
    transcript = "\n".join(f"{l['speaker']}: {l['text']}" for l in test.get("script") or [])

    try:
        admin_db().table("listening_attempts").insert({
            "user_id": user["id"],
            "title": test.get("title"),
            "transcript": transcript,
            "level": level,
            "score": score,
            "questions": test.get("questions") or [],
            "answers": details,
        }).execute()
    except Exception:
        logger.exception("Could not save listening attempt")

    record_activity(
        user["id"],
        "listening",
        test.get("title") or "Listening practice",
        score,
        {
            "level": level,
            "correct": correct,
            "total": total,
            "format": test.get("format"),
            "plays_used": plays,
            "test_id": payload.test_id,
        },
        tested_level=level,
        evidence_source="explicit_cefr_listening_practice",
    )

    listening_audio.mark_checked(payload.test_id)

    return {
        "score": score,
        "correct": correct,
        "total": total,
        "level": level,
        "details": details,
        "speakers": test.get("speakers") or [],
        "script": test.get("script") or [],
    }


def _admin_emails() -> set[str]:
    # Tolerate spaces, quotes and ";" separators in the ADMIN_EMAILS setting.
    raw = get_settings().admin_emails.replace(";", ",")
    return {e.strip().strip('"').strip("'").strip().lower() for e in raw.split(",") if e.strip()}


def _is_admin(user: dict) -> bool:
    return (user.get("email") or "").strip().lower() in _admin_emails()


def _require_admin(user: dict) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin only.")


@router.get("/listening/bank/status")
def listening_bank_status(user=Depends(require_user)):
    _require_admin(user)
    try:
        return listening_bank.fill_status()
    except Exception as exc:
        logger.exception("Backup bank status failed")
        return {
            "error": "The backup bank is not set up in Supabase yet. Run database/listening_bank.sql "
                     f"in the Supabase SQL Editor. Details: {exc}",
            "counts": {}, "per_level": listening_bank.BANK_PER_LEVEL, "running": False,
        }


@router.post("/listening/bank/fill")
def listening_bank_fill(user=Depends(require_user)):
    _require_admin(user)
    started = listening_bank.start_fill(listening_agent.generate, listening_audio.build_audio)
    return {"started": started, **listening_bank.fill_status()}


@router.post("/test/generate")
def test_generate(payload: TestPayload, user=Depends(require_user)):
    return generate_test(payload.level, payload.focus)


@router.post("/test/submit")
def test_submit(payload: TestSubmission, user=Depends(require_user)):
    correct = 0
    details = []
    by_skill: dict[str, dict[str, int]] = {}

    for q in payload.questions:
        qid = str(q.get("id"))
        chosen = payload.answers.get(qid)
        expected = q.get("answer_index")
        ok = chosen == expected
        correct += int(ok)

        skill = str(q.get("skill") or "").lower().strip()
        if skill in {"grammar", "vocabulary", "reading"}:
            bucket = by_skill.setdefault(skill, {"correct": 0, "total": 0})
            bucket["total"] += 1
            bucket["correct"] += int(ok)

        details.append(
            {
                "id": q.get("id"),
                "skill": skill or None,
                "correct": ok,
                "chosen": chosen,
                "answer_index": expected,
                "explanation": q.get("explanation"),
            }
        )

    total = len(payload.questions)
    score = round(correct / max(total, 1) * 100)

    # Save the diagnostic as its own assessment, not as "Grammar".
    db = admin_db()
    test_row = db.table("tests").insert({
        "user_id": user["id"],
        "title": payload.title,
        "test_type": "adaptive_diagnostic",
        "focus": "mixed",
        "level": payload.level,
        "questions": payload.questions,
    }).execute()
    test_id = test_row.data[0]["id"]

    attempt_row = db.table("test_attempts").insert({
        "user_id": user["id"],
        "test_id": test_id,
        "title": payload.title,
        "level": payload.level,
        "focus": "mixed",
        "score": score,
        "correct_count": correct,
        "total_count": total,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"type": "adaptive_diagnostic", "by_skill": by_skill},
    }).execute()
    attempt_id = attempt_row.data[0]["id"]

    answer_rows = []
    for q, detail in zip(payload.questions, details):
        chosen_idx = detail.get("chosen")
        expected_idx = detail.get("answer_index")
        options = q.get("options") or []
        answer_rows.append({
            "attempt_id": attempt_id,
            "question_number": int(q.get("id") or 0),
            "skill": detail.get("skill"),
            "question": q.get("question") or "",
            "selected_answer": options[chosen_idx] if isinstance(chosen_idx, int) and 0 <= chosen_idx < len(options) else None,
            "correct_answer": options[expected_idx] if isinstance(expected_idx, int) and 0 <= expected_idx < len(options) else None,
            "is_correct": detail.get("correct"),
            "explanation": detail.get("explanation"),
        })
    if answer_rows:
        db.table("test_answers").insert(answer_rows).execute()

    # The overall mixed score is an activity, not a CEFR promotion.
    record_activity(
        user["id"],
        "diagnostic",
        f"Mini-test: {payload.title}",
        score,
        {
            "tested_level": payload.level,
            "attempt_id": attempt_id,
            "by_skill": by_skill,
        },
        tested_level=payload.level,
        evidence_source="adaptive_mixed_diagnostic",
        update_skill=False,
    )

    # Update a specific skill only when the test actually contained at least
    # two questions for that skill. One question is too weak as CEFR evidence.
    skill_results = {}
    for skill, stats in by_skill.items():
        if stats["total"] < 2:
            continue
        skill_score = round(stats["correct"] / stats["total"] * 100)
        skill_results[skill] = {
            "score": skill_score,
            "correct": stats["correct"],
            "total": stats["total"],
        }
        record_activity(
            user["id"],
            skill,
            f"Mini-test {payload.level}: {payload.title}",
            skill_score,
            {
                "tested_level": payload.level,
                "source": "mini_test",
                "question_count": stats["total"],
            },
            tested_level=payload.level,
            evidence_source="explicit_cefr_mini_test",
        )

    return {
        "score": score,
        "correct": correct,
        "total": total,
        "details": details,
        "skill_results": skill_results,
        "tested_level": payload.level,
        "mastery_status": (
            "developing" if score < 70 else
            "proficient" if score < 85 else
            "strong"
        ),
    }

@router.post("/speaking")
def speaking(payload: SpeakingPayload, user=Depends(require_user)):
    # Transcript-only speaking feedback. Pronunciation remains intentionally unscored.
    instructions = """
You are an English speaking-practice coach evaluating a TRANSCRIPT only.
Return JSON with exactly:
practice_score (integer 0-100),
estimated_level (one of A1/A2/B1/B2/C1/C2; approximate transcript evidence only),
fluency_coherence (integer 0-100),
lexical_resource (integer 0-100),
grammar_accuracy (integer 0-100),
positive_point (string),
correction (string),
suggestion (string),
follow_up_question (string),
disclaimer (string).

practice_score must be the rounded average of fluency_coherence, lexical_resource,
and grammar_accuracy. Do not score pronunciation or claim to have heard acoustic features.
estimated_level must be based on complexity/control visible in the transcript, not directly
mapped from practice_score.
"""
    result = ask_json(
        instructions,
        f"Scenario: {payload.scenario}\nTranscript:\n{payload.text}",
    )

    component_scores = [
        int(result.get("fluency_coherence", 0)),
        int(result.get("lexical_resource", 0)),
        int(result.get("grammar_accuracy", 0)),
    ]
    practice_score = round(sum(component_scores) / 3)
    result["practice_score"] = practice_score
    estimated_level = result.get("estimated_level")

    admin_db().table("speaking_attempts").insert({
        "user_id": user["id"],
        "scenario": payload.scenario,
        "transcript": payload.text,
        "score": practice_score,
        "estimated_level": estimated_level,
        "fluency_score": component_scores[0],
        "vocabulary_score": component_scores[1],
        "grammar_score": component_scores[2],
        "pronunciation_score": None,
        "feedback": result,
    }).execute()

    record_activity(
        user["id"],
        "speaking",
        payload.scenario,
        practice_score,
        result,
        tested_level=estimated_level,
        evidence_source="transcript_only_speaking_sample",
    )
    return result

@router.post("/audio/transcribe")
async def audio_transcribe(
    audio: UploadFile = File(...),
    scenario: str = Form(default="general conversation"),
    user=Depends(require_user),
):
    allowed = {
        "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4",
        "audio/wav", "audio/x-wav", "audio/aac", "application/octet-stream"
    }
    raw_content_type = audio.content_type or "application/octet-stream"
    # Browsers often send values such as "audio/webm;codecs=opus".
    # The actual container type is the part before the semicolon.
    content_type = raw_content_type.split(";", 1)[0].strip().lower()

    if content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format: {raw_content_type}",
        )

    content = await audio.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio is too large. Keep recordings short.")

    transcript = transcribe_audio(audio.filename or "recording.webm", content, content_type)
    if not transcript:
        raise HTTPException(status_code=422, detail="No speech could be transcribed.")

    record_activity(
        user["id"], "speaking", f"Transcription: {scenario}", None,
        {"transcript": transcript, "source": "gpt-4o-mini-transcribe"},
        update_skill=False,
    )
    return {"transcript": transcript}


@router.post("/audio/tts")
def audio_tts(payload: TTSPayload, user=Depends(require_user)):
    audio_bytes = synthesize_speech(payload.text, payload.voice)
    return StreamingResponse(
        BytesIO(audio_bytes),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


class IELTSProfilePayload(BaseModel):
    exam_type: str = "Academic"
    planned_exam_date: str | None = None


class IELTSWritingPromptPayload(BaseModel):
    test_type: str = "Academic"
    task_number: int = Field(default=2, ge=1, le=2)


class IELTSWritingAssessPayload(BaseModel):
    test_type: str = "Academic"
    task_number: int = Field(default=2, ge=1, le=2)
    prompt: str = Field(min_length=5, max_length=8000)
    response: str = Field(min_length=20, max_length=20000)
    chart: dict | None = None  # Academic Task 1 visual data


class IELTSSpeakingGeneratePayload(BaseModel):
    part: int = Field(default=1, ge=1, le=3)


class IELTSSpeakingAssessPayload(BaseModel):
    part: int = Field(default=1, ge=1, le=3)
    prompt: str = Field(min_length=1, max_length=8000)
    transcript: str = Field(min_length=1, max_length=12000)


class IELTSPracticeGeneratePayload(BaseModel):
    test_type: str = "Academic"


class IELTSObjectiveSubmitPayload(BaseModel):
    test_id: str
    answers: dict[str, str]


def _normalize_answer(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


@router.get("/ielts/dashboard")
def ielts_dashboard(user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    return get_ielts_dashboard(user["id"])


@router.post("/ielts/profile")
def ielts_profile(payload: IELTSProfilePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return update_ielts_profile(user["id"], payload.model_dump())


@router.post("/ielts/writing/generate")
def ielts_writing_generate(payload: IELTSWritingPromptPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_writing_prompt(payload.test_type, payload.task_number)


@router.post("/ielts/writing/assess")
def ielts_writing_assess(payload: IELTSWritingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    result = ielts_assess_writing(payload.test_type, payload.task_number, payload.prompt, payload.response,
                                  payload.chart)
    save_ielts_writing(user["id"], payload.test_type, payload.task_number, payload.prompt, payload.response, result)
    return result  # IELTS stays separate from the general (CEFR) progress


@router.post("/ielts/speaking/generate")
def ielts_speaking_generate(payload: IELTSSpeakingGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_speaking_set(payload.part)


@router.post("/ielts/speaking/assess")
def ielts_speaking_assess(payload: IELTSSpeakingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    result = ielts_assess_speaking(payload.part, payload.prompt, payload.transcript)
    save_ielts_speaking(user["id"], payload.part, payload.prompt, payload.transcript, result)
    return result


IELTS_LISTENING_VOICE = (
    "Read this as an IELTS Listening recording at a natural exam pace. "
    "Use natural intonation and clearly different delivery for different speakers."
)


def _ielts_practice_store(user: dict, section: str, test_type: str, content: dict) -> str:
    content = dict(content)
    content["test_type"] = test_type
    row = admin_db().table("tests").insert({
        "user_id": user["id"],
        "title": content.get("title") or f"IELTS {section.title()} practice",
        "test_type": "ielts_practice",
        "focus": f"ielts_{section}",
        "questions": content,  # answer key, explanations and script stay on the server
    }).execute()
    return row.data[0]["id"]


def _ielts_practice_load(test_id: str, user_id: str) -> dict | None:
    rows = (
        admin_db().table("tests").select("*")
        .eq("id", test_id).eq("user_id", user_id).eq("test_type", "ielts_practice")
        .limit(1).execute()
    ).data
    if not rows:
        return None
    content = rows[0].get("questions") or {}
    content["_section"] = str(rows[0].get("focus", "")).replace("ielts_", "")
    return content


def _ielts_public_questions(content: dict) -> list[dict]:
    return [
        {k: q.get(k) for k in ("id", "type", "question", "options", "instruction") if k in q}
        for q in content.get("questions") or []
    ]


@router.post("/ielts/reading/generate")
def ielts_reading_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    test_type = "General Training" if payload.test_type.lower().startswith("general") else "Academic"
    content = ielts_generate_reading(test_type)
    test_id = _ielts_practice_store(user, "reading", test_type, content)
    return {"test_id": test_id, "title": content.get("title"), "passage": content.get("passage"),
            "questions": _ielts_public_questions(content)}


@router.post("/ielts/listening/generate")
def ielts_listening_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    content = ielts_generate_listening()
    test_id = _ielts_practice_store(user, "listening", "Academic", content)
    script = content.get("audio_script") or ""
    listening_audio.start(
        test_id, user["id"], builder=lambda: synthesize_speech(script, instructions=IELTS_LISTENING_VOICE),
        max_plays=1,
    )
    return {"test_id": test_id, "title": content.get("title"), "max_plays": 1,
            "questions": _ielts_public_questions(content)}


@router.get("/ielts/practice/{test_id}/audio")
def ielts_practice_audio(test_id: str, user=Depends(require_user)):
    def loader():
        c = _ielts_practice_load(test_id, user["id"])
        return c if c and c.get("_section") == "listening" else None

    def audio_loader(c):
        script = c.get("audio_script") or ""
        return lambda: synthesize_speech(script, instructions=IELTS_LISTENING_VOICE)

    try:
        audio, plays_left = listening_audio.get_for_play(test_id, user["id"], loader, audio_loader, max_plays=1)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    headers = {"Cache-Control": "no-store"}
    if plays_left is not None:
        headers["X-Plays-Left"] = str(plays_left)
    return StreamingResponse(BytesIO(audio), media_type="audio/mpeg", headers=headers)


@router.post("/ielts/objective/submit")
def ielts_objective_submit(payload: IELTSObjectiveSubmitPayload, user=Depends(require_user)):
    """Short practice sets are checked against the key stored on the server.
    No band is given: 10 questions are too few for a reliable IELTS band."""
    content = _ielts_practice_load(payload.test_id, user["id"])
    if not content:
        raise HTTPException(status_code=404, detail="Practice set not found.")
    section = content["_section"]
    key = content.get("answer_key") or {}
    explanations = content.get("explanations") or {}
    correct, details = 0, []
    for q in content.get("questions") or []:
        qid = str(q.get("id"))
        given = payload.answers.get(qid, "")
        accepted = key.get(qid, "")
        ok = _mock_match_answer(given, accepted)
        correct += int(ok)
        details.append({
            "id": qid, "correct": ok, "answer": given,
            "expected": accepted if isinstance(accepted, str) else " / ".join(map(str, accepted)),
            "explanation": explanations.get(qid, ""),
        })
    total = max(len(content.get("questions") or []), 1)
    pct = round(correct / total * 100)
    save_ielts_objective(user["id"], section, content.get("test_type") or "Academic",
                         content.get("title") or f"IELTS {section} practice", pct, None, payload.answers, details)
    if section == "listening":
        listening_audio.mark_checked(payload.test_id)
    return {
        "section": section,
        "score_percent": pct,
        "correct": correct,
        "total": total,
        "details": details,
        "transcript": content.get("audio_script") if section == "listening" else None,
        "note": "Short practice set: percentage and review only. A band is given only for a full 40-question test.",
    }


# ========================= FULL IELTS MOCK EXAM (computer-delivered format) =========================

class IELTSMockStartPayload(BaseModel):
    test_type: str = "Academic"


class IELTSMockSectionPayload(BaseModel):
    section: Literal["listening", "reading", "writing", "speaking"]


class IELTSMockAnswersPayload(BaseModel):
    section: Literal["listening", "reading", "writing"]
    # listening/reading: {"1": {"1": "answer", ...}, "2": {...}}; writing: {"1": "text", "2": "text"}
    answers: dict


class IELTSMockSpeakingSubmitPayload(BaseModel):
    transcripts: dict[str, str]


_MOCK_HIDDEN = {"answer_key", "explanations", "audio_script", "script", "speakers", "intro"}


def _mock_safe_content(section: str, content: dict) -> dict:
    return {k: v for k, v in (content or {}).items() if k not in _MOCK_HIDDEN}


def _mock_match_answer(given: str, accepted) -> bool:
    return ielts_exam.is_correct(given, accepted)


def _mock_band_from_raw(section: str, raw: int, test_type: str) -> float:
    # Practice conversion based on IELTS-published anchor points plus common half-band interpolation.
    # Exact raw-score cut-offs vary slightly between official test versions.
    if section == "listening":
        table = [
            (39, 9.0), (37, 8.5), (35, 8.0), (32, 7.5), (30, 7.0),
            (26, 6.5), (23, 6.0), (18, 5.5), (16, 5.0), (13, 4.5),
            (10, 4.0), (8, 3.5), (6, 3.0), (4, 2.5), (0, 2.0),
        ]
    elif test_type.lower().startswith("general"):
        table = [
            (40, 9.0), (39, 8.5), (37, 8.0), (36, 7.5), (34, 7.0),
            (32, 6.5), (30, 6.0), (27, 5.5), (23, 5.0), (19, 4.5),
            (15, 4.0), (12, 3.5), (9, 3.0), (6, 2.5), (0, 2.0),
        ]
    else:
        table = [
            (39, 9.0), (37, 8.5), (35, 8.0), (33, 7.5), (30, 7.0),
            (27, 6.5), (23, 6.0), (19, 5.5), (15, 5.0), (13, 4.5),
            (10, 4.0), (8, 3.5), (6, 3.0), (4, 2.5), (0, 2.0),
        ]
    for minimum, band in table:
        if raw >= minimum:
            return band
    return 1.0


_MOCK_GENERATORS = {
    "listening": mock_generate_listening_part,
    "reading": mock_generate_reading_section,
    "writing": mock_generate_full_writing,
    "speaking": mock_generate_full_speaking,
}


def _mock_or_404(user: dict, mock_id: str) -> dict:
    try:
        return mock_get(user["id"], mock_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mock exam not found.")


def _mock_section_done(mock: dict, section: str) -> bool:
    done = [s for s in mock.get("sections", []) if s["section"] == section and s["status"] == "completed"]
    return len(done) >= ielts_exam.PARTS[section]


def _mock_start_prep(user: dict, mock: dict) -> None:
    ielts_exam.prepare(
        mock["id"], user["id"], mock.get("test_type") or "Academic",
        _MOCK_GENERATORS, mock_save_generated_section, mock_get_section,
    )


def _mock_finalize_objective(user_id: str, mock: dict, section: str, submitted: dict | None = None) -> None:
    """Mark Listening/Reading from the saved answers (merged with a timely submission)."""
    mock_id = mock["id"]
    test_type = mock.get("test_type") or "Academic"
    raw_total, total, review = 0, 0, {}
    for part in range(1, ielts_exam.PARTS[section] + 1):
        row = mock_get_section(user_id, mock_id, section, part)
        if not row:
            continue
        answers = dict(row.get("answers") or {})
        if submitted and isinstance(submitted.get(str(part)), dict):
            answers.update({str(k): str(v) for k, v in submitted[str(part)].items()})
        correct, details = ielts_exam.mark_part(row.get("content") or {}, answers)
        raw_total += correct
        total += len(details)
        review[str(part)] = details
        mock_complete_section(user_id, mock_id, section, part, answers=answers, raw_score=correct,
                              total_questions=len(details), result={"details": details})
    band = _mock_band_from_raw(section, raw_total, test_type)
    mock_update_band(user_id, mock_id, section, band)
    save_ielts_objective(user_id, section, test_type, f"Full IELTS Mock {section.title()}",
                         round(raw_total / max(total, 1) * 100), band, {"raw_score": raw_total},
                         [{"raw_score": raw_total, "total": total}])


def _mock_assess_writing(user_id: str, mock_id: str, test_type: str) -> None:
    def blank():
        return {"task_achievement": 0, "task_response": 0, "coherence_cohesion": 0, "lexical_resource": 0,
                "grammatical_range_accuracy": 0, "overall_band": 0.0, "word_count": 0,
                "strengths": [], "improvements": [{"criterion": "Task", "issue": "No answer was written.",
                                                   "action": "Always write a response to both tasks."}]}
    try:
        results = {}
        for task in (1, 2):
            row = mock_get_section(user_id, mock_id, "writing", task)
            text = str((row.get("answers") or {}).get("response") or "").strip()
            results[task] = (ielts_assess_writing(test_type, task, row["content"]["prompt"], text,
                                                  row["content"].get("chart"))
                             if len(text.split()) >= 20 else blank())
            mock_complete_section(user_id, mock_id, "writing", task, estimated_band=results[task]["overall_band"],
                                  result=results[task])
            save_ielts_writing(user_id, test_type, task, row["content"]["prompt"], text or "(no answer)", results[task])
        band = ielts_writing_band(results[1]["overall_band"], results[2]["overall_band"])
        mock_update_band(user_id, mock_id, "writing", band)
        ielts_exam.update_meta(mock_id, lambda m: m.update(writing_assessment="done"))
    except Exception as exc:
        logger.exception("Mock writing assessment failed")
        ielts_exam.update_meta(mock_id, lambda m: m.update(writing_assessment="error", writing_error=str(exc)))


def _mock_finalize_expired(user: dict, mock: dict) -> dict:
    """If the learner left during a timed section, mark it from the saved answers after the deadline."""
    meta = mock.get("metadata") or {}
    changed = False
    for section in ("listening", "reading"):
        if (section in (meta.get("timing") or {}) and not _mock_section_done(mock, section)
                and ielts_exam.deadline_passed(meta, section, ielts_exam.SUBMIT_GRACE)):
            _mock_finalize_objective(user["id"], mock, section)
            changed = True
    if ("writing" in (meta.get("timing") or {}) and not meta.get("writing_assessment")
            and ielts_exam.deadline_passed(meta, "writing", ielts_exam.SUBMIT_GRACE)):
        _mock_submit_writing(user, mock, None)
        changed = True
    return mock_get(user["id"], mock["id"]) if changed else mock


def _mock_submit_writing(user: dict, mock: dict, texts: dict | None) -> None:
    for task in (1, 2):
        row = mock_get_section(user["id"], mock["id"], "writing", task)
        saved = str((row.get("answers") or {}).get("response") or "")
        text = str((texts or {}).get(str(task), saved))
        mock_complete_section(user["id"], mock["id"], "writing", task, answers={"response": text})
    ielts_exam.update_meta(mock["id"], lambda m: m.update(writing_assessment="running"))
    threading.Thread(target=_mock_assess_writing,
                     args=(user["id"], mock["id"], mock.get("test_type") or "Academic"), daemon=True).start()


def _mock_status_payload(mock: dict) -> dict:
    meta = mock.get("metadata") or {}
    next_section = next((s for s in ielts_exam.ORDER if not _mock_section_done(mock, s)), "results")
    if next_section == "writing" and meta.get("writing_assessment"):
        next_section = "speaking"
    return {
        "mock_id": mock["id"],
        "test_type": mock.get("test_type"),
        "status": mock.get("status"),
        "version": meta.get("version"),
        "prep": meta.get("prep") or {},
        "preparing": ielts_exam.is_preparing(mock["id"]),
        "timing": meta.get("timing") or {},
        "listening_plan": ielts_exam.listening_plan(meta.get("listening_durations") or {})
            if len(meta.get("listening_durations") or {}) == 4 else None,
        "listening_served": meta.get("listening_served") or {},
        "writing_assessment": meta.get("writing_assessment"),
        "next_section": next_section,
        "server_time": ielts_exam.iso(ielts_exam.now()),
    }


@router.post("/ielts/mock/start")
def ielts_mock_start(payload: IELTSMockStartPayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    ensure_ielts_profile(user["id"])
    test_type = "General Training" if payload.test_type.lower().startswith("general") else "Academic"
    row = mock_start(user["id"], test_type)
    ielts_exam.update_meta(row["id"], lambda m: m.update(version=ielts_exam.VERSION, prep={"status": "running"}))
    mock = mock_get(user["id"], row["id"])
    _mock_start_prep(user, mock)
    return _mock_status_payload(mock_get(user["id"], row["id"]))


@router.get("/ielts/mock/{mock_id}")
def ielts_mock_status(mock_id: str, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    if mock.get("status") == "in_progress" and (mock.get("metadata") or {}).get("version") == ielts_exam.VERSION:
        mock = _mock_finalize_expired(user, mock)
    return _mock_status_payload(mock)


@router.post("/ielts/mock/{mock_id}/prepare")
def ielts_mock_prepare(mock_id: str, user=Depends(require_user)):
    """Resume preparation after an error or a server restart."""
    mock = _mock_or_404(user, mock_id)
    _mock_start_prep(user, mock)
    return _mock_status_payload(mock_get(user["id"], mock_id))


@router.post("/ielts/mock/{mock_id}/abandon")
def ielts_mock_abandon(mock_id: str, user=Depends(require_user)):
    _mock_or_404(user, mock_id)
    admin_db().table("ielts_mock_tests").update({"status": "abandoned"}).eq("id", mock_id).eq("user_id", user["id"]).execute()
    return {"ok": True}


@router.post("/ielts/mock/{mock_id}/section/start")
def ielts_mock_section_start(mock_id: str, payload: IELTSMockSectionPayload, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    if mock.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="This mock exam is not active.")
    mock = _mock_finalize_expired(user, mock)
    section = payload.section
    status = _mock_status_payload(mock)
    if status["next_section"] != section:
        raise HTTPException(status_code=409, detail=f"The next section is {status['next_section']}.")

    rows = [mock_get_section(user["id"], mock_id, section, p) for p in range(1, ielts_exam.PARTS[section] + 1)]
    meta = mock.get("metadata") or {}
    if section == "listening" and len(meta.get("listening_durations") or {}) < 4:
        rows = [None]
    if not all(rows):
        if not ielts_exam.is_preparing(mock_id):
            _mock_start_prep(user, mock)
        raise HTTPException(status_code=409, detail="The exam paper is still being prepared. Please wait a moment.")

    timing = ielts_exam.start_timer(mock_id, section)
    for p in range(1, len(rows) + 1):
        if rows[p - 1].get("status") == "ready":
            mock_start_section(user["id"], mock_id, section, p)
    parts = [{
        "part": p,
        "content": _mock_safe_content(section, rows[p - 1].get("content") or {}),
        "answers": rows[p - 1].get("answers") or {},
    } for p in range(1, len(rows) + 1)]
    result = _mock_status_payload(mock_get(user["id"], mock_id))
    result.update(section=section, timing=timing, parts=parts)
    return result


@router.get("/ielts/mock/{mock_id}/listening/{part}/audio")
def ielts_mock_listening_audio(mock_id: str, part: int, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    if "listening" not in (meta.get("timing") or {}) or _mock_section_done(mock, "listening"):
        raise HTTPException(status_code=403, detail="The Listening test is not in progress.")
    if part not in (1, 2, 3, 4):
        raise HTTPException(status_code=404, detail="Listening has Parts 1-4.")
    if not ielts_exam.mark_audio_served(mock_id, part):
        raise HTTPException(status_code=403, detail="In the exam each recording is played only once.")
    audio = ielts_exam.load_audio(mock_id, part)
    if not audio:
        raise HTTPException(status_code=503, detail="The recording is unavailable. Please contact the administrator.")
    return StreamingResponse(BytesIO(audio), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/ielts/mock/{mock_id}/answers")
def ielts_mock_save_answers(mock_id: str, payload: IELTSMockAnswersPayload, user=Depends(require_user)):
    """Autosave while the section is running; the server marks these if time runs out."""
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    section = payload.section
    if (section not in (meta.get("timing") or {}) or _mock_section_done(mock, section)
            or (section == "writing" and meta.get("writing_assessment"))
            or ielts_exam.deadline_passed(meta, section, ielts_exam.SUBMIT_GRACE)):
        return {"saved": False}
    for part, value in (payload.answers or {}).items():
        if not str(part).isdigit() or not 1 <= int(part) <= ielts_exam.PARTS[section]:
            continue
        answers = {"response": str(value)[:30000]} if section == "writing" else \
            {str(k): str(v)[:200] for k, v in (value or {}).items()}
        (admin_db().table("ielts_mock_sections").update({"answers": answers})
         .eq("mock_id", mock_id).eq("user_id", user["id"]).eq("section", section).eq("part", int(part)).execute())
    return {"saved": True, "server_time": ielts_exam.iso(ielts_exam.now())}


@router.post("/ielts/mock/{mock_id}/section/submit")
def ielts_mock_section_submit(mock_id: str, payload: IELTSMockAnswersPayload, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    section = payload.section
    if section not in (meta.get("timing") or {}):
        raise HTTPException(status_code=400, detail="This section has not started.")
    late = ielts_exam.deadline_passed(meta, section, ielts_exam.SUBMIT_GRACE)
    if section in ("listening", "reading"):
        if not _mock_section_done(mock, section):
            _mock_finalize_objective(user["id"], mock, section, None if late else payload.answers)
    elif not meta.get("writing_assessment"):
        _mock_submit_writing(user, mock, None if late else payload.answers)
    return _mock_status_payload(mock_get(user["id"], mock_id))


@router.post("/ielts/mock/{mock_id}/speaking/submit")
def ielts_mock_speaking_submit(mock_id: str, payload: IELTSMockSpeakingSubmitPayload, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    parts = {}
    for p in (1, 2, 3):
        row = mock_get_section(user["id"], mock_id, "speaking", p)
        transcript = (payload.transcripts.get(str(p)) or "").strip()
        if not row or not transcript:
            raise HTTPException(status_code=400, detail=f"Speaking Part {p} is incomplete.")
        parts[f"part{p}"] = {"prompt": row["content"], "transcript": transcript}

    result = mock_assess_full_speaking(parts)
    band = float(result.get("provisional_language_band") or 0)
    for p in (1, 2, 3):
        row = mock_get_section(user["id"], mock_id, "speaking", p)
        transcript = payload.transcripts[str(p)]
        mock_complete_section(user["id"], mock_id, "speaking", p, answers={"transcript": transcript}, estimated_band=band, result=result)
        save_ielts_speaking(user["id"], p, str(row["content"]), transcript, result)
    mock_update_band(user["id"], mock_id, "speaking", band)
    return _mock_status_payload(mock_get(user["id"], mock_id))


@router.post("/ielts/mock/{mock_id}/finish")
def ielts_mock_finish(mock_id: str, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    if meta.get("writing_assessment") == "running":
        raise HTTPException(status_code=202, detail="Writing is still being assessed. Please wait a moment.")
    if meta.get("writing_assessment") == "error":
        threading.Thread(target=_mock_assess_writing,
                         args=(user["id"], mock_id, mock.get("test_type") or "Academic"), daemon=True).start()
        ielts_exam.update_meta(mock_id, lambda m: m.update(writing_assessment="running"))
        raise HTTPException(status_code=202, detail="Retrying the Writing assessment. Please wait a moment.")
    bands = [mock.get("listening_band"), mock.get("reading_band"), mock.get("writing_band"), mock.get("speaking_band")]
    if any(x is None for x in bands):
        raise HTTPException(status_code=400, detail="Complete all four IELTS sections before finishing the mock.")
    overall = ielts_overall_band([float(x) for x in bands])
    if mock.get("status") != "completed":
        mock_finish(user["id"], mock_id, overall)
        db = admin_db()
        db.table("ielts_profiles").update({
            "current_overall_band": overall,
            "listening_band": float(bands[0]), "reading_band": float(bands[1]),
            "writing_band": float(bands[2]), "speaking_band": float(bands[3]),
        }).eq("user_id", user["id"]).execute()
        db.table("ielts_band_history").insert({
            "user_id": user["id"], "section": "overall", "band": overall,
            "source": "full_mock_ai_practice",
            "note": "Practice overall uses transcript-only provisional Speaking; pronunciation was not assessed.",
        }).execute()

    full = mock_get(user["id"], mock_id)
    review = {}
    for section in ("listening", "reading", "writing", "speaking"):
        for p in range(1, ielts_exam.PARTS[section] + 1):
            row = mock_get_section(user["id"], mock_id, section, p) or {}
            review.setdefault(section, {})[str(p)] = {
                "title": (row.get("content") or {}).get("title"),
                "raw_score": row.get("raw_score"),
                "total": row.get("total_questions"),
                "band": row.get("estimated_band"),
                "result": row.get("result") or {},
                "transcript": (row.get("content") or {}).get("audio_script") if section == "listening" else None,
            }
    return {
        "mock": {k: full.get(k) for k in ("id", "test_type", "status", "started_at", "completed_at")},
        "overall_band": overall,
        "bands": {"listening": float(bands[0]), "reading": float(bands[1]),
                  "writing": float(bands[2]), "speaking": float(bands[3])},
        "review": review,
        "disclaimer": "Practice estimate only. This is not an official IELTS result; Speaking pronunciation is not assessed in transcript-only mode.",
    }
