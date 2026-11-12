import app.routes.api as api


def reading_set(instructions, user_input):
    return {"title": "Bees", "passage": "Bees are important. " * 50,
            "questions": [{"id": i, "type": "true_false_not_given" if i <= 7 else "multiple_choice",
                           "question": f"q{i}", "options": ["TRUE", "FALSE", "NOT GIVEN"] if i <= 7 else ["A. x", "B. y"]}
                          for i in range(1, 11)],
            "answer_key": {**{str(i): "TRUE" for i in range(1, 8)}, **{str(i): "A. x" for i in range(8, 11)}},
            "explanations": {}, "groups": []}


def test_targeted_drill_submit_and_analytics(client, ai, monkeypatch, db):
    ai.json(reading_set)
    import app.agents.ielts_agent as agent
    monkeypatch.setattr(agent, "verify_key", lambda d, *a: d)
    saved = []
    monkeypatch.setattr(api, "_bank_save_quietly", lambda *a, **k: saved.append(a))

    r = client.post("/api/ielts/reading/generate", json={"test_type": "Academic", "focus_type": "tfng"})
    assert r.status_code == 200, r.text
    assert "Targeted drill" in ai.calls[-1][1] and saved == []        # drills stay out of the shared bank
    test_id = r.json()["test_id"]
    res = client.post("/api/ielts/objective/submit", json={"test_id": test_id, "answers": {"1": "TRUE", "2": "FALSE"}}).json()
    assert res["correct"] == 1 and res["details"][0]["type"] == "true_false_not_given"

    a = client.get("/api/ielts/analytics").json()
    tfng = next(x for x in a["by_type"] if x["type"] == "true_false_not_given")
    assert (tfng["correct"], tfng["total"], tfng["trainable"]) == (1, 7, True)
    assert a["weakest"][0]["type"] in ("true_false_not_given", "multiple_choice")


def test_improve_and_vocabulary_add(client, ai, db):
    ai.json(lambda i, u: {"model_answer": "M", "upgraded": "U", "changes": [], "vocabulary": [{"word": "commute"}],
                          "grammar_focus": [], "estimated_cefr": "B2"})
    d = client.post("/api/ielts/improve", json={"kind": "speaking", "prompt": "Describe a trip", "text": "I go Almaty", "band": 5.5}).json()
    assert d["target_band"] == 6 and d["kind"] == "speaking"
    r = client.post("/api/ielts/vocabulary/add", json={"items": d["vocabulary"], "level": "B2"}).json()
    assert r["added"] == ["commute"]
    assert db.tables["vocabulary_words"][0]["topic"] == "IELTS feedback"


def test_ai_down_gives_clear_message(client):
    r = client.post("/api/ielts/improve", json={"prompt": "p", "text": "t"})
    assert r.status_code == 503
