import json
import logging
import re

from typing import Literal
from datetime import datetime, timezone, timedelta
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
from app.services import listening_audio, listening_bank, ielts_analytics, placement, roadmap as roadmap_store, review, progress
from app.config import get_settings
from app.agents.assessment_agent import generate as generate_test
from app.services.openai_service import ask_text, ask_json, transcribe_audio, synthesize_speech
from app.services.ielts_scoring import overall_band as ielts_overall_band, writing_band as ielts_writing_band
from app.services import ielts_exam, ielts_speaking, ielts_pronunciation, ielts_bank, storage_cleanup
from app.services.ielts_scoring import criteria_band as ielts_criteria_band
import threading
from app.agents import ielts_coach
from app.agents.ielts_rubrics import language_rule
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
    storage_cleanup.maybe_run_in_background()  # deletes audio older than 30 days, at most every 12 hours
    context = get_student_context(user["id"])
    context["email"] = user.get("email")
    context["is_admin"] = _is_admin(user)
    try:  # level roadmap summary for the Dashboard (missing table = roadmap not set up yet)
        rmv = roadmap_store.view(user["id"])
        cur = rmv["current"]
        context["roadmap"] = {"current": cur, "levels": rmv["levels"], "next": rmv["next"],
                              "progress": rmv["progress"].get(cur) if cur else None,
                              "placement_status": rmv["placement"]["status"]}
    except Exception:
        context["roadmap"] = None
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
    result = grammar_analyze(payload.text, level, _feedback_lang(user["id"]))
    review.save_errors(user["id"], result.get("errors"), payload.text, "grammar")  # -> "My mistakes"
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
        result = writing_analyze(payload.text, payload.purpose, _feedback_lang(user["id"]))
    except Exception as exc:
        logger.exception("Writing analysis failed")
        raise HTTPException(status_code=502, detail=f"AI review failed: {exc}")

    score = _clean_score(result.get("score"))
    estimated_level = _clean_level(result.get("estimated_level"))
    review.save_errors(user["id"], result.get("errors"), payload.text, "writing")  # -> "My mistakes"
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
    result = create_set(payload.topic, payload.level, _feedback_lang(user["id"]))

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
                "translation": item.get("translation"),
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

    result = create_practice(payload.topic, payload.level, payload.items, _feedback_lang(user["id"]))
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
    return generate_test(payload.level, payload.focus, _feedback_lang(user["id"]))


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
    return _speaking_feedback(user, payload.text, payload.scenario, None)


@router.post("/speaking/full")
async def speaking_full(text: str = Form(...), scenario: str = Form(default="general conversation"),
                        audio: UploadFile | None = File(default=None), user=Depends(require_user)):
    """Transcript feedback + pronunciation assessed from the recording (when there is one)."""
    data = await audio.read() if audio is not None else None
    if data and len(data) > 12 * 1024 * 1024:
        data = None
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_speaking_feedback, user, text[:6000], scenario[:100], data)


def _speaking_feedback(user: dict, text: str, scenario: str, audio: bytes | None) -> dict:
    lang = _feedback_lang(user["id"])
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
""" + language_rule(lang) + " This applies to positive_point, correction explanations, suggestion and disclaimer."
    result = ask_json(
        instructions,
        f"Scenario: {scenario}\nTranscript:\n{text}",
    )

    component_scores = [
        int(result.get("fluency_coherence", 0)),
        int(result.get("lexical_resource", 0)),
        int(result.get("grammar_accuracy", 0)),
    ]
    pron_score = None
    if audio:
        try:
            pron = ielts_pronunciation.assess([audio], text, lang)
        except Exception:
            logger.exception("Pronunciation assessment failed")
            pron = None
        if pron:
            pron_score = round(float(pron["pronunciation"]) / 9 * 100)
            result["pronunciation_score"] = pron_score
            result["pronunciation_feedback"] = pron
            component_scores.append(pron_score)
            result["disclaimer"] = ("Practice estimate. Pronunciation is assessed from your recording by an AI model; "
                                    "the other scores come from the transcript.")
    practice_score = round(sum(component_scores) / len(component_scores))
    result["practice_score"] = practice_score
    estimated_level = result.get("estimated_level")

    admin_db().table("speaking_attempts").insert({
        "user_id": user["id"],
        "scenario": scenario,
        "transcript": text,
        "score": practice_score,
        "estimated_level": estimated_level,
        "fluency_score": component_scores[0],
        "vocabulary_score": component_scores[1],
        "grammar_score": component_scores[2],
        "pronunciation_score": pron_score,
        "feedback": result,
    }).execute()

    record_activity(
        user["id"],
        "speaking",
        scenario,
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
    feedback_language: Literal["en", "ru", "kk"] | None = None
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
    focus_type: str | None = None   # targeted drill of a weak question type (from analytics)


class IELTSObjectiveSubmitPayload(BaseModel):
    test_id: str
    answers: dict[str, str]


def _normalize_answer(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


@router.get("/ielts/analytics")
def ielts_analytics_route(user=Depends(require_user)):
    """Accuracy by question type, band history and a forecast with a range."""
    return ielts_analytics.analytics(user["id"])


@router.get("/ielts/dashboard")
def ielts_dashboard(user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    data = get_ielts_dashboard(user["id"])
    data["feedback_language"] = _feedback_lang(user["id"])
    return data


def _feedback_lang(user_id: str) -> str:
    """Language of AI feedback chosen on the IELTS Dashboard (profiles.preferred_explanation_language)."""
    try:
        rows = admin_db().table("profiles").select("preferred_explanation_language").eq("id", user_id).limit(1).execute().data
        lang = (rows or [{}])[0].get("preferred_explanation_language") or "en"
        return lang if lang in ("en", "ru", "kk") else "en"
    except Exception:
        return "en"


@router.post("/ielts/profile")
def ielts_profile(payload: IELTSProfilePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    if payload.feedback_language:
        admin_db().table("profiles").update({"preferred_explanation_language": payload.feedback_language}) \
            .eq("id", user["id"]).execute()
    return update_ielts_profile(user["id"], payload.model_dump())


AI_DOWN = "The AI service is unavailable right now."


def _practice_writing_key(test_type: str, task: int) -> str:
    kind = "General Training" if test_type.lower().startswith("general") else "Academic"
    return f"writing:{kind}:1" if task == 1 else "writing:2"


def _bank_save_quietly(kind: str, key: str, title: str, content: dict, audio: dict | None = None, source_id=None):
    try:
        return ielts_bank.save(kind, key, title, content, audio, source_id)
    except Exception:
        logger.exception("Could not save to the IELTS bank")


def _bank_backup(key: str, user: dict, reason: str) -> dict:
    """A practice item from the bank that this learner has not seen; 503 if none."""
    try:
        item = ielts_bank.pick(key, user["id"])
    except Exception:
        logger.exception("IELTS bank unavailable")
        item = None
    if not item:
        raise HTTPException(status_code=503, detail=f"{reason} No backup task is available for you yet. Please try again later.")
    ielts_bank.mark_used(user["id"], item)
    return item


@router.post("/ielts/writing/generate")
def ielts_writing_generate(payload: IELTSWritingPromptPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    key = _practice_writing_key(payload.test_type, payload.task_number)
    try:
        task = ielts_generate_writing_prompt(payload.test_type, payload.task_number)
    except Exception:
        logger.exception("Writing task generation failed; using the IELTS bank")
        task = dict(_bank_backup(key, user, AI_DOWN)["content"])
        task["backup"] = True
        task["notice"] = f"{AI_DOWN} This is a ready-made task from the bank."
        return task
    _bank_save_quietly("practice", key, task.get("title") or key, task)
    return task


@router.post("/ielts/writing/assess")
def ielts_writing_assess(payload: IELTSWritingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    try:
        result = ielts_assess_writing(payload.test_type, payload.task_number, payload.prompt, payload.response,
                                      payload.chart, _feedback_lang(user["id"]))
    except Exception:
        logger.exception("Writing assessment failed")
        raise HTTPException(status_code=503, detail=f"{AI_DOWN} Your text is still on the page — "
                                                    "press Assess again in a few minutes.")
    save_ielts_writing(user["id"], payload.test_type, payload.task_number, payload.prompt, payload.response, result)
    review.save_errors(user["id"], result.get("error_examples"), payload.response, "IELTS writing")
    return result  # IELTS stays separate from the general (CEFR) progress


class IELTSImprovePayload(BaseModel):
    kind: Literal["writing", "speaking"] = "writing"
    prompt: str = Field(min_length=1, max_length=6000)
    text: str = Field(min_length=1, max_length=12000)
    band: float | None = None
    test_type: str = "Academic"
    task_number: int = 2
    chart: dict | None = None


class IELTSVocabularyAddPayload(BaseModel):
    items: list[dict] = Field(default_factory=list, max_length=20)
    level: str | None = None
    topic: str = Field(default="IELTS feedback", max_length=100)


@router.post("/ielts/improve")
def ielts_improve(payload: IELTSImprovePayload, user=Depends(require_user)):
    """Band 9 model answer + the learner's text rewritten one band higher + words and grammar to study."""
    try:
        return ielts_coach.improve(payload.kind, payload.prompt, payload.text, payload.band,
                                   _feedback_lang(user["id"]), payload.test_type, payload.task_number, payload.chart)
    except Exception:
        logger.exception("IELTS improve failed")
        raise HTTPException(status_code=503, detail=f"{AI_DOWN} Try again in a few minutes.")


