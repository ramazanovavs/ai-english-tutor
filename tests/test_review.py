from datetime import datetime, timedelta, timezone

from app.services import review


def test_sm2_intervals_grow_and_reset():
    row = {}
    intervals = []
    for _ in range(5):
        row.update(review.sm2(row, 4))
        intervals.append(row["interval_days"])
    assert intervals[:3] == [1, 3, 8] and intervals[4] > intervals[3]
    assert row["status"] == "mastered"
    forgot = review.sm2(row, 1)
    assert forgot["interval_days"] == 1 and forgot["repetitions"] == 0 and forgot["ease_factor"] < row["ease_factor"]


def test_due_words_and_review(client, db):
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    db.tables["vocabulary_words"] = [
        {"id": "w1", "user_id": "u1", "word": "commute", "next_review_at": None},
        {"id": "w2", "user_id": "u1", "word": "later", "next_review_at": future},
        {"id": "w3", "user_id": "u1", "word": "overdue", "next_review_at": "2026-01-01T00:00:00+00:00"},
    ]
    d = client.get("/api/review/words").json()
    assert [w["word"] for w in d["words"]] == ["overdue", "commute"]
    r = client.post("/api/review/words/w1", json={"quality": 5}).json()
    assert r["interval_days"] == 2
    assert client.get("/api/review/words").json()["due"] == 1
    assert db.tables["vocabulary_reviews"][0]["correct"] is True


def test_errors_saved_from_grammar_coach_and_practised(client, ai, db):
    ai.json(lambda i, u: {"score": 60, "estimated_level": "B1", "corrected_text": "", "summary": "", "topic": "past",
                          "practice": [], "errors": [{"original": "I go yesterday", "correction": "I went yesterday",
                                                      "rule": "Past Simple", "explanation": "past time"},
                                                     {"original": "same", "correction": "same"}]})
    client.post("/api/grammar", json={"text": "I go yesterday to school."})
    client.post("/api/grammar", json={"text": "I go yesterday to school."})   # duplicate is not stored twice
    assert len(db.tables["grammar_errors"]) == 1
    m = client.get("/api/mistakes").json()
    assert m["open_count"] == 1 and m["due_count"] == 1      # fake rows are dated in the past
    err_id = db.tables["grammar_errors"][0]["id"]
    ai.json(lambda i, u: {"items": [{"error_id": err_id, "sentence": "Last week she ___ to Almaty.",
                                     "answer": ["went"], "rule": "Past Simple", "explanation": "finished past time"}]})
    p = client.post("/api/mistakes/practice").json()
    assert p["items"][0]["sentence"].startswith("Last week") and "answer" not in p["items"][0]
    r = client.post("/api/mistakes/submit", json={"test_id": p["test_id"], "answers": {"1": "Went"}}).json()
    assert r["resolved"] == 1
    assert client.get("/api/mistakes").json()["open_count"] == 0


def test_fresh_mistakes_wait_a_day(db):
    now = datetime.now(timezone.utc).isoformat()
    db.tables["grammar_errors"] = [{"id": "e1", "user_id": "u1", "original_fragment": "a", "corrected_fragment": "b",
                                    "resolved": False, "created_at": now}]
    assert review.mistakes("u1")["due_count"] == 0


def test_today_plan(client, db):
    db.tables["learner_roadmap"] = [{"user_id": "u1", "levels": {"overall": "B1.1", "grammar": "B1.1", "writing": "A2.2",
                                                                 "reading": "B1.2"}, "items": {}, "placement": {}}]
    db.tables["vocabulary_words"] = [{"id": "w1", "user_id": "u1", "word": "commute", "next_review_at": None}]
    t = client.get("/api/today").json()
    kinds = [x["kind"] for x in t["tasks"]]
    assert kinds == ["words", "skill"]
    skill = t["tasks"][1]["item"]
    assert skill["skill"] == "writing" and skill["sub"] == "A2.2"     # the weakest skill comes first
    db.tables["learner_roadmap"][0]["levels"] = {}
    assert client.get("/api/today").json()["tasks"][0]["kind"] == "placement"
