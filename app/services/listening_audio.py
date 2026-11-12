"""
Audio for General English Listening practice.

* Each speaker gets their own TTS voice; lines are synthesized in parallel and joined
  into one MP3.
* Audio is generated ONCE per test, in the background right after the test is created,
  and kept in server memory (not in the database). It is dropped 30 minutes after the
  answers are checked, or after 2 hours if the test is never submitted.
* Before the answers are checked a learner may play the recording at most MAX_PLAYS times;
  afterwards replay is unlimited (review mode with the transcript).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.services.openai_service import synthesize_speech

MAX_PLAYS = 3
TTL_UNSUBMITTED = 2 * 60 * 60
TTL_AFTER_CHECK = 30 * 60
MAX_ENTRIES = 25  # keeps memory small on a free Render instance (~1 MB per test)

FEMALE_VOICES = ["marin", "coral"]
MALE_VOICES = ["cedar", "ash"]

PACE = {
    "A1": "Speak slowly and very clearly, with short pauses between sentences.",
    "A2": "Speak slowly and clearly, with short pauses between sentences.",
    "B1": "Speak clearly at a moderate, natural pace.",
    "B2": "Speak at a natural conversational pace.",
    "C1": "Speak at a natural, fairly fast native-speaker pace.",
    "C2": "Speak at a fully natural native-speaker pace.",
}

_lock = threading.Lock()
_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- MP3 joining

_BITRATES = {  # kbps, layer III
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _frame_length(header: bytes) -> int | None:
    if len(header) < 4 or header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
        return None
    version_bits = (header[1] >> 3) & 0x03      # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
    layer_bits = (header[1] >> 1) & 0x03        # 1 = layer III
    bitrate_idx = (header[2] >> 4) & 0x0F
    sr_idx = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01
    if version_bits == 1 or layer_bits != 1 or bitrate_idx in (0, 15) or sr_idx == 3:
        return None
    bitrate = _BITRATES[1 if version_bits == 3 else 2][bitrate_idx] * 1000
    sample_rate = _SAMPLE_RATES[version_bits][sr_idx]
    coefficient = 144 if version_bits == 3 else 72
    return coefficient * bitrate // sample_rate + padding


def _strip_mp3(data: bytes) -> bytes:
    """Remove ID3 tags and the Xing/Info/VBRI header frame so segments join cleanly."""
    if data[:3] == b"ID3" and len(data) > 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        footer = 10 if data[5] & 0x10 else 0
        data = data[10 + size + footer:]
    if len(data) > 128 and data[-128:-125] == b"TAG":
        data = data[:-128]
    start = 0
    while start < len(data) - 4 and _frame_length(data[start:start + 4]) is None:
        start += 1
    data = data[start:]
    first = _frame_length(data[:4])
    if first and any(tag in data[:first] for tag in (b"Xing", b"Info", b"VBRI")):
        data = data[first:]
    return data


def join_mp3(segments: list[bytes]) -> bytes:
    return b"".join(_strip_mp3(s) for s in segments if s)


# ---------------------------------------------------------------- synthesis

def _voice_map(speakers: list[dict]) -> dict[str, str]:
    female, male, voices = iter(FEMALE_VOICES), iter(MALE_VOICES), {}
    for s in speakers:
        pool = male if s.get("gender") == "male" else female
        voices[s["name"]] = next(pool, None) or (MALE_VOICES[0] if s.get("gender") == "male" else FEMALE_VOICES[0])
    return voices


def build_audio(test: dict) -> bytes:
    speakers = test.get("speakers") or [{"name": "Speaker", "gender": "female"}]
    voices = _voice_map(speakers)
    fmt = test.get("format") or "dialogue"
    style = {
        "dialogue": "This is one turn in a natural everyday conversation; sound like a real person.",
        "monologue": "This is part of a natural spoken talk.",
        "announcement": "This is a public announcement or recorded message; use a clear announcer style.",
    }.get(fmt, "")
    instructions = f"{PACE.get(test.get('level'), PACE['B1'])} {style} Do not add any words."

    # Merge consecutive lines of the same speaker into one request.
    chunks: list[tuple[str, str]] = []
    for line in test.get("script") or []:
        voice = voices.get(line["speaker"], FEMALE_VOICES[0])
        if chunks and chunks[-1][0] == voice and len(chunks[-1][1]) + len(line["text"]) < 3500:
            chunks[-1] = (voice, chunks[-1][1] + " " + line["text"])
        else:
            chunks.append((voice, line["text"]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        parts = list(pool.map(lambda c: synthesize_speech(c[1], voice=c[0], instructions=instructions), chunks))
    return join_mp3(parts)


# ---------------------------------------------------------------- cache

def _cleanup_locked() -> None:
    now = time.time()
    for key in [k for k, v in _cache.items() if v["expires"] < now]:
        _cache.pop(key, None)
    while len(_cache) > MAX_ENTRIES:
        oldest = min(_cache, key=lambda k: _cache[k]["created"])
        _cache.pop(oldest, None)


def _worker(test_id: str, builder, on_ready) -> None:
    entry = _cache.get(test_id)
    if entry is None:
        return
    try:
        entry["audio"] = builder()
        if not entry["audio"]:
            raise RuntimeError("empty audio")
    except Exception as exc:  # shown to the learner on Play
        entry["error"] = str(exc)
    finally:
        entry["ready"].set()
    if entry["audio"] and on_ready:
        try:
            on_ready(entry["audio"])
        except Exception:
            pass  # saving to the backup bank must never break the learner's test


def start(test_id: str, user_id: str, test: dict | None = None, *,
          builder=None, audio: bytes | None = None, on_ready=None, max_plays: int = MAX_PLAYS) -> None:
    """
    Begin preparing audio in the background as soon as the test exists.
    builder: callable returning MP3 bytes (default: TTS from the test script);
    audio: ready MP3 bytes (backup-bank test); on_ready(audio): called after TTS succeeds.
    """
    with _lock:
        _cleanup_locked()
        entry = {
            "user_id": user_id,
            "audio": audio,
            "error": None,
            "ready": threading.Event(),
            "plays": 0,
            "max_plays": max_plays,
            "checked": False,
            "created": time.time(),
            "expires": time.time() + TTL_UNSUBMITTED,
        }
        _cache[test_id] = entry
    if audio:
        entry["ready"].set()
        return
    builder = builder or (lambda: build_audio(test))
    threading.Thread(target=_worker, args=(test_id, builder, on_ready), daemon=True).start()


def get_for_play(test_id: str, user_id: str, test_loader, audio_loader=None,
                 max_plays: int = MAX_PLAYS) -> tuple[bytes, int | None]:
    """
    Return (mp3, plays_left). plays_left is None after the answers were checked.
    Raises PermissionError when the play limit is reached, LookupError if unknown.
    test_loader() is used to rebuild the audio if the server restarted;
    audio_loader(test) may return a builder (e.g. download from the backup bank) or None for TTS.
    """
    with _lock:
        entry = _cache.get(test_id)
    if entry is None:
        test = test_loader()
        if not test:
            raise LookupError("Listening practice not found.")
        start(test_id, user_id, test, builder=audio_loader(test) if audio_loader else None,
              max_plays=max_plays)
        with _lock:
            entry = _cache[test_id]
    if entry["user_id"] != user_id:
        raise LookupError("Listening practice not found.")

    limit = entry.get("max_plays", MAX_PLAYS)
    used_up = (f"The recording can be played only once, as in the exam." if limit == 1
               else f"You have used all {limit} plays. Answer the questions and check them.")
    with _lock:
        if not entry["checked"] and entry["plays"] >= limit:
            raise PermissionError(used_up)

    if not entry["ready"].wait(timeout=120):
        raise TimeoutError("The audio is still being prepared. Please try again in a moment.")
    if entry["error"] or not entry["audio"]:
        with _lock:
            _cache.pop(test_id, None)  # allow a fresh attempt on the next click
        raise RuntimeError(f"Could not create the audio: {entry['error'] or 'empty audio'}")

    with _lock:
        if entry["checked"]:
            return entry["audio"], None
        if entry["plays"] >= limit:
            raise PermissionError(used_up)
        entry["plays"] += 1
        return entry["audio"], limit - entry["plays"]


def plays_used(test_id: str) -> int:
    with _lock:
        entry = _cache.get(test_id)
        return entry["plays"] if entry else 0


def mark_checked(test_id: str) -> None:
    with _lock:
        entry = _cache.get(test_id)
        if entry:
            entry["checked"] = True
            entry["expires"] = time.time() + TTL_AFTER_CHECK
