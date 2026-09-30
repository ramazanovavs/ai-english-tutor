from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.supabase_service import admin_db


def start_mock(user_id: str, test_type: str, target_band: float) -> dict:
    db = admin_db()
    row = db.table("ielts_mock_tests").insert({
        "user_id": user_id,
        "test_type": test_type,
        "status": "in_progress",
        "metadata": {
            "target_band": target_band,
            "current_section": "listening",
            "version": "full_mock_v1",
        },
    }).execute()
    return row.data[0]


def get_mock(user_id: str, mock_id: str) -> dict:
    db = admin_db()
    r = db.table("ielts_mock_tests").select("*").eq("id", mock_id).eq("user_id", user_id).limit(1).execute()
    if not r.data:
        raise ValueError("Mock test not found")
    mock = r.data[0]
    s = (
        db.table("ielts_mock_sections")
        .select("section,part,status,raw_score,total_questions,estimated_band,started_at,completed_at")
        .eq("mock_id", mock_id)
        .eq("user_id", user_id)
        .order("section")
        .order("part")
        .execute()
    )
    mock["sections"] = s.data or []
    return mock


def get_section(user_id: str, mock_id: str, section: str, part: int) -> dict | None:
    r = (
        admin_db().table("ielts_mock_sections").select("*")
        .eq("mock_id", mock_id).eq("user_id", user_id)
        .eq("section", section).eq("part", part).limit(1).execute()
    )
    return r.data[0] if r.data else None


def save_generated_section(user_id: str, mock_id: str, section: str, part: int, content: dict) -> dict:
    existing = get_section(user_id, mock_id, section, part)
    if existing:
        return existing
    r = admin_db().table("ielts_mock_sections").insert({
        "mock_id": mock_id,
        "user_id": user_id,
        "section": section,
        "part": part,
        "status": "ready",
        "content": content,
    }).execute()
    return r.data[0]


def start_section(user_id: str, mock_id: str, section: str, part: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    (
        admin_db().table("ielts_mock_sections")
        .update({"status": "in_progress", "started_at": now})
        .eq("mock_id", mock_id).eq("user_id", user_id)
        .eq("section", section).eq("part", part).execute()
    )


def complete_section(
    user_id: str, mock_id: str, section: str, part: int,
    answers: dict | None = None, raw_score: int | None = None,
    total_questions: int | None = None, estimated_band: float | None = None,
    result: dict | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if answers is not None:
        payload["answers"] = answers
    if raw_score is not None:
        payload["raw_score"] = raw_score
    if total_questions is not None:
        payload["total_questions"] = total_questions
    if estimated_band is not None:
        payload["estimated_band"] = estimated_band
    if result is not None:
        payload["result"] = result
    (
        admin_db().table("ielts_mock_sections").update(payload)
        .eq("mock_id", mock_id).eq("user_id", user_id)
        .eq("section", section).eq("part", part).execute()
    )


def update_mock_band(user_id: str, mock_id: str, section: str, band: float) -> None:
    field = f"{section}_band"
    if field not in {"listening_band","reading_band","writing_band","speaking_band"}:
        return
    admin_db().table("ielts_mock_tests").update({field: band}).eq("id", mock_id).eq("user_id", user_id).execute()


def set_mock_metadata(user_id: str, mock_id: str, values: dict) -> None:
    db = admin_db()
    r = db.table("ielts_mock_tests").select("metadata").eq("id", mock_id).eq("user_id", user_id).single().execute()
    metadata = dict(r.data.get("metadata") or {})
    metadata.update(values)
    db.table("ielts_mock_tests").update({"metadata": metadata}).eq("id", mock_id).eq("user_id", user_id).execute()


def finish_mock(user_id: str, mock_id: str, overall_band: float) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    db = admin_db()
    db.table("ielts_mock_tests").update({
        "status": "completed",
        "overall_band": overall_band,
        "completed_at": now,
    }).eq("id", mock_id).eq("user_id", user_id).execute()
    return get_mock(user_id, mock_id)
