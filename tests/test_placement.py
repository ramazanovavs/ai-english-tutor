import time

from app.data import placement_items as bank, roadmap as rm
from app.services import placement, roadmap as store
from conftest import patch_everywhere


def test_bank_and_map_are_well_formed():
    assert len(bank.ITEMS) == 48 and all(len(set(it["options"])) == 4 for it in bank.ITEMS)
    assert len({it["id"] for it in rm.ITEMS}) == len(rm.ITEMS)
    for sub in rm.SUBLEVELS:
        skills = {it["skill"] for it in rm.items_for(sub)}
        assert skills == set(rm.SKILLS), sub
    for it in rm.ITEMS:
        assert all(it["text"][lang].strip() for lang in ("ru", "kk", "en"))


def test_ability_estimate_moves_with_answers():
    strong = [{"difficulty": b, "correct": True} for b in (2.5, 3.5, 4.5, 4.5, 5.5)]
    weak = [{"difficulty": b, "correct": False} for b in (2.5, 1.5, 0.5, 0.5)]
    assert placement.estimate(strong)[0] > 4 and placement.estimate(weak)[0] < 1.2
    assert placement.theta_to_sub(2.3) == "B1.1" and placement.theta_to_sub(2.7) == "B1.2"
    assert placement.theta_to_sub(5.9) == "C2" and placement.theta_to_sub(-1) == "A1.1"


def test_receptive_sublevels():
    assert placement.receptive_sub("B1", 85) == "B1.2"
    assert placement.receptive_sub("B1", 65) == "B1.1"
    assert placement.receptive_sub("B1", 50) == "A2.2"
    assert placement.receptive_sub("A1", 20) == "A1.1"
    assert placement.receptive_sub("C2", 55) == "C1.2"


def _wait(client, cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        s = client.get("/api/placement").json()
        if cond(s):
            return s
        time.sleep(0.05)
    raise AssertionError(s)


def test_full_placement(client, ai, monkeypatch, db, fixture_bytes):
    def model(instructions, user_input):
        if "reading text for a CEFR placement" in instructions:
            return {"title": "T", "passage": "A short text about bees. " * 20,
                    "questions": [{"id": i, "type": "multiple_choice", "question": f"q{i}",
                                   "options": ["right", "w1", "w2", "w3"]} for i in range(1, 7)],
                    "answer_key": {str(i): "right" for i in range(1, 7)}, "explanations": {}}
        if "writing examiner" in instructions:
            return {"sublevel": "b2.1", "strengths": ["s"], "improve": ["i"], "comment": "c"}
        if "speaking examiner" in instructions:
            return {"sublevel": "B1.2", "strengths": [], "improve": [], "comment": "c"}
        return {}
    ai.json(model)
    patch_everywhere(monkeypatch, "verify", lambda d, *a: d)
    import app.agents.ielts_verify as iv
    monkeypatch.setattr(iv, "verify", lambda d, *a: d)
    mp3 = fixture_bytes("speech.mp3")
    patch_everywhere(monkeypatch, "synthesize_speech", lambda *a, **k: mp3)
    patch_everywhere(monkeypatch, "transcribe_audio", lambda *a: "I live in Uralsk with my parents and my sister.")
    import app.agents.listening_agent as la
    monkeypatch.setattr(la, "generate", lambda level, topic, fmt: {
        "title": "Shop", "speakers": [{"name": "A", "gender": "female"}],
        "script": [{"speaker": "A", "text": "It costs ten pounds."}],
        "questions": [{"id": i, "type": "gap", "question": f"Price {i}", "options": [], "answer": "ten"} for i in range(1, 6)]})

    s = client.post("/api/placement/start").json()
    assert s["stage"] == "core" and "answer" not in s["item"]
    seen = []
    while s["stage"] == "core":
        it = s["item"]
        seen.append(bank.ITEMS_BY_ID[it["id"]]["level"])
        key = bank.ITEMS_BY_ID[it["id"]]["answer"]
        # a B1-ish learner: right up to B1, wrong above
        choice = key if bank.ITEMS_BY_ID[it["id"]]["level"] in ("A1", "A2", "B1") else it["options"][0] if it["options"][0] != key else it["options"][1]
        s = client.post("/api/placement/answer", json={"item_id": it["id"], "choice": choice}).json()
    assert len(seen) == placement.CORE_ITEMS
    assert s["core"]["level"] in ("B1", "B2")
    assert "B2" in seen or "C1" in seen       # the test probed above the learner's level

    s = _wait(client, lambda x: x["reading"]["status"] != "preparing")
    assert s["reading"]["status"] == "ready" and "answer_key" not in s["reading"]["content"]
    s = client.post("/api/placement/reading", json={"answers": {str(i): "right" for i in range(1, 6)}}).json()
    assert s["stage"] == "listening"
    s = _wait(client, lambda x: x["listening"]["status"] != "preparing")
    tid = s["listening"]["content"]["test_id"]
    assert client.get(f"/api/listening/{tid}/audio").status_code == 200
    s = client.post("/api/placement/listening", json={"answers": {str(i): "ten" for i in range(1, 6)}}).json()
    assert s["stage"] == "writing" and s["writing"]["prompt"]
    s = client.post("/api/placement/writing", json={"text": "word " * 120}).json()
    assert s["stage"] == "speaking" and len(s["speaking"]["questions"]) == 2
    webm = fixture_bytes("answer.webm")
    for i in (0, 1):
        client.post(f"/api/placement/speaking/answer/{i}", files={"audio": ("a.webm", webm, "audio/webm")})
    client.post("/api/placement/speaking/submit", json={})
    s = _wait(client, lambda x: x["stage"] == "done")
    levels = s["result"]["levels"]
    assert levels["reading"] in ("B1.2", "B2.2") and levels["listening"].endswith(".2")
    assert levels["writing"] == "B2.1" and levels["speaking"] == "B1.2" and levels["overall"]

    r = client.get("/api/roadmap").json()
    assert r["current"] == levels["overall"]
    below = [it for it in rm.ITEMS if rm.sub_index(it["sub"]) < rm.sub_index(levels[it["skill"]])]
    assert all(r["status"][it["id"]]["status"] == "confirmed" for it in below)
    skills = {x["category"]: x for x in db.tables["student_skills"]}
    assert skills["writing"]["level"] == "B2"


def test_roadmap_evidence(client, db):
    item = rm.ITEMS_BY_ID["B1.2-g1"]
    r = client.post("/api/roadmap/evidence", json={"item_id": item["id"], "module": "test", "score": 60}).json()
    assert r["status"] == "practised"
    r = client.post("/api/roadmap/evidence", json={"item_id": item["id"], "module": "test", "score": 80}).json()
    assert r["confirmed"] and r["attempts"] == 2
    w = client.post("/api/roadmap/evidence", json={"item_id": "B2.1-w1", "module": "writing", "score": 75, "level": "B1"}).json()
    assert not w["confirmed"]
    assert client.post("/api/profile/language", json={"language": "kk"}).json() == {"language": "kk"}
    assert client.get("/api/roadmap").json()["language"] == "kk"
