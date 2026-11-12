"""
Monthly check of AI scoring: the same control texts are scored again and compared with the baseline run.
A shift of more than half a band (IELTS) or of a CEFR level means the model changed its behaviour:
the admin is warned and the procedure document says what to do.
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.data import calibration_set as cs
from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)
TABLE = "ai_calibration_runs"
CEFR = ["A1", "A2", "B1", "B2", "C1", "C2"]
BAND_DRIFT = 0.5


def _score_all() -> list[dict]:
    from app.agents.ielts_agent import assess_writing
    from app.agents.writing_agent import analyze
    out = []
    for t in cs.TEXTS:
        row = {"id": t["id"], "intended_band": t["intended_band"], "intended_cefr": t["intended_cefr"]}
        try:
            r = assess_writing("Academic", 2, cs.PROMPT, t["text"])
            row["band"] = float(r.get("overall_band")) if r.get("overall_band") is not None else None
        except Exception as exc:
            row["band_error"] = type(exc).__name__
        try:
            w = analyze(t["text"], "academic")
            row["cefr"] = str(w.get("estimated_level") or "")[:2].upper() or None
            row["score"] = w.get("score")
        except Exception as exc:
            row["cefr_error"] = type(exc).__name__
        out.append(row)
    return out


def start_run() -> str:
    has_baseline = bool(admin_db().table(TABLE).select("id").eq("is_baseline", True).limit(1).execute().data)
    row = admin_db().table(TABLE).insert({"model": get_settings().openai_model, "status": "running",
                                         "is_baseline": not has_baseline}).execute().data[0]

    def work():
        try:
            results = _score_all()
            admin_db().table(TABLE).update({"status": "done", "results": results}).eq("id", row["id"]).execute()
        except Exception as exc:
            logger.exception("Calibration run failed")
            admin_db().table(TABLE).update({"status": "error", "error": str(exc)[:300]}).eq("id", row["id"]).execute()

    threading.Thread(target=work, daemon=True).start()
    return row["id"]


def set_baseline(run_id: str) -> None:
    admin_db().table(TABLE).update({"is_baseline": False}).eq("is_baseline", True).execute()
    admin_db().table(TABLE).update({"is_baseline": True}).eq("id", run_id).execute()


def compare(run: dict, baseline: dict | None) -> dict:
    """Per-text shift against the baseline; drift = any band shift > 0.5 or any CEFR shift of a level or more."""
    base = {r["id"]: r for r in (baseline or {}).get("results") or []}
    rows, drift = [], False
    for r in run.get("results") or []:
        b = base.get(r["id"], {})
        band_shift = (r["band"] - b["band"]) if r.get("band") is not None and b.get("band") is not None else None
        cefr_shift = (CEFR.index(r["cefr"]) - CEFR.index(b["cefr"])) \
            if r.get("cefr") in CEFR and b.get("cefr") in CEFR else None
        flag = (band_shift is not None and abs(band_shift) > BAND_DRIFT) or (cefr_shift is not None and cefr_shift != 0)
        drift = drift or flag
        rows.append({**r, "band_shift": band_shift, "cefr_shift": cefr_shift, "flag": flag})
    return {"rows": rows, "drift": drift}


def overview() -> dict:
    runs = admin_db().table(TABLE).select("*").order("created_at", desc=True).limit(24).execute().data or []
    baseline = next((r for r in runs if r.get("is_baseline")), None)
    out = []
    for r in runs:
        item = {k: r.get(k) for k in ("id", "created_at", "model", "status", "is_baseline", "error")}
        if r.get("status") == "done":
            item.update(compare(r, baseline if baseline and baseline["id"] != r["id"] else None))
        out.append(item)
    return {"runs": out, "baseline_id": baseline["id"] if baseline else None, "texts": len(cs.TEXTS),
            "drift_rule": f"band shift > {BAND_DRIFT} or a different CEFR level than the baseline"}
