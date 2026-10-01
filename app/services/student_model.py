from datetime import datetime, timezone
from statistics import median_low

from app.services.supabase_service import admin_db


DEFAULT_SKILLS = [
    ("grammar", "Grammar"),
    ("vocabulary", "Vocabulary"),
    ("writing", "Writing"),
    ("speaking", "Speaking"),
    ("reading", "Reading"),
    ("listening", "Listening"),
]

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
SKILL_CATEGORIES = {x[0] for x in DEFAULT_SKILLS}


def _valid_level(level: str | None) -> str | None:
    if not level:
        return None
    level = str(level).upper().strip()
    return level if level in CEFR_LEVELS else None


def mastery_from_score(score: int | float | None) -> str | None:
    if score is None:
        return None
    score = float(score)
    if score < 70:
        return "developing"
    if score < 85:
        return "proficient"
    return "strong"


def next_cefr(level: str | None) -> str | None:
    level = _valid_level(level)
    if not level:
        return None
    idx = CEFR_LEVELS.index(level)
    return CEFR_LEVELS[idx + 1] if idx < len(CEFR_LEVELS) - 1 else None


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
            "mastery_status": None,
            "last_tested_level": None,
            "last_test_score": None,
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
        .select(
            "category,skill,score,level,mastery_status,attempts,"
            "last_tested_level,last_test_score,evidence_count,confidence,last_practiced"
        )
        .eq("user_id", user_id)
        .order("skill")
        .execute()
    )
    recent = (
        db.table("learning_events")
        .select("activity_type,topic,score,metadata,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    return {
        "profile": (profile.data or [{}])[0],
        "skills": skills.data or [],
        "recent": recent.data or [],
    }


def _recalculate_overall_cefr(user_id: str) -> None:
    """
    Overall CEFR is conservative:
    - only skills with a real CEFR evidence level count;
    - only proficient/strong evidence counts as "confirmed";
    - at least 3 different assessed skills are required;
    - use the lower median CEFR level, so one stronger skill cannot inflate the profile.
    """
    db = admin_db()
    rows = (
        db.table("student_skills")
        .select("level,mastery_status,attempts")
        .eq("user_id", user_id)
        .execute()
    ).data or []

    confirmed = [
        CEFR_LEVELS.index(row["level"])
        for row in rows
        if row.get("level") in CEFR_LEVELS
        and row.get("mastery_status") in {"proficient", "strong"}
        and int(row.get("attempts") or 0) > 0
    ]

    overall_level = None
    if len(confirmed) >= 3:
        overall_level = CEFR_LEVELS[int(median_low(confirmed))]

    db.table("profiles").update(
        {"cefr_level": overall_level}
    ).eq("id", user_id).execute()


def record_activity(
    user_id: str,
    activity_type: str,
    topic: str,
    score: int | None,
    metadata: dict | None = None,
    *,
    tested_level: str | None = None,
    evidence_source: str | None = None,
    update_skill: bool = True,
) -> None:
    """
    Store an activity and, when enough evidence exists, update the skill model.

    Important: score is NOT converted into CEFR.
    CEFR evidence comes from tested_level (explicit test level or an AI-estimated
    language level for free production tasks).
    """
    db = admin_db()
    metadata = dict(metadata or {})
    tested_level = _valid_level(tested_level or metadata.get("tested_level") or metadata.get("level"))

    if tested_level:
        metadata["tested_level"] = tested_level
    if evidence_source:
        metadata["evidence_source"] = evidence_source

    activity_mastery = mastery_from_score(score)
    if activity_mastery:
        metadata["mastery_status"] = activity_mastery

    db.table("learning_events").insert(
        {
            "user_id": user_id,
            "activity_type": activity_type,
            "topic": topic[:160],
            "score": score,
            "metadata": metadata,
        }
    ).execute()

    # Non-assessment events (generation, transcription, IELTS bridge until tested)
    # remain in history but must not alter the general CEFR student model.
    if (
        not update_skill
        or score is None
        or activity_type not in SKILL_CATEGORIES
        or not tested_level
    ):
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
    old_level = _valid_level(old.get("level"))
    attempts = int(old.get("attempts") or 0)
    old_score = old.get("score")
    old_score = float(old_score) if old_score is not None else None

    tested_idx = CEFR_LEVELS.index(tested_level)
    old_idx = CEFR_LEVELS.index(old_level) if old_level else None

    new_level = old_level
    new_score = old_score
    new_mastery = old.get("mastery_status")

    if old_level is None:
        # First actual evidence: report the level that was assessed, with a
        # mastery status that makes weak evidence visible rather than "promoting".
        new_level = tested_level
        new_score = round(score)
        new_mastery = activity_mastery

    elif tested_level == old_level:
        # Repeated evidence at the same CEFR level may update the score smoothly.
        if old_score is None:
            new_score = round(score)
        else:
            weight = min(max(attempts, 1), 4)
            new_score = round((old_score * weight + score) / (weight + 1))
        new_mastery = mastery_from_score(new_score)

    elif tested_idx == old_idx + 1:
        # Promotion is only possible by demonstrating the immediately next CEFR.
        if score >= 70:
            new_level = tested_level
            new_score = round(score)
            new_mastery = activity_mastery
        # If the next-level test is not passed, keep the confirmed lower-level
        # card score/level. The failed/harder evidence remains in last_test_*.

    elif tested_idx < old_idx:
        # A lower-level exercise never downgrades an already demonstrated level.
        pass

    else:
        # A jump over one or more CEFR bands is not accepted as a promotion.
        # The evidence is logged and can be used to recommend the missing next level.
        pass

    evidence_count = int(old.get("evidence_count") or 0) + 1
    confidence = min(100, evidence_count * 20)

    db.table("student_skills").update(
        {
            "score": new_score,
            "level": new_level,
            "mastery_status": new_mastery,
            "attempts": attempts + 1,
            "last_tested_level": tested_level,
            "last_test_score": round(score),
            "evidence_count": evidence_count,
            "confidence": confidence,
            "last_practiced": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", old["id"]).execute()

    _recalculate_overall_cefr(user_id)
