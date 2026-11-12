from app.services import ielts_analytics as an


def test_family_aliases():
    assert an.family("TFNG") == "true_false_not_given"
    assert an.family("sentence_completion") == "completion"
    assert an.family("whatever") is None


def test_forecast_needs_all_four_skills():
    hist = [{"section": "listening", "band": 6.5}, {"section": "reading", "band": 7}]
    assert an.forecast(hist)["missing"] == ["writing", "speaking"]


def test_forecast_range_narrows_with_stable_results():
    few = [{"section": s, "band": b} for s, b in (("listening", 7), ("reading", 7), ("writing", 6), ("speaking", 6.5))]
    f = an.forecast(few)
    assert f["overall"] == 6.5 and f["high"] - f["low"] == 2.0
    many = few * 3
    f = an.forecast(many)
    assert f["overall"] == 6.5 and (f["low"], f["high"]) == (6.0, 7.0)


def test_type_stats_from_practice_and_mock(db):
    db.tables["ielts_objective_attempts"] = [{"user_id": "u1", "section": "reading", "details": [
        {"type": "true_false_not_given", "correct": False}, {"type": "TFNG", "correct": True},
        {"type": "multiple_choice", "correct": True}]}]
    db.tables["ielts_mock_sections"] = [{"user_id": "u1", "section": "listening", "status": "completed",
                                         "content": {"questions": [{"id": 1, "type": "completion"}]},
                                         "result": {"details": [{"id": 1, "correct": True}]}}]
    rows = {(r["section"], r["type"]): r for r in an.type_stats("u1")}
    assert rows[("reading", "true_false_not_given")]["percent"] == 50
    assert rows[("listening", "completion")]["correct"] == 1     # type taken from the section content
