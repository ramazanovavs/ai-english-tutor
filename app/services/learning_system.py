from datetime import datetime, timezone
from app.services.supabase_service import admin_db


def start_session(user_id: str, session_type: str, title: str | None = None, level: str | None = None) -> str:
    result = admin_db().table("learning_sessions").insert({
        "user_id": user_id,
        "session_type": session_type,
        "title": title,
        "cefr_level": level,
    }).execute()
    return result.data[0]["id"]


def finish_session(session_id: str, score: int | None = None, metadata: dict | None = None) -> None:
    db = admin_db()
    row = db.table("learning_sessions").select("started_at").eq("id", session_id).single().execute()
    started = datetime.fromisoformat(row.data["started_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    db.table("learning_sessions").update({
        "ended_at": now.isoformat(),
        "duration_seconds": max(0, int((now - started).total_seconds())),
        "score": score,
        "metadata": metadata or {},
    }).eq("id", session_id).execute()


def save_grammar_errors(user_id: str, errors: list[dict], source_text: str, session_id: str | None = None) -> None:
    rows = [{
        "user_id": user_id,
        "session_id": session_id,
        "source_text": source_text,
        "original_fragment": e.get("original", ""),
        "corrected_fragment": e.get("correction", ""),
        "rule_name": e.get("rule"),
        "explanation": e.get("explanation"),
    } for e in errors]
    if rows:
        admin_db().table("grammar_errors").insert(rows).execute()


def save_writing_attempt(user_id: str, original_text: str, purpose: str, result: dict, session_id: str | None = None) -> None:
    admin_db().table("writing_attempts").insert({
        "user_id": user_id,
        "session_id": session_id,
        "purpose": purpose,
        "original_text": original_text,
        "revised_text": result.get("revised_sample"),
        "score": result.get("score"),
        "estimated_level": result.get("estimated_level"),
        "feedback": result,
    }).execute()


def save_speaking_attempt(user_id: str, transcript: str, scenario: str, feedback: str, session_id: str | None = None) -> None:
    admin_db().table("speaking_attempts").insert({
        "user_id": user_id,
        "session_id": session_id,
        "scenario": scenario,
        "transcript": transcript,
        "feedback": {"text": feedback},
    }).execute()


def save_test_attempt(user_id: str, title: str, level: str, focus: str, score: int, correct: int, total: int, details: list[dict]) -> str:
    result = admin_db().table("test_attempts").insert({
        "user_id": user_id,
        "title": title,
        "level": level,
        "focus": focus,
        "score": score,
        "correct_count": correct,
        "total_count": total,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"details": details},
    }).execute()
    return result.data[0]["id"]


def add_vocabulary_word(user_id: str, item: dict, topic: str, level: str) -> None:
    admin_db().table("vocabulary_words").upsert({
        "user_id": user_id,
        "word": item.get("word", "").strip(),
        "part_of_speech": item.get("part_of_speech"),
        "definition": item.get("definition"),
        "example_sentence": item.get("example"),
        "collocation": item.get("collocation"),
        "topic": topic,
        "cefr_level": level,
        "status": "learning",
    }, on_conflict="user_id,word").execute()
