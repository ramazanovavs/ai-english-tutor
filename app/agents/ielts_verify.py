"""
Automatic check of answer keys for generated Reading and Listening sets.

1. A separate "solver" call answers every question from the passage/transcript WITHOUT the key.
2. Answers that disagree with the key (or that the solver marks ambiguous) go to a "reviewer"
   call, which corrects the key or rewrites the question so that exactly one answer is defensible.
3. The fixed questions are solved again once; anything still disputed is listed in
   content["verification"]["unverified"] (shown to the admin in the IELTS bank).
If the AI is unavailable during the check, the set is kept as generated and marked unchecked.
"""

from __future__ import annotations

import json
import logging

from app.services.openai_service import ask_json
from app.services.ielts_exam import is_correct

logger = logging.getLogger(__name__)

SOLVER = """
You are a careful IELTS candidate with perfect English. Using ONLY the {source} below, answer every question.
Follow each instruction and word limit exactly. For a question with options, return the exact text of the chosen
option. If a question has no single answer that the {source} clearly supports, add its id to "ambiguous".
Return JSON: {{"answers": {{"<id>": "<answer>", ...}}, "ambiguous": ["<id>", ...]}}
"""

REVIEWER = """
You are an IELTS item reviewer. Some questions below were answered differently by an independent candidate
than the answer key says. For EACH listed question, check the {source} and fix the item:
- if the key is wrong, correct it;
- if two answers are defensible or the answer is not clearly in the {source}, rewrite the question and/or options
  so that exactly one answer is supported by the {source}, keeping the same question type and word limit.
Never change the {source}. Return JSON:
{{"fixes": [{{"id": <id>, "question": string, "options": [..] or [], "answer": [accepted answers], "explanation": string}}]}}
"""


def _key_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if str(value or "").strip() else []


def _public(questions: list[dict]) -> list[dict]:
    return [{k: q.get(k) for k in ("id", "type", "question", "options", "instruction") if k in q} for q in questions]


def _solve(source_name: str, source: str, questions: list[dict]) -> tuple[dict, set]:
    data = ask_json(SOLVER.format(source=source_name),
                    f"{source_name.upper()}:\n{source}\n\nQUESTIONS:\n{json.dumps(_public(questions), ensure_ascii=False)}")
    answers = {str(k): str(v) for k, v in (data.get("answers") or {}).items()}
    return answers, {str(x) for x in (data.get("ambiguous") or [])}


def _disputed(questions: list[dict], key: dict, answers: dict, ambiguous: set) -> list[dict]:
    out = []
    for q in questions:
        qid = str(q.get("id"))
        given = answers.get(qid, "")
        if qid in ambiguous or not is_correct(given, _key_list(key.get(qid)), q.get("instruction"), q.get("options")):
            out.append({"id": q.get("id"), "question": q.get("question"), "options": q.get("options") or [],
                        "instruction": q.get("instruction"), "answer_key": _key_list(key.get(qid)),
                        "candidate_answer": given, "candidate_marked_ambiguous": qid in ambiguous})
    return out


def verify(content: dict, source_field: str, source_name: str) -> dict:
    """Check and repair content["answer_key"] in place; returns content with a "verification" report."""
    questions = content.get("questions") or []
    key = content.get("answer_key") or {}
    source = str(content.get(source_field) or "")
    if not questions or not source:
        return content
    list_keys = any(isinstance(v, list) for v in key.values())
    report = {"checked": len(questions), "fixed": [], "unverified": [], "status": "ok"}
    try:
        answers, ambiguous = _solve(source_name, source, questions)
        disputed = _disputed(questions, key, answers, ambiguous)
        if disputed:
            fixes = ask_json(REVIEWER.format(source=source_name),
                             f"{source_name.upper()}:\n{source}\n\nDISPUTED ITEMS:\n{json.dumps(disputed, ensure_ascii=False)}"
                             ).get("fixes") or []
            by_id = {str(q.get("id")): q for q in questions}
            for f in fixes:
                q = by_id.get(str(f.get("id")))
                accepted = _key_list(f.get("answer"))
                if not q or not accepted:
                    continue
                if f.get("question"):
                    q["question"] = str(f["question"])
                if isinstance(f.get("options"), list) and (q.get("options") or f["options"]):
                    q["options"] = [str(o) for o in f["options"]]
                key[str(q.get("id"))] = accepted if list_keys else accepted[0]
                if f.get("explanation"):
                    content.setdefault("explanations", {})[str(q.get("id"))] = str(f["explanation"])
                report["fixed"].append(str(q.get("id")))
            # Second, final check of the disputed items only.
            recheck = [by_id[str(d["id"])] for d in disputed if str(d["id"]) in by_id]
            answers2, ambiguous2 = _solve(source_name, source, recheck)
            report["unverified"] = [str(d["id"]) for d in _disputed(recheck, key, answers2, ambiguous2)]
        content["answer_key"] = key
    except Exception as exc:
        logger.exception("Answer-key verification unavailable")
        report["status"] = f"not checked: {exc}"
    content["verification"] = report
    return content
