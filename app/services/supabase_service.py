import logging
import time
from functools import lru_cache

import httpx
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions
from supabase_auth.errors import AuthRetryableError
from fastapi import HTTPException, Header

from app.config import get_settings

settings = get_settings()
log = logging.getLogger(__name__)

# Network errors after which the request may not have reached Supabase or may have been cut off mid-way.
_TRANSIENT = (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, httpx.PoolTimeout)
# Errors where the request certainly never reached the server, so any method is safe to resend.
_NOT_SENT = (httpx.ConnectError, httpx.PoolTimeout)
_IDEMPOTENT = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "PATCH"}


def _safe_to_resend(request: httpx.Request) -> bool:
    if request.method in _IDEMPOTENT:
        return True
    # PostgREST upsert (POST with merge-duplicates) gives the same result when repeated.
    return "merge-duplicates" in request.headers.get("prefer", "")


class _RetryTransport(httpx.HTTPTransport):
    """Re-sends a request once or twice after a transient network failure (dropped / stale connection)."""

    attempts = 3

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.attempts):
            try:
                return super().handle_request(request)
            except _TRANSIENT as exc:
                last = attempt == self.attempts - 1
                if last or not (isinstance(exc, _NOT_SENT) or _safe_to_resend(request)):
                    raise
                log.warning("Supabase %s %s failed (%s: %s), retry %d",
                            request.method, request.url.path, type(exc).__name__, exc, attempt + 1)
                time.sleep(0.2 * (attempt + 1))
        raise RuntimeError("unreachable")


def _http_client() -> httpx.Client:
    # HTTP/1.1 on purpose: supabase-py defaults to HTTP/2, and one sync HTTP/2 connection shared by
    # FastAPI's worker threads fails under concurrent requests with
    # "httpx.ReadError: [Errno 11] Resource temporarily unavailable". The HTTP/1.1 pool is thread-safe.
    return httpx.Client(
        transport=_RetryTransport(http2=False),
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0),
        follow_redirects=True,
    )


def _make_client(key: str) -> Client:
    return create_client(settings.supabase_url, key, options=SyncClientOptions(httpx_client=_http_client()))


@lru_cache
def admin_db() -> Client:
    # Service-role key stays server-side only.
    return _make_client(settings.supabase_service_role_key)


@lru_cache
def auth_client() -> Client:
    return _make_client(settings.supabase_anon_key)


def require_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        result = auth_client().auth.get_user(token)
        user = result.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        confirmed = bool(getattr(user, "email_confirmed_at", None) or getattr(user, "confirmed_at", None))
        return {"id": str(user.id), "email": user.email, "email_confirmed": confirmed}
    except HTTPException:
        raise
    except (httpx.TransportError, AuthRetryableError):
        # A network hiccup is not an expired session: don't make the browser log the student out.
        log.exception("Supabase auth unreachable while checking the session")
        raise HTTPException(status_code=503, detail="Server is busy, please try again.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