@router.post("/ielts/vocabulary/add")
def ielts_vocabulary_add(payload: IELTSVocabularyAddPayload, user=Depends(require_user)):
    """Words from IELTS feedback go to the learner's Vocabulary list (spaced review)."""
    ensure_profile(user["id"], user.get("email"))
    level = _clean_level(payload.level)
    added = []
    for item in payload.items:
        word = str(item.get("word") or "").strip()[:80]
        if not word:
            continue
        admin_db().table("vocabulary_words").upsert({
            "user_id": user["id"], "word": word,
            "part_of_speech": item.get("part_of_speech"), "definition": item.get("definition"),
            "example_sentence": item.get("example"), "collocation": item.get("collocation"),
            "topic": payload.topic, "cefr_level": level, "status": "learning",
        }, on_conflict="user_id,word").execute()
        added.append(word)
    return {"added": added}


@router.post("/ielts/speaking/generate")
def ielts_speaking_generate(payload: IELTSSpeakingGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    return ielts_generate_speaking_set(payload.part)


@router.post("/ielts/speaking/assess")
def ielts_speaking_assess(payload: IELTSSpeakingAssessPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    result = ielts_assess_speaking(payload.part, payload.prompt, payload.transcript, _feedback_lang(user["id"]))
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


# ---------- Speaking with an examiner (practice: one part) ----------

class IELTSSpeakingSessionPayload(BaseModel):
    part: int = Field(default=1, ge=1, le=3)


def _speaking_store_audio(prefix: str, turn_id: str, data: bytes, content_type: str | None) -> str | None:
    path = f"speaking/{prefix}/{turn_id}.webm"
    try:
        admin_db().storage.from_(ielts_exam.BUCKET).upload(
            path, data, {"content-type": content_type or "audio/webm", "upsert": "true"})
        return path
    except Exception:
        logger.exception("Could not store speaking audio")
        return None


def _speaking_take_answer(session: dict, prefix: str, turn_id: str, audio: UploadFile, seconds: float | None) -> None:
    pending = ielts_speaking.pending_turn(session)
    if not pending or pending["id"] != turn_id:
        raise HTTPException(status_code=409, detail="This question has already been answered.")
    data = audio.file.read()
    transcript, pending = "", False
    path = _speaking_store_audio(prefix, turn_id, data, audio.content_type) if data else None
    if data and len(data) > 1000:
        try:
            transcript = transcribe_audio(audio.filename or "answer.webm", data, audio.content_type)
        except Exception as exc:
            logger.exception("Transcription failed")
            if not path:  # neither transcribed nor stored: the answer would be lost
                raise HTTPException(status_code=502, detail=f"Could not save your answer: {exc}")
            pending = True  # the stored recording is transcribed later
    ielts_speaking.record_answer(session, turn_id, transcript, path, seconds, pending)


def _speaking_fill_transcripts(session: dict) -> bool:
    """Transcribe answers recorded while transcription was unavailable. True when none is left."""
    done = True
    for t in session["turns"]:
        if not t.get("transcript_pending"):
            continue
        try:
            data = admin_db().storage.from_(ielts_exam.BUCKET).download(t["audio_path"])
            t["transcript"] = transcribe_audio("answer.webm", data, "audio/webm")
            t["transcript_pending"] = False
        except Exception:
            logger.exception("Transcription still unavailable")
            done = False
    return done


def _speaking_add_pronunciation(session: dict, result: dict, parts: list[int], language: str = "en") -> float:
    """Listen to the recorded answers and add Pronunciation; returns the Speaking band."""
    language = [result.get("fluency_coherence"), result.get("lexical_resource"), result.get("grammatical_range_accuracy")]
    turns = ielts_pronunciation.pick_turns([t for t in session["turns"] if t["part"] in parts])
    recordings = []
    for t in turns:
        try:
            recordings.append(admin_db().storage.from_(ielts_exam.BUCKET).download(t["audio_path"]))
        except Exception:
            logger.exception("Could not load a speaking recording")
    transcript = "\n\n".join(v for k, v in ielts_speaking.transcripts_by_part(session).items() if int(k) in parts)
    pron = ielts_pronunciation.assess(recordings, transcript, language) if recordings else None
    if not pron:
        result["pronunciation"] = None
        return float(result.get("provisional_language_band") or 0)
    result["pronunciation"] = pron["pronunciation"]
    result["pronunciation_feedback"] = pron
    result["speaking_band"] = ielts_criteria_band(language + [pron["pronunciation"]])
    result["disclaimer"] = ("AI practice estimate from your recorded answers (all four criteria, including "
                            "Pronunciation). Only an official test can award an IELTS band.")
    return float(result["speaking_band"])


def _speaking_reply(session: dict) -> dict:
    turn = ielts_speaking.pending_turn(session) or session["turns"][-1]
    try:
        return {"turn": ielts_speaking.public_turn(turn), "done": session["done"]}
    except Exception:
        logger.exception("Examiner audio unavailable")
        raise HTTPException(status_code=503, detail="The examiner's voice is not available right now. "
                                                    "Your answers are saved — please try again in a few minutes.")


def _practice_session_load(session_id: str, user_id: str) -> dict:
    rows = (admin_db().table("tests").select("*").eq("id", session_id).eq("user_id", user_id)
            .eq("test_type", "ielts_speaking_session").limit(1).execute()).data
    if not rows:
        raise HTTPException(status_code=404, detail="Speaking session not found.")
    return rows[0]["questions"]


def _practice_session_save(session_id: str, content: dict) -> None:
    admin_db().table("tests").update({"questions": content}).eq("id", session_id).execute()


@router.post("/ielts/speaking/session")
def ielts_speaking_session_start(payload: IELTSSpeakingSessionPayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    key = f"speaking:{payload.part}"
    backup = False
    try:
        material = ielts_generate_speaking_set(payload.part)
        content = {str(payload.part): {
            "questions": material.get("questions") or [],
            "cue_card": material.get("cue_card") or "",
            "bullet_points": material.get("bullet_points") or [],
        }}
        if _bank_save_quietly("practice", key, material.get("title") or key, {"material": material, "content": content}):
            ielts_speaking.prevoice(content, [payload.part])
    except Exception:
        logger.exception("Speaking set generation failed; using the IELTS bank")
        item = _bank_backup(key, user, AI_DOWN)
        material, content, backup = item["content"]["material"], item["content"]["content"], True
    session = ielts_speaking.new_session(content, [payload.part])
    ielts_speaking.next_turn(session)
    row = admin_db().table("tests").insert({
        "user_id": user["id"], "title": material.get("title") or f"IELTS Speaking Part {payload.part}",
        "test_type": "ielts_speaking_session", "focus": "ielts_speaking",
        "questions": {"session": session, "material": material},
    }).execute()
    reply = _speaking_reply(session)
    reply.update(session_id=row.data[0]["id"], part=payload.part, backup=backup)
    return reply


@router.post("/ielts/speaking/session/{session_id}/answer")
def ielts_speaking_session_answer(session_id: str, turn_id: str = Form(...), seconds: float | None = Form(None),
                                  audio: UploadFile = File(...), user=Depends(require_user)):
    content = _practice_session_load(session_id, user["id"])
    session = content["session"]
    _speaking_take_answer(session, f"practice/{session_id}", turn_id, audio, seconds)
    if not session["done"]:
        ielts_speaking.next_turn(session)
    _practice_session_save(session_id, content)
    return _speaking_reply(session)


@router.post("/ielts/speaking/session/{session_id}/assess")
def ielts_speaking_session_assess(session_id: str, user=Depends(require_user)):
    content = _practice_session_load(session_id, user["id"])
    session = content["session"]
    if not session["done"]:
        raise HTTPException(status_code=400, detail="Finish the speaking part first.")
    if content.get("result"):
        return content["result"]
    part = session["parts"][0]
    unavailable = HTTPException(status_code=503, detail=f"{AI_DOWN} Your answers are saved — "
                                                         "press Assess again in a few minutes.")
    if not _speaking_fill_transcripts(session):
        _practice_session_save(session_id, content)
        raise unavailable
    transcript = ielts_speaking.transcripts_by_part(session).get(str(part), "")
    material = content.get("material") or {}
    prompt = material.get("cue_card") or "\n".join(material.get("questions") or [])
    try:
        lang = _feedback_lang(user["id"])
        result = ielts_assess_speaking(part, prompt, transcript, lang)
    except Exception:
        logger.exception("Speaking assessment failed")
        _practice_session_save(session_id, content)
        raise unavailable
    result["transcript"] = transcript
    result["prompt"] = prompt  # used by "Learn from this answer"
    _speaking_add_pronunciation(session, result, [part], lang)
    save_ielts_speaking(user["id"], part, prompt, transcript, result)
    content["result"] = result
    _practice_session_save(session_id, content)
    return result


@router.post("/ielts/reading/generate")
def ielts_reading_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    test_type = "General Training" if payload.test_type.lower().startswith("general") else "Academic"
    key = f"reading:{test_type}"
    notice = ""
    try:
        focus = ielts_analytics.family(payload.focus_type)
        content = ielts_generate_reading(test_type, focus)
        if not focus:  # targeted drills are not mixed into the shared bank
            _bank_save_quietly("practice", key, content.get("title") or key, content)
    except Exception:
        logger.exception("Reading generation failed; using the IELTS bank")
        item = _bank_backup(key, user, AI_DOWN)
        content = dict(item["content"], bank_id=str(item["id"]))
        notice = f"{AI_DOWN} This is a ready-made practice set from the bank."
    test_id = _ielts_practice_store(user, "reading", test_type, content)
    return {"test_id": test_id, "title": content.get("title"), "passage": content.get("passage"),
            "questions": _ielts_public_questions(content), "groups": content.get("groups") or [], "notice": notice}


@router.post("/ielts/listening/generate")
def ielts_listening_generate(payload: IELTSPracticeGeneratePayload, user=Depends(require_user)):
    ensure_ielts_profile(user["id"])
    notice = ""
    try:
        focus = ielts_analytics.family(payload.focus_type)
        content = ielts_generate_listening(focus)
        test_id = _ielts_practice_store(user, "listening", "Academic", content)
        script = content.get("audio_script") or ""
        listening_audio.start(
            test_id, user["id"], builder=lambda: synthesize_speech(script, instructions=IELTS_LISTENING_VOICE),
            max_plays=1,
            # a successfully voiced set may refill the bank (10 sets kept); targeted drills are not
            on_ready=None if focus else (lambda audio: _bank_save_quietly(
                "practice", "listening", content.get("title") or "listening", content, {"1": audio})),
        )
    except Exception:
        logger.exception("Listening generation failed; using the IELTS bank")
        item = _bank_backup("listening", user, AI_DOWN)
        audio = ielts_bank.load_audio((item.get("audio") or {}).get("1", ""))
        if not audio:
            raise HTTPException(status_code=503, detail=f"{AI_DOWN} The backup recording could not be loaded.")
        content = dict(item["content"], bank_id=str(item["id"]), bank_audio=(item.get("audio") or {}).get("1"))
        test_id = _ielts_practice_store(user, "listening", "Academic", content)
        listening_audio.start(test_id, user["id"], audio=audio, max_plays=1)
        notice = f"{AI_DOWN} This is a ready-made practice set from the bank."
    return {"test_id": test_id, "title": content.get("title"), "max_plays": 1,
            "questions": _ielts_public_questions(content), "notice": notice}


@router.get("/ielts/practice/{test_id}/audio")
def ielts_practice_audio(test_id: str, user=Depends(require_user)):
    def loader():
        c = _ielts_practice_load(test_id, user["id"])
        return c if c and c.get("_section") == "listening" else None

    def audio_loader(c):
        if c.get("bank_audio"):
            return lambda: ielts_bank.load_audio(c["bank_audio"])
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
            "id": qid, "type": q.get("type"), "correct": ok, "answer": given,
            "expected": accepted if isinstance(accepted, str) else " / ".join(map(str, accepted)),
            "explanation": explanations.get(qid, ""),
        })
    total = max(len(content.get("questions") or []), 1)
    pct = round(correct / total * 100)
    save_ielts_objective(user["id"], section, content.get("test_type") or "Academic",
                         content.get("title") or f"IELTS {section} practice", pct, None, payload.answers, details)
    if content.get("bank_id"):
        ielts_bank.record_stats(content["bank_id"], {"1": details})
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
    source: Literal["new", "verified"] = "new"   # "verified": an admin-approved exam from the bank


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
    mock_id, test_type = mock["id"], mock.get("test_type") or "Academic"
    ielts_exam.prepare(
        mock_id, user["id"], test_type,
        _MOCK_GENERATORS, mock_save_generated_section, mock_get_section,
        on_ready=lambda: _mock_bank_save(user["id"], mock_id, test_type),
        on_error=lambda: _mock_fill_from_bank(user["id"], mock_id, test_type),
    )


def _mock_set_content(user_id: str, mock_id: str, section: str, part: int, content: dict) -> None:
    if mock_get_section(user_id, mock_id, section, part):
        (admin_db().table("ielts_mock_sections").update({"content": content})
         .eq("mock_id", mock_id).eq("user_id", user_id).eq("section", section).eq("part", part).execute())
    else:
        mock_save_generated_section(user_id, mock_id, section, part, content)


def _mock_bank_save(user_id: str, mock_id: str, test_type: str) -> None:
    """Copy a fully prepared paper into the bank (up to 5 per test type) and pre-voice the examiner."""
    meta = ielts_exam._get_meta(mock_id)
    key = ielts_bank.mock_key(test_type)
    if meta.get("bank_id") or ielts_bank.count(key) >= ielts_bank.MOCK_LIMIT:
        return
    paper, audio = {}, {}
    for section, n in ielts_exam.PARTS.items():
        for p in range(1, n + 1):
            row = mock_get_section(user_id, mock_id, section, p)
            if not row:
                return
            paper.setdefault(section, {})[str(p)] = row["content"]
    for p in (1, 2, 3, 4):
        audio[str(p)] = ielts_exam.load_audio(mock_id, p)
        if not audio[str(p)]:
            return
    paper["listening_durations"] = meta.get("listening_durations") or {}
    title = f"{test_type} mock · {(paper['reading']['1'] or {}).get('title', '')}"
    if _bank_save_quietly("mock", key, title, paper, audio, source_id=mock_id):
        ielts_speaking.prevoice({p: paper["speaking"][p] for p in ("1", "2", "3")}, [1, 2, 3])


def _mock_fill_from_bank(user_id: str, mock_id: str, test_type: str,
                         verified_only: bool = False, replace_all: bool = False) -> bool:
    """Complete (or replace) the paper with a bank exam the learner has not taken."""
    item = ielts_bank.pick(ielts_bank.mock_key(test_type), user_id, verified_only)
    if not item:
        return False
    paper = item["content"]
    meta = ielts_exam._get_meta(mock_id)
    durations = dict(meta.get("listening_durations") or {})
    for section, n in ielts_exam.PARTS.items():
        for p in range(1, n + 1):
            have = mock_get_section(user_id, mock_id, section, p)
            if section == "listening":
                missing = replace_all or not have or str(p) not in durations or not ielts_exam.load_audio(mock_id, p)
                if missing:
                    audio = ielts_bank.load_audio((item.get("audio") or {}).get(str(p), ""))
                    if not audio:
                        return False
                    _mock_set_content(user_id, mock_id, section, p, paper[section][str(p)])
                    ielts_exam.store_audio(mock_id, p, audio)
                    durations[str(p)] = (paper.get("listening_durations") or {}).get(str(p)) \
                        or round(listening_audio.mp3_duration(audio), 1)
            elif replace_all or not have:
                _mock_set_content(user_id, mock_id, section, p, paper[section][str(p)])
    ielts_exam.update_meta(mock_id, lambda m: m.update(
        bank_id=str(item["id"]), listening_durations=durations, source="verified" if verified_only else "backup"))
    ielts_bank.mark_used(user_id, item)
    return True


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
    bank_id = (mock.get("metadata") or {}).get("bank_id")
    if bank_id and (mock.get("metadata") or {}).get("source") in ("verified", "backup"):
        try:
            ielts_bank.record_stats(bank_id, {f"{section}{p}": d for p, d in review.items()})
        except Exception:
            logger.exception("Could not record bank statistics")
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
                                                  row["content"].get("chart"), _feedback_lang(user_id))
                             if len(text.split()) >= 20 else blank())
            mock_complete_section(user_id, mock_id, "writing", task, estimated_band=results[task]["overall_band"],
                                  result=results[task])
            save_ielts_writing(user_id, test_type, task, row["content"]["prompt"], text or "(no answer)", results[task])
        band = ielts_writing_band(results[1]["overall_band"], results[2]["overall_band"])
        mock_update_band(user_id, mock_id, "writing", band)
        ielts_exam.update_meta(mock_id, lambda m: m.update(writing_assessment="done"))
    except Exception as exc:
        logger.exception("Mock writing assessment failed")
        ielts_exam.update_meta(mock_id, lambda m: m.update(writing_assessment="pending", writing_error=str(exc),
                                                           pending_tried_at=ielts_exam.iso(ielts_exam.now())))


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
        "pending": _mock_pending(meta),
        "source": meta.get("source") or "new",
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
    if payload.source == "verified":
        if not _mock_fill_from_bank(user["id"], row["id"], test_type, verified_only=True, replace_all=True):
            admin_db().table("ielts_mock_tests").update({"status": "abandoned"}).eq("id", row["id"]).execute()
            raise HTTPException(status_code=409, detail="There is no verified exam of this type that you have not taken yet. "
                                                        "Start a new exam instead.")
        ielts_exam.update_meta(row["id"], lambda m: m.update(prep={"status": "ready", "step": "Done", "listening_ready": True}))
        return _mock_status_payload(mock_get(user["id"], row["id"]))
    mock = mock_get(user["id"], row["id"])
    _mock_start_prep(user, mock)
    return _mock_status_payload(mock_get(user["id"], row["id"]))


