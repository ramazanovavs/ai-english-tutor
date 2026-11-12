"""
Computer-delivered IELTS mock exam: preparation, timing and marking.

* Preparation: as soon as a mock starts, the whole paper is generated in the background
  (Listening Parts 1-4 with audio first, then Reading, Writing, Speaking), so the exam is
  not interrupted by generation. Listening audio is voiced once and stored in Supabase
  Storage (bucket "listening-audio"); a copy is kept in memory as a fallback.
* Timing lives on the server (mock metadata), so reloading the page never resets a clock.
  Listening follows the exam rhythm: time to look at the questions, the recording (played
  once), time to check, and 2 minutes to review at the end. Reading and Writing: 60 minutes.
  When a deadline has passed, the saved answers are marked automatically.
* Marking of Listening/Reading answers follows IELTS conventions: word limits are enforced,
  "12" = "twelve", hyphens/case/punctuation do not matter.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.services import listening_audio
from app.services.supabase_service import admin_db

logger = logging.getLogger(__name__)

VERSION = "cd_mock_v2"
BUCKET = "listening-audio"

PREVIEW_SECONDS = 30      # "you have some time to look at questions ..."
CHECK_SECONDS = 30        # "now you have 30 seconds to check your answers"
REVIEW_SECONDS = 120      # computer-delivered IELTS: 2 minutes to review at the end
LOAD_ALLOWANCE = 60       # network time for downloading four recordings
READING_SECONDS = 60 * 60
WRITING_SECONDS = 60 * 60
SPEAKING_SECONDS = 14 * 60
SUBMIT_GRACE = 45         # late submissions within this window are still accepted
AUDIO_RETRY_SECONDS = 20  # a failed download may be retried within this window

ORDER = ["listening", "reading", "writing", "speaking"]
PARTS = {"listening": 4, "reading": 3, "writing": 2, "speaking": 3}

_meta_lock = threading.Lock()
_preparing: set[str] = set()
_memory_audio: dict[str, bytes] = {}


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


# ---------------------------------------------------------------- metadata

def _get_meta(mock_id: str) -> dict:
    r = admin_db().table("ielts_mock_tests").select("metadata").eq("id", mock_id).limit(1).execute()
    return dict((r.data or [{}])[0].get("metadata") or {})


def update_meta(mock_id: str, fn) -> dict:
    """Read-modify-write of mock metadata under a lock (background threads write too)."""
    with _meta_lock:
        meta = _get_meta(mock_id)
        fn(meta)
        admin_db().table("ielts_mock_tests").update({"metadata": meta}).eq("id", mock_id).execute()
        return meta


# ---------------------------------------------------------------- audio storage

def _audio_path(mock_id: str, part: int) -> str:
    return f"ielts-mock/{mock_id}/part{part}.mp3"


MEMORY_AUDIO_LIMIT = 8  # ~2 mocks; the free Render instance has only 512 MB of RAM


def _remember(path: str, audio: bytes) -> None:
    _memory_audio.pop(path, None)
    _memory_audio[path] = audio
    while len(_memory_audio) > MEMORY_AUDIO_LIMIT:
        _memory_audio.pop(next(iter(_memory_audio)))


def store_audio(mock_id: str, part: int, audio: bytes) -> None:
    _remember(_audio_path(mock_id, part), audio)
    try:
        admin_db().storage.from_(BUCKET).upload(
            _audio_path(mock_id, part), audio, {"content-type": "audio/mpeg", "upsert": "true"})
    except Exception:
        logger.exception("Could not upload mock audio; keeping it in memory only")


def load_audio(mock_id: str, part: int) -> bytes | None:
    path = _audio_path(mock_id, part)
    if path in _memory_audio:
        return _memory_audio[path]
    try:
        data = admin_db().storage.from_(BUCKET).download(path)
        _remember(path, data)
        return data
    except Exception:
        logger.exception("Mock audio not found in storage")
        return None


# ---------------------------------------------------------------- preparation

def prepare(mock_id: str, user_id: str, test_type: str, generators: dict, save_section, get_section,
            on_ready=None, on_error=None) -> bool:
    """Start background generation of everything that is still missing. Returns False if already running."""
    if mock_id in _preparing:
        return False
    _preparing.add(mock_id)

    def set_prep(**values):
        update_meta(mock_id, lambda m: m.setdefault("prep", {}).update(values))

    def listening_part(part: int):
        row = get_section(user_id, mock_id, "listening", part)
        content = row["content"] if row else None
        if not content:
            content = generators["listening"](part)
            save_section(user_id, mock_id, "listening", part, content)
        meta = _get_meta(mock_id)
        if str(part) in (meta.get("listening_durations") or {}) and load_audio(mock_id, part):
            return
        audio = listening_audio.build_audio({
            "speakers": content.get("speakers"),
            "script": content.get("script"),
            "intro": content.get("intro"),
            "outro": f"That is the end of Part {part}." + (
                " You now have 30 seconds to check your answers." if part < 4 else ""),
            "voice_instructions": (
                "Read this as a recording from an English listening exam at a natural, native pace, "
                "with natural intonation. Do not add any words."),
        })
        store_audio(mock_id, part, audio)
        seconds = round(listening_audio.mp3_duration(audio), 1)
        update_meta(mock_id, lambda m: m.setdefault("listening_durations", {}).update({str(part): seconds}))

    def reading_part(part: int):
        if not get_section(user_id, mock_id, "reading", part):
            save_section(user_id, mock_id, "reading", part, generators["reading"](test_type, part))

    def writing_paper():
        if not (get_section(user_id, mock_id, "writing", 1) and get_section(user_id, mock_id, "writing", 2)):
            paper = generators["writing"](test_type)
            save_section(user_id, mock_id, "writing", 1, paper["task1"])
            save_section(user_id, mock_id, "writing", 2, paper["task2"])

    def speaking_paper():
        if not all(get_section(user_id, mock_id, "speaking", p) for p in (1, 2, 3)):
            paper = generators["speaking"]()
            for p in (1, 2, 3):
                save_section(user_id, mock_id, "speaking", p, paper[f"part{p}"])

    def with_retry(fn, *args):
        last = None
        for _ in range(2):
            try:
                return fn(*args)
            except Exception as exc:
                last = exc
                logger.exception("Mock preparation step failed")
        raise last

    def job():
        try:
            set_prep(status="running", step="Listening", error=None)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda p: with_retry(listening_part, p), [1, 2, 3, 4]))
            set_prep(listening_ready=True, step="Reading")
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda p: with_retry(reading_part, p), [1, 2, 3]))
            set_prep(step="Writing")
            with_retry(writing_paper)
            set_prep(step="Speaking")
            with_retry(speaking_paper)
            set_prep(status="ready", step="Done")
            if on_ready:
                try:
                    on_ready()
                except Exception:
                    logger.exception("Post-preparation step failed")
        except Exception as exc:
            # OpenAI unavailable or failing: complete the paper from the IELTS bank if possible.
            recovered = False
            if on_error:
                try:
                    recovered = bool(on_error())
                except Exception:
                    logger.exception("Backup paper could not be used")
            if recovered:
                set_prep(status="ready", step="Done", backup=True, error=None, listening_ready=True)
            else:
                set_prep(status="error", error=str(exc))
        finally:
            _preparing.discard(mock_id)

    threading.Thread(target=job, daemon=True).start()
    return True


def is_preparing(mock_id: str) -> bool:
    return mock_id in _preparing


# ---------------------------------------------------------------- timing

def listening_plan(durations: dict) -> list[dict]:
    """The fixed rhythm of the Listening test, in seconds from the start."""
    plan, t = [], 0.0
    for part in (1, 2, 3, 4):
        plan.append({"phase": "preview", "part": part, "start": t, "seconds": PREVIEW_SECONDS})
        t += PREVIEW_SECONDS
        d = float(durations.get(str(part)) or 0)
        plan.append({"phase": "audio", "part": part, "start": t, "seconds": d})
        t += d
        if part < 4:
            plan.append({"phase": "check", "part": part, "start": t, "seconds": CHECK_SECONDS})
            t += CHECK_SECONDS
    plan.append({"phase": "review", "part": 4, "start": t, "seconds": REVIEW_SECONDS})
    return plan


def section_seconds(section: str, meta: dict) -> int:
    if section == "listening":
        plan = listening_plan(meta.get("listening_durations") or {})
        return int(plan[-1]["start"] + plan[-1]["seconds"] + LOAD_ALLOWANCE)
    return {"reading": READING_SECONDS, "writing": WRITING_SECONDS, "speaking": SPEAKING_SECONDS}[section]


def start_timer(mock_id: str, section: str) -> dict:
    """Idempotent: the first call fixes the start time and deadline on the server."""
    def fn(meta):
        timing = meta.setdefault("timing", {})
        if section not in timing:
            started = now()
            timing[section] = {
                "started_at": iso(started),
                "deadline": iso(started + timedelta(seconds=section_seconds(section, meta))),
            }
    return update_meta(mock_id, fn)["timing"][section]


def deadline_passed(meta: dict, section: str, grace: int = 0) -> bool:
    deadline = parse(((meta.get("timing") or {}).get(section) or {}).get("deadline"))
    return bool(deadline and now() > deadline + timedelta(seconds=grace))


def mark_audio_served(mock_id: str, part: int) -> bool:
    """Each Listening recording is served once (a quick retry is allowed if the download failed)."""
    allowed = {"ok": False}

    def fn(meta):
        served = meta.setdefault("listening_served", {})
        first = parse(served.get(str(part)))
        if first is None:
            served[str(part)] = iso(now())
            allowed["ok"] = True
        elif now() - first <= timedelta(seconds=AUDIO_RETRY_SECONDS):
            allowed["ok"] = True

    update_meta(mock_id, fn)
    return allowed["ok"]


# ---------------------------------------------------------------- marking

_NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_LIMIT_WORDS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


def _numbers_to_digits(tokens: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in _NUMBER_WORDS:
            value = _NUMBER_WORDS[t]
            if 20 <= value <= 90 and i + 1 < len(tokens) and tokens[i + 1] in _NUMBER_WORDS \
                    and _NUMBER_WORDS[tokens[i + 1]] < 10:
                value += _NUMBER_WORDS[tokens[i + 1]]
                i += 1
            out.append(str(value))
        else:
            out.append(t)
        i += 1
    return out


def normalize_answer(value) -> str:
    text = str(value or "").lower().replace("’", "'")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)          # 1,500 -> 1500
    text = re.sub(r"[-/]", " ", text)                       # well-known = well known
    text = re.sub(r"[^\w\s'£$%.:]", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)          # keep decimal points only
    tokens = _numbers_to_digits(text.split())
    return " ".join(tokens)


def word_limit(instruction: str | None) -> tuple[int | None, bool]:
    """('Write NO MORE THAN TWO WORDS AND/OR A NUMBER') -> (2, True)."""
    text = str(instruction or "").upper()
    number_allowed = "A NUMBER" in text
    m = re.search(r"NO MORE THAN (\w+) WORDS?", text)
    if m:
        n = _LIMIT_WORDS.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else None)
        return n, number_allowed
    if re.search(r"ONE WORD ONLY", text):
        return 1, number_allowed
    if re.search(r"A NUMBER ONLY|ONE NUMBER", text):
        return 0, True
    return None, number_allowed


def is_correct(given, accepted, instruction: str | None = None, options: list | None = None) -> bool:
    g = normalize_answer(given)
    if not g:
        return False
    accepted_list = accepted if isinstance(accepted, list) else [accepted]
    accepted_norm = [normalize_answer(a) for a in accepted_list if str(a).strip()]
    if options:  # choice questions: the selected option must match exactly
        return g in accepted_norm
    limit, number_ok = word_limit(instruction)
    if limit is not None:
        tokens = g.split()
        words = [w for w in tokens if not (number_ok and re.fullmatch(r"[\d.:£$%]+", w))]
        numbers = len(tokens) - len(words)
        if len(words) > limit or numbers > 1:  # "AND/OR A NUMBER" allows one number on top
            return False
    strip_article = lambda s: re.sub(r"^(a|an|the) ", "", s)
    return any(g == a or strip_article(g) == strip_article(a) for a in accepted_norm)


def mark_part(content: dict, answers: dict) -> tuple[int, list[dict]]:
    key = content.get("answer_key") or {}
    explanations = content.get("explanations") or {}
    correct, details = 0, []
    for q in content.get("questions") or []:
        qid = str(q.get("id"))
        given = str((answers or {}).get(qid, "")).strip()
        accepted = key.get(qid, [])
        ok = is_correct(given, accepted, q.get("instruction"), q.get("options"))
        correct += int(ok)
        details.append({
            "id": qid,
            "question": q.get("question"),
            "answer": given,
            "accepted": accepted if isinstance(accepted, list) else [accepted],
            "correct": ok,
            "explanation": explanations.get(qid, ""),
        })
    return correct, details
