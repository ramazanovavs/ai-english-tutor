"""
Test harness: an in-memory Supabase (tables + storage) and a switched-off OpenAI.
No test ever reaches the network; a test that needs AI output sets it with the `ai` fixture.
"""

from __future__ import annotations

import copy
import itertools
import os
import pathlib
import sys

for _k in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
    os.environ.setdefault(_k, "https://tests.supabase.co")

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

_ids = itertools.count(1)


class _Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data) if isinstance(data, list) else None


class FakeQuery:
    def __init__(self, db: "FakeDB", name: str):
        self.db, self.name = db, name
        self.filters, self.op, self.payload = [], "select", None
        self._order, self._limit, self._single, self._on_conflict = None, None, False, None

    # filters
    def select(self, *a, **k): return self
    def eq(self, k, v): self.filters.append(lambda r: str(r.get(k)) == str(v)); return self
    def neq(self, k, v): self.filters.append(lambda r: str(r.get(k)) != str(v)); return self
    def lt(self, k, v): self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) < str(v)); return self
    def lte(self, k, v): self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) <= str(v)); return self
    def gt(self, k, v): self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) > str(v)); return self
    def gte(self, k, v): self.filters.append(lambda r: r.get(k) is not None and str(r.get(k)) >= str(v)); return self
    def in_(self, k, vs): vs = {str(v) for v in vs}; self.filters.append(lambda r: str(r.get(k)) in vs); return self
    def is_(self, k, v): self.filters.append(lambda r: r.get(k) is None if str(v) == "null" else r.get(k) == v); return self
    def order(self, k, desc=False, **_): self._order = (k, desc); return self
    def limit(self, n, *a): self._limit = n; return self
    def range(self, a, b): self._limit = b + 1; return self
    def single(self): self._single = True; return self
    def maybe_single(self): self._single = True; return self

    # writes
    def insert(self, row, **_): self.op, self.payload = "insert", row; return self
    def update(self, row): self.op, self.payload = "update", row; return self
    def upsert(self, row, on_conflict=None, **_): self.op, self.payload, self._on_conflict = "upsert", row, on_conflict; return self
    def delete(self): self.op = "delete"; return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op in ("insert", "upsert"):
            out = []
            for row in (self.payload if isinstance(self.payload, list) else [self.payload]):
                row = copy.deepcopy(row)
                if self.op == "upsert" and self._on_conflict:
                    keys = [k.strip() for k in self._on_conflict.split(",")]
                    hit = next((r for r in rows if all(r.get(k) == row.get(k) for k in keys)), None)
                    if hit:
                        hit.update(row)
                        out.append(copy.deepcopy(hit))
                        continue
                row.setdefault("id", f"{self.name[:6]}-{next(_ids)}")
                row.setdefault("created_at", f"2026-01-01T00:00:{next(_ids) % 60:02d}+00:00")
                for k, v in self.db.defaults.get(self.name, {}).items():
                    row.setdefault(k, copy.deepcopy(v))
                rows.append(row)
                out.append(copy.deepcopy(row))
            return _Result(out)
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(copy.deepcopy(self.payload))
        elif self.op == "delete":
            self.db.tables[self.name] = [r for r in rows if r not in hit]
        if self._order:
            k, desc = self._order
            hit = sorted(hit, key=lambda r: str(r.get(k) or ""), reverse=desc)
        if self._limit is not None:
            hit = hit[: self._limit]
        data = copy.deepcopy(hit)
        if self._single:
            data = data[0] if data else None
        return _Result(data)


class FakeBucket:
    def __init__(self, store: dict, name: str):
        self.store, self.name = store, name
    def upload(self, path, data, file_options=None, **_): self.store[path] = bytes(data)
    def update(self, path, data, file_options=None, **_): self.store[path] = bytes(data)
    def download(self, path):
        if path not in self.store:
            raise FileNotFoundError(path)
        return self.store[path]
    def remove(self, paths): [self.store.pop(p, None) for p in paths]; return []
    def list(self, prefix="", *a, **k):
        return [{"name": p[len(prefix):].lstrip("/")} for p in self.store if p.startswith(prefix)]


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.store: dict[str, bytes] = {}
        self.defaults = {
            "ielts_mock_tests": {"status": "in_progress", "metadata": {}},
            "ielts_mock_sections": {"answers": {}, "result": {}},
            "ielts_profiles": {},
        }
        db = self
        self.storage = type("Storage", (), {"from_": lambda _self, bucket: FakeBucket(db.store, bucket)})()

    def table(self, name):
        return FakeQuery(self, name)


def _app_modules():
    import app.routes.api  # noqa: F401  (imports every service and agent)
    import app.agents.ielts_coach, app.agents.ielts_verify, app.services.ielts_analytics  # noqa: F401,E401
    import app.services.storage_cleanup, app.services.ielts_pronunciation  # noqa: F401,E401
    return [m for name, m in list(sys.modules.items()) if name.startswith("app.") and m is not None]


class AI:
    """Set what the model 'answers': ai.json(fn) where fn(instructions, user_input) -> dict."""
    def __init__(self, monkeypatch):
        self.mp, self.calls = monkeypatch, []

    def json(self, responder):
        def ask_json(instructions, user_input):
            self.calls.append((instructions, user_input))
            return copy.deepcopy(responder(instructions, user_input))
        for m in _app_modules():
            if hasattr(m, "ask_json"):
                self.mp.setattr(m, "ask_json", ask_json)


@pytest.fixture(autouse=True)
def db(monkeypatch):
    fake = FakeDB()
    fake.tables["ielts_profiles"] = [{"id": "p1", "user_id": "u1", "exam_type": "Academic"}]
    fake.tables["profiles"] = [{"id": "u1", "preferred_explanation_language": "en"}]

    def offline(*a, **k):
        raise RuntimeError("OpenAI is switched off in tests")

    for m in _app_modules():
        if hasattr(m, "admin_db"):
            monkeypatch.setattr(m, "admin_db", lambda: fake)
        for fn in ("ask_json", "ask_text", "synthesize_speech", "transcribe_audio"):
            if hasattr(m, fn):
                monkeypatch.setattr(m, fn, offline)
    import app.services.ielts_pronunciation as pron
    monkeypatch.setattr(pron, "client", None)   # pronunciation falls back to "not assessed"
    import app.services.storage_cleanup as sc
    monkeypatch.setattr(sc, "maybe_run_in_background", lambda: None)
    return fake


@pytest.fixture
def ai(monkeypatch):
    return AI(monkeypatch)


@pytest.fixture
def client(db, monkeypatch):
    from fastapi.testclient import TestClient
    import app.routes.api as api
    from app.main import app
    app.dependency_overrides[api.require_user] = lambda: {"id": "u1", "email": "student@example.com"}
    monkeypatch.setattr(api, "ensure_profile", lambda *a, **k: None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def patch_everywhere(monkeypatch, name, value):
    """Replace a function in every app module that imported it."""
    for m in _app_modules():
        if hasattr(m, name):
            monkeypatch.setattr(m, name, value)


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()
