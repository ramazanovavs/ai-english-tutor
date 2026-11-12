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
    target_band: float = Field(default=6.5, ge=1.0, le=9.0)
    planned_exam_date: str | None = None


class IELTSWritingPromptPayload(BaseModel):
    test_type: str = "Academic"
    task_number: int = Field(default=2, ge=1, le=2)
    target_band: float = Field(default=6.5, ge=1.0, le=9.0)


class IELTSWritingAssessPayload(BaseModel):
    test_type: str = "Academic"
    task_number: int = Field(default=2, ge=1, le=2)
    prompt: str = Field(min_length=5, max_length=8000)
    response: str = Field(min_length=20, max_length=20000)


class IELTSSpeakingGeneratePayload(BaseModel):
    part: int = Field(default=1, ge=1, le=3)
    target_band: float = Field(default=6.5, ge=1.0, le=9.0)


class IELTSSpeakingAssessPayload(BaseModel):
    part: int = Field(default=1, ge=1, le=3)
    prompt: str = Field(min_length=1, max_length=8000)
    transcript: str = Field(min_length=1, max_length=12000)


class IELTSPracticeGeneratePayload(BaseModel):
    test_type: str = "Academic"
    target_band: float = Field(default=6.5, ge=1.0, le=9.0)


class IELTSObjectiveSubmitPayload(BaseModel):
    section: str
    test_type: str = "Academic"
    title: str
    questions: list[dict]
    answer_key: dict[str, str]
    explanations: dict[str, str] = {}
    answers: dict[str, str]


