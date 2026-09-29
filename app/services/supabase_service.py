from functools import lru_cache
from supabase import create_client, Client
from fastapi import HTTPException, Header

from app.config import get_settings

settings = get_settings()


@lru_cache
def admin_db() -> Client:
    # Service-role key stays server-side only.
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


@lru_cache
def auth_client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def require_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")

    token = authorization.split(" ", 1)[1].strip()
    try:
        result = auth_client().auth.get_user(token)
        user = result.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        return {"id": str(user.id), "email": user.email}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