@router.get("/ielts/mock/{mock_id}")
def ielts_mock_status(mock_id: str, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    if mock.get("status") == "in_progress" and (mock.get("metadata") or {}).get("version") == ielts_exam.VERSION:
        mock = _mock_finalize_expired(user, mock)
        if _mock_pending(mock.get("metadata") or {}):
            _mock_process_pending(user, mock)
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


def _mock_speaking_session(user: dict, mock: dict) -> dict:
    meta = mock.get("metadata") or {}
    if meta.get("speaking_session"):
        return meta["speaking_session"]
    content = {}
    for p in (1, 2, 3):
        row = mock_get_section(user["id"], mock["id"], "speaking", p) or {}
        content[str(p)] = row.get("content") or {}
    session = ielts_speaking.new_session(content, [1, 2, 3])
    ielts_speaking.next_turn(session)
    return session


def _mock_speaking_ready(user: dict, mock_id: str) -> dict:
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    if "speaking" not in (meta.get("timing") or {}) or _mock_section_done(mock, "speaking"):
        raise HTTPException(status_code=403, detail="The Speaking test is not in progress.")
    return mock


@router.post("/ielts/mock/{mock_id}/speaking/turn")
def ielts_mock_speaking_turn(mock_id: str, user=Depends(require_user)):
    mock = _mock_speaking_ready(user, mock_id)
    session = _mock_speaking_session(user, mock)
    ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_session=session))
    return _speaking_reply(session)


