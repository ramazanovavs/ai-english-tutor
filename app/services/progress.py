"""
Stage 3 of the learning path:
- streak: consecutive days with any learning activity (in the learner's time zone);
- goals: target sublevel, target date, planned study days per week, with a forecast;
- level check: a control test that unlocks when 80% of the current sublevel is confirmed;
  passing it moves the learner to the next sublevel.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from app.data import roadmap as rm
from app.services import roadmap as store
from app.services.supabase_service import admin_db
from app.services.openai_service import ask_json

logger = logging.getLogger(__name__)

UNLOCK_PERCENT = 80
PASS_PERCENT = 75
RETRY_HOURS = 24
# Approximate cumulative guided learning hours at the START of each sublevel (after Cambridge English
# estimates: A2 ≈ 180–200 h, B1 ≈ 350–400, B2 ≈ 500–600, C1 ≈ 700–800, C2 ≈ 1000–1200).
HOURS_AT = {"A1.1": 0, "A1.2": 45, "A2.1": 95, "A2.2": 145, "B1.1": 190, "B1.2": 280, "B2.1": 375,
            "B2.2": 460, "C1.1": 550, "C1.2": 650, "C2": 750}
SESSION_HOURS = 0.5          # one study day on the site ≈ 30 minutes

ACTIVITY_SOURCES = [
    ("learning_events", "created_at"), ("vocabulary_reviews", "reviewed_at"),
    ("ielts_objective_attempts", "created_at"), ("ielts_writing_attempts", "created_at"),
    ("ielts_speaking_attempts", "created_at"), ("ielts_mock_tests", "started_at"),
]


def _tz(user_id: str):
    name = "Asia/Almaty"
    try:
        rows = admin_db().table("profiles").select("timezone").eq("id", user_id).limit(1).execute().data or []
        name = (rows[0].get("timezone") if rows else None) or name
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=5))   # Kazakhstan


def _parse(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def activity_days(user_id: str, days: int = 120) -> tuple[dict[str, int], date]:
    """{local date: number of activities} for the last `days` days, and today's local date."""
    tz = _tz(user_id)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts: dict[str, int] = {}
    for table, col in ACTIVITY_SOURCES:
        try:
            rows = admin_db().table(table).select(col).eq("user_id", user_id).gte(col, since).execute().data or []
        except Exception:
            continue
        for r in rows:
            dt = _parse(r.get(col))
            if dt:
                d = dt.astimezone(tz).date().isoformat()
                counts[d] = counts.get(d, 0) + 1
    return counts, datetime.now(tz).date()


