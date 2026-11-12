"""
Learner roadmap storage and logic: sublevels per skill, item statuses (can-do statements) and the IELTS bridge.
One row per learner in public.learner_roadmap (server-side only, RLS without policies).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median_low

from app.data import roadmap as rm
from app.services.supabase_service import admin_db

TABLE = "learner_roadmap"
CONFIRM_SCORE = 70           # objective practice score needed to confirm a can-do statement
_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def lock(user_id: str) -> threading.Lock:
    return _locks[user_id]


def load(user_id: str) -> dict:
    rows = admin_db().table(TABLE).select("*").eq("user_id", user_id).limit(1).execute().data or []
    if rows:
        row = rows[0]
        for key in ("levels", "items", "placement"):
            row[key] = row.get(key) or {}
        return row
    row = {"user_id": user_id, "levels": {}, "items": {}, "placement": {}}
    admin_db().table(TABLE).upsert(row, on_conflict="user_id").execute()
    return row


def save(user_id: str, **fields) -> None:
    fields["updated_at"] = now_iso()
    admin_db().table(TABLE).update(fields).eq("user_id", user_id).execute()


def overall(levels: dict) -> str | None:
    idx = [rm.sub_index(levels.get(s)) for s in rm.SKILLS if rm.sub_index(levels.get(s)) >= 0]
    if not idx:
        return None
    return rm.SUBLEVELS[median_low(idx)]


def apply_placement_items(items: dict, levels: dict) -> dict:
    """Everything below the placed sublevel of a skill counts as shown by the placement test."""
    items = dict(items)
    for it in rm.ITEMS:
        placed = rm.sub_index(levels.get(it["skill"]))
        if placed > rm.sub_index(it["sub"]) and (items.get(it["id"]) or {}).get("status") != "confirmed":
            items[it["id"]] = {"status": "confirmed", "source": "placement", "at": now_iso()}
    return items


def record_evidence(user_id: str, item_id: str, module: str, score: float | None, level: str | None) -> dict:
    item = rm.ITEMS_BY_ID.get(item_id)
    if not item:
        raise KeyError(item_id)
    main = rm.main_level(item["sub"])
    if item["skill"] in ("writing", "speaking"):
        lv = str(level or "").upper()[:2]
        ok = lv in rm.LEVEL_SUMMARY and list(rm.LEVEL_SUMMARY).index(lv) >= list(rm.LEVEL_SUMMARY).index(main) \
            and (score is None or float(score) >= 60)
    else:
        ok = score is not None and float(score) >= CONFIRM_SCORE
    with lock(user_id):
        row = load(user_id)
        items = row["items"]
        old = items.get(item_id) or {}
        entry = {"status": "confirmed" if ok or old.get("status") == "confirmed" else "practised",
                 "source": "practice" if ok else old.get("source", "practice"),
                 "score": round(float(score)) if score is not None else None, "level": level,
                 "attempts": int(old.get("attempts") or 0) + 1, "at": now_iso()}
        items[item_id] = entry
        save(user_id, items=items)
    return {"item_id": item_id, "confirmed": entry["status"] == "confirmed", **entry}


def view(user_id: str, ielts_band: float | None = None) -> dict:
    row = load(user_id)
    levels, items = row["levels"], row["items"]
    current = levels.get("overall")
    progress = {}
    for sub in rm.SUBLEVELS:
        subset = rm.items_for(sub)
        done = sum(1 for it in subset if (items.get(it["id"]) or {}).get("status") == "confirmed")
        progress[sub] = {"confirmed": done, "total": len(subset), "percent": round(done / len(subset) * 100)}
    nxt = None
    if current:
        i = rm.sub_index(current)
        nxt = rm.SUBLEVELS[i + 1] if i + 1 < len(rm.SUBLEVELS) else None
    placement = row["placement"] or {}
    return {
        "levels": levels,
        "current": current,
        "next": nxt,
        "sublevels": rm.SUBLEVELS,
        "skills": rm.SKILLS,
        "skill_names": rm.SKILL_NAMES,
        "level_summary": rm.LEVEL_SUMMARY,
        "items": rm.ITEMS,
        "status": items,
        "progress": progress,
        "ielts": {"bridge": rm.IELTS_BRIDGE, "estimate": rm.IELTS_BRIDGE.get(current) if current else None,
                  "current_band": ielts_band},
        "placement": {"status": placement.get("status"), "stage": placement.get("stage"),
                      "finished_at": placement.get("finished_at")},
        "placed_at": row.get("placed_at"),
    }