@router.post("/ielts/mock/{mock_id}/speaking/answer")
def ielts_mock_speaking_answer(mock_id: str, turn_id: str = Form(...), seconds: float | None = Form(None),
                               audio: UploadFile = File(...), user=Depends(require_user)):
    mock = _mock_speaking_ready(user, mock_id)
    session = _mock_speaking_session(user, mock)
    _speaking_take_answer(session, f"mock/{mock_id}", turn_id, audio, seconds)
    if not session["done"]:
        ielts_speaking.next_turn(session)
    ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_session=session))
    return _speaking_reply(session)


def _mock_assess_speaking(user_id: str, mock_id: str) -> bool:
    """Transcribe (if needed) and assess the recorded interview. False if the AI is unavailable."""
    mock = mock_get(user_id, mock_id)
    session = (mock.get("metadata") or {}).get("speaking_session")
    ok = _speaking_fill_transcripts(session)
    ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_session=session))
    if not ok:
        return False
    transcripts = ielts_speaking.transcripts_by_part(session)
    parts = {}
    for p in (1, 2, 3):
        row = mock_get_section(user_id, mock_id, "speaking", p)
        parts[f"part{p}"] = {"prompt": (row or {}).get("content"), "transcript": transcripts.get(str(p), "(no answer)")}
    try:
        lang = _feedback_lang(user_id)
        result = mock_assess_full_speaking(parts, lang)
    except Exception:
        logger.exception("Speaking assessment unavailable")
        return False
    band = _speaking_add_pronunciation(session, result, [1, 2, 3], lang)
    for p in (1, 2, 3):
        row = mock_get_section(user_id, mock_id, "speaking", p)
        mock_complete_section(user_id, mock_id, "speaking", p, estimated_band=band, result=result,
                              answers={"transcript": transcripts.get(str(p), ""),
                                       "audio": [t["audio_path"] for t in session["turns"] if t["part"] == p and t.get("audio_path")]})
        save_ielts_speaking(user_id, p, str((row or {}).get("content")), transcripts.get(str(p), ""), result)
    mock_update_band(user_id, mock_id, "speaking", band)
    ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_assessment="done"))
    return True


