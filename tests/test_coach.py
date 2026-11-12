from app.agents import ielts_coach


def test_target_is_one_band_higher():
    assert [ielts_coach.target_band(b) for b in (None, 6.0, 6.5, 8.5, 9)] == [7, 7, 7, 9, 9]


def test_improve_cleans_model_output(ai):
    ai.json(lambda i, u: {"model_answer": "M", "upgraded": "U", "vocabulary": [{"word": "commute"}, {"word": ""}],
                          "grammar_focus": None, "estimated_cefr": "c1 maybe"})
    d = ielts_coach.improve("writing", "Task", "My text", 6.0, "ru")
    assert d["target_band"] == 7 and d["estimated_cefr"] == "C1"
    assert [v["word"] for v in d["vocabulary"]] == ["commute"] and d["grammar_focus"] == []
    instructions, user_input = ai.calls[0]
    assert "Russian" in instructions and "<<<LEARNER" in user_input
