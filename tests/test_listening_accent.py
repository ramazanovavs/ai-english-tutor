"""General Listening: the chosen accent reaches the voice instructions and the bank."""
import app.agents.listening_agent as la
import app.services.listening_audio as audio_mod
import app.services.listening_bank as bank
import app.routes.api as api


def _test(accent=None):
    t = {"title": "At the shop", "level": "B1", "format": "dialogue",
         "speakers": [{"name": "A", "gender": "female"}, {"name": "B", "gender": "male"}],
         "script": [{"speaker": "A", "text": "Hello."}, {"speaker": "B", "text": "Hi."}]}
    if accent:
        t["accent"] = accent
    return t


def test_accent_in_voice_instructions(monkeypatch):
    seen = []
    monkeypatch.setattr(audio_mod, "synthesize_speech", lambda text, voice=None, instructions=None: seen.append(instructions) or b"")
    audio_mod.build_audio(_test("american"))
    assert seen and all("General American" in i for i in seen)
    seen.clear()
    audio_mod.build_audio(_test("british"))
    assert all("British" in i for i in seen)
    seen.clear()
    audio_mod.build_audio(_test())              # old tests and bank items without an accent still work
    assert seen and not any("accent" in i for i in seen)


def test_generate_keeps_accent(client, monkeypatch, db):
    monkeypatch.setattr(la, "generate", lambda level, topic, fmt: {
        "title": "T", "speakers": [{"name": "A", "gender": "female"}], "script": [{"speaker": "A", "text": "x"}],
        "questions": [{"id": 1, "type": "gap", "question": "q ___", "options": [], "answer": "x"}]})
    monkeypatch.setattr(api.listening_audio, "start", lambda *a, **k: None)
    r = client.post("/api/listening/generate", json={"level": "B1", "format": "dialogue", "accent": "american"}).json()
    assert r["accent"] == "american"
    assert db.tables["tests"][-1]["questions"]["accent"] == "american"
    assert client.post("/api/listening/generate", json={"level": "B1", "accent": "scottish"}).status_code == 422


def test_bank_prefers_same_accent(monkeypatch, db):
    db.tables[bank.TABLE] = [
        {"id": 1, "level": "B1", "format": "dialogue", "times_used": 0, "content": {"accent": "british"}},
        {"id": 2, "level": "B1", "format": "dialogue", "times_used": 3, "content": {"accent": "american"}},
    ]
    monkeypatch.setattr(bank, "used_bank_ids", lambda uid: set())
    assert bank.pick("B1", "dialogue", "u", "american")["id"] == 2
    assert bank.pick("B1", "dialogue", "u")["id"] == 1