def _mock_pending(meta: dict) -> list[str]:
    out = []
    if meta.get("writing_assessment") in ("running", "pending", "error"):
        out.append("writing")
    if meta.get("speaking_assessment") in ("running", "pending"):
        out.append("speaking")
    return out


def _mock_process_pending(user: dict, mock: dict, force: bool = False) -> None:
    """Retry assessments that waited for the AI service (at most every 2 minutes)."""
    meta = mock.get("metadata") or {}
    last = ielts_exam.parse(meta.get("pending_tried_at"))
    if not force and last and ielts_exam.now() - last < timedelta(minutes=2):
        return
    mock_id, test_type = mock["id"], mock.get("test_type") or "Academic"
    todo = []
    if meta.get("writing_assessment") in ("pending", "error"):
        todo.append("writing")
    if meta.get("speaking_assessment") == "pending":
        todo.append("speaking")
    if not todo:
        return

    def mark(m):
        m["pending_tried_at"] = ielts_exam.iso(ielts_exam.now())
        for section in todo:
            m[f"{section}_assessment"] = "running"
    ielts_exam.update_meta(mock_id, mark)

    def job():
        if "writing" in todo:
            _mock_assess_writing(user["id"], mock_id, test_type)
        if "speaking" in todo:
            ok = _mock_assess_speaking(user["id"], mock_id)
            if not ok:
                ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_assessment="pending"))
    threading.Thread(target=job, daemon=True).start()


