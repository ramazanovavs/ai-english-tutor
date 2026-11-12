"""
Backup bank of ready-made Listening tests (script + 10 questions + MP3).

* Up to BANK_PER_LEVEL tests per CEFR level, mixed formats.
* Filled automatically: a successfully voiced learner test is copied into the bank while
  its level has fewer than BANK_PER_LEVEL tests. An admin can also fill all levels at once.
* Used ONLY when OpenAI is unavailable (generation or speech fails).
* Test content lives in the table public.listening_bank; MP3 files live in the private
  Supabase Storage bucket "listening-audio" (not on Render, whose disk is temporary).
"""

import logging
import threading
import uuid

from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)

BUCKET = "listening-audio"
TABLE = "listening_bank"
BANK_PER_LEVEL = 5
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
FORMAT_CYCLE = ["dialogue", "monologue", "announcement", "dialogue", "monologue"]
FILL_TOPICS = {
    "A1": ["at a café", "my family", "shopping for clothes", "a new classmate", "the weather today"],
    "A2": ["booking a table", "weekend plans", "at the train station", "a lost bag", "a visit to the doctor"],
    "B1": ["choosing a university course", "a part-time job", "a museum tour", "planning a trip", "a library service"],
    "B2": ["remote work", "a city transport change", "a volunteering project", "healthy habits at university", "online shopping problems"],
    "C1": ["AI in education", "urban green spaces", "a research seminar", "consumer behaviour", "workplace motivation"],
    "C2": ["ethics of data collection", "language and identity", "the future of cities", "scientific peer review", "economic inequality"],
}

_save_lock = threading.Lock()
_fill_state = {"running": False, "done": 0, "failed": 0, "message": ""}


def _storage():
    return admin_db().storage.from_(BUCKET)


def counts() -> dict[str, int]:
    rows = admin_db().table(TABLE).select("level").execute().data or []
    result = {lvl: 0 for lvl in LEVELS}
    for r in rows:
        if r.get("level") in result:
            result[r["level"]] += 1
    return result


def save_if_needed(test: dict, audio: bytes) -> bool:
    """Copy a voiced test into the bank if its level still has room."""
    level = test.get("level")
    if level not in LEVELS or not audio or test.get("bank_id"):
        return False
    with _save_lock:
        if counts().get(level, 0) >= BANK_PER_LEVEL:
            return False
        path = f"{level}/{uuid.uuid4()}.mp3"
        _storage().upload(path, audio, {"content-type": "audio/mpeg", "upsert": "true"})
        content = {k: test[k] for k in ("title", "speakers", "script", "questions", "level", "format", "topic") if k in test}
        try:
            admin_db().table(TABLE).insert({
                "level": level,
                "format": test.get("format"),
                "topic": test.get("topic") or "",
                "title": test.get("title"),
                "content": content,
                "audio_path": path,
                "size_bytes": len(audio),
            }).execute()
        except Exception:
            _storage().remove([path])
            raise
    logger.info("Saved listening test to backup bank: %s", path)
    return True


def used_bank_ids(user_id: str) -> set[str]:
    rows = (
        admin_db().table("tests")
        .select("questions")
        .eq("user_id", user_id)
        .eq("test_type", "listening_bank")
        .execute()
    ).data or []
    return {str((r.get("questions") or {}).get("bank_id")) for r in rows if isinstance(r.get("questions"), dict)}


def pick(level: str, fmt: str, user_id: str) -> dict | None:
    """A bank test of this level the learner has not had yet; same format preferred."""
    rows = admin_db().table(TABLE).select("*").eq("level", level).execute().data or []
    seen = used_bank_ids(user_id)
    fresh = [r for r in rows if str(r["id"]) not in seen]
    if not fresh:
        return None
    fresh.sort(key=lambda r: (r.get("format") != fmt, int(r.get("times_used") or 0)))
    return fresh[0]


def download(path: str) -> bytes:
    return _storage().download(path)


def mark_used(row: dict) -> None:
    try:
        admin_db().table(TABLE).update(
            {"times_used": int(row.get("times_used") or 0) + 1}
        ).eq("id", row["id"]).execute()
    except Exception:
        logger.exception("Could not update times_used")


# ---------------------------------------------------------------- admin fill

def fill_status() -> dict:
    return {**_fill_state, "counts": counts(), "per_level": BANK_PER_LEVEL}


def start_fill(generate, build_audio) -> bool:
    """Fill every level up to BANK_PER_LEVEL in a background thread."""
    if _fill_state["running"]:
        return False
    _fill_state.update(running=True, done=0, failed=0, message="Starting…")

    def job():
        try:
            for level in LEVELS:
                failures_here = 0
                while counts()[level] < BANK_PER_LEVEL and failures_here < 3:
                    n = counts()[level]
                    fmt = FORMAT_CYCLE[n % len(FORMAT_CYCLE)]
                    topic = FILL_TOPICS[level][n % len(FILL_TOPICS[level])]
                    _fill_state["message"] = f"{level}: creating test {n + 1} of {BANK_PER_LEVEL} ({fmt}, {topic})"
                    try:
                        test = generate(level, topic, fmt)
                        test.update(level=level, format=fmt, topic=topic)
                        save_if_needed(test, build_audio(test))
                        _fill_state["done"] += 1
                    except Exception as exc:
                        failures_here += 1
                        _fill_state["failed"] += 1
                        logger.exception("Bank fill failed for %s", level)
                        _fill_state["message"] = f"{level}: error — {exc}"
            _fill_state["message"] = "Finished."
        finally:
            _fill_state["running"] = False

    threading.Thread(target=job, daemon=True).start()
    return True
