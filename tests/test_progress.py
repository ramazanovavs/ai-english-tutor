import time
from datetime import date, datetime, timedelta, timezone

from app.data import roadmap as rm
from app.services import progress


def test_streaks():
    today = date(2026, 11, 12)
    counts = {(today - timedelta(days=i)).isoformat(): 1 for i in (1, 2, 3, 6, 7)}
    s = progress.streaks(counts, today)
    assert s["current"] == 3 and not s["active_today"]          # yesterday keeps the streak alive
    counts[today.isoformat()] = 2
    s = progress.streaks(counts, today)
    assert s["current"] == 4 and s["best"] == 4 and s["today_count"] == 2
    assert progress.streaks({(today - timedelta(days=2)).isoformat(): 1}, today)["current"] == 0


def test_forecast():
    f = progress.forecast("B1.1", {"target": "B2.1", "days_per_week": 5,
                                   "target_date": (date.today() + timedelta(days=730)).isoformat()}, 10)
    assert f["hours"] == 185 and f["on_track"] is True and f["needed_days_per_week"] < 5
    assert progress.forecast("B2.1", {"target": "B1.1"}, 10) is None


def test_overview_counts_activity(client, db):
    now = datetime.now(timezone.utc).isoformat()
    db.tables["learning_events"] = [{"user_id": "u1", "created_at": now}]
    client.post("/api/goals", json={"target": "B2.1", "days_per_week": 4})
    o = client.get("/api/progress/overview").json()
    assert o["streak"]["current"] == 1 and o["goal"]["target"] == "B2.1"


def _wait(client, cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        s = client.get("/api/level-check").json()
        if cond(s):
            return s
        time.sleep(0.05)
    raise AssertionError(s)


def test_level_check_unlock_and_pass(client, ai, db):
    items = {it["id"]: {"status": "confirmed", "source": "practice"} for it in rm.items_for("B1.1")[:4]}
    db.tables["learner_roadmap"] = [{"user_id": "u1", "levels": {"overall": "B1.1", "grammar": "B1.1", "vocabulary": "B1.1",
                                                                 "reading": "B1.1", "writing": "B1.1"},
                                     "items": items, "placement": {}, "goals": {}, "checks": {}}]
    st = client.get("/api/level-check").json()
    assert st["target"] == "B1.2" and st["percent"] == 57 and not st["available"]
    assert client.post("/api/level-check/start").status_code == 409

    for it in rm.items_for("B1.1"):
        db.tables["learner_roadmap"][0]["items"][it["id"]] = {"status": "confirmed"}
    assert client.get("/api/level-check").json()["available"]

    qs = [{"id": i, "skill": "grammar" if i <= 6 else "vocabulary" if i <= 12 else "reading",
           "question": f"q{i}", "options": ["a", "b", "c", "d"]} for i in range(1, 17)]
    key = {str(i): "a" for i in range(1, 17)}

    def model(instructions, user_input):
        if "level check" in instructions:
            return {"questions": qs, "answer_key": key, "passage": "Text " * 50, "passage_title": "T"}
        if "expert in English" in instructions:
            return {"answers": {**key, "16": "b"}}           # the solver disagrees on item 16 -> dropped
        if "writing examiner" in instructions:
            return {"sublevel": "B1.2", "comment": "ok"}
        return {}
    ai.json(model)
    client.post("/api/level-check/start")
    st = _wait(client, lambda s: (s.get("active") or {}).get("status") == "ready")
    shown = st["active"]["content"]["questions"]
    assert len(shown) == 15 and "answer_key" not in st["active"]["content"]
    answers = {str(q["id"]): "a" for q in shown}
    answers["1"] = "b"
    st = client.post("/api/level-check/answers", json={"answers": answers}).json()
    assert st["active"]["status"] == "writing"
    assert client.post("/api/level-check/writing", json={"text": "short"}).status_code == 409
    client.post("/api/level-check/writing", json={"text": "word " * 120})
    st = _wait(client, lambda s: "finished" in s)
    assert st["finished"]["result"]["passed"] is True
    row = db.tables["learner_roadmap"][0]
    assert row["levels"]["overall"] == "B1.2" and row["levels"]["writing"] == "B1.2"
    assert row["checks"]["history"][-1]["passed"]
