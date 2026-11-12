"""A whole computer-delivered mock through the HTTP API: Listening -> Reading -> Writing -> Speaking -> results."""
import time
from datetime import timedelta

import app.routes.api as api
from app.services import ielts_exam as ex
from conftest import patch_everywhere


def listening_part(instructions, user_input):
    return {"title": "Booking", "intro": "You will hear a conversation.",
            "speakers": [{"name": "Ann", "gender": "female"}, {"name": "Bob", "gender": "male"}],
            "script": [{"speaker": "Ann", "text": "A table for twelve, please."}, {"speaker": "Bob", "text": "Certainly."}],
            "questions": [{"id": i, "type": "completion", "question": f"Group size ({i})", "options": [],
                           "instruction": "Write NO MORE THAN TWO WORDS AND/OR A NUMBER"} for i in range(1, 11)],
            "answer_key": {str(i): "12" for i in range(1, 11)}, "explanations": {}}


def reading(test_type, part):
    n = 13 if part < 3 else 14
    return {"title": f"P{part}", "passage": "text",
            "questions": [{"id": i, "type": "short_answer", "question": "q", "options": [],
                           "instruction": "Write ONE WORD ONLY"} for i in range(1, n + 1)],
            "answer_key": {str(i): ["bees"] for i in range(1, n + 1)}, "explanations": {}}


def writing(test_type):
    return {"task1": {"title": "T1", "prompt": "Describe the chart", "minimum_words": 150},
            "task2": {"title": "T2", "prompt": "Discuss both views", "minimum_words": 250}}


def speaking():
    return {"part1": {"questions": ["Do you work or study?", "Where do you live?"]},
            "part2": {"cue_card": "Describe a trip", "bullet_points": ["where", "when", "who", "why"]},
            "part3": {"questions": ["Why do people travel?"]}}


def wait(cond, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_full_mock(client, ai, monkeypatch, db, fixture_bytes):
    mp3 = fixture_bytes("speech.mp3")
    webm = fixture_bytes("answer.webm")

    def model(instructions, user_input):
        if "Listening" in instructions or "speakers" in instructions:
            return listening_part(instructions, user_input)
        if "task_response" in instructions or "task_achievement" in instructions:
            return {"task_response": 7, "task_achievement": 6, "coherence_cohesion": 7, "lexical_resource": 7,
                    "grammatical_range_accuracy": 6, "improvements": [], "error_examples": []}
        if "fluency_coherence" in instructions:
            return {"fluency_coherence": 7, "lexical_resource": 6, "grammatical_range_accuracy": 6, "improvements": []}
        return {"question": "Why?", "answers": {}}
    ai.json(model)
    patch_everywhere(monkeypatch, "verify_key", lambda d, *a: d)
    patch_everywhere(monkeypatch, "synthesize_speech", lambda text, voice=None, instructions=None: mp3)
    patch_everywhere(monkeypatch, "transcribe_audio", lambda *a: "I went to Almaty with my family last summer.")
    monkeypatch.setitem(api._MOCK_GENERATORS, "reading", reading)
    monkeypatch.setitem(api._MOCK_GENERATORS, "writing", writing)
    monkeypatch.setitem(api._MOCK_GENERATORS, "speaking", speaking)
    monkeypatch.setattr(api, "mock_assess_full_speaking", lambda parts, *a, **k: {
        "fluency_coherence": 7, "lexical_resource": 6, "grammatical_range_accuracy": 6,
        "provisional_language_band": 6.0, "improvements": []})

    s = client.post("/api/ielts/mock/start", json={"test_type": "Academic"}).json()
    mid = s["mock_id"]
    assert wait(lambda: client.get(f"/api/ielts/mock/{mid}").json()["prep"].get("status") in ("ready", "error"))
    assert client.get(f"/api/ielts/mock/{mid}").json()["prep"]["status"] == "ready"

    # Listening: answer keys are never sent to the browser; each part's audio is served once
    d = client.post(f"/api/ielts/mock/{mid}/section/start", json={"section": "listening"}).json()
    assert not {"answer_key", "script", "audio_script"} & set(d["parts"][0]["content"])
    assert client.get(f"/api/ielts/mock/{mid}/listening/1/audio").status_code == 200
    ex.update_meta(mid, lambda m: m["listening_served"].update({"1": ex.iso(ex.now() - timedelta(seconds=60))}))
    assert client.get(f"/api/ielts/mock/{mid}/listening/1/audio").status_code == 403
    answers = {str(p): {str(i): ("twelve" if p < 4 else "wrong") for i in range(1, 11)} for p in range(1, 5)}
    st = client.post(f"/api/ielts/mock/{mid}/section/submit", json={"section": "listening", "answers": answers}).json()
    assert st["next_section"] == "reading"

    # Reading: autosaved answers are marked when the deadline passes
    client.post(f"/api/ielts/mock/{mid}/section/start", json={"section": "reading"})
    client.post(f"/api/ielts/mock/{mid}/answers", json={"section": "reading", "answers": {
        "1": {str(i): "Bees" for i in range(1, 14)}, "2": {str(i): "bees" for i in range(1, 14)},
        "3": {str(i): "the bees" for i in range(1, 15)}}})
    ex.update_meta(mid, lambda m: m["timing"]["reading"].update(deadline=ex.iso(ex.now() - timedelta(minutes=5))))
    assert client.get(f"/api/ielts/mock/{mid}").json()["next_section"] == "writing"

    client.post(f"/api/ielts/mock/{mid}/section/start", json={"section": "writing"})
    client.post(f"/api/ielts/mock/{mid}/section/submit", json={"section": "writing",
                                                               "answers": {"1": "word " * 160, "2": "word " * 260}})

    client.post(f"/api/ielts/mock/{mid}/section/start", json={"section": "speaking"})
    r = client.post(f"/api/ielts/mock/{mid}/speaking/turn").json()
    for _ in range(30):
        if r["done"]:
            break
        r = client.post(f"/api/ielts/mock/{mid}/speaking/answer", data={"turn_id": r["turn"]["id"], "seconds": "40"},
                        files={"audio": ("a.webm", webm, "audio/webm")}).json()
    assert r["done"]
    client.post(f"/api/ielts/mock/{mid}/speaking/submit")

    f = {}
    def finished():
        f.update(client.post(f"/api/ielts/mock/{mid}/finish").json())
        return f.get("overall_band") is not None
    assert wait(finished, 15), f
    assert f["bands"]["listening"] is not None and f["bands"]["reading"] is not None
    assert f["review"]["writing"]["1"]["prompt"] == "Describe the chart"

    sections = {r["section"] for r in db.tables["ielts_band_history"]}
    assert sections == {"listening", "reading", "writing", "speaking", "overall"}
    a = client.get("/api/ielts/analytics").json()
    assert a["forecast"]["overall"] is not None and a["forecast"]["low"] <= a["forecast"]["overall"] <= a["forecast"]["high"]
