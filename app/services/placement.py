"""
Placement test (about 30 minutes):
1. core — 18 adaptive grammar/vocabulary items from a fixed bank (3-parameter logistic model, EAP estimate);
2. reading — a text at the estimated level with 6 questions (AI-generated while the learner does part 1...
   prepared in the background right after part 1);
3. listening — a recording at the estimated level (backup bank first, otherwise generated and voiced);
4. writing — a short text (can be skipped);
5. speaking — two recorded answers, transcribed; pronunciation from audio (can be skipped).
Result: a CEFR sublevel (A1.1 ... C2) for every skill and overall, saved to the roadmap and the student model.
Answer keys stay on the server.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import uuid

from app.data import placement_items as bank
from app.data import roadmap as rm
from app.services import roadmap as store
from app.services.supabase_service import admin_db
from app.services.openai_service import ask_json, transcribe_audio
from app.agents.ielts_rubrics import DATA_GUARD, learner_block, language_rule

logger = logging.getLogger(__name__)

CORE_ITEMS = 18
GRID = [i / 20 for i in range(0, 121)]          # ability scale 0..6 (A1 = 0-1 ... C2 = 5-6)
_AUDIO: dict[str, dict[int, bytes]] = {}        # speaking recordings by question (memory only, for pronunciation)
TERMINAL = ("done", "skipped", "error")


# ---------------------------------------------------------------- ability estimate
def _p(theta: float, b: float) -> float:
    return 0.25 + 0.75 / (1 + math.exp(-1.7 * (theta - b)))


def estimate(responses: list[dict]) -> tuple[float, float]:
    """EAP ability and its SD from responses [{"difficulty", "correct"}]; prior N(2.5, 1.5)."""
    post = []
    for t in GRID:
        w = math.exp(-((t - 2.5) ** 2) / (2 * 1.5 ** 2))
        for r in responses:
            p = _p(t, r["difficulty"])
            w *= p if r["correct"] else (1 - p)
        post.append(w)
    total = sum(post) or 1.0
    mean = sum(t * w for t, w in zip(GRID, post)) / total
    sd = math.sqrt(sum((t - mean) ** 2 * w for t, w in zip(GRID, post)) / total)
    return mean, sd


def theta_to_sub(theta: float) -> str:
    k = int(min(5, max(0, math.floor(theta))))
    if k == 5:
        return "C2"
    return f"{bank.LEVELS[k]}.{1 if theta - k < 0.5 else 2}"


def receptive_sub(level: str, percent: float) -> str:
    """Sublevel from a Reading/Listening score on a text written AT `level`."""
    k = bank.LEVELS.index(level)
    if level == "C2":
        return "C2" if percent >= 70 else "C1.2" if percent >= 50 else "C1.1"
    if percent >= 80:
        return f"{level}.2"
    if percent >= 60:
        return f"{level}.1"
    if k == 0:
        return "A1.1"
    return f"{bank.LEVELS[k - 1]}.{2 if percent >= 40 else 1}"


def next_item(state: dict) -> str | None:
    used = {r["id"] for r in state["responses"]}
    counts = {"grammar": 0, "vocabulary": 0}
    for r in state["responses"]:
        counts[bank.ITEMS_BY_ID[r["id"]]["skill"]] += 1
    skill = "grammar" if counts["grammar"] <= counts["vocabulary"] else "vocabulary"
    theta, _ = estimate([{"difficulty": bank.ITEMS_BY_ID[r["id"]]["difficulty"], "correct": r["correct"]}
                         for r in state["responses"]])
    pool = [it for it in bank.ITEMS if it["id"] not in used and it["skill"] == skill] or \
           [it for it in bank.ITEMS if it["id"] not in used]
    if not pool:
        return None
    best = min(abs(it["difficulty"] - theta) for it in pool)
    return random.choice([it for it in pool if abs(it["difficulty"] - theta) <= best + 1e-9])["id"]


def public_item(state: dict, item_id: str) -> dict:
    it = bank.ITEMS_BY_ID[item_id]
    return {"id": item_id, "question": it["question"], "options": state["order"][item_id],
            "number": len(state["responses"]) + 1, "total": CORE_ITEMS}


def _core_result(state: dict) -> dict:
    def est(skill=None):
        rs = [{"difficulty": bank.ITEMS_BY_ID[r["id"]]["difficulty"], "correct": r["correct"]}
              for r in state["responses"] if skill is None or bank.ITEMS_BY_ID[r["id"]]["skill"] == skill]
        return estimate(rs)[0]
    theta = est()
    return {"theta": round(theta, 2), "level": bank.LEVELS[int(min(5, max(0, math.floor(theta))))],
            "grammar": theta_to_sub(est("grammar")), "vocabulary": theta_to_sub(est("vocabulary")),
            "correct": sum(r["correct"] for r in state["responses"]), "total": len(state["responses"])}


# ---------------------------------------------------------------- state
def _lang(user_id: str) -> str:
    try:
        rows = admin_db().table("profiles").select("preferred_explanation_language").eq("id", user_id) \
            .limit(1).execute().data or []
        lang = (rows[0].get("preferred_explanation_language") if rows else None) or "ru"
        return lang if lang in ("ru", "kk", "en") else "ru"
    except Exception:
        return "ru"


def get_state(user_id: str) -> dict:
    return store.load(user_id)["placement"] or {}


def update_state(user_id: str, fn) -> dict:
    with store.lock(user_id):
        state = store.load(user_id)["placement"] or {}
        fn(state)
        store.save(user_id, placement=state)
        return state


def start(user_id: str) -> dict:
    order = {}
    for it in bank.ITEMS:
        opts = list(it["options"])
        random.shuffle(opts)
        order[it["id"]] = opts
    state = {"id": str(uuid.uuid4()), "status": "in_progress", "stage": "core", "started_at": store.now_iso(),
             "order": order, "responses": [], "lang": _lang(user_id)}
    state["current"] = next_item(state)
    with store.lock(user_id):
        store.load(user_id)
        store.save(user_id, placement=state)
    return state


def answer_core(user_id: str, item_id: str, choice: str) -> dict:
    started_receptive = {}

    def fn(state):
        if state.get("stage") != "core" or state.get("current") != item_id:
            raise ValueError("This question is no longer active.")
        it = bank.ITEMS_BY_ID[item_id]
        state["responses"].append({"id": item_id, "choice": choice, "correct": choice == it["answer"]})
        if len(state["responses"]) >= CORE_ITEMS:
            state["current"] = None
            state["core"] = _core_result(state)
            state["stage"] = "reading"
            level = state["core"]["level"]
            state["reading"] = {"status": "preparing", "level": level}
            state["listening"] = {"status": "preparing", "level": level}
            b = bank.band(level)
            state["writing"] = {"status": "pending", **bank.WRITING_TASKS[b]}
            state["speaking"] = {"status": "pending", "questions": bank.SPEAKING_QUESTIONS[b], "transcripts": {}}
            started_receptive["level"] = level
        else:
            state["current"] = next_item(state)

    state = update_state(user_id, fn)
    if started_receptive:
        threading.Thread(target=_prepare_reading, args=(user_id, started_receptive["level"]), daemon=True).start()
        threading.Thread(target=_prepare_listening, args=(user_id, started_receptive["level"]), daemon=True).start()
    return state


# ---------------------------------------------------------------- reading
READING_LENGTH = {"A1": "90-130", "A2": "140-200", "B1": "220-300", "B2": "300-380", "C1": "380-480", "C2": "450-550"}

READING_PROMPT = """
Write an original English reading text for a CEFR placement test and 6 questions on it.
Return JSON with exactly: title, passage, questions, answer_key, explanations.
passage: {words} words, written exactly at CEFR {level} (vocabulary, grammar and ideas typical of {level}).
questions: exactly 6 objects {{id (1-6), type "multiple_choice", question, options (4 strings)}}; questions test
gist, detail, vocabulary in context and (from B1) inference; only one option is supported by the text.
answer_key: object id -> the exact text of the correct option. explanations: object id -> one short sentence.
"""


def _prepare_reading(user_id: str, level: str) -> None:
    try:
        from app.agents.ielts_verify import verify
        data = ask_json(READING_PROMPT.format(level=level, words=READING_LENGTH[level]), f"CEFR {level}")
        qs = [q for q in data.get("questions") or [] if len(q.get("options") or []) >= 3][:6]
        if len(qs) < 4 or not data.get("passage"):
            raise ValueError("Reading set is incomplete.")
        data["questions"] = qs
        data = verify(data, "passage", "passage")
        update_state(user_id, lambda s: s.get("reading", {}).update(status="ready", content=data))
    except Exception as exc:
        logger.exception("Placement reading could not be prepared")
        update_state(user_id, lambda s: s.get("reading", {}).update(status="error", error=str(exc)[:200]))


def reading_public(state: dict) -> dict | None:
    r = state.get("reading") or {}
    c = r.get("content")
    if not c:
        return None
    return {"title": c.get("title"), "passage": c.get("passage"),
            "questions": [{"id": q["id"], "question": q.get("question"), "options": q.get("options")}
                          for q in c["questions"]]}


def submit_reading(user_id: str, answers: dict) -> dict:
    def fn(state):
        r = state.get("reading") or {}
        if state.get("stage") != "reading":
            raise ValueError("Reading is not active.")
        if r.get("status") == "ready":
            c = r["content"]
            key = c.get("answer_key") or {}
            correct = 0
            for q in c["questions"]:
                k = key.get(str(q["id"]))
                k = k[0] if isinstance(k, list) and k else k
                correct += int(str(answers.get(str(q["id"]), "")).strip() == str(k).strip())
            pct = round(correct / len(c["questions"]) * 100)
            r.update(status="done", correct=correct, total=len(c["questions"]), percent=pct,
                     sub=receptive_sub(r["level"], pct))
        else:
            r.update(status="skipped")
        state["stage"] = "listening"
    return update_state(user_id, fn)


# ---------------------------------------------------------------- listening
def _prepare_listening(user_id: str, level: str) -> None:
    from app.services import listening_bank, listening_audio
    from app.agents import listening_agent
    try:
        row = None
        try:
            row = listening_bank.pick(level, "dialogue", user_id) or listening_bank.pick(level, "monologue", user_id)
        except Exception:
            logger.exception("Listening bank unavailable for placement")
        if row:
            test = dict(row["content"])
            test.update(level=level, bank_id=str(row["id"]), audio_path=row["audio_path"])
            audio = listening_bank.download(row["audio_path"])
        else:
            test = listening_agent.generate(level, "", "dialogue")
            test.update(level=level, format="dialogue")
            audio = None
        db_row = admin_db().table("tests").insert({
            "user_id": user_id, "title": test.get("title") or "Placement listening", "test_type": "placement_listening",
            "focus": "listening", "level": level, "questions": test}).execute()
        test_id = db_row.data[0]["id"]
        if audio:
            listening_audio.start(test_id, user_id, test, audio=audio, max_plays=2)
            listening_bank.mark_used(row)
        else:
            listening_audio.start(test_id, user_id, test, max_plays=2)
        update_state(user_id, lambda s: s.get("listening", {}).update(status="ready", test_id=test_id))
    except Exception as exc:
        logger.exception("Placement listening could not be prepared")
        update_state(user_id, lambda s: s.get("listening", {}).update(status="error", error=str(exc)[:200]))


def _listening_test(user_id: str, test_id: str) -> dict | None:
    rows = admin_db().table("tests").select("questions").eq("id", test_id).eq("user_id", user_id).limit(1) \
        .execute().data or []
    return rows[0]["questions"] if rows else None


def listening_public(user_id: str, state: dict) -> dict | None:
    li = state.get("listening") or {}
    if li.get("status") != "ready":
        return None
    test = _listening_test(user_id, li["test_id"]) or {}
    return {"test_id": li["test_id"], "title": test.get("title"), "max_plays": 2,
            "questions": [{"id": q["id"], "type": q.get("type"), "question": q.get("question"),
                           "options": q.get("options") or []} for q in test.get("questions") or []]}


def submit_listening(user_id: str, answers: dict) -> dict:
    from app.agents import listening_agent
    from app.services import listening_audio

    li_test = {}
    state = get_state(user_id)
    li = state.get("listening") or {}
    if li.get("status") == "ready":
        li_test = _listening_test(user_id, li["test_id"]) or {}

    def fn(state):
        li = state.get("listening") or {}
        if state.get("stage") != "listening":
            raise ValueError("Listening is not active.")
        qs = li_test.get("questions") or []
        if li.get("status") == "ready" and qs:
            correct = sum(int(listening_agent.is_correct(q, str(answers.get(str(q["id"]), "")))) for q in qs)
            pct = round(correct / len(qs) * 100)
            li.update(status="done", correct=correct, total=len(qs), percent=pct, sub=receptive_sub(li["level"], pct))
            listening_audio.mark_checked(li["test_id"])
        else:
            li.update(status="skipped")
        state["stage"] = "writing"
    return update_state(user_id, fn)


# ---------------------------------------------------------------- writing and speaking
SUBLEVEL_RULE = (
    "Choose ONE sublevel from: A1.1, A1.2, A2.1, A2.2, B1.1, B1.2, B2.1, B2.2, C1.1, C1.2, C2 "
    "(X.1 = the lower half of CEFR level X, X.2 = the upper half). Judge range, accuracy, coherence and task fulfilment "
    "against the CEFR descriptors; a very short, memorised or off-task answer cannot be above A2.2. "
    "A higher-level task does not raise the level by itself."
)


def _clean_sub(value) -> str | None:
    v = str(value or "").upper().replace(" ", "")
    return v if v in rm.SUBLEVELS else ("C2" if v.startswith("C2") else None)


def assess_writing(task: dict, text: str, lang: str) -> dict:
    data = ask_json(
        f"You are a CEFR writing examiner. {SUBLEVEL_RULE}\nReturn JSON: sublevel, strengths (2 strings), "
        f"improve (2 strings), comment (1-2 sentences).\n{DATA_GUARD}\n{language_rule(lang)}",
        f"Task ({task.get('words')} words): {task.get('prompt')}\nWord count: {len(text.split())}\n\n"
        f"Learner text:\n{learner_block(text[:5000])}")
    sub = _clean_sub(data.get("sublevel"))
    if not sub:
        raise ValueError("No sublevel in the writing assessment.")
    if len(text.split()) < 15:
        sub = min(sub, "A1.2", key=rm.sub_index)
    data["sublevel"] = sub
    return data


def assess_speaking(questions: list[str], transcripts: dict, pronunciation: dict | None, lang: str) -> dict:
    joined = "\n\n".join(f"Q: {q}\nA: {transcripts.get(str(i), '')}" for i, q in enumerate(questions))
    pron = (f"\nPronunciation assessed from the audio (IELTS-style band 0-9): {pronunciation.get('pronunciation')}; "
            f"{pronunciation.get('summary', '')}") if pronunciation else "\nPronunciation could not be assessed."
    data = ask_json(
        f"You are a CEFR speaking examiner working from transcripts. {SUBLEVEL_RULE} Take pronunciation into account "
        f"when it is given.\nReturn JSON: sublevel, strengths (2 strings), improve (2 strings), comment.\n"
        f"{DATA_GUARD}\n{language_rule(lang)}",
        learner_block(joined[:6000]) + pron)
    sub = _clean_sub(data.get("sublevel"))
    if not sub:
        raise ValueError("No sublevel in the speaking assessment.")
    data["sublevel"] = sub
    if pronunciation:
        data["pronunciation"] = {k: pronunciation.get(k) for k in ("pronunciation", "summary", "issues")}
    return data


def submit_writing(user_id: str, text: str | None) -> dict:
    def fn(state):
        if state.get("stage") != "writing":
            raise ValueError("Writing is not active.")
        w = state["writing"]
        if text and text.strip():
            w.update(status="assessing", text=text.strip()[:6000])
        else:
            w.update(status="skipped")
        state["stage"] = "speaking"
    state = update_state(user_id, fn)
    if state["writing"]["status"] == "assessing":
        threading.Thread(target=_run_writing, args=(user_id,), daemon=True).start()
    return state


def _run_writing(user_id: str) -> None:
    state = get_state(user_id)
    w = state.get("writing") or {}
    try:
        result = assess_writing(w, w.get("text", ""), state.get("lang", "ru"))
        update_state(user_id, lambda s: s["writing"].update(status="done", result=result, sub=result["sublevel"]))
    except Exception as exc:
        logger.exception("Placement writing assessment failed")
        update_state(user_id, lambda s: s["writing"].update(status="error", error=str(exc)[:200]))
    maybe_finalize(user_id)


def speaking_answer(user_id: str, index: int, audio: bytes, content_type: str | None) -> dict:
    state = get_state(user_id)
    if state.get("stage") != "speaking":
        raise ValueError("Speaking is not active.")
    transcript = transcribe_audio("answer.webm", audio, content_type or "audio/webm") or ""
    _AUDIO.setdefault(state["id"], {})[index] = audio   # a re-recording replaces the earlier answer
    return update_state(user_id, lambda s: s["speaking"]["transcripts"].update({str(index): transcript[:4000]}))


def submit_speaking(user_id: str, skip: bool = False) -> dict:
    def fn(state):
        if state.get("stage") != "speaking":
            raise ValueError("Speaking is not active.")
        sp = state["speaking"]
        said = " ".join(sp.get("transcripts", {}).values()).split()
        sp["status"] = "skipped" if skip or len(said) < 5 else "assessing"
        state["stage"] = "assessing"
    state = update_state(user_id, fn)
    if state["speaking"]["status"] == "assessing":
        threading.Thread(target=_run_speaking, args=(user_id,), daemon=True).start()
    else:
        _AUDIO.pop(state["id"], None)
        maybe_finalize(user_id)
    return state


def _run_speaking(user_id: str) -> None:
    from app.services import ielts_pronunciation
    state = get_state(user_id)
    sp = state.get("speaking") or {}
    lang = state.get("lang", "ru")
    recordings = [a for _, a in sorted(_AUDIO.pop(state.get("id"), {}).items())]
    try:
        transcript = " ".join(sp.get("transcripts", {}).values())
        pron = None
        try:
            pron = ielts_pronunciation.assess(recordings, transcript, lang) if recordings else None
        except Exception:
            logger.exception("Placement pronunciation failed")
        result = assess_speaking(sp.get("questions") or [], sp.get("transcripts") or {}, pron, lang)
        update_state(user_id, lambda s: s["speaking"].update(status="done", result=result, sub=result["sublevel"]))
    except Exception as exc:
        logger.exception("Placement speaking assessment failed")
        update_state(user_id, lambda s: s["speaking"].update(status="error", error=str(exc)[:200]))
    maybe_finalize(user_id)


# ---------------------------------------------------------------- result
def maybe_finalize(user_id: str) -> dict:
    with store.lock(user_id):
        row = store.load(user_id)
        state = row["placement"] or {}
        if state.get("stage") != "assessing":
            return state
        if any((state.get(k) or {}).get("status") not in TERMINAL for k in ("reading", "listening", "writing", "speaking")):
            return state
        core = state["core"]
        levels = {"grammar": core["grammar"], "vocabulary": core["vocabulary"]}
        for skill in ("reading", "listening", "writing", "speaking"):
            part = state.get(skill) or {}
            if part.get("status") == "done" and part.get("sub"):
                levels[skill] = part["sub"]
        levels["overall"] = store.overall(levels)
        state.update(status="done", stage="done", finished_at=store.now_iso(),
                     result={"levels": levels, "ielts": rm.IELTS_BRIDGE.get(levels["overall"])})
        merged = {**row["levels"], **levels}
        # skills not measured now keep an earlier placement result, if any
        merged["overall"] = store.overall(merged)
        items = store.apply_placement_items(row["items"], merged)
        store.save(user_id, placement=state, levels=merged, items=items, placed_at=store.now_iso())
    try:
        from app.services.student_model import apply_placement
        apply_placement(user_id, merged)
    except Exception:
        logger.exception("Could not copy placement levels to the student model")
    return state


def public_state(user_id: str) -> dict:
    state = maybe_finalize(user_id) if get_state(user_id).get("stage") == "assessing" else get_state(user_id)
    if not state:
        return {"status": None}
    out = {k: state.get(k) for k in ("status", "stage", "started_at", "finished_at", "result")}
    stage = state.get("stage")
    if stage == "core" and state.get("current"):
        out["item"] = public_item(state, state["current"])
    if state.get("core"):
        out["core"] = {k: state["core"][k] for k in ("level",)}
    if stage == "reading":
        out["reading"] = {"status": state["reading"]["status"], "content": reading_public(state)}
    if stage == "listening":
        out["listening"] = {"status": state["listening"]["status"], "content": listening_public(user_id, state)}
    if stage == "writing":
        out["writing"] = {k: state["writing"].get(k) for k in ("prompt", "words")}
    if stage == "speaking":
        sp = state["speaking"]
        out["speaking"] = {"questions": sp["questions"], "answered": sorted(sp.get("transcripts", {}).keys())}
    if stage in ("assessing", "done"):
        out["parts"] = {k: {"status": (state.get(k) or {}).get("status"), "sub": (state.get(k) or {}).get("sub"),
                            "percent": (state.get(k) or {}).get("percent"),
                            "feedback": {f: ((state.get(k) or {}).get("result") or {}).get(f)
                                         for f in ("strengths", "improve", "comment", "pronunciation")}}
                        for k in ("reading", "listening", "writing", "speaking")}
        if state.get("core"):
            out["parts"]["grammar"] = {"status": "done", "sub": state["core"]["grammar"]}
            out["parts"]["vocabulary"] = {"status": "done", "sub": state["core"]["vocabulary"]}
    return out
