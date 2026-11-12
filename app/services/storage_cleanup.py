"""
Automatic deletion of old audio in Supabase Storage (bucket "listening-audio").

Deleted after RETENTION_DAYS:
* full mock: the four Listening recordings and the candidate's Speaking answers
  (completed or abandoned mocks, and mocks left unfinished for that long);
* practice Speaking: the candidate's answers.
Never deleted: the IELTS bank and the Listening bank (they have their own copies), stored examiner
phrases, and recordings whose assessment is still waiting for the AI service.
Texts, transcripts, scores and reviews stay in the database.

The free Render instance has no scheduler, so the cleanup runs in the background at most every
CHECK_HOURS, triggered by ordinary requests, and an admin can start it by hand.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)

BUCKET = "listening-audio"
RETENTION_DAYS = 30
CHECK_HOURS = 12

_state = {"last_run": 0.0, "running": False, "last_result": None}
_lock = threading.Lock()


def _cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()


def _remove(paths: list[str]) -> int:
    paths = [p for p in paths if p]
    if not paths:
        return 0
    for i in range(0, len(paths), 100):
        admin_db().storage.from_(BUCKET).remove(paths[i:i + 100])
    return len(paths)


def _mock_paths(mock: dict) -> list[str]:
    meta = mock.get("metadata") or {}
    paths = [f"ielts-mock/{mock['id']}/part{p}.mp3" for p in (1, 2, 3, 4)]
    for t in (meta.get("speaking_session") or {}).get("turns") or []:
        if t.get("audio_path"):
            paths.append(t["audio_path"])
    rows = admin_db().table("ielts_mock_sections").select("answers").eq("mock_id", mock["id"]) \
        .eq("section", "speaking").execute().data or []
    for r in rows:
        paths += list((r.get("answers") or {}).get("audio") or [])
    return sorted(set(paths))


def run() -> dict:
    """Delete old recordings now. Returns counts."""
    cutoff = _cutoff()
    result = {"mocks": 0, "practice_sessions": 0, "files": 0, "skipped_pending": 0, "errors": 0}

    mocks = admin_db().table("ielts_mock_tests").select("id,status,metadata,started_at,completed_at") \
        .lt("started_at", cutoff).execute().data or []
    for m in mocks:
        meta = m.get("metadata") or {}
        if meta.get("storage_cleaned"):
            continue
        if meta.get("writing_assessment") in ("pending", "running", "error") or \
                meta.get("speaking_assessment") in ("pending", "running"):
            result["skipped_pending"] += 1  # the recordings are still needed for the assessment
            continue
        finished = m.get("completed_at") or m.get("started_at")
        if finished and finished > cutoff:
            continue
        try:
            result["files"] += _remove(_mock_paths(m))
            meta["storage_cleaned"] = datetime.now(timezone.utc).isoformat()
            admin_db().table("ielts_mock_tests").update({"metadata": meta}).eq("id", m["id"]).execute()
            result["mocks"] += 1
        except Exception:
            logger.exception("Could not clean mock %s", m["id"])
            result["errors"] += 1

    sessions = admin_db().table("tests").select("id,questions,created_at").eq("test_type", "ielts_speaking_session") \
        .lt("created_at", cutoff).execute().data or []
    for s in sessions:
        content = s.get("questions") or {}
        if content.get("audio_cleaned"):
            continue
        turns = (content.get("session") or {}).get("turns") or []
        try:
            result["files"] += _remove([t.get("audio_path") for t in turns])
            content["audio_cleaned"] = datetime.now(timezone.utc).isoformat()
            admin_db().table("tests").update({"questions": content}).eq("id", s["id"]).execute()
            result["practice_sessions"] += 1
        except Exception:
            logger.exception("Could not clean speaking session %s", s["id"])
            result["errors"] += 1

    result["ran_at"] = datetime.now(timezone.utc).isoformat()
    result["retention_days"] = RETENTION_DAYS
    return result


def maybe_run_in_background() -> None:
    """Called on ordinary requests; starts a cleanup if the last one was long enough ago."""
    with _lock:
        if _state["running"] or time.time() - _state["last_run"] < CHECK_HOURS * 3600:
            return
        _state["running"] = True
        _state["last_run"] = time.time()

    def job():
        try:
            _state["last_result"] = run()
        except Exception:
            logger.exception("Storage cleanup failed")
        finally:
            _state["running"] = False
    threading.Thread(target=job, daemon=True).start()


def status() -> dict:
    return {"running": _state["running"], "last_result": _state["last_result"],
            "retention_days": RETENTION_DAYS, "check_hours": CHECK_HOURS}