@router.post("/ielts/mock/{mock_id}/speaking/submit")
def ielts_mock_speaking_submit(mock_id: str, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    if _mock_section_done(mock, "speaking"):
        return _mock_status_payload(mock)
    session = meta.get("speaking_session")
    if not session or not session.get("done"):
        raise HTTPException(status_code=400, detail="The Speaking test is not finished yet.")
    if not _mock_assess_speaking(user["id"], mock_id):
        # The AI is unavailable: keep the recordings and assess later.
        transcripts = ielts_speaking.transcripts_by_part(session)
        for p in (1, 2, 3):
            mock_complete_section(user["id"], mock_id, "speaking", p,
                                  answers={"transcript": transcripts.get(str(p), ""),
                                           "audio": [t["audio_path"] for t in session["turns"] if t["part"] == p and t.get("audio_path")]})
        ielts_exam.update_meta(mock_id, lambda m: m.update(speaking_assessment="pending",
                                                           pending_tried_at=ielts_exam.iso(ielts_exam.now())))
    return _mock_status_payload(mock_get(user["id"], mock_id))


@router.post("/ielts/mock/{mock_id}/finish")
def ielts_mock_finish(mock_id: str, user=Depends(require_user)):
    mock = _mock_or_404(user, mock_id)
    meta = mock.get("metadata") or {}
    pending = _mock_pending(meta)
    if pending:
        _mock_process_pending(user, mock)
    bands = [mock.get("listening_band"), mock.get("reading_band"), mock.get("writing_band"), mock.get("speaking_band")]
    if not pending and any(x is None for x in bands):
        raise HTTPException(status_code=400, detail="Complete all four IELTS sections before finishing the mock.")
    if pending:
        return {
            "pending": pending,
            "overall_band": None,
            "bands": {k: (float(v) if v is not None else None) for k, v in
                      zip(("listening", "reading", "writing", "speaking"), bands)},
            "review": _mock_review(user, mock_id),
            "detail": ("Listening and Reading are marked. " + " and ".join(s.title() for s in pending) +
                       (" is" if len(pending) == 1 else " are") +
                       " waiting for the AI examiner, which is busy or unavailable right now. "
                       "Your answers are saved; the result will appear here automatically."),
        }
    overall = ielts_overall_band([float(x) for x in bands])
    if mock.get("status") != "completed":
        mock_finish(user["id"], mock_id, overall)
        db = admin_db()
        db.table("ielts_profiles").update({
            "current_overall_band": overall,
            "listening_band": float(bands[0]), "reading_band": float(bands[1]),
            "writing_band": float(bands[2]), "speaking_band": float(bands[3]),
        }).eq("user_id", user["id"]).execute()
        # one row per skill feeds the progress chart and the forecast
        db.table("ielts_band_history").insert([
            {"user_id": user["id"], "section": s, "band": float(b), "source": "full_mock_ai_practice",
             "note": "Section band from the full mock."}
            for s, b in zip(("listening", "reading", "writing", "speaking"), bands)
        ] + [{
            "user_id": user["id"], "section": "overall", "band": overall,
            "source": "full_mock_ai_practice",
            "note": "Practice overall from the full mock (AI estimates).",
        }]).execute()

    full = mock_get(user["id"], mock_id)
    review = _mock_review(user, mock_id)
    return {
        "mock": {k: full.get(k) for k in ("id", "test_type", "status", "started_at", "completed_at")},
        "overall_band": overall,
        "bands": {"listening": float(bands[0]), "reading": float(bands[1]),
                  "writing": float(bands[2]), "speaking": float(bands[3])},
        "review": review,
        "source": meta.get("source") or "new",
        "disclaimer": ("Practice estimate only. This is not an official IELTS result."
                       + ("" if (review.get("speaking", {}).get("1", {}).get("result") or {}).get("pronunciation") is not None
                          else " Speaking pronunciation could not be assessed this time, so Speaking uses three criteria.")),
    }


def _mock_review(user: dict, mock_id: str) -> dict:
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
                "transcript": (row.get("content") or {}).get("audio_script") if section == "listening"
                              else ((row.get("answers") or {}).get("transcript") if section == "speaking" else None),
            }
            if section == "writing":  # for "Learn from this answer" in the results
                review[section][str(p)].update(prompt=(row.get("content") or {}).get("prompt"),
                                               response=(row.get("answers") or {}).get("response"),
                                               chart=(row.get("content") or {}).get("chart"))
    return review


# ========================= ADMIN: IELTS bank =========================

class IELTSBankVerifyPayload(BaseModel):
    verified: bool


@router.get("/admin/ielts-bank")
def admin_ielts_bank_list(user=Depends(require_user)):
    _require_admin(user)
    try:
        items = ielts_bank.list_items()
    except Exception as exc:
        logger.exception("IELTS bank list failed")
        return {"error": "The IELTS bank is not set up in Supabase yet. Run database/ielts_bank.sql "
                         f"in the Supabase SQL Editor. Details: {exc}", "items": []}
    return {"items": items, "mock_limit": ielts_bank.MOCK_LIMIT, "practice_limit": ielts_bank.PRACTICE_LIMIT}


@router.get("/admin/ielts-bank/{item_id}")
def admin_ielts_bank_item(item_id: str, user=Depends(require_user)):
    _require_admin(user)
    item = ielts_bank.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")
    item["suspicious"] = ielts_bank.suspicious(item)
    return item


