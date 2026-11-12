"""
IELTS learning analytics: accuracy by question type, band history and a forecast with a range.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from app.services.supabase_service import admin_db
from app.services.ielts_scoring import overall_band, half_band_nearest

SECTIONS = ("listening", "reading", "writing", "speaking")

# Exam-style families of question types (what a learner would train separately).
FAMILIES = {
    "multiple_choice": "Multiple choice",
    "true_false_not_given": "TRUE / FALSE / NOT GIVEN",
    "yes_no_not_given": "YES / NO / NOT GIVEN",
    "matching": "Matching information",
    "matching_headings": "Matching headings",
    "summary_completion": "Summary completion (word bank)",
    "diagram_labelling": "Diagram labelling",
    "completion": "Form / note / sentence completion",
    "short_answer": "Short-answer questions",
}
ALIASES = {
    "mcq": "multiple_choice", "multiple choice": "multiple_choice", "tfng": "true_false_not_given",
    "true/false/not given": "true_false_not_given", "true_false": "true_false_not_given",
    "ynng": "yes_no_not_given", "matching-style": "matching", "matching_information": "matching",
    "matching_features": "matching", "sentence_completion": "completion", "note_completion": "completion",
    "form_completion": "completion", "table_completion": "completion", "gap": "completion",
    "short answer": "short_answer", "short-answer": "short_answer",
}
TRAINABLE = {
    "reading": ["multiple_choice", "true_false_not_given", "yes_no_not_given", "matching", "matching_headings",
                "summary_completion", "diagram_labelling", "completion", "short_answer"],
    "listening": ["multiple_choice", "completion", "short_answer"],
}


def family(qtype: str | None) -> str | None:
    t = str(qtype or "").strip().lower()
    t = ALIASES.get(t, t)
    return t if t in FAMILIES else None


def _add(stats: dict, section: str, qtype: str | None, correct: bool) -> None:
    fam = family(qtype)
    if not fam:
        return
    s = stats.setdefault((section, fam), {"correct": 0, "total": 0})
    s["total"] += 1
    s["correct"] += int(bool(correct))


def type_stats(user_id: str) -> list[dict]:
    db = admin_db()
    stats: dict = {}
    # Full mocks: question types come from the section content.
    rows = (db.table("ielts_mock_sections").select("section,content,result,status")
            .eq("user_id", user_id).eq("status", "completed").execute().data or [])
    for r in rows:
        if r.get("section") not in ("listening", "reading"):
            continue
        types = {str(q.get("id")): q.get("type") for q in (r.get("content") or {}).get("questions") or []}
        for d in (r.get("result") or {}).get("details") or []:
            _add(stats, r["section"], d.get("type") or types.get(str(d.get("id"))), d.get("correct"))
    # Practice sets: the type is stored with each answer.
    rows = (db.table("ielts_objective_attempts").select("section,details")
            .eq("user_id", user_id).execute().data or [])
    for r in rows:
        for d in r.get("details") or []:
            if isinstance(d, dict) and "correct" in d:
                _add(stats, r["section"], d.get("type"), d.get("correct"))
    out = []
    for (section, fam), s in stats.items():
        out.append({"section": section, "type": fam, "label": FAMILIES[fam], **s,
                    "percent": round(s["correct"] / s["total"] * 100) if s["total"] else 0,
                    "trainable": fam in TRAINABLE.get(section, [])})
    return sorted(out, key=lambda x: (x["percent"], -x["total"]))


def history(user_id: str) -> list[dict]:
    rows = (admin_db().table("ielts_band_history").select("section,band,source,created_at")
            .eq("user_id", user_id).execute().data or [])
    rows = [r for r in rows if r.get("section") in SECTIONS + ("overall",)]
    return sorted(rows, key=lambda r: str(r.get("created_at")))


def forecast(hist: list[dict]) -> dict | None:
    """Mean of the latest results per skill; the range widens when there is little or uneven evidence."""
    latest: dict[str, list[float]] = {}
    for r in hist:
        if r["section"] in SECTIONS:
            latest.setdefault(r["section"], []).append(float(r["band"]))
    if not all(s in latest for s in SECTIONS):
        return {"overall": None, "missing": [s for s in SECTIONS if s not in latest]}
    per_skill, spread = {}, []
    for s in SECTIONS:
        recent = latest[s][-3:]
        per_skill[s] = half_band_nearest(statistics.mean(recent))
        spread.append(statistics.pstdev(recent) if len(recent) > 1 else 0.75)
    centre = overall_band(list(per_skill.values()))
    margin = 0.5 if statistics.mean(spread) <= 0.5 else 1.0
    evidence = sum(min(len(latest[s]), 3) for s in SECTIONS)
    if evidence < 8:
        margin = max(margin, 1.0)
    return {"overall": centre, "low": max(0.0, centre - margin), "high": min(9.0, centre + margin),
            "skills": per_skill, "results_used": evidence}


def analytics(user_id: str) -> dict:
    stats = type_stats(user_id)
    hist = history(user_id)
    weak = [s for s in stats if s["total"] >= 3 and s["percent"] < 70][:3]
    return {"by_type": stats, "weakest": weak, "history": hist, "forecast": forecast(hist),
            "generated_at": datetime.now(timezone.utc).isoformat()}
