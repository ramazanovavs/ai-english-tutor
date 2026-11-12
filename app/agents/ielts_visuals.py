"""
Academic Writing Task 1 visuals.

The model returns the DATA of a chart/table/process/map; the website draws it (SVG), so the
candidate analyses a real visual instead of a text description, as in the exam.
"""

from __future__ import annotations

import random

CHART_TYPES = ["bar", "line", "pie", "table", "process", "map"]

CHART_SPEC = """
"chart" (object) describing the visual that the website will DRAW. Do not describe the data in "prompt".
Common fields: type (one of "bar", "line", "pie", "table", "process", "map"), title (string).
- bar / line: categories (array of 4-8 x-axis labels, e.g. years or countries),
  series (array of 1-4 objects {name, values}; values are numbers, one per category),
  y_label (string, include the unit, e.g. "Percentage of households (%)"), x_label (string).
- pie: categories (array of 3-6 segment labels), series (array of 1-3 objects {name, values}); each
  series is one pie (e.g. "2000" and "2020"); values are percentages summing to 100.
- table: columns (array of header strings), rows (array of arrays; first cell is the row label,
  other cells numbers), unit (string).
- process: steps (array of 5-10 short strings, in order), cycle (boolean: true if the last step leads back).
- map: panels (array of exactly 2 objects {title, features}); title like "1995" / "Now";
  features: array of 5-9 objects {label, x, y, w, h} on a 10x10 grid (integers, x+w<=10, y+h<=10);
  show realistic changes between the two panels (things added, removed, enlarged, moved).
The data must contain clear trends, comparisons and at least one exception worth reporting.
"""


def pick_type() -> str:
    # Rough frequency in the exam: charts most common, then tables, processes and maps.
    return random.choices(CHART_TYPES, weights=[25, 22, 12, 15, 13, 13])[0]


def _nums(values, n) -> list[float]:
    out = []
    for v in list(values or [])[:n]:
        try:
            out.append(round(float(v), 2))
        except (TypeError, ValueError):
            out.append(0.0)
    return out + [0.0] * (n - len(out))


def clean_chart(chart: dict | None) -> dict:
    """Validate the model's chart data; raises ValueError so the caller can regenerate."""
    if not isinstance(chart, dict):
        raise ValueError("Task 1 visual is missing.")
    kind = str(chart.get("type") or "").lower()
    title = str(chart.get("title") or "").strip()
    if kind in ("bar", "line", "pie"):
        cats = [str(c) for c in (chart.get("categories") or [])][:10]
        series = [
            {"name": str(s.get("name") or f"Series {i + 1}"), "values": _nums(s.get("values"), len(cats))}
            for i, s in enumerate((chart.get("series") or [])[:4]) if isinstance(s, dict)
        ]
        if len(cats) < 2 or not series:
            raise ValueError("Task 1 chart has no data.")
        out = {"type": kind, "title": title, "categories": cats, "series": series,
               "y_label": str(chart.get("y_label") or ""), "x_label": str(chart.get("x_label") or "")}
        if kind == "pie":
            out["series"] = series[:3]
        return out
    if kind == "table":
        cols = [str(c) for c in (chart.get("columns") or [])][:8]
        rows = [[str(r[0])] + [str(x) for x in r[1:len(cols)]] for r in (chart.get("rows") or []) if r][:10]
        if len(cols) < 2 or len(rows) < 2:
            raise ValueError("Task 1 table has no data.")
        return {"type": "table", "title": title, "columns": cols, "rows": rows, "unit": str(chart.get("unit") or "")}
    if kind == "process":
        steps = [str(s) for s in (chart.get("steps") or []) if str(s).strip()][:12]
        if len(steps) < 3:
            raise ValueError("Task 1 process has too few steps.")
        return {"type": "process", "title": title, "steps": steps, "cycle": bool(chart.get("cycle"))}
    if kind == "map":
        panels = []
        for p in (chart.get("panels") or [])[:2]:
            feats = []
            for f in (p.get("features") or [])[:12]:
                try:
                    x, y = max(0, min(9, int(f.get("x", 0)))), max(0, min(9, int(f.get("y", 0))))
                    w, h = max(1, min(10 - x, int(f.get("w", 1)))), max(1, min(10 - y, int(f.get("h", 1))))
                except (TypeError, ValueError):
                    continue
                feats.append({"label": str(f.get("label") or ""), "x": x, "y": y, "w": w, "h": h})
            panels.append({"title": str(p.get("title") or ""), "features": feats})
        if len(panels) != 2 or not all(p["features"] for p in panels):
            raise ValueError("Task 1 map needs two panels.")
        return {"type": "map", "title": title, "panels": panels}
    raise ValueError(f"Unknown Task 1 visual type: {kind}")


def chart_as_text(chart: dict | None) -> str:
    """Plain-text version of the visual for the examiner model (it cannot see the drawing)."""
    if not chart:
        return ""
    import json
    return "\n\nVISUAL SHOWN TO THE CANDIDATE (data):\n" + json.dumps(chart, ensure_ascii=False)
