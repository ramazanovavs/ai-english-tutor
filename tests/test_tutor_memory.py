"""AI tutor remembers the conversation; "New conversation" starts a clean one."""
import app.agents.tutor_agent as tutor


def _fake_model(monkeypatch, answers):
    calls = []

    def stream_text(instructions, user_input):
        calls.append(user_input)
        yield answers[len(calls) - 1]

    monkeypatch.setattr(tutor, "stream_text", stream_text)
    return calls


def _say(client, text):
    r = client.post("/api/chat/stream", json={"text": text})
    assert r.status_code == 200 and '"done"' in r.text


def test_third_message_uses_first_two(client, monkeypatch):
    calls = _fake_model(monkeypatch, ["Present Perfect = have/has + V3.", "Because the time is not finished.", "ok"])
    _say(client, "What is Present Perfect?")
    _say(client, "Why do we use it with 'today'?")
    _say(client, "And what about the phrase you used?")
    third = calls[2]
    assert "Learner: What is Present Perfect?" in third
    assert "Tutor: Present Perfect = have/has + V3." in third
    assert "Learner: Why do we use it with 'today'?" in third
    assert "Tutor: Because the time is not finished." in third
    convo = third.split("CONVERSATION>>>")[0]
    assert "And what about the phrase you used?" not in convo        # the new message is not doubled
    assert "first message of the conversation" in calls[0]


def test_new_conversation_and_history(client, monkeypatch):
    calls = _fake_model(monkeypatch, ["Hello!", "Fine.", "Hi again"])
    _say(client, "Hi")
    _say(client, "How are you?")
    h = client.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in h] == ["user", "assistant", "user", "assistant"] and h[0]["content"] == "Hi"
    assert client.post("/api/chat/new").json()["ok"]
    assert client.get("/api/chat/history").json()["messages"] == []
    _say(client, "New topic")
    assert "first message of the conversation" in calls[2] and "How are you?" not in calls[2]


def test_history_is_capped(client, monkeypatch, db):
    uid = None
    calls = _fake_model(monkeypatch, ["a"] * 30)
    _say(client, "start")
    uid = db.tables["messages"][0]["user_id"]
    for i in range(30):
        db.tables["messages"].append({"user_id": uid, "role": "user" if i % 2 == 0 else "assistant",
                                      "content": f"msg{i:02d} " + "x" * 2000,
                                      "created_at": f"2099-01-01T10:{i:02d}:00+00:00"})
    hist = tutor.load_history(uid)
    assert len(hist) <= tutor.HISTORY_MESSAGES
    assert sum(len(m["content"]) for m in hist) <= tutor.HISTORY_CHARS + 20
    assert hist[-1]["content"].startswith("msg29") and hist[-1]["content"].endswith("[…]")


def test_prompt_answers_first():
    text = tutor.INSTRUCTIONS
    assert "ANSWER THE LEARNER'S QUESTION FIRST" in text and "Small correction:" in text
    assert "YOUR previous answer" in text
