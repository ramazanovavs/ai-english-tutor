from conftest import patch_everywhere


def reading_set(instructions, user_input):
    return {"title": "Sleep", "passage": "Sleep matters. " * 40,
            "questions": [{"id": 1, "type": "multiple_choice", "question": "q1", "options": ["A. a", "B. b", "C. c", "D. d"]},
                          {"id": 2, "type": "true_false_not_given", "question": "q2", "options": ["TRUE", "FALSE", "NOT GIVEN"]},
                          {"id": 3, "type": "short_answer", "question": "q3", "options": [],
                           "instruction": "Write NO MORE THAN THREE WORDS from the text"},
                          {"id": 4, "type": "true_false_not_given", "question": "q4", "options": ["TRUE", "FALSE", "NOT GIVEN"]},
                          {"id": 5, "type": "multiple_choice", "question": "q5", "options": ["A. a", "B. b", "C. c", "D. d"]}],
            "answer_key": {"1": ["B. b"], "2": ["NOT GIVEN"], "3": ["eight hours", "8 hours"], "4": ["TRUE"], "5": ["A. a"]},
            "explanations": {"1": "line 2"}, "glossary": [{"word": "matter", "definition": "be important"}]}


def test_reading_module(client, ai, monkeypatch, db):
    ai.json(reading_set)
    import app.agents.ielts_verify as iv
    monkeypatch.setattr(iv, "verify", lambda d, *a: d)
    g = client.post("/api/reading/generate", json={"level": "B1", "genre": "article"}).json()
    assert "answer_key" not in g and len(g["questions"]) == 5
    r = client.post("/api/reading/submit", json={"test_id": g["test_id"], "answers": {
        "1": "B. b", "2": "NOT GIVEN", "3": "the eight hours", "4": "FALSE"}}).json()
    assert (r["correct"], r["score"]) == (3, 60) and r["glossary"][0]["word"] == "matter"
    assert db.tables["reading_attempts"][0]["score"] == 60
    assert db.tables["learning_events"][-1]["activity_type"] == "reading"
    added = client.post("/api/ielts/vocabulary/add", json={"items": r["glossary"], "level": "B1", "topic": "Reading: Sleep"}).json()
    assert added["added"] == ["matter"] and db.tables["vocabulary_words"][0]["topic"] == "Reading: Sleep"


def test_speaking_with_pronunciation(client, ai, monkeypatch, db, fixture_bytes):
    ai.json(lambda i, u: {"estimated_level": "B1", "fluency_coherence": 70, "lexical_resource": 60, "grammar_accuracy": 50,
                          "positive_point": "p", "correction": "c", "suggestion": "s", "follow_up_question": "f"})
    import app.services.ielts_pronunciation as pr
    monkeypatch.setattr(pr, "assess", lambda recs, text, lang: {"pronunciation": 6, "summary": "clear", "issues": [],
                                                                 "sample_seconds": 8})
    r = client.post("/api/speaking/full", data={"text": "I like my city.", "scenario": "general conversation"},
                    files={"audio": ("a.webm", fixture_bytes("answer.webm"), "audio/webm")}).json()
    assert r["pronunciation_score"] == 67 and r["practice_score"] == round((70 + 60 + 50 + 67) / 4)
    assert db.tables["speaking_attempts"][-1]["pronunciation_score"] == 67
    r = client.post("/api/speaking/full", data={"text": "I like my city."}).json()
    assert "pronunciation_score" not in r and r["practice_score"] == 60


def test_feedback_language_reaches_the_prompts(client, ai, db):
    db.tables["profiles"][0]["preferred_explanation_language"] = "kk"
    ai.json(lambda i, u: {"score": 80, "estimated_level": "B1", "errors": [], "items": [], "questions": []})
    client.post("/api/grammar", json={"text": "I am fine."})
    client.post("/api/writing", json={"text": "I am fine.", "purpose": "general"})
    client.post("/api/vocabulary", json={"topic": "travel", "level": "B1"})
    client.post("/api/test/generate", json={"level": "B1", "focus": "grammar"})
    assert all("Kazakh" in instr for instr, _ in ai.calls), [c[0][-200:] for c in ai.calls]
