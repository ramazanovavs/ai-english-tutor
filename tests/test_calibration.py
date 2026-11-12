import time

import app.routes.api as api
from app.services import calibration


def test_compare_flags_drift():
    base = {"results": [{"id": "c1", "band": 5.0, "cefr": "B1"}, {"id": "c2", "band": 6.5, "cefr": "B2"}]}
    run = {"results": [{"id": "c1", "band": 5.5, "cefr": "B1"}, {"id": "c2", "band": 7.5, "cefr": "C1"}]}
    c = calibration.compare(run, base)
    assert [r["flag"] for r in c["rows"]] == [False, True] and c["drift"]


def test_calibration_run_and_baseline(client, ai, monkeypatch, db):
    monkeypatch.setattr(api, "_is_admin", lambda user: True)
    ai.json(lambda i, u: {"task_response": 6, "coherence_cohesion": 6, "lexical_resource": 6,
                          "grammatical_range_accuracy": 6, "score": 70, "estimated_level": "B2",
                          "strengths": [], "issues": [], "errors": []})
    assert client.post("/api/admin/calibration/run").status_code == 200
    end = time.time() + 5
    while time.time() < end and db.tables["ai_calibration_runs"][0]["status"] == "running":
        time.sleep(0.05)
    d = client.get("/api/admin/calibration").json()
    run = d["runs"][0]
    assert run["status"] == "done" and run["is_baseline"] and len(run["rows"]) == 4
    assert all(r["band"] is not None and r["cefr"] == "B2" for r in run["rows"])   # short essays get the official length penalty
