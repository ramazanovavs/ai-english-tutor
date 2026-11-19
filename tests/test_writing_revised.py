"""Writing: the improved version applies the advice, keeps paragraphs and lists its changes."""
import app.agents.writing_agent as wa


def test_prompt_requires_applying_advice_and_paragraphs():
    p = wa.INSTRUCTIONS
    assert "Preserve the learner's meaning in revised_sample." not in p            # the old "change nothing" rule
    assert "APPLIES EVERY item" in p and "Never advise adding something the text" in p
    assert "\\n\\n" in p and "changes" in p and "quote" in p


def test_route_returns_revised_with_paragraphs(client, ai, db):
    text = "Dear Sir,\n\nI am write to complain about my order.\n\nBest regards,\nAslan"
    revised = "Dear Sir or Madam,\n\nI am writing to complain about my recent order.\n\nKind regards,\nAslan"

    def model(instructions, user_input):
        assert "Dear Sir," in user_input
        return {"score": 55, "estimated_level": "B1", "strengths": ["Clear purpose"],
                "improvements": [{"category": "Register", "issue": "Too short", "suggestion": "Add the order number",
                                  "quote": "my order"}],
                "errors": [{"original": "I am write", "correction": "I am writing", "rule": "Present Continuous",
                            "explanation": "be + V-ing"}],
                "revised_sample": revised,
                "changes": [{"original": "I am write", "improved": "I am writing", "why": "be + V-ing"}],
                "next_task": "Write a reply.", "topic": "Complaint letter"}
    ai.json(model)
    d = client.post("/api/writing", json={"text": text, "purpose": "business"}).json()
    assert d["revised_sample"].count("\n\n") == 2
    assert d["changes"][0]["improved"] == "I am writing" and d["improvements"][0]["quote"] == "my order"
    assert db.tables["writing_attempts"][0]["revised_text"] == revised
