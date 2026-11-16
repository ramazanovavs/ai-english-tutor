import io

from PIL import Image

import app.routes.api as api
from app.services import screenshots


def png_bytes(size=(800, 600), exif=False):
    img = Image.new("RGB", size, (30, 120, 200))
    out = io.BytesIO()
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretPhoneMaker"          # camera maker tag
        img.save(out, "JPEG", exif=e)
    else:
        img.save(out, "PNG")
    return out.getvalue()


def send_feedback(client, db):
    from datetime import datetime, timezone
    fid = client.post("/api/feedback", json={"section": "Грамматика", "comment": "Кнопка не работает"}).json()["id"]
    db.tables["feedback"][-1]["created_at"] = datetime.now(timezone.utc).isoformat()   # the fake DB dates rows in the past
    return fid


def test_sanitize_strips_metadata_and_hidden_payload():
    clean = screenshots.sanitize(png_bytes(exif=True) + b"<script>alert(1)</script>")
    assert clean.startswith(b"\xff\xd8\xff") and b"SecretPhoneMaker" not in clean and b"<script>" not in clean
    big = screenshots.sanitize(png_bytes((4000, 3000)))
    assert max(Image.open(io.BytesIO(big)).size) == 1920


def test_rejects_non_images_and_svg():
    for bad in (b"<svg onload=alert(1)>", b"GIF89a....", b"\x89PNG\r\n\x1a\nbroken", b"MZ\x90\x00 exe", b""):
        try:
            screenshots.sanitize(bad)
            raise AssertionError(bad)
        except screenshots.BadImage:
            pass


def test_upload_flow(client, db, monkeypatch):
    fid = send_feedback(client, db)
    for i in range(3):
        r = client.post(f"/api/feedback/{fid}/screenshot", files={"file": ("s.png", png_bytes(), "image/png")})
        assert r.status_code == 200 and r.json()["count"] == i + 1
    assert client.post(f"/api/feedback/{fid}/screenshot", files={"file": ("s.png", png_bytes(), "image/png")}).status_code == 409
    assert client.post(f"/api/feedback/{fid}/screenshot",
                       files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")}).status_code in (400, 409)
    assert set(db.store) >= {f"feedback/{fid}/1.jpg", f"feedback/{fid}/3.jpg"}
    assert client.get("/api/feedback/mine").json()["items"][0]["screenshots"] == 3
    # only an admin can open them
    assert client.get(f"/api/admin/feedback/{fid}/screenshot/1").status_code == 403
    monkeypatch.setattr(api, "_is_admin", lambda user: True)
    r = client.get(f"/api/admin/feedback/{fid}/screenshot/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "no-store" in r.headers["cache-control"]


def test_cannot_attach_to_someone_elses_feedback(client, db):
    db.tables["feedback"] = [{"id": "other", "user_id": "u2", "context": {}, "created_at": "2026-11-16T10:00:00+00:00"}]
    r = client.post("/api/feedback/other/screenshot", files={"file": ("s.png", png_bytes(), "image/png")})
    assert r.status_code == 404


def test_late_upload_is_refused(client, db):
    db.tables["feedback"] = [{"id": "old", "user_id": "u1", "context": {}, "created_at": "2026-11-01T10:00:00+00:00"}]
    r = client.post("/api/feedback/old/screenshot", files={"file": ("s.png", png_bytes(), "image/png")})
    assert r.status_code == 409
