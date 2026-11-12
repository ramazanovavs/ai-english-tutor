import pytest
from app.agents.ielts_reading_types import clean_groups


def base():
    return {"questions": [{"id": i, "type": "x", "question": f"q{i}", "options": []} for i in range(1, 7)],
            "answer_key": {"1": "ii", "2": "i", "3": "B", "4": "A", "5": "valve", "6": "pump"}}


def test_groups_are_normalised():
    c = base()
    c["groups"] = [
        {"type": "matching_headings", "question_ids": [1, 2], "options": ["i. Early history", "ii. Modern uses", "iii. Costs"]},
        {"type": "summary_completion", "question_ids": [3, 4], "summary": "It is [Q3] and [Q4].", "options": ["A. cheap", "B. light", "C. new"]},
        {"type": "diagram_labelling", "question_ids": [5, 6], "diagram": {"title": "Pump", "parts": [
            {"text": "tank", "x": 1, "y": 1, "w": 2, "h": 1}, {"gap": 5, "x": 4, "y": 1, "w": 2, "h": 1},
            {"gap": 6, "x": 7, "y": 1, "w": 2, "h": 1}], "links": [[0, 1], [1, 2]]}},
    ]
    out = clean_groups(c, "summary_completion")
    assert out["answer_key"]["1"] == "ii. Modern uses"       # key fixed to the full option text
    assert out["answer_key"]["3"] == "B. light"
    assert {q["type"] for q in out["questions"]} == {"matching_headings", "summary_completion", "diagram_labelling"}


def test_bad_key_and_missing_group_are_rejected():
    c = base()
    c["groups"] = [{"type": "matching_headings", "question_ids": [1, 2], "options": ["i. A", "iii. C"]}]
    with pytest.raises(ValueError):
        clean_groups(c)
    with pytest.raises(ValueError):
        clean_groups(base() | {"groups": []}, "diagram_labelling")
