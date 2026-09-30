from datetime import datetime, timezone

from app.services.supabase_service import admin_db


DEFAULT_SKILLS = [
    ("grammar", "Grammar"),
    ("vocabulary", "Vocabulary"),
    ("writing", "Writing"),
    ("speaking", "Speaking"),
    ("reading", "Reading"),
    ("listening", "Listening"),
]


def ensure_profile(user_id: str, email: str | None = None) -> None:
    db = admin_db()
    db.table("profiles").upsert(
        {"id": user_id, "email": email},
        on_conflict="id",
    ).execute()

    existing = db.table("student_skills").select("category").eq("user_id", user_id).execute()
    have = {row["category"] for row in (existing.data or [])}
    missing = [
        {
            "user_id": user_id,
            "category": category,
            "skill": label,
            "score": None,
            "level": None,
            "attempts": 0,
        }
        for category, label in DEFAULT_SKILLS
        if category not in have
    ]
    if missing:
        db.table("student_skills").insert(missing).execute()


def get_student_context(user_id: str) -> dict:
    db = admin_db()
    profile = db.table("profiles").select("*").eq("id", user_id).limit(1).execute()
    skills = (
        db.table("student_skills")
        .select("category,skill,score,level,attempts,last_practiced")
        .eq("user_id", user_id)
        .order("score")
        .execute()
    )
    recent = (
        db.table("learning_events")
        .select("activity_type,topic,score,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(8)
        .execute()
    )
    return {
        "profile": (profile.data or [{}])[0],
        "skills": skills.data or [],
        "recent": recent.data or [],
    }


def record_activity(
    user_id: str,
    activity_type: str,
    topic: str,
    score: int | None,
    metadata: dict | None = None,
) -> None:
    db = admin_db()
    db.table("learning_events").insert(
        {
            "user_id": user_id,
            "activity_type": activity_type,
            "topic": topic[:160],
            "score": score,
            "metadata": metadata or {},
        }
    ).execute()

    if score is None:
        return

    row = (
        db.table("student_skills")
        .select("*")
        .eq("user_id", user_id)
        .eq("category", activity_type)
        .limit(1)
        .execute()
    )
    if not row.data:
        return

    old = row.data[0]
    attempts = int(old.get("attempts") or 0)
    old_score = float(old["score"]) if old.get("score") is not None else float(score)
    # First assessed attempt becomes the first real score.
    # Later attempts use a capped smoothing window.
    if attempts == 0:
        new_score = round(score)
    else:
        weight = min(attempts, 4)
        new_score = round((old_score * weight + score) / (weight + 1))
    level = score_to_cefr(new_score)

    (
        db.table("student_skills")
        .update(
            {
                "score": new_score,
                "level": level,
                "attempts": attempts + 1,
                "last_practiced": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", old["id"])
        .execute()
    )

    # Determine an overall CEFR only after there is enough evidence.
    # Until at least 3 different skill categories have been assessed,
    # the overall level remains NULL / "Not assessed".
    all_skills = (
        db.table("student_skills")
        .select("score,attempts")
        .eq("user_id", user_id)
        .execute()
    )
    assessed_scores = [
        float(x["score"])
        for x in (all_skills.data or [])
        if int(x.get("attempts") or 0) > 0 and x.get("score") is not None
    ]

    if len(assessed_scores) >= 3:
        overall = round(sum(assessed_scores) / len(assessed_scores))
        overall_level = score_to_cefr(overall)
    else:
        overall_level = None

    db.table("profiles").update(
        {"cefr_level": overall_level}
    ).eq("id", user_id).execute()


def score_to_cefr(score: float) -> str:
    if score < 25:
        return "A1"
    if score < 40:
        return "A2"
    if score < 58:
        return "B1"
    if score < 72:
        return "B2"
    if score < 86:
        return "C1"
    return "C2"
