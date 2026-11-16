"""
Screenshots attached to feedback.

Safety:
- only PNG, JPEG and WebP are accepted, checked by the file's own bytes (not by its name or the browser's type);
- every image is decoded and re-drawn by the server into a fresh JPEG: hidden data (EXIF with GPS and device,
  scripts hidden in a "polyglot" file, extra payload after the image) does not survive;
- size limits: 8 MB per file, 40 megapixels (protects against "decompression bombs"), 3 files per feedback;
- files live in the private Storage bucket; only an admin can open them, through the server;
- they are deleted automatically after RETENTION_DAYS.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

BUCKET = "listening-audio"            # private bucket (no public URLs)
MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 3
MAX_SIDE = 1920
RETENTION_DAYS = 90
Image.MAX_IMAGE_PIXELS = 40_000_000   # larger images raise DecompressionBombError

SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


class BadImage(ValueError):
    pass


def _looks_like_image(data: bytes) -> bool:
    return data.startswith(SIGNATURES) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def sanitize(data: bytes) -> bytes:
    """Return a clean JPEG copy of the screenshot or raise BadImage."""
    if not data or len(data) > MAX_BYTES:
        raise BadImage("The file is empty or larger than 8 MB.")
    if not _looks_like_image(data):
        raise BadImage("Only PNG, JPEG or WebP screenshots can be attached.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ("PNG", "JPEG", "WEBP"):
                raise BadImage("Only PNG, JPEG or WebP screenshots can be attached.")
            probe.verify()                        # structural check of the whole file
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)    # keep the picture upright, then drop the metadata
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)   # no exif / icc / comments are written
            return out.getvalue()
    except BadImage:
        raise
    except Exception as exc:   # broken file, bomb, unsupported content
        raise BadImage("The image could not be read. Please attach a normal screenshot (PNG or JPEG).") from exc


def path_for(feedback_id: str, n: int) -> str:
    return f"feedback/{feedback_id}/{n}.jpg"
