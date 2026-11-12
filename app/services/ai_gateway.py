"""
AI gateway: every call to OpenAI goes through here.
- per-user limits (burst and daily) and a global daily limit, so one account or a script cannot run up the bill;
- a journal of AI calls (public.ai_usage): who, which model, how many tokens/characters, how long, success —
  never the prompts or the learner's texts;
- the current user is taken from the request (set by the middleware in app.main).
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_user", default=None)
current_path: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_path", default=None)


class AILimitExceeded(Exception):
    """Raised when a user or the whole site has used up its AI allowance."""


_lock = threading.Lock()
_burst: dict[str, deque] = defaultdict(deque)          # user -> timestamps of recent calls
_day = {"date": None, "users": defaultdict(lambda: {"calls": 0, "tts": 0}), "total": 0, "loaded": set()}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _roll_day() -> None:
    if _day["date"] != _today():
        _day.update(date=_today(), users=defaultdict(lambda: {"calls": 0, "tts": 0}), total=0, loaded=set())


def _load_user_today(user_id: str) -> None:
    """After a restart, continue counting from the journal instead of from zero."""
    if user_id in _day["loaded"]:
        return
    _day["loaded"].add(user_id)
    try:
        from app.services.supabase_service import admin_db
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows = admin_db().table("ai_usage").select("kind,units").eq("user_id", user_id).gte("created_at", start) \
            .execute().data or []
        u = _day["users"][user_id]
        u["calls"] = max(u["calls"], len(rows))
        u["tts"] = max(u["tts"], sum(int(r.get("units") or 0) for r in rows if r.get("kind") == "tts"))
    except Exception:
        pass


def check(kind: str, units: int = 0) -> None:
    """Call before an AI request. kind: text | stream | json | tts | stt | audio."""
    user = current_user.get()
    with _lock:
        _roll_day()
        if _day["total"] >= settings.ai_daily_calls_limit:
            raise AILimitExceeded("The site has reached today's AI limit. Please try again tomorrow.")
        if user:
            _load_user_today(user)
            u = _day["users"][user]
            if u["calls"] >= settings.ai_user_calls_per_day:
                raise AILimitExceeded("You have reached today's limit of AI requests. Please continue tomorrow.")
            if kind == "tts" and u["tts"] + units > settings.ai_user_tts_chars_per_day:
                raise AILimitExceeded("You have reached today's limit for voiced audio. Please continue tomorrow.")
            q = _burst[user]
            now = time.monotonic()
            while q and now - q[0] > 600:
                q.popleft()
            if len(q) >= settings.ai_user_calls_per_10min:
                raise AILimitExceeded("Too many AI requests in a short time. Please wait a few minutes.")
            q.append(now)
            u["calls"] += 1
            if kind == "tts":
                u["tts"] += units
        _day["total"] += 1


def log(kind: str, model: str, *, ok: bool, started: float, input_tokens: int | None = None,
        output_tokens: int | None = None, units: int | None = None, error: str | None = None) -> None:
    """Record one AI call (no prompt text). Written in the background so the learner does not wait."""
    row = {
        "user_id": current_user.get(), "path": (current_path.get() or "")[:120], "kind": kind, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens, "units": units,
        "duration_ms": int((time.monotonic() - started) * 1000), "ok": ok, "error": (error or "")[:200] or None,
        "cost_usd": estimate_cost(kind, input_tokens, output_tokens, units),
    }

    def write():
        try:
            from app.services.supabase_service import admin_db
            admin_db().table("ai_usage").insert(row).execute()
        except Exception:
            logger.debug("AI usage journal is not available", exc_info=True)

    threading.Thread(target=write, daemon=True).start()


def estimate_cost(kind: str, input_tokens: int | None, output_tokens: int | None, units: int | None) -> float | None:
    """Approximate cost with the prices set in the settings (USD); a guide, not an invoice."""
    if kind == "tts":
        return round((units or 0) / 1_000_000 * settings.ai_price_tts_per_m_chars, 6)
    if kind == "stt":
        return round((units or 0) / 60 * settings.ai_price_stt_per_minute, 6)   # units = seconds of audio
    if input_tokens is None and output_tokens is None:
        return None
    return round((input_tokens or 0) / 1_000_000 * settings.ai_price_input_per_m
                 + (output_tokens or 0) / 1_000_000 * settings.ai_price_output_per_m, 6)


def today_summary() -> dict:
    with _lock:
        _roll_day()
        return {"date": _day["date"], "calls_total": _day["total"], "limit_total": settings.ai_daily_calls_limit,
                "users_active": len(_day["users"])}
