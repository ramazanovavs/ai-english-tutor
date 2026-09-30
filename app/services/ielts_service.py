from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.supabase_service import admin_db


def ensure_ielts_profile(user_id: str) -> dict:
    db = admin_db()
    result = db.table("ielts_profiles").select("*").eq("user_id", user_id).limit(1).execute()
    if result.data:
        return result.data[0]
    created = db.table("ielts_profiles").insert({"user_id": user_id}).execute()
    return created.data[0]


def update_ielts_profile(user_id: str, values: dict[str, Any]) -> dict:
    allowed = {"exam_type", "target_band", "planned_exam_date"}
    payload = {k: v for k, v in values.items() if k in allowed}
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = db = admin_db()
    db.table("ielts_profiles").update(payload).eq("user_id", user_id).execute()
    return ensure_ielts_profile(user_id)


def save_writing_attempt(user_id: str, test_type: str, task_number: int, prompt: str, response: str, result: dict) -> None:
    criterion_key = "task_achievement" if task_number == 1 else "task_response"
    admin_db().table("ielts_writing_attempts").insert({
        "user_id": user_id,
        "test_type": test_type,
        "task_number": task_number,
        "prompt": prompt,
        "response_text": response,
        "word_count": result.get("word_count"),
        "task_score": result.get(criterion_key),
        "coherence_cohesion": result.get("coherence_cohesion"),
        "lexical_resource": result.get("lexical_resource"),
        "grammatical_range_accuracy": result.get("grammatical_range_accuracy"),
        "estimated_band": result.get("overall_band"),
        "feedback": result,
    }).execute()
    _refresh_section_band(user_id, "writing")


def save_speaking_attempt(user_id: str, part: int, prompt: str, transcript: str, result: dict) -> None:
    admin_db().table("ielts_speaking_attempts").insert({
        "user_id": user_id,
        "part": part,
        "prompt": prompt,
        "transcript": transcript,
        "fluency_coherence": result.get("fluency_coherence"),
        "lexical_resource": result.get("lexical_resource"),
        "grammatical_range_accuracy": result.get("grammatical_range_accuracy"),
        "pronunciation": None,
        "provisional_language_band": result.get("provisional_language_band"),
        "feedback": result,
    }).execute()
    _refresh_section_band(user_id, "speaking")


def save_objective_attempt(user_id: str, section: str, test_type: str, title: str, score_percent: int, estimated_band: float, answers: dict, details: list[dict]) -> None:
    admin_db().table("ielts_objective_attempts").insert({
        "user_id": user_id,
        "section": section,
        "test_type": test_type,
        "title": title,
        "score_percent": score_percent,
        "estimated_band": estimated_band,
        "answers": answers,
        "details": details,
    }).execute()
    _refresh_section_band(user_id, section)


def get_ielts_dashboard(user_id: str) -> dict:
    db = admin_db()
    profile = ensure_ielts_profile(user_id)
    writing = db.table("ielts_writing_attempts").select("estimated_band,task_number,created_at").eq("user_id", user_id).order("created_at", desc=True).limit(10).execute()
    speaking = db.table("ielts_speaking_attempts").select("provisional_language_band,part,created_at").eq("user_id", user_id).order("created_at", desc=True).limit(10).execute()
    objective = db.table("ielts_objective_attempts").select("section,estimated_band,score_percent,created_at,title").eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
    history = db.table("ielts_band_history").select("section,band,source,created_at").eq("user_id", user_id).order("created_at", desc=True).limit(30).execute()
    bands = {
        "listening": profile.get("listening_band"),
        "reading": profile.get("reading_band"),
        "writing": profile.get("writing_band"),
        "speaking": profile.get("speaking_band"),
    }
    measured = {k: float(v) for k, v in bands.items() if v is not None}
    if not measured:
        recommendation = "Start with one practice activity in each IELTS section to build your baseline."
    else:
        weakest = min(measured, key=measured.get)
        gap = max(0.0, float(profile.get("target_band") or 6.5) - measured[weakest])
        recommendation = f"Focus next on {weakest.title()}: current practice estimate {measured[weakest]:.1f}, target {float(profile.get('target_band') or 6.5):.1f} (gap {gap:.1f})."
    return {
        "profile": profile,
        "writing_recent": writing.data or [],
        "speaking_recent": speaking.data or [],
        "objective_recent": objective.data or [],
        "band_history": history.data or [],
        "recommendation": recommendation,
    }


def _refresh_section_band(user_id: str, section: str) -> None:
    db = admin_db()
    if section == "writing":
        r = db.table("ielts_writing_attempts").select("estimated_band").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        vals = [float(x["estimated_band"]) for x in (r.data or []) if x.get("estimated_band") is not None]
    elif section == "speaking":
        r = db.table("ielts_speaking_attempts").select("provisional_language_band").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute()
        vals = [float(x["provisional_language_band"]) for x in (r.data or []) if x.get("provisional_language_band") is not None]
    else:
        r = db.table("ielts_objective_attempts").select("estimated_band").eq("user_id", user_id).eq("section", section).order("created_at", desc=True).limit(5).execute()
        vals = [float(x["estimated_band"]) for x in (r.data or []) if x.get("estimated_band") is not None]
    if not vals:
        return
    band = round((sum(vals) / len(vals)) * 2) / 2
    field = f"{section}_band"
    if field in {"listening_band", "reading_band", "writing_band", "speaking_band"}:
        db.table("ielts_profiles").update({field: band, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("user_id", user_id).execute()
        db.table("ielts_band_history").insert({
            "user_id": user_id,
            "section": section,
            "band": band,
            "source": "ai_practice_estimate",
        }).execute()
        _refresh_overall(user_id)


def _refresh_overall(user_id: str) -> None:
    db = admin_db()
    profile = ensure_ielts_profile(user_id)
    vals = [profile.get(k) for k in ("listening_band", "reading_band", "writing_band", "speaking_band")]
    vals = [float(v) for v in vals if v is not None]
    if len(vals) < 4:
        return
    avg = sum(vals) / 4
    # IELTS overall-band rounding: .25 -> .5 and .75 -> next whole band; otherwise nearest half.
    overall = round(avg * 2) / 2
    db.table("ielts_profiles").update({"current_overall_band": overall}).eq("user_id", user_id).execute()
