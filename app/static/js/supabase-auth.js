(function () {
  "use strict";

  const STORAGE_KEY = "ai-english-tutor-supabase-session";

  function safeJson(text) {
    try { return text ? JSON.parse(text) : {}; }
    catch (_) { return {}; }
  }

  function readStoredSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function writeStoredSession(session) {
    try {
      if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
      else localStorage.removeItem(STORAGE_KEY);
    } catch (_) {}
  }

  function normalizeError(body, status) {
    const message =
      body?.msg ||
      body?.message ||
      body?.error_description ||
      body?.error ||
      `Authentication request failed (${status})`;
    return { message: String(message), status };
  }

  function makeSession(body) {
    if (!body || !body.access_token) return null;

    const expiresIn = Number(body.expires_in || 3600);
    const expiresAt = Number(body.expires_at || 0) ||
      Math.floor(Date.now() / 1000) + expiresIn;

    return {
      access_token: body.access_token,
      refresh_token: body.refresh_token || null,
      token_type: body.token_type || "bearer",
      expires_in: expiresIn,
      expires_at: expiresAt,
      user: body.user || null
    };
  }

  function createClient(baseUrl, anonKey) {
    const authBase = String(baseUrl || "").replace(/\/+$/, "") + "/auth/v1";

    async function request(path, options = {}) {
      const headers = {
        "apikey": anonKey,
        "Content-Type": "application/json",
        ...(options.headers || {})
      };

      let response;
      try {
        response = await fetch(authBase + path, {
          ...options,
          headers
        });
      } catch (e) {
        return {
          body: {},
          error: { message: e?.message || "Network error", status: 0 },
          status: 0
        };
      }

      const text = await response.text();
      const body = safeJson(text);

      if (!response.ok) {
        return {
          body,
          error: normalizeError(body, response.status),
          status: response.status
        };
      }

      return { body, error: null, status: response.status };
    }

    async function signUp({ email, password }) {
      const { body, error } = await request("/signup", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });

      if (error) return { data: { user: null, session: null }, error };

      const session = makeSession(body);
      if (session) writeStoredSession(session);

      return {
        data: {
          user: body.user || session?.user || null,
          session
        },
        error: null
      };
    }

    async function signInWithPassword({ email, password }) {
      const { body, error } = await request("/token?grant_type=password", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });

      if (error) return { data: { user: null, session: null }, error };

      const session = makeSession(body);
      if (!session) {
        return {
          data: { user: body.user || null, session: null },
          error: { message: "Authentication succeeded but no session token was returned.", status: 500 }
        };
      }

      writeStoredSession(session);
      return {
        data: { user: body.user || session.user || null, session },
        error: null
      };
    }

    async function refreshSession(session) {
      if (!session?.refresh_token) return session;

      const { body, error } = await request("/token?grant_type=refresh_token", {
        method: "POST",
        body: JSON.stringify({ refresh_token: session.refresh_token })
      });

      if (error) return null;

      const refreshed = makeSession(body);
      if (refreshed) {
        writeStoredSession(refreshed);
        return refreshed;
      }
      return null;
    }

    async function getSession() {
      let session = readStoredSession();
      if (!session) return { data: { session: null }, error: null };

      const now = Math.floor(Date.now() / 1000);
      const expiresAt = Number(session.expires_at || 0);

      // Refresh a little before expiry.
      if (expiresAt && expiresAt <= now + 60) {
        session = await refreshSession(session);
        if (!session) {
          writeStoredSession(null);
          return { data: { session: null }, error: null };
        }
      }

      return { data: { session }, error: null };
    }

    async function signOut() {
      const session = readStoredSession();

      if (session?.access_token) {
        // Best-effort remote logout. Local logout must always complete.
        try {
          await request("/logout", {
            method: "POST",
            headers: {
              "Authorization": `Bearer ${session.access_token}`
            },
            body: "{}"
          });
        } catch (_) {}
      }

      writeStoredSession(null);
      return { error: null };
    }

    return {
      auth: {
        signUp,
        signInWithPassword,
        signOut,
        getSession
      }
    };
  }

  window.supabase = { createClient };
})();