def streaks(counts: dict[str, int], today: date) -> dict:
    active = {d for d, n in counts.items() if n > 0}
    # the streak continues while yesterday is active, even before today's first activity
    day = today if today.isoformat() in active else today - timedelta(days=1)
    current = 0
    while day.isoformat() in active:
        current += 1
        day -= timedelta(days=1)
    best, run = 0, 0
    for i in range(120, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        run = run + 1 if d in active else 0
        best = max(best, run)
    last28 = sum(1 for i in range(28) if (today - timedelta(days=i)).isoformat() in active)
    week = [{"date": (today - timedelta(days=i)).isoformat(),
             "count": counts.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(6, -1, -1)]
    return {"current": current, "best": max(best, current), "active_today": today.isoformat() in active,
            "today_count": counts.get(today.isoformat(), 0), "active_days_28": last28, "week": week}


def _hours_between(start: str, target: str) -> float:
    return max(0.0, HOURS_AT.get(target, 0) - HOURS_AT.get(start, 0))


def forecast(current: str | None, goal: dict, active_days_28: int) -> dict | None:
    target = goal.get("target")
    if not current or not target or rm.sub_index(target) <= rm.sub_index(current):
        return None
    hours = _hours_between(current, target)
    planned = int(goal.get("days_per_week") or 0)
    actual = round(active_days_28 / 4, 1)
    per_week = planned or actual or 1
    weeks = hours / (per_week * SESSION_HOURS)
    eta = (date.today() + timedelta(weeks=weeks)).isoformat()
    out = {"hours": round(hours), "days_per_week_used": per_week, "actual_days_per_week": actual,
           "eta": eta, "weeks": round(weeks)}
    if goal.get("target_date"):
        try:
            deadline = date.fromisoformat(goal["target_date"])
            weeks_left = max((deadline - date.today()).days / 7, 0.1)
            needed = hours / SESSION_HOURS / weeks_left
            out.update(target_date=goal["target_date"], on_track=date.fromisoformat(eta) <= deadline,
                       needed_days_per_week=round(needed, 1), realistic=needed <= 7)
        except ValueError:
            pass
    return out


def overview(user_id: str) -> dict:
    counts, today = activity_days(user_id)
    st = streaks(counts, today)
    row = store.load(user_id)
    goal = row.get("goals") or {}
    current = (row.get("levels") or {}).get("overall")
    return {"streak": st, "goal": goal, "current": current, "forecast": forecast(current, goal, st["active_days_28"])}


def set_goal(user_id: str, target: str | None, target_date: str | None, days_per_week: int | None) -> dict:
    if target and target not in rm.SUBLEVELS:
        raise ValueError("Unknown target level.")
    if target_date:
        date.fromisoformat(target_date)
    goal = {"target": target, "target_date": target_date or None,
            "days_per_week": max(1, min(7, int(days_per_week))) if days_per_week else None, "set_at": store.now_iso()}
    with store.lock(user_id):
        store.load(user_id)
        store.save(user_id, goals=goal)
    return goal


# ---------------------------------------------------------------- level check
CHECK_PROMPT = """
Create a CEFR level check for the sublevel {target} (X.1 = lower half of level X, X.2 = upper half).
Return JSON with exactly: questions, passage_title, passage, answer_key.
questions: exactly 16 multiple-choice objects {{id (1-16), skill, question, options (4 strings)}}:
- ids 1-6: skill "grammar", testing these points: {grammar};
- ids 7-12: skill "vocabulary", testing words and collocations for: {vocab};
- ids 13-16: skill "reading", about the passage.
passage: {words} words at exactly {target}, genre: {genre}.
Exactly one option is correct; distractors are typical learner errors. answer_key: id -> exact text of the correct option.
"""

SOLVER = """
You are an expert in English. Answer every multiple-choice question (reading questions are about the passage).
Return JSON {"answers": {"<id>": "<exact option text>"}}.
"""

WORDS = {"A1": "90-120", "A2": "130-180", "B1": "200-260", "B2": "260-320", "C1": "320-400", "C2": "380-450"}


def check_status(user_id: str) -> dict:
    row = store.load(user_id)
    levels, items = row.get("levels") or {}, row.get("items") or {}
    current = levels.get("overall")
    checks = row.get("checks") or {}
    active = checks.get("active") or {}
    if not current:
        return {"available": False, "reason": "no_level"}
    i = rm.sub_index(current)
    if i + 1 >= len(rm.SUBLEVELS):
        return {"available": False, "reason": "top", "current": current}
    target = rm.SUBLEVELS[i + 1]
    subset = rm.items_for(current)
    percent = round(sum(1 for it in subset if (items.get(it["id"]) or {}).get("status") == "confirmed") / len(subset) * 100)
    history = checks.get("history") or []
    last = history[-1] if history else None
    retry_at = None
    if last and not last.get("passed") and last.get("target") == target:
        retry = _parse(last.get("at")) + timedelta(hours=RETRY_HOURS)
        if retry > datetime.now(timezone.utc):
            retry_at = retry.isoformat()
    out = {"current": current, "target": target, "percent": percent, "unlock_percent": UNLOCK_PERCENT,
           "available": percent >= UNLOCK_PERCENT and not retry_at, "retry_at": retry_at, "last": last,
           "history": history[-5:]}
    if active and active.get("status") not in ("done",):
        out["active"] = _public_active(active)
    elif active.get("status") == "done":
        out["finished"] = {"result": active.get("result"), "target": active.get("target")}
    return out


def _public_active(a: dict) -> dict:
    out = {k: a.get(k) for k in ("id", "target", "status", "error")}
    if a.get("status") == "ready":
        c = a["content"]
        out["content"] = {"passage_title": c.get("passage_title"), "passage": c.get("passage"),
                          "questions": [{k: q.get(k) for k in ("id", "skill", "question", "options")} for q in c["questions"]]}
    if a.get("status") == "writing":
        out["writing"] = {"prompt": a["writing"]["prompt"], "objective_percent": a["objective"]["percent"]}
    return out


def _update_checks(user_id: str, fn) -> dict:
    with store.lock(user_id):
        row = store.load(user_id)
        checks = row.get("checks") or {}
        fn(checks)
        store.save(user_id, checks=checks)
        return checks


def start_check(user_id: str) -> dict:
    st = check_status(user_id)
    if not st.get("available"):
        raise ValueError("The level check is not available yet.")
    target = st["target"]
    writing_item = next(it for it in rm.items_for(target) if it["skill"] == "writing")
    active = {"id": str(uuid.uuid4()), "target": target, "from": st["current"], "status": "preparing",
              "started_at": store.now_iso(), "writing": {"prompt": writing_item["action"]["task"]}}
    _update_checks(user_id, lambda c: c.update(active=active))
    threading.Thread(target=_prepare_check, args=(user_id, active["id"], target), daemon=True).start()
    return check_status(user_id)


def _prepare_check(user_id: str, check_id: str, target: str) -> None:
    items = rm.items_for(target)
    grammar = "; ".join(it["action"]["focus"].split(": ", 1)[-1] for it in items if it["skill"] == "grammar")
    vocab = "; ".join(it["action"]["topic"] for it in items if it["skill"] == "vocabulary")
    genre = next((it["action"].get("topic") for it in items if it["skill"] == "reading"), "an article")
    try:
        content = None
        for _ in range(2):
            data = ask_json(CHECK_PROMPT.format(target=target, grammar=grammar, vocab=vocab, genre=genre,
                                                words=WORDS[target[:2]]), f"Sublevel {target}")
            qs = [q for q in data.get("questions") or [] if len(q.get("options") or []) == 4]
            key = {str(k): (v[0] if isinstance(v, list) else v) for k, v in (data.get("answer_key") or {}).items()}
            # an independent solver must agree with the key; disputed items are dropped
            solved = ask_json(SOLVER, f"PASSAGE:\n{data.get('passage')}\n\nQUESTIONS:\n" +
                              "\n".join(f"{q['id']}. {q['question']} OPTIONS: {q['options']}" for q in qs))
            answers = {str(k): str(v).strip() for k, v in (solved.get("answers") or {}).items()}
            kept = [q for q in qs if str(key.get(str(q["id"]), "")).strip() in q["options"]
                    and answers.get(str(q["id"])) == str(key[str(q["id"])]).strip()]
            if len(kept) >= 12 and data.get("passage"):
                data["questions"], data["answer_key"] = kept, key
                content = data
                break
        if not content:
            raise ValueError("Could not build a reliable level check.")

        def ready(c):
            a = c.get("active") or {}
            if a.get("id") == check_id:
                a.update(status="ready", content=content)
        _update_checks(user_id, ready)
    except Exception as exc:
        logger.exception("Level check preparation failed")

        def fail(c):
            a = c.get("active") or {}
            if a.get("id") == check_id:
                a.update(status="error", error=str(exc)[:200])
        _update_checks(user_id, fail)


def submit_check_answers(user_id: str, answers: dict) -> dict:
    def fn(c):
        a = c.get("active") or {}
        if a.get("status") != "ready":
            raise ValueError("The level check is not active.")
        key = a["content"]["answer_key"]
        by_skill = {}
        for q in a["content"]["questions"]:
            ok = str(answers.get(str(q["id"]), "")).strip() == str(key[str(q["id"])]).strip()
            s = by_skill.setdefault(q.get("skill") or "grammar", {"correct": 0, "total": 0})
            s["total"] += 1
            s["correct"] += int(ok)
        correct = sum(s["correct"] for s in by_skill.values())
        total = sum(s["total"] for s in by_skill.values())
        for s in by_skill.values():
            s["percent"] = round(s["correct"] / s["total"] * 100)
        a["objective"] = {"correct": correct, "total": total, "percent": round(correct / total * 100), "by_skill": by_skill}
        a["status"] = "writing"
    _update_checks(user_id, fn)
    return check_status(user_id)


def submit_check_writing(user_id: str, text: str) -> dict:
    if len((text or "").split()) < 30:
        raise ValueError("Write at least 30 words.")

    def fn(c):
        a = c.get("active") or {}
        if a.get("status") != "writing":
            raise ValueError("The writing part is not active.")
        a["writing"]["text"] = text.strip()[:8000]
        a["status"] = "assessing"
    _update_checks(user_id, fn)
    threading.Thread(target=_finish_check, args=(user_id,), daemon=True).start()
    return check_status(user_id)


def _finish_check(user_id: str) -> None:
    from app.services import placement
    row = store.load(user_id)
    a = (row.get("checks") or {}).get("active") or {}
    lang = placement._lang(user_id)
    try:
        wr = placement.assess_writing({"prompt": a["writing"]["prompt"], "words": ""}, a["writing"]["text"], lang)
    except Exception:
        logger.exception("Level check writing assessment failed")
        wr = None
    target = a["target"]
    obj = a["objective"]
    w_sub = wr["sublevel"] if wr else None
    writing_ok = w_sub is None or rm.sub_index(w_sub) >= rm.sub_index(target) - 1
    passed = obj["percent"] >= PASS_PERCENT and writing_ok
    weak = [s for s, v in obj["by_skill"].items() if v["percent"] < PASS_PERCENT]
    if not writing_ok:
        weak.append("writing")
    result = {"passed": passed, "target": target, "from": a.get("from"), "objective": obj, "writing_sub": w_sub,
              "writing_feedback": {k: (wr or {}).get(k) for k in ("comment", "strengths", "improve")}, "weak": weak}

    merged = None
    with store.lock(user_id):
        row = store.load(user_id)
        checks = row.get("checks") or {}
        act = checks.get("active") or {}
        if act.get("id") != a.get("id"):
            return
        act.update(status="done", result=result, finished_at=store.now_iso())
        history = checks.get("history") or []
        history.append({"target": target, "passed": passed, "at": store.now_iso(), "percent": obj["percent"],
                        "writing": w_sub})
        checks["history"] = history[-20:]
        fields = {"checks": checks}
        if passed:
            levels = dict(row.get("levels") or {})
            for skill in ("grammar", "vocabulary", "reading"):
                if obj["by_skill"].get(skill, {}).get("percent", 0) >= PASS_PERCENT \
                        and rm.sub_index(levels.get(skill)) < rm.sub_index(target):
                    levels[skill] = target
            if w_sub and rm.sub_index(w_sub) > rm.sub_index(levels.get("writing")):
                levels["writing"] = w_sub
            levels["overall"] = target
            fields["levels"] = levels
            fields["items"] = store.apply_placement_items(row.get("items") or {}, levels, source="level_check")
            merged = levels
        store.save(user_id, **fields)
    if merged:
        try:
            from app.services.student_model import apply_placement
            apply_placement(user_id, merged)
        except Exception:
            logger.exception("Could not copy the new level to the student model")
