"""
Stage 2 of the learning path:
- spaced repetition of saved words (SM-2: intervals grow after every successful recall);
- "My mistakes": language errors from Grammar Coach, Writing Coach and IELTS Writing are collected and
  re-tested a day later in new sentences; an error correctly fixed in a re-test is marked resolved;
- "Today": a short daily plan built from due words, due mistakes and the next roadmap skill.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from app.data import roadmap as rm
from app.services.supabase_service import admin_db
from app.services.openai_service import ask_json
from app.agents.ielts_rubrics import DATA_GUARD, language_rule

logger = logging.getLogger(__name__)

MISTAKE_DELAY_HOURS = 20       # a mistake comes back for practice the next day, not straight away
MASTERED_DAYS = 21


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _today_start() -> datetime:
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------- spaced repetition
def sm2(row: dict, quality: int) -> dict:
    """SM-2 update. quality: 1 = forgot, 3 = hard, 4 = good, 5 = easy."""
    quality = max(0, min(5, int(quality)))
    ease = float(row.get("ease_factor") or 2.5)
    reps = int(row.get("repetitions") or 0)
    interval = int(row.get("interval_days") or 0)
    if quality < 3:
        reps, interval = 0, 1
    else:
        reps += 1
        interval = 1 if reps == 1 else 3 if reps == 2 else max(interval + 1, round(interval * ease))
        if quality == 5 and reps <= 2:
            interval += 1
    ease = max(1.3, round(ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 2))
    now = _now()
    return {
        "ease_factor": ease, "repetitions": reps, "interval_days": interval,
        "last_reviewed_at": now.isoformat(), "next_review_at": (now + timedelta(days=interval)).isoformat(),
        "status": "mastered" if interval >= MASTERED_DAYS else "review" if reps >= 2 else "learning",
        "correct_count": int(row.get("correct_count") or 0) + int(quality >= 3),
        "wrong_count": int(row.get("wrong_count") or 0) + int(quality < 3),
    }


def _words(user_id: str) -> list[dict]:
    return admin_db().table("vocabulary_words").select("*").eq("user_id", user_id).execute().data or []


def due_words(user_id: str, limit: int = 20) -> tuple[list[dict], int]:
    now = _now()
    due = [w for w in _words(user_id) if (_parse(w.get("next_review_at")) or now) <= now]
    # overdue first, then never-reviewed words in the order they were added
    due.sort(key=lambda w: (w.get("next_review_at") is None, str(w.get("next_review_at") or w.get("created_at") or "")))
    fields = ("id", "word", "part_of_speech", "definition", "translation", "example_sentence", "collocation",
              "topic", "cefr_level", "repetitions", "interval_days")
    return [{k: w.get(k) for k in fields} for w in due[:limit]], len(due)


def review_word(user_id: str, word_id: str, quality: int) -> dict:
    db = admin_db()
    rows = db.table("vocabulary_words").select("*").eq("id", word_id).eq("user_id", user_id).limit(1).execute().data
    if not rows:
        raise KeyError(word_id)
    update = sm2(rows[0], quality)
    db.table("vocabulary_words").update(update).eq("id", word_id).execute()
    try:
        db.table("vocabulary_reviews").insert({"user_id": user_id, "vocabulary_id": word_id, "quality": int(quality),
                                               "correct": int(quality) >= 3}).execute()
    except Exception:
        logger.exception("Could not log a vocabulary review")
    return {"id": word_id, "next_review_at": update["next_review_at"], "interval_days": update["interval_days"],
            "status": update["status"]}


# ---------------------------------------------------------------- my mistakes
def _norm(s) -> str:
    s = str(s or "").lower().replace("’", "'").strip()
    s = re.sub(r"[^\w\s']", " ", s)
    return " ".join(s.split())


def save_errors(user_id: str, errors: list[dict] | None, source_text: str = "", source: str = "") -> int:
    """Store real language errors (original != correction); skips duplicates that are still unresolved."""
    errors = [e for e in (errors or []) if isinstance(e, dict)]
    if not errors:
        return 0
    db = admin_db()
    try:
        open_rows = db.table("grammar_errors").select("original_fragment,resolved").eq("user_id", user_id) \
            .eq("resolved", False).execute().data or []
        seen = {_norm(r.get("original_fragment")) for r in open_rows}
        rows = []
        for e in errors[:8]:
            original = str(e.get("original") or "").strip()[:300]
            correction = str(e.get("correction") or "").strip()[:300]
            if not original or not correction or _norm(original) == _norm(correction) or _norm(original) in seen:
                continue
            seen.add(_norm(original))
            rows.append({"user_id": user_id, "source_text": (source_text or "")[:2000], "resolved": False,
                         "original_fragment": original, "corrected_fragment": correction,
                         "rule_name": (str(e.get("rule") or e.get("category") or source or "")[:120]) or None,
                         "explanation": str(e.get("explanation") or "")[:500] or None})
        if rows:
            db.table("grammar_errors").insert(rows).execute()
        return len(rows)
    except Exception:
        logger.exception("Could not save learner errors")
        return 0


def mistakes(user_id: str) -> dict:
    rows = admin_db().table("grammar_errors").select("*").eq("user_id", user_id).execute().data or []
    open_rows = [r for r in rows if not r.get("resolved")]
    cutoff = _now() - timedelta(hours=MISTAKE_DELAY_HOURS)
    due = [r for r in open_rows if (_parse(r.get("created_at")) or _now()) <= cutoff]
    fields = ("id", "original_fragment", "corrected_fragment", "rule_name", "explanation", "created_at")
    open_rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"open": [{k: r.get(k) for k in fields} for r in open_rows[:60]],
            "open_count": len(open_rows), "due_count": len(due),
            "resolved_count": sum(1 for r in rows if r.get("resolved"))}


PRACTICE_PROMPT = """
You are an English teacher. For EACH learner error below write ONE new gap-fill sentence that tests the same
rule in a different context (do not reuse the learner's sentence). The gap is written as ___ and needs 1-3 words.
Return JSON: {{"items": [{{"error_id": "<id>", "sentence": "...___...", "answer": ["accepted answer", ...],
"rule": "short rule name", "explanation": "one sentence"}}]}}
List every correct variant in "answer" (e.g. with and without a contraction). {lang}
"""


def mistakes_practice(user_id: str, lang: str = "en", size: int = 6) -> dict:
    data = mistakes(user_id)
    rows = admin_db().table("grammar_errors").select("*").eq("user_id", user_id).eq("resolved", False).execute().data or []
    cutoff = _now() - timedelta(hours=MISTAKE_DELAY_HOURS)
    due = sorted([r for r in rows if (_parse(r.get("created_at")) or _now()) <= cutoff],
                 key=lambda r: str(r.get("created_at") or ""))[:size]
    if not due:
        raise LookupError("Nothing to practise yet: mistakes come back for practice the day after they were made.")
    listing = "\n".join(f"id={r['id']} | wrong: {r.get('original_fragment')} | correct: {r.get('corrected_fragment')} "
                        f"| rule: {r.get('rule_name') or ''}" for r in due)
    out = ask_json(PRACTICE_PROMPT.format(lang=language_rule(lang) + " " + DATA_GUARD), listing)
    ids = {str(r["id"]) for r in due}
    items = []
    for it in out.get("items") or []:
        answers = it.get("answer") if isinstance(it.get("answer"), list) else [it.get("answer")]
        answers = [str(a) for a in answers if str(a or "").strip()]
        if str(it.get("error_id")) in ids and "___" in str(it.get("sentence") or "") and answers:
            items.append({"n": len(items) + 1, "error_id": str(it["error_id"]), "sentence": str(it["sentence"]),
                          "answer": answers, "rule": it.get("rule"), "explanation": it.get("explanation")})
    if not items:
        raise ValueError("The practice could not be created.")
    row = admin_db().table("tests").insert({"user_id": user_id, "title": "My mistakes", "test_type": "mistakes_practice",
                                           "focus": "grammar", "level": None, "questions": {"items": items}}).execute()
    return {"test_id": row.data[0]["id"], "total_due": data["due_count"],
            "items": [{"n": i["n"], "sentence": i["sentence"], "rule": i["rule"]} for i in items]}


def submit_mistakes(user_id: str, test_id: str, answers: dict) -> dict:
    db = admin_db()
    rows = db.table("tests").select("questions,test_type").eq("id", test_id).eq("user_id", user_id).limit(1).execute().data
    if not rows or rows[0].get("test_type") != "mistakes_practice":
        raise KeyError(test_id)
    items = (rows[0]["questions"] or {}).get("items") or []
    details, resolved = [], 0
    for it in items:
        given = str(answers.get(str(it["n"]), "")).strip()
        ok = _norm(given) in {_norm(a) for a in it["answer"]}
        if ok:
            db.table("grammar_errors").update({"resolved": True, "resolved_at": _now().isoformat()}) \
                .eq("id", it["error_id"]).eq("user_id", user_id).execute()
            resolved += 1
        details.append({"n": it["n"], "answer": given, "correct": ok, "expected": it["answer"][0],
                        "explanation": it.get("explanation"), "rule": it.get("rule")})
    return {"correct": resolved, "total": len(items), "resolved": resolved, "details": details}


# ---------------------------------------------------------------- today
def next_roadmap_item(levels: dict, statuses: dict, day: int) -> dict | None:
    """The weakest measured skill first; its first unconfirmed statement from its current sublevel upwards."""
    measured = [s for s in rm.SKILLS if rm.sub_index(levels.get(s)) >= 0]
    if not measured:
        return None
    rotated = rm.SKILLS[day % len(rm.SKILLS):] + rm.SKILLS[:day % len(rm.SKILLS)]
    order = sorted(measured, key=lambda s: (rm.sub_index(levels[s]), rotated.index(s)))
    for skill in order:
        start = rm.sub_index(levels[skill])
        for it in rm.ITEMS:
            if it["skill"] == skill and rm.sub_index(it["sub"]) >= start \
                    and (statuses.get(it["id"]) or {}).get("status") != "confirmed":
                return it
    return None


def today(user_id: str) -> dict:
    from app.services import roadmap as store
    tasks = []
    start = _today_start()
    try:
        row = store.load(user_id)
    except Exception:
        row = None
    if row is not None and not (row.get("levels") or {}).get("overall"):
        tasks.append({"kind": "placement", "minutes": 30,
                      "done": False, "in_progress": (row.get("placement") or {}).get("status") == "in_progress"})
    words, due_count = due_words(user_id, limit=20)
    reviews_today = 0
    try:
        logs = admin_db().table("vocabulary_reviews").select("reviewed_at").eq("user_id", user_id) \
            .gte("reviewed_at", start.isoformat()).execute().data or []
        reviews_today = len(logs)
    except Exception:
        pass
    if due_count or reviews_today:
        tasks.append({"kind": "words", "count": due_count, "reviewed_today": reviews_today,
                      "minutes": max(3, min(10, due_count // 3 + 2)), "done": due_count == 0})
    m = mistakes(user_id)
    resolved_today = 0
    try:
        rows = admin_db().table("grammar_errors").select("resolved_at").eq("user_id", user_id).eq("resolved", True) \
            .gte("resolved_at", start.isoformat()).execute().data or []
        resolved_today = len(rows)
    except Exception:
        pass
    if m["due_count"] or resolved_today:
        tasks.append({"kind": "mistakes", "count": m["due_count"], "resolved_today": resolved_today,
                      "minutes": 5, "done": m["due_count"] == 0})
    if row is not None and (row.get("levels") or {}).get("overall"):
        statuses = row.get("items") or {}
        practised_today = [iid for iid, st in statuses.items() if (_parse(st.get("at")) or start - timedelta(1)) >= start
                           and st.get("source") != "placement"]
        item = next_roadmap_item(row["levels"], statuses, _now().timetuple().tm_yday)
        if item:
            tasks.append({"kind": "skill", "item": item, "minutes": 15,
                          "done": bool(practised_today), "practised_today": len(practised_today)})
    return {"tasks": tasks, "date": start.date().isoformat(),
            "done": sum(1 for t in tasks if t.get("done")), "total": len(tasks)}
