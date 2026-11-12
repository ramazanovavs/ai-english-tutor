"""
IELTS bank of ready-made content (table public.ielts_bank, audio in the "listening-audio" bucket).

* Full mock papers: up to MOCK_LIMIT per test type (Academic / General Training).
* Practice sets: up to PRACTICE_LIMIT per key (e.g. "reading:Academic", "speaking:2").
* Filled automatically from successful generations; used when OpenAI is unavailable and,
  for mocks, as a source of admin-verified exams. A learner never gets the same item twice.
* Answer statistics help the admin to spot a wrong answer key.
"""

from __future__ import annotations

import logging
import threading
import uuid

from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)

TABLE = "ielts_bank"
USAGE = "ielts_bank_usage"
BUCKET = "listening-audio"
MOCK_LIMIT = 5
PRACTICE_LIMIT = 10
SUSPICIOUS_MIN_ANSWERS = 3
SUSPICIOUS_MAX_SHARE = 0.2

_lock = threading.Lock()


def mock_key(test_type: str) -> str:
    return f"mock:{'General Training' if str(test_type).lower().startswith('general') else 'Academic'}"


def _storage():
    return admin_db().storage.from_(BUCKET)


def count(key: str) -> int:
    return len(admin_db().table(TABLE).select("id").eq("bank_key", key).execute().data or [])


def save(kind: str, key: str, title: str, content: dict, audio: dict[str, bytes] | None = None,
         source_id: str | None = None) -> str | None:
    """Store an item if its key still has room. Returns the new id or None."""
    limit = MOCK_LIMIT if kind == "mock" else PRACTICE_LIMIT
    with _lock:
        if count(key) >= limit:
            return None
        item_id = str(uuid.uuid4())
        paths = {}
        for name, data in (audio or {}).items():
            if not data:
                continue
            path = f"ielts-bank/{item_id}/{name}.mp3"
            _storage().upload(path, data, {"content-type": "audio/mpeg", "upsert": "true"})
            paths[str(name)] = path
        admin_db().table(TABLE).insert({
            "id": item_id, "kind": kind, "bank_key": key, "title": title or key,
            "content": content, "audio": paths, "source_id": source_id,
        }).execute()
    logger.info("Saved %s to the IELTS bank (%s)", key, item_id)
    return item_id


def get(item_id: str) -> dict | None:
    rows = admin_db().table(TABLE).select("*").eq("id", item_id).limit(1).execute().data
    return rows[0] if rows else None


def list_items() -> list[dict]:
    rows = admin_db().table(TABLE).select("id,kind,bank_key,title,verified,times_used,stats,created_at,audio").execute().data or []
    for r in rows:
        r["suspicious"] = suspicious(r)
        r.pop("stats", None)
    return sorted(rows, key=lambda r: (r["bank_key"], str(r.get("created_at"))))


def used_ids(user_id: str) -> set[str]:
    rows = admin_db().table(USAGE).select("bank_id").eq("user_id", user_id).execute().data or []
    return {str(r["bank_id"]) for r in rows}


def pick(key: str, user_id: str, verified_only: bool = False) -> dict | None:
    """An item the learner has not had yet: verified first, then the least used."""
    rows = admin_db().table(TABLE).select("*").eq("bank_key", key).execute().data or []
    seen = used_ids(user_id)
    rows = [r for r in rows if str(r["id"]) not in seen and (r.get("verified") or not verified_only)]
    if not rows:
        return None
    rows.sort(key=lambda r: (not r.get("verified"), int(r.get("times_used") or 0)))
    return rows[0]


def mark_used(user_id: str, item: dict) -> None:
    try:
        admin_db().table(USAGE).insert({"user_id": user_id, "bank_id": item["id"]}).execute()
        admin_db().table(TABLE).update({"times_used": int(item.get("times_used") or 0) + 1}).eq("id", item["id"]).execute()
    except Exception:
        logger.exception("Could not record bank usage")


def load_audio(path: str) -> bytes | None:
    try:
        return _storage().download(path)
    except Exception:
        logger.exception("Bank audio not found: %s", path)
        return None


def set_verified(item_id: str, verified: bool) -> None:
    admin_db().table(TABLE).update({"verified": bool(verified)}).eq("id", item_id).execute()


def delete(item_id: str) -> None:
    item = get(item_id)
    if not item:
        return
    paths = list((item.get("audio") or {}).values())
    if paths:
        try:
            _storage().remove(paths)
        except Exception:
            logger.exception("Could not delete bank audio")
    admin_db().table(TABLE).delete().eq("id", item_id).execute()


def record_stats(item_id: str, details_by_part: dict[str, list[dict]]) -> None:
    """Count right/wrong answers per question of a bank item."""
    with _lock:
        item = get(item_id)
        if not item:
            return
        stats = dict(item.get("stats") or {})
        for part, details in details_by_part.items():
            for d in details:
                key = f"{part}:{d.get('id')}"
                right, total = stats.get(key, [0, 0])
                stats[key] = [right + int(bool(d.get("correct"))), total + int(bool(str(d.get("answer") or "").strip()))]
        admin_db().table(TABLE).update({"stats": stats}).eq("id", item_id).execute()


def suspicious(item: dict) -> list[str]:
    """Questions that almost nobody answers 'correctly' — the key may be wrong."""
    out = []
    for key, (right, total) in (item.get("stats") or {}).items():
        if total >= SUSPICIOUS_MIN_ANSWERS and right / total <= SUSPICIOUS_MAX_SHARE:
            out.append(key)
    return sorted(out)
