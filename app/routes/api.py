from typing import Literal
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
from app.agents.tutor_agent import chat as tutor_chat
from app.agents.grammar_agent import analyze as grammar_analyze
from app.agents.writing_agent import analyze as writing_analyze
from app.agents.vocabulary_agent import create_set
from app.agents.assessment_agent import generate as generate_test
from app.services.openai_service import ask_text, transcribe_audio, synthesize_speech

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
    return get_student_context(user["id"])


@router.post("/chat")
def chat(payload: ChatPayload, user=Depends(require_user)):
    ensure_profile(user["id"], user.get("email"))
    answer = tutor_chat(user["id"], payload.text)

    db = admin_db()
    db.table("messages").insert([
        {"user_id": user["id"], "role": "user", "content": payload.text},
        {"user_id": user["id"], "role": "assistant", "content": answer},
    ]).execute()
    return {"answer": answer}


@router.post("/grammar")
def grammar(payload: TextPayload, user=Depends(require_user)):
    context = get_student_context(user["id"])
    level = context.get("profile", {}).get("cefr_level", "B1")
    result = grammar_analyze(payload.text, level)
    score = int(result.get("score", 0))
    record_activity(
        user["id"], "grammar", result.get("topic", "Grammar"), score, result
    )
    return result


@router.post("/writing")
def writing(payload: WritingPayload, user=Depends(require_user)):
    result = writing_analyze(payload.text, payload.purpose)
    score = int(result.get("score", 0))
    record_activity(
        user["id"], "writing", result.get("topic", payload.purpose), score, result
    )
    return result


@router.post("/vocabulary")
def vocabulary(payload: VocabularyPayload, user=Depends(require_user)):
    result = create_set(payload.topic, payload.level)
    record_activity(user["id"], "vocabulary", payload.topic, None, {"level": payload.level})
    return result


@router.post("/test/generate")
def test_generate(payload: TestPayload, user=Depends(require_user)):
    return generate_test(payload.level, payload.focus)


@router.post("/test/submit")
def test_submit(payload: TestSubmission, user=Depends(require_user)):
    correct = 0
    details = []
    for q in payload.questions:
        qid = str(q.get("id"))
        chosen = payload.answers.get(qid)
        expected = q.get("answer_index")
        ok = chosen == expected
        correct += int(ok)
        details.append(
            {
                "id": q.get("id"),
                "correct": ok,
                "chosen": chosen,
                "answer_index": expected,
                "explanation": q.get("explanation"),
            }
        )

    score = round(correct / max(len(payload.questions), 1) * 100)
    record_activity(
        user["id"], "grammar", f"Mini-test: {payload.title}", score,
        {"level": payload.level, "details": details},
    )
    return {"score": score, "correct": correct, "total": len(payload.questions), "details": details}


@router.post("/speaking")
def speaking(payload: SpeakingPayload, user=Depends(require_user)):
    # Browser speech-to-text sends the transcript. The AI evaluates language,
    # not acoustic pronunciation. This avoids pretending text alone can score pronunciation.
    prompt = f"""You are evaluating an English speaking-practice transcript.
Scenario: {payload.scenario}

Return feedback in plain text with:
- one brief positive point
- corrected version of the most important language error (if any)
- fluency/vocabulary suggestion
- one follow-up question to continue the role-play

Do NOT claim to evaluate pronunciation because only a transcript is available.

Transcript:
{payload.text}
"""
    answer = ask_text("You are a concise CEFR-aware speaking coach.", prompt)
    record_activity(user["id"], "speaking", payload.scenario, None, {"transcript": payload.text})
    return {"feedback": answer}


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
    content_type = audio.content_type or "application/octet-stream"
    if content_type not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported audio format.")

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
