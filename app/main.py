import base64
import json
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from fastapi import FastAPI, Request
import logging
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.routes.api import router as api_router
from app.services import ai_gateway

settings = get_settings()
BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=settings.app_name)
app.include_router(api_router)

_rate: dict[str, deque] = defaultdict(deque)


def _token_subject(request: Request) -> str | None:
    """User id from the bearer token, for accounting only (the token itself is verified by require_user)."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = auth.split(" ", 1)[1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
        return str(sub)[:64] if sub else None
    except Exception:
        return None


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob: data:; font-src 'self' data:; "
       f"connect-src 'self' {settings.supabase_url}; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
       "form-action 'self'")


@app.middleware("http")
async def security_and_limits(request: Request, call_next):
    path = request.url.path
    subject = _token_subject(request) if path.startswith("/api/") else None
    if path.startswith("/api/"):
        key = subject or (request.client.host if request.client else "anon")
        q, now = _rate[key], time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= settings.api_requests_per_minute:
            return JSONResponse(status_code=429, content={"detail": "Too many requests. Please wait a minute."})
        q.append(now)
    t1 = ai_gateway.current_user.set(subject)
    t2 = ai_gateway.current_path.set(path)
    try:
        response = await call_next(request)
    finally:
        ai_gateway.current_user.reset(t1)
        ai_gateway.current_path.reset(t2)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(ai_gateway.AILimitExceeded)
async def ai_limit(request: Request, exc: Exception):
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Details go to the server log only; the learner gets a short message with a reference number.
    ref = uuid.uuid4().hex[:8]
    logging.getLogger(__name__).exception("Unhandled error %s on %s", ref, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Something went wrong on the server (ref. {ref}). Please try again."})

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "supabase_url": settings.supabase_url,
            "supabase_anon_key": settings.supabase_anon_key,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
