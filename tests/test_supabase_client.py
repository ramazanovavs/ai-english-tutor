import httpx
import pytest

from app.services import supabase_service as ss


class _Flaky(ss._RetryTransport):
    """Fails the first `fails` sends with `exc`, then answers 200."""

    def __init__(self, exc, fails):
        super().__init__(http2=False)
        self.exc, self.fails, self.calls = exc, fails, 0

    def handle_request(self, request):
        return super().handle_request(request)


def _client(exc, fails, monkeypatch):
    t = _Flaky(exc, fails)

    def send(self, request):  # replaces the real network send in httpx.HTTPTransport
        t.calls += 1
        if t.calls <= t.fails:
            raise t.exc("Resource temporarily unavailable")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send)
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    return httpx.Client(transport=t), t


def test_get_is_retried_after_read_error(monkeypatch):
    c, t = _client(httpx.ReadError, 1, monkeypatch)
    assert c.get("https://x.supabase.co/rest/v1/p").status_code == 200
    assert t.calls == 2


def test_upsert_and_patch_are_retried(monkeypatch):
    c, t = _client(httpx.ReadError, 2, monkeypatch)
    r = c.post("https://x.supabase.co/rest/v1/p", headers={"Prefer": "resolution=merge-duplicates,return=representation"})
    assert r.status_code == 200 and t.calls == 3
    c, t = _client(httpx.ReadError, 1, monkeypatch)
    assert c.patch("https://x.supabase.co/rest/v1/p").status_code == 200


def test_plain_insert_not_resent_after_read_error(monkeypatch):
    # The insert may already have been stored; resending could duplicate the row.
    c, t = _client(httpx.ReadError, 1, monkeypatch)
    with pytest.raises(httpx.ReadError):
        c.post("https://x.supabase.co/rest/v1/p")
    assert t.calls == 1


def test_insert_resent_when_never_sent(monkeypatch):
    c, t = _client(httpx.ConnectError, 1, monkeypatch)
    assert c.post("https://x.supabase.co/rest/v1/p").status_code == 200


def test_gives_up_after_three_attempts(monkeypatch):
    c, t = _client(httpx.ReadError, 5, monkeypatch)
    with pytest.raises(httpx.ReadError):
        c.get("https://x.supabase.co/rest/v1/p")
    assert t.calls == 3


def test_supabase_clients_use_http11():
    c = ss._http_client()
    assert c._transport._pool._http2 is False and c._transport._pool._http1 is True


def test_auth_network_error_is_503_not_logout(monkeypatch):
    from supabase_auth.errors import AuthRetryableError

    class _Auth:
        def get_user(self, token):
            raise AuthRetryableError("Resource temporarily unavailable", 0)

    class _C:
        auth = _Auth()

    monkeypatch.setattr(ss, "auth_client", lambda: _C())
    with pytest.raises(ss.HTTPException) as e:
        ss.require_user("Bearer abc")
    assert e.value.status_code == 503