@router.get("/admin/ielts-bank/{item_id}/audio/{name}")
def admin_ielts_bank_audio(item_id: str, name: str, user=Depends(require_user)):
    _require_admin(user)
    item = ielts_bank.get(item_id)
    path = ((item or {}).get("audio") or {}).get(name)
    audio = ielts_bank.load_audio(path) if path else None
    if not audio:
        raise HTTPException(status_code=404, detail="Recording not found.")
    return StreamingResponse(BytesIO(audio), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/admin/ielts-bank/{item_id}/verify")
def admin_ielts_bank_verify(item_id: str, payload: IELTSBankVerifyPayload, user=Depends(require_user)):
    _require_admin(user)
    ielts_bank.set_verified(item_id, payload.verified)
    return {"ok": True}


@router.delete("/admin/ielts-bank/{item_id}")
def admin_ielts_bank_delete(item_id: str, user=Depends(require_user)):
    _require_admin(user)
    ielts_bank.delete(item_id)
    return {"ok": True}


# ========================= ADMIN: storage cleanup =========================

@router.get("/admin/storage-cleanup")
def admin_storage_cleanup_status(user=Depends(require_user)):
    _require_admin(user)
    return storage_cleanup.status()


@router.post("/admin/storage-cleanup")
def admin_storage_cleanup_run(user=Depends(require_user)):
    _require_admin(user)
    try:
        return storage_cleanup.run()
    except Exception as exc:
        logger.exception("Storage cleanup failed")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {exc}")


# ========================= LEVEL ROADMAP AND PLACEMENT TEST =========================

class PlacementAnswerPayload(BaseModel):
    item_id: str = Field(max_length=20)
    choice: str = Field(max_length=300)


class PlacementAnswersPayload(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class PlacementWritingPayload(BaseModel):
    text: str | None = Field(default=None, max_length=8000)


class PlacementSpeakingDonePayload(BaseModel):
    skip: bool = False


class RoadmapEvidencePayload(BaseModel):
    item_id: str = Field(max_length=20)
    module: str = Field(max_length=30)
    score: float | None = None
    level: str | None = Field(default=None, max_length=10)


class LanguagePayload(BaseModel):
    language: Literal["ru", "kk", "en"]


ROADMAP_SETUP = ("The level roadmap is not set up in Supabase yet: run database/roadmap.sql in the "
                 "Supabase SQL Editor.")


def _placement_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Placement step failed")
        if "learner_roadmap" in str(exc):
            raise HTTPException(status_code=503, detail=ROADMAP_SETUP)
        raise HTTPException(status_code=503, detail=f"{AI_DOWN} Your progress is saved — try again in a minute.")


@router.post("/profile/language")
def set_language(payload: LanguagePayload, user=Depends(require_user)):
    """One language setting for explanations, feedback and the roadmap (ru / kk / en)."""
    ensure_profile(user["id"], user.get("email"))
    admin_db().table("profiles").update({"preferred_explanation_language": payload.language}).eq("id", user["id"]).execute()
    return {"language": payload.language}


@router.get("/roadmap")
def roadmap_view(user=Depends(require_user)):
    band = None
    try:
        rows = admin_db().table("ielts_profiles").select("current_overall_band").eq("user_id", user["id"]).limit(1).execute().data
        band = (rows or [{}])[0].get("current_overall_band")
    except Exception:
        pass
    try:
        data = roadmap_store.view(user["id"], float(band) if band is not None else None)
    except Exception as exc:
        logger.exception("Roadmap failed")
        raise HTTPException(status_code=503, detail=ROADMAP_SETUP if "learner_roadmap" in str(exc) else str(exc))
    data["language"] = _feedback_lang(user["id"])
    return data


@router.post("/roadmap/evidence")
def roadmap_evidence(payload: RoadmapEvidencePayload, user=Depends(require_user)):
    try:
        return roadmap_store.record_evidence(user["id"], payload.item_id, payload.module, payload.score, payload.level)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown roadmap item.")


@router.post("/placement/start")
def placement_start(user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    _placement_call(placement.start, user["id"])
    return placement.public_state(user["id"])


@router.get("/placement")
def placement_state(user=Depends(require_user)):
    return _placement_call(placement.public_state, user["id"])


@router.post("/placement/answer")
def placement_answer(payload: PlacementAnswerPayload, user=Depends(require_user)):
    _placement_call(placement.answer_core, user["id"], payload.item_id, payload.choice)
    return placement.public_state(user["id"])


@router.post("/placement/reading")
def placement_reading(payload: PlacementAnswersPayload, user=Depends(require_user)):
    _placement_call(placement.submit_reading, user["id"], payload.answers)
    return placement.public_state(user["id"])


@router.post("/placement/listening")
def placement_listening(payload: PlacementAnswersPayload, user=Depends(require_user)):
    _placement_call(placement.submit_listening, user["id"], payload.answers)
    return placement.public_state(user["id"])


@router.post("/placement/writing")
def placement_writing(payload: PlacementWritingPayload, user=Depends(require_user)):
    _placement_call(placement.submit_writing, user["id"], payload.text)
    return placement.public_state(user["id"])


@router.post("/placement/speaking/answer/{index}")
async def placement_speaking_answer(index: int, audio: UploadFile = File(...), user=Depends(require_user)):
    data = await audio.read()
    if not data or len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="The recording is empty or too long.")
    if index not in (0, 1):
        raise HTTPException(status_code=400, detail="Unknown question.")
    content_type = (audio.content_type or "audio/webm").split(";", 1)[0]
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(_placement_call, placement.speaking_answer, user["id"], index, data, content_type)
    return placement.public_state(user["id"])


@router.post("/placement/speaking/submit")
def placement_speaking_submit(payload: PlacementSpeakingDonePayload, user=Depends(require_user)):
    _placement_call(placement.submit_speaking, user["id"], payload.skip)
    return placement.public_state(user["id"])


# ========================= REVIEW: words (spaced repetition), my mistakes, today =========================

class WordReviewPayload(BaseModel):
    quality: int = Field(ge=0, le=5)


class MistakesSubmitPayload(BaseModel):
    test_id: str
    answers: dict[str, str] = Field(default_factory=dict)


@router.get("/today")
def today_plan(user=Depends(require_user)):
    return review.today(user["id"])


@router.get("/review/words")
def review_words(user=Depends(require_user)):
    words, due = review.due_words(user["id"], limit=20)
    return {"words": words, "due": due}


@router.post("/review/words/{word_id}")
def review_word(word_id: str, payload: WordReviewPayload, user=Depends(require_user)):
    try:
        return review.review_word(user["id"], word_id, payload.quality)
    except KeyError:
        raise HTTPException(status_code=404, detail="Word not found.")


@router.get("/mistakes")
def mistakes_list(user=Depends(require_user)):
    return review.mistakes(user["id"])


@router.post("/mistakes/practice")
def mistakes_practice(user=Depends(require_user)):
    try:
        return review.mistakes_practice(user["id"], _feedback_lang(user["id"]))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        logger.exception("Mistakes practice failed")
        raise HTTPException(status_code=503, detail=f"{AI_DOWN} Try again in a few minutes.")


@router.post("/mistakes/submit")
def mistakes_submit(payload: MistakesSubmitPayload, user=Depends(require_user)):
    try:
        return review.submit_mistakes(user["id"], payload.test_id, payload.answers)
    except KeyError:
        raise HTTPException(status_code=404, detail="Practice not found.")


# ========================= GOALS, STREAK AND LEVEL CHECK =========================

class GoalPayload(BaseModel):
    target: str | None = Field(default=None, max_length=5)
    target_date: str | None = Field(default=None, max_length=10)
    days_per_week: int | None = Field(default=None, ge=1, le=7)


class CheckAnswersPayload(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class CheckWritingPayload(BaseModel):
    text: str = Field(max_length=8000)


def _progress_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("Progress call failed")
        raise HTTPException(status_code=503, detail=ROADMAP_SETUP if ("goals" in str(exc) or "checks" in str(exc)
                                                                      or "learner_roadmap" in str(exc)) else str(exc))


@router.get("/progress/overview")
def progress_overview(user=Depends(require_user)):
    return _progress_call(progress.overview, user["id"])


@router.post("/goals")
def goals_set(payload: GoalPayload, user=Depends(require_user)):
    _progress_call(progress.set_goal, user["id"], payload.target, payload.target_date, payload.days_per_week)
    return progress.overview(user["id"])


@router.get("/level-check")
def level_check_status(user=Depends(require_user)):
    return _progress_call(progress.check_status, user["id"])


@router.post("/level-check/start")
def level_check_start(user=Depends(require_user)):
    return _progress_call(progress.start_check, user["id"])


@router.post("/level-check/answers")
def level_check_answers(payload: CheckAnswersPayload, user=Depends(require_user)):
    return _progress_call(progress.submit_check_answers, user["id"], payload.answers)


@router.post("/level-check/writing")
def level_check_writing(payload: CheckWritingPayload, user=Depends(require_user)):
    return _progress_call(progress.submit_check_writing, user["id"], payload.text)


# ========================= READING (General English) =========================

class ReadingGeneratePayload(BaseModel):
    level: Literal["A1", "A2", "B1", "B2", "C1", "C2"] = "B1"
    genre: str = Field(default="article", max_length=30)
    topic: str = Field(default="", max_length=100)


class ReadingSubmitPayload(BaseModel):
    test_id: str
    answers: dict[str, str] = Field(default_factory=dict)


def _load_reading(test_id: str, user_id: str) -> dict | None:
    rows = admin_db().table("tests").select("questions").eq("id", test_id).eq("user_id", user_id) \
        .eq("focus", "reading").limit(1).execute().data
    return rows[0]["questions"] if rows else None


@router.post("/reading/generate")
def reading_generate(payload: ReadingGeneratePayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    from app.agents import reading_agent
    from app.agents.ielts_verify import verify
    try:
        content = reading_agent.generate(payload.level, payload.genre, payload.topic.strip(), _feedback_lang(user["id"]))
        content = verify(content, "passage", "passage")      # an independent check of the answer key
    except Exception:
        logger.exception("Reading generation failed")
        raise HTTPException(status_code=503, detail=f"{AI_DOWN} Try again in a few minutes.")
    content["level"] = payload.level
    row = admin_db().table("tests").insert({"user_id": user["id"], "title": content.get("title") or "Reading",
                                            "test_type": "reading_practice", "focus": "reading",
                                            "level": payload.level, "questions": content}).execute()
    return {"test_id": row.data[0]["id"], "title": content.get("title"), "passage": content.get("passage"),
            "level": payload.level,
            "questions": [{k: q.get(k) for k in ("id", "type", "question", "options", "instruction")}
                          for q in content["questions"]]}


@router.post("/reading/submit")
def reading_submit(payload: ReadingSubmitPayload, user=Depends(require_user)):
    content = _load_reading(payload.test_id, user["id"])
    if not content:
        raise HTTPException(status_code=404, detail="Reading practice not found.")
    key = content.get("answer_key") or {}
    explanations = content.get("explanations") or {}
    details, correct = [], 0
    for q in content["questions"]:
        qid = str(q.get("id"))
        accepted = key.get(qid) if isinstance(key.get(qid), list) else [key.get(qid)]
        accepted = [str(a) for a in accepted if a]
        given = str(payload.answers.get(qid, "")).strip()
        ok = ielts_exam.is_correct(given, accepted, q.get("instruction"), q.get("options"))
        correct += int(ok)
        details.append({"id": q.get("id"), "type": q.get("type"), "answer": given, "correct": ok,
                        "expected": accepted[0] if accepted else "", "explanation": explanations.get(qid, "")})
    total = len(details)
    score = round(correct / max(total, 1) * 100)
    level = content.get("level")
    try:
        admin_db().table("reading_attempts").insert({
            "user_id": user["id"], "title": content.get("title"), "source_text": content.get("passage"),
            "level": level, "score": score, "questions": content["questions"], "answers": details}).execute()
    except Exception:
        logger.exception("Could not save reading attempt")
    record_activity(user["id"], "reading", content.get("title") or "Reading practice", score,
                    {"level": level, "correct": correct, "total": total, "test_id": payload.test_id},
                    tested_level=level, evidence_source="explicit_cefr_reading_practice")
    return {"score": score, "correct": correct, "total": total, "level": level, "details": details,
            "glossary": content.get("glossary") or []}


# ========================= FEEDBACK FROM TESTERS =========================

FEEDBACK_SETUP = "Feedback is not set up in Supabase yet: run database/feedback.sql in the Supabase SQL Editor."


class FeedbackPayload(BaseModel):
    role: Literal["student", "masters", "teacher", "other"] = "student"
    section: str = Field(min_length=1, max_length=60)
    kind: Literal["bug", "inconvenience", "wish", "impression"] = "impression"
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(min_length=3, max_length=5000)
    context: dict = Field(default_factory=dict)


class FeedbackStatusPayload(BaseModel):
    status: Literal["new", "seen", "done"]
    admin_note: str | None = Field(default=None, max_length=2000)


def _feedback_rows(user_id: str | None = None) -> list[dict]:
    q = admin_db().table("feedback").select("*")
    if user_id:
        q = q.eq("user_id", user_id)
    return q.order("created_at", desc=True).limit(1000).execute().data or []


@router.post("/feedback")
def feedback_send(payload: FeedbackPayload, user=Depends(require_user)):
    context = {k: str(v)[:200] for k, v in list(payload.context.items())[:10]}
    try:
        row = admin_db().table("feedback").insert({
            "user_id": user["id"], "email": user.get("email"), "role": payload.role,
            "section": payload.section.strip(), "kind": payload.kind, "rating": payload.rating,
            "comment": payload.comment.strip(), "context": context}).execute()
    except Exception as exc:
        logger.exception("Feedback could not be saved")
        raise HTTPException(status_code=503, detail=FEEDBACK_SETUP if "feedback" in str(exc) else str(exc))
    return {"id": row.data[0]["id"], "saved": True}


@router.get("/feedback/mine")
def feedback_mine(user=Depends(require_user)):
    try:
        rows = _feedback_rows(user["id"])
    except Exception:
        return {"items": []}
    return {"items": [{k: r.get(k) for k in ("id", "section", "kind", "rating", "comment", "status", "created_at")}
                      for r in rows]}


@router.get("/admin/feedback")
def admin_feedback(user=Depends(require_user)):
    _require_admin(user)
    try:
        rows = _feedback_rows()
    except Exception:
        logger.exception("Feedback list failed")
        return {"items": [], "error": FEEDBACK_SETUP}
    by_section: dict[str, dict] = {}
    for r in rows:
        s = by_section.setdefault(r.get("section") or "—", {"count": 0, "ratings": []})
        s["count"] += 1
        if r.get("rating"):
            s["ratings"].append(int(r["rating"]))
    summary = [{"section": k, "count": v["count"],
                "avg_rating": round(sum(v["ratings"]) / len(v["ratings"]), 1) if v["ratings"] else None}
               for k, v in sorted(by_section.items(), key=lambda kv: -kv[1]["count"])]
    return {"items": rows, "summary": summary}


@router.post("/admin/feedback/{feedback_id}")
def admin_feedback_status(feedback_id: str, payload: FeedbackStatusPayload, user=Depends(require_user)):
    _require_admin(user)
    fields = {"status": payload.status}
    if payload.admin_note is not None:
        fields["admin_note"] = payload.admin_note
    admin_db().table("feedback").update(fields).eq("id", feedback_id).execute()
    return {"ok": True}


@router.get("/admin/feedback.csv")
def admin_feedback_csv(user=Depends(require_user)):
    """All feedback as a CSV file that opens in Excel (UTF-8 with BOM, ';' separator)."""
    _require_admin(user)
    import csv
    import io
    rows = _feedback_rows()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Дата", "Email", "Роль", "Раздел", "Тип", "Оценка", "Замечание", "Язык", "Уровень", "Статус", "Заметка"])
    for r in rows:
        ctx = r.get("context") or {}
        w.writerow([str(r.get("created_at") or "")[:16].replace("T", " "), r.get("email"), r.get("role"), r.get("section"),
                    r.get("kind"), r.get("rating") or "", r.get("comment"), ctx.get("lang", ""), ctx.get("level", ""),
                    r.get("status"), r.get("admin_note") or ""])
    data = "﻿" + buf.getvalue()
    return StreamingResponse(BytesIO(data.encode("utf-8")), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=feedback.csv"})
