from app.services import ielts_exam as ex


def test_word_limit_and_numbers():
    two = "Write NO MORE THAN TWO WORDS AND/OR A NUMBER"
    assert ex.word_limit(two) == (2, True)
    assert ex.is_correct("twelve", ["12"], two)
    assert ex.is_correct("the library", ["library"], two)          # article ignored
    assert not ex.is_correct("the big old library", ["library"], two)
    assert ex.is_correct("1,500", ["1500"], two)
    assert not ex.is_correct("the bees", ["bees"], "Write ONE WORD ONLY")


def test_choice_questions_need_exact_option():
    opts = ["A. cheaper", "B. faster", "C. safer"]
    assert ex.is_correct("B. faster", ["B. faster"], None, opts)
    assert not ex.is_correct("faster", ["B. faster"], None, opts)


def test_mark_part_reports_question_type():
    content = {"questions": [{"id": 1, "type": "true_false_not_given", "question": "q1", "options": ["TRUE", "FALSE", "NOT GIVEN"]},
                             {"id": 2, "type": "completion", "question": "q2", "options": []}],
               "answer_key": {"1": "TRUE", "2": ["museum"]}, "explanations": {"1": "line 3"}}
    correct, details = ex.mark_part(content, {"1": "TRUE", "2": "park"})
    assert correct == 1
    assert [d["type"] for d in details] == ["true_false_not_given", "completion"]
    assert details[1]["accepted"] == ["museum"] and details[0]["explanation"] == "line 3"
