"""
Grouped IELTS Reading question types: Matching Headings, Summary completion with a word bank,
Diagram labelling. A group carries what its questions share (the list of headings, the word bank,
the summary text with gaps, the diagram); the website shows it once above its questions.
"""

from __future__ import annotations

import re

GROUP_SPEC = """
Organise the questions into groups as in a real IELTS paper and return them in "groups" (array).
Each group: type, instruction (the exam rubric, e.g. "Questions 1-5. Choose the correct heading for each
paragraph from the list of headings below."), question_ids (ids of its questions, consecutive),
plus by type:
- "matching_headings": label the passage paragraphs with capital letters at the start of each paragraph
  ("A ...", "B ..."), one heading question per paragraph ("question": "Paragraph B");
  options = a list of headings "i. ...", "ii. ...", with 2-3 more headings than questions; each question's
  options = that same list; the answer is the full heading text including its numeral.
- "summary_completion": "summary" = a 60-120 word summary of part of the passage with numbered gaps written
  as [Q<id>]; options = a word bank "A. word", "B. word", ... with 3-4 more items than gaps; each question:
  "question": "Gap <id>", options = the word bank; the answer is the full word-bank item (e.g. "C. migration").
- "diagram_labelling": "diagram" = {"title": string, "parts": [{"text": string, "x": 0-9, "y": 0-7, "w": 1-4,
  "h": 1-2, "gap": <question id or null>}], "links": [[part index, part index], ...]}: 5-9 parts of a device,
  structure or system described in the passage, laid out on a 10 x 8 grid; parts with a gap hide their label;
  each such question: "question": "Label <id>", options [], instruction "Write NO MORE THAN TWO WORDS from the
  passage"; the answer is the hidden label taken word-for-word from the passage.
- "standard": any other IELTS types (multiple choice, TRUE/FALSE/NOT GIVEN, YES/NO/NOT GIVEN, sentence or
  short-answer completion) with their own options/instruction.
"""

SPECIAL = {"matching_headings", "summary_completion", "diagram_labelling"}


def _match_option(value: str, options: list[str]) -> str | None:
    """'iv' / 'C' / 'migration' -> the full option text."""
    v = str(value or "").strip().lower().rstrip(".")
    for o in options:
        head = re.split(r"[.)]\s*", o.strip(), maxsplit=1)
        label = head[0].lower()
        rest = head[1].lower() if len(head) > 1 else ""
        if v in (o.strip().lower(), label, rest):
            return o
    return None


def _clean_diagram(d: dict) -> dict:
    parts = []
    for p in (d.get("parts") or [])[:12]:
        try:
            x, y = max(0, min(9, int(p.get("x", 0)))), max(0, min(7, int(p.get("y", 0))))
            w, h = max(1, min(10 - x, int(p.get("w", 2)))), max(1, min(8 - y, int(p.get("h", 1))))
        except (TypeError, ValueError):
            continue
        gap = p.get("gap")
        parts.append({"text": str(p.get("text") or ""), "x": x, "y": y, "w": w, "h": h,
                      "gap": int(gap) if str(gap or "").isdigit() else None})
    links = [[int(a), int(b)] for a, b in (d.get("links") or []) if str(a).isdigit() and str(b).isdigit()
             and int(a) < len(parts) and int(b) < len(parts)] if isinstance(d.get("links"), list) else []
    return {"title": str(d.get("title") or ""), "parts": parts, "links": links}


def clean_groups(content: dict, required: str | None = None) -> dict:
    """Validate groups; fix keys to full option text. Raises ValueError if a required type is missing."""
    questions = content.get("questions") or []
    by_id = {str(q.get("id")): q for q in questions}
    key = content.get("answer_key") or {}
    groups_out = []
    for g in content.get("groups") or []:
        gtype = str(g.get("type") or "standard")
        ids = [str(i) for i in (g.get("question_ids") or []) if str(i) in by_id]
        if not ids:
            continue
        group = {"type": gtype, "instruction": str(g.get("instruction") or ""), "question_ids": [int(i) if i.isdigit() else i for i in ids]}
        if gtype in ("matching_headings", "summary_completion"):
            options = [str(o) for o in (g.get("options") or []) if str(o).strip()]
            if len(options) < len(ids):
                raise ValueError(f"{gtype}: not enough options.")
            group["options"] = options
            if gtype == "summary_completion":
                group["summary"] = str(g.get("summary") or "")
            for qid in ids:
                q = by_id[qid]
                q["type"], q["options"] = gtype, options
                accepted = key.get(qid)
                values = accepted if isinstance(accepted, list) else [accepted]
                fixed = [m for m in (_match_option(v, options) for v in values) if m]
                if not fixed:
                    raise ValueError(f"{gtype}: the key of question {qid} is not one of the options.")
                key[qid] = fixed if isinstance(accepted, list) else fixed[0]
        elif gtype == "diagram_labelling":
            group["diagram"] = _clean_diagram(g.get("diagram") or {})
            if len(group["diagram"]["parts"]) < 3:
                raise ValueError("diagram_labelling: the diagram is missing.")
            for qid in ids:
                by_id[qid]["type"] = "diagram_labelling"
                by_id[qid]["options"] = []
        groups_out.append(group)
    if required and not any(g["type"] == required for g in groups_out):
        raise ValueError(f"The section has no {required} group.")
    content["groups"] = groups_out
    content["answer_key"] = key
    return content