def _normalize_answer(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _estimate_ielts_band(section: str, correct: int, total: int, test_type: str) -> float:
    if total <= 0:
        return 0.0
    pct = correct / total
    # Formative approximation for short practice sets, not official score conversion.
    if section == "reading" and test_type.lower().startswith("general"):
        cuts = [(0.90, 7.0), (0.80, 6.5), (0.70, 6.0), (0.58, 5.5), (0.45, 5.0), (0.30, 4.0)]
    else:
        cuts = [(0.88, 8.0), (0.75, 7.0), (0.60, 6.0), (0.48, 5.5), (0.38, 5.0), (0.25, 4.0)]
    for threshold, band in cuts:
        if pct >= threshold:
            return band
    return 3.0


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
    return ielts_generate_writing_prompt(payload.test_type, payload.task_number, payload.target_band)


@router.post("/ielts/writing/assess")
def ielts_writing_assess(payload: IELTSWritingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    result = ielts_assess_writing(payload.test_type, payload.task_number, payload.prompt, payload.response)
    save_ielts_writing(user["id"], payload.test_type, payload.task_number, payload.prompt, payload.response, result)
    # Feed the general student model too, using a simple 0-100 transformation.
    record_activity(user["id"], "writing", f"IELTS Writing Task {payload.task_number}", round(float(result.get("overall_band", 0)) / 9 * 100), result)
    return result


@router.post("/ielts/speaking/generate")
def ielts_speaking_generate(payload: IELTSSpeakingGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_speaking_set(payload.part, payload.target_band)


@router.post("/ielts/speaking/assess")
def ielts_speaking_assess(payload: IELTSSpeakingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    result = ielts_assess_speaking(payload.part, payload.prompt, payload.transcript)
    save_ielts_speaking(user["id"], payload.part, payload.prompt, payload.transcript, result)
    record_activity(user["id"], "speaking", f"IELTS Speaking Part {payload.part}", round(float(result.get("provisional_language_band", 0)) / 9 * 100), result)
    return result


@router.post("/ielts/reading/generate")
def ielts_reading_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_reading(payload.test_type, payload.target_band)


@router.post("/ielts/listening/generate")
def ielts_listening_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_listening(payload.target_band)


@router.post("/ielts/objective/submit")
def ielts_objective_submit(payload: IELTSObjectiveSubmitPayload, user=Depends(require_user)):
    section = payload.section.lower().strip()
    if section not in {"reading", "listening"}:
        raise HTTPException(status_code=400, detail="Section must be reading or listening.")
    correct = 0
    details = []
    for q in payload.questions:
        qid = str(q.get("id"))
        given = payload.answers.get(qid, "")
        expected = payload.answer_key.get(qid, "")
        ok = _normalize_answer(given) == _normalize_answer(expected)
        correct += int(ok)
        details.append({
            "id": qid,
            "correct": ok,
            "answer": given,
            "expected": expected,
            "explanation": payload.explanations.get(qid, ""),
        })
    total = max(len(payload.questions), 1)
    pct = round(correct / total * 100)
    band = _estimate_ielts_band(section, correct, total, payload.test_type)
    save_ielts_objective(user["id"], section, payload.test_type, payload.title, pct, band, payload.answers, details)
    record_activity(user["id"], section, f"IELTS {section.title()} practice", pct, {"estimated_band": band, "details": details})
    return {
        "score_percent": pct,
        "correct": correct,
        "total": total,
        "estimated_band": band,
        "details": details,
        "disclaimer": "Band is an approximate practice estimate from a short AI-generated set, not an official IELTS conversion or score.",
    }


# ========================= FULL IELTS MOCK EXAM =========================

class IELTSMockStartPayload(BaseModel):
    test_type: str = "Academic"
    target_band: float = Field(default=6.5, ge=1.0, le=9.0)


class IELTSMockGeneratePayload(BaseModel):
    section: str
    part: int = Field(ge=1, le=4)


class IELTSMockObjectiveSubmitPayload(BaseModel):
    section: str
    part: int = Field(ge=1, le=4)
    answers: dict[str, str]


class IELTSMockWritingSubmitPayload(BaseModel):
    task1_response: str = Field(min_length=20, max_length=20000)
    task2_response: str = Field(min_length=20, max_length=30000)


class IELTSMockSpeakingSubmitPayload(BaseModel):
    transcripts: dict[str, str]


def _mock_safe_content(section: str, content: dict) -> dict:
    hidden = {"answer_key", "explanations", "audio_script"}
    return {k: v for k, v in content.items() if k not in hidden}


def _mock_match_answer(given: str, accepted) -> bool:
    g = _normalize_answer(given)
    if isinstance(accepted, list):
        return any(g == _normalize_answer(x) for x in accepted)
    return g == _normalize_answer(accepted)


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


def _mock_round_half(value: float) -> float:
    return round(float(value) * 2) / 2


@router.post("/ielts/mock/start")
def ielts_mock_start(payload: IELTSMockStartPayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    ensure_ielts_profile(user["id"])
    test_type = "General Training" if payload.test_type.lower().startswith("general") else "Academic"
    row = mock_start(user["id"], test_type, payload.target_band)
    return {
        "mock_id": row["id"],
        "test_type": test_type,
        "target_band": payload.target_band,
        "status": row["status"],
    }


@router.get("/ielts/mock/{mock_id}")
def ielts_mock_status(mock_id: str, user=Depends(require_user)):
    try:
        return mock_get(user["id"], mock_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mock exam not found.")


@router.post("/ielts/mock/{mock_id}/generate")
def ielts_mock_generate(mock_id: str, payload: IELTSMockGeneratePayload, user=Depends(require_user)):
    try:
        mock = mock_get(user["id"], mock_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Mock exam not found.")
    if mock.get("status") != "in_progress":
        raise HTTPException(status_code=400, detail="This mock exam is not active.")

    section = payload.section.lower().strip()
    part = payload.part
    existing = mock_get_section(user["id"], mock_id, section, part)
    if existing and section in {"listening", "reading"}:
        mock_start_section(user["id"], mock_id, section, part)
        return {
            "section": section,
            "part": part,
            "content": _mock_safe_content(section, existing.get("content") or {}),
        }

    target_band = float((mock.get("metadata") or {}).get("target_band") or 6.5)
    test_type = mock.get("test_type") or "Academic"

    if section == "listening":
        if part not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="Listening has Parts 1-4.")
        content = mock_generate_listening_part(part, target_band)
    elif section == "reading":
        if part not in (1, 2, 3):
            raise HTTPException(status_code=400, detail="Reading has Sections 1-3.")
        content = mock_generate_reading_section(test_type, part, target_band)
    elif section == "writing":
        if part != 1:
            raise HTTPException(status_code=400, detail="Generate Writing with part=1.")
        t1_existing = mock_get_section(user["id"], mock_id, "writing", 1)
        t2_existing = mock_get_section(user["id"], mock_id, "writing", 2)
        if t1_existing and t2_existing:
            mock_start_section(user["id"], mock_id, "writing", 1)
            mock_start_section(user["id"], mock_id, "writing", 2)
            return {
                "section": "writing",
                "content": {"task1": t1_existing["content"], "task2": t2_existing["content"]},
            }
        paper = mock_generate_full_writing(test_type, target_band)
        for task_no, key in ((1, "task1"), (2, "task2")):
            mock_save_generated_section(user["id"], mock_id, "writing", task_no, paper[key])
            mock_start_section(user["id"], mock_id, "writing", task_no)
        return {"section": "writing", "content": paper}
    elif section == "speaking":
        if part != 1:
            raise HTTPException(status_code=400, detail="Generate Speaking with part=1.")
        existing_parts = [mock_get_section(user["id"], mock_id, "speaking", p) for p in (1, 2, 3)]
        if all(existing_parts):
            for p in (1, 2, 3):
                mock_start_section(user["id"], mock_id, "speaking", p)
            return {
                "section": "speaking",
                "content": {
                    "part1": existing_parts[0]["content"],
                    "part2": existing_parts[1]["content"],
                    "part3": existing_parts[2]["content"],
                },
            }
        paper = mock_generate_full_speaking(target_band)
        for pno, key in ((1, "part1"), (2, "part2"), (3, "part3")):
            mock_save_generated_section(user["id"], mock_id, "speaking", pno, paper[key])
            mock_start_section(user["id"], mock_id, "speaking", pno)
        return {"section": "speaking", "content": paper}
    else:
        raise HTTPException(status_code=400, detail="Unknown mock section.")

    row = mock_save_generated_section(user["id"], mock_id, section, part, content)
    mock_start_section(user["id"], mock_id, section, part)
    return {"section": section, "part": part, "content": _mock_safe_content(section, row["content"])}


@router.get("/ielts/mock/{mock_id}/listening/{part}/audio")
def ielts_mock_listening_audio(mock_id: str, part: int, user=Depends(require_user)):
    row = mock_get_section(user["id"], mock_id, "listening", part)
    if not row:
        raise HTTPException(status_code=404, detail="Generate this Listening part first.")
    script = (row.get("content") or {}).get("audio_script")
    if not script:
        raise HTTPException(status_code=404, detail="Listening script is unavailable.")
    audio_bytes = synthesize_speech(
        script,
        instructions=(
            "Read this as a natural IELTS-style listening recording at a normal exam pace. "
            "Use natural intonation and subtle changes of delivery when the text contains more than one speaker."
        ),
    )
    return StreamingResponse(
        BytesIO(audio_bytes),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/ielts/mock/{mock_id}/objective/submit")
def ielts_mock_objective_submit(mock_id: str, payload: IELTSMockObjectiveSubmitPayload, user=Depends(require_user)):
    section = payload.section.lower().strip()
    if section not in {"listening", "reading"}:
        raise HTTPException(status_code=400, detail="Objective section must be listening or reading.")
    row = mock_get_section(user["id"], mock_id, section, payload.part)
    if not row:
        raise HTTPException(status_code=404, detail="Generate this section first.")

    content = row.get("content") or {}
    key = content.get("answer_key") or {}
    explanations = content.get("explanations") or {}
    details, correct = [], 0
    for q in content.get("questions") or []:
        qid = str(q.get("id"))
        given = payload.answers.get(qid, "")
        accepted = key.get(qid, [])
        ok = _mock_match_answer(given, accepted)
        correct += int(ok)
        details.append({
            "id": qid,
            "correct": ok,
            "answer": given,
            "accepted": accepted,
            "explanation": explanations.get(qid, ""),
        })

    total = len(content.get("questions") or [])
    mock_complete_section(
        user["id"], mock_id, section, payload.part,
        answers=payload.answers, raw_score=correct, total_questions=total,
        result={"details": details},
    )

    mock = mock_get(user["id"], mock_id)
    expected_parts = 4 if section == "listening" else 3
    completed = [x for x in mock.get("sections", []) if x["section"] == section and x["status"] == "completed"]
    section_band = None
    raw_total = sum(int(x.get("raw_score") or 0) for x in completed)
    question_total = sum(int(x.get("total_questions") or 0) for x in completed)
    if len(completed) == expected_parts and question_total == 40:
        section_band = _mock_band_from_raw(section, raw_total, mock.get("test_type") or "Academic")
        mock_update_band(user["id"], mock_id, section, section_band)
        save_ielts_objective(
            user["id"], section, mock.get("test_type") or "Academic",
            f"Full IELTS Mock {section.title()}", round(raw_total / 40 * 100),
            section_band, {"raw_score": raw_total}, [{"parts_completed": expected_parts}],
        )

    return {
        "part_raw_score": correct,
        "part_total": total,
        "details": details,
        "section_raw_score": raw_total,
        "section_total": question_total,
        "section_band": section_band,
        "band_note": "Practice conversion. Official raw-score cut-offs can vary slightly between test versions.",
    }


@router.post("/ielts/mock/{mock_id}/writing/submit")
def ielts_mock_writing_submit(mock_id: str, payload: IELTSMockWritingSubmitPayload, user=Depends(require_user)):
    mock = mock_get(user["id"], mock_id)
    t1 = mock_get_section(user["id"], mock_id, "writing", 1)
    t2 = mock_get_section(user["id"], mock_id, "writing", 2)
    if not t1 or not t2:
        raise HTTPException(status_code=404, detail="Generate the Writing paper first.")

    r1 = ielts_assess_writing(mock["test_type"], 1, t1["content"]["prompt"], payload.task1_response)
    r2 = ielts_assess_writing(mock["test_type"], 2, t2["content"]["prompt"], payload.task2_response)
    b1 = float(r1.get("overall_band") or 0)
    b2 = float(r2.get("overall_band") or 0)
    writing_band = _mock_round_half((b1 + 2 * b2) / 3)

    mock_complete_section(user["id"], mock_id, "writing", 1, answers={"response": payload.task1_response}, estimated_band=b1, result=r1)
    mock_complete_section(user["id"], mock_id, "writing", 2, answers={"response": payload.task2_response}, estimated_band=b2, result=r2)
    mock_update_band(user["id"], mock_id, "writing", writing_band)

    save_ielts_writing(user["id"], mock["test_type"], 1, t1["content"]["prompt"], payload.task1_response, r1)
    save_ielts_writing(user["id"], mock["test_type"], 2, t2["content"]["prompt"], payload.task2_response, r2)

    db = admin_db()
    db.table("ielts_profiles").update({"writing_band": writing_band}).eq("user_id", user["id"]).execute()
    db.table("ielts_band_history").insert({
        "user_id": user["id"], "section": "writing", "band": writing_band,
        "source": "full_mock_ai_practice", "note": "Task 2 weighted twice Task 1."
    }).execute()

    return {
        "task1": r1,
        "task2": r2,
        "writing_band": writing_band,
        "disclaimer": "AI formative estimate only; official IELTS Writing is scored by certified examiners.",
    }


@router.post("/ielts/mock/{mock_id}/speaking/submit")
def ielts_mock_speaking_submit(mock_id: str, payload: IELTSMockSpeakingSubmitPayload, user=Depends(require_user)):
    mock = mock_get(user["id"], mock_id)
    parts = {}
    for p in (1, 2, 3):
        row = mock_get_section(user["id"], mock_id, "speaking", p)
        transcript = (payload.transcripts.get(str(p)) or "").strip()
        if not row or not transcript:
            raise HTTPException(status_code=400, detail=f"Speaking Part {p} is incomplete.")
        parts[f"part{p}"] = {"prompt": row["content"], "transcript": transcript}

    result = mock_assess_full_speaking(parts)
    band = _mock_round_half(float(result.get("provisional_language_band") or 0))
    for p in (1, 2, 3):
        row = mock_get_section(user["id"], mock_id, "speaking", p)
        transcript = payload.transcripts[str(p)]
        mock_complete_section(user["id"], mock_id, "speaking", p, answers={"transcript": transcript}, estimated_band=band, result=result)
        save_ielts_speaking(user["id"], p, str(row["content"]), transcript, result)

    mock_update_band(user["id"], mock_id, "speaking", band)
    db = admin_db()
    db.table("ielts_profiles").update({"speaking_band": band}).eq("user_id", user["id"]).execute()
    db.table("ielts_band_history").insert({
        "user_id": user["id"], "section": "speaking", "band": band,
        "source": "full_mock_transcript_practice",
        "note": "Provisional transcript-only estimate; pronunciation not assessed."
    }).execute()
    return result


@router.post("/ielts/mock/{mock_id}/finish")
def ielts_mock_finish(mock_id: str, user=Depends(require_user)):
    mock = mock_get(user["id"], mock_id)
    bands = [
        mock.get("listening_band"), mock.get("reading_band"),
        mock.get("writing_band"), mock.get("speaking_band"),
    ]
    if any(x is None for x in bands):
        raise HTTPException(status_code=400, detail="Complete all four IELTS sections before finishing the mock.")
    overall = _mock_round_half(sum(float(x) for x in bands) / 4)
    finished = mock_finish(user["id"], mock_id, overall)

    db = admin_db()
    db.table("ielts_profiles").update({
        "current_overall_band": overall,
        "listening_band": float(bands[0]),
        "reading_band": float(bands[1]),
        "writing_band": float(bands[2]),
        "speaking_band": float(bands[3]),
    }).eq("user_id", user["id"]).execute()
    db.table("ielts_band_history").insert({
        "user_id": user["id"], "section": "overall", "band": overall,
        "source": "full_mock_ai_practice",
        "note": "Practice overall uses transcript-only provisional Speaking; pronunciation was not assessed."
    }).execute()
    return {
        "mock": finished,
        "overall_band": overall,
        "bands": {
            "listening": float(bands[0]), "reading": float(bands[1]),
            "writing": float(bands[2]), "speaking": float(bands[3]),
        },
        "disclaimer": "Practice estimate only. This is not an official IELTS result; Speaking pronunciation is not assessed in transcript-only mode.",
    }
