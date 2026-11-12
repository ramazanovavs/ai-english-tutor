import json
import re
import time
from typing import Any
from openai import OpenAI

from app.config import get_settings
from app.services import ai_gateway

settings = get_settings()
client = OpenAI(api_key=settings.openai_api_key)


def _usage(response) -> tuple[int | None, int | None]:
    u = getattr(response, "usage", None)
    return (getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)) if u else (None, None)


def ask_text(instructions: str, user_input: str) -> str:
    ai_gateway.check("text")
    started = time.monotonic()
    try:
        response = client.responses.create(
            model=settings.openai_model,
            instructions=instructions,
            input=user_input,
        )
    except Exception as exc:
        ai_gateway.log("text", settings.openai_model, ok=False, started=started, error=type(exc).__name__)
        raise
    tin, tout = _usage(response)
    ai_gateway.log("text", settings.openai_model, ok=True, started=started, input_tokens=tin, output_tokens=tout)
    return response.output_text.strip()


def stream_text(instructions: str, user_input: str):
    """Yield text deltas from the Responses API as soon as they arrive."""
    ai_gateway.check("stream")
    started = time.monotonic()
    chars = 0
    stream = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=user_input,
        stream=True,
    )
    for event in stream:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            if delta:
                chars += len(delta)
                yield delta
        elif event_type == "response.completed":
            tin, tout = _usage(getattr(event, "response", None))
            ai_gateway.log("stream", settings.openai_model, ok=True, started=started, input_tokens=tin,
                           output_tokens=tout, units=chars)
        elif event_type == "error":
            message = getattr(event, "message", None) or "OpenAI streaming error"
            ai_gateway.log("stream", settings.openai_model, ok=False, started=started, error="stream error")
            raise RuntimeError(message)


def ask_json(instructions: str, user_input: str) -> dict[str, Any]:
    prompt = (
        user_input
        + "\n\nReturn ONLY valid JSON. Do not use markdown fences or add commentary."
    )
    text = ask_text(instructions, prompt)
    text = text.strip()

    # Tolerate accidental markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("Model returned invalid JSON")


def transcribe_audio(filename: str, content: bytes, content_type: str | None = None) -> str:
    """Transcribe uploaded learner audio with the low-cost speech-to-text model."""
    file_arg = (filename or "recording.webm", content, content_type or "application/octet-stream")
    ai_gateway.check("stt")
    started = time.monotonic()
    try:
        result = client.audio.transcriptions.create(
            model=settings.openai_transcribe_model,
            file=file_arg,
            language="en",
        )
    except Exception as exc:
        ai_gateway.log("stt", settings.openai_transcribe_model, ok=False, started=started, error=type(exc).__name__)
        raise
    # units: approximate seconds of audio (Opus/WebM ~ 4 KB per second)
    ai_gateway.log("stt", settings.openai_transcribe_model, ok=True, started=started, units=max(1, len(content) // 4000))
    return (result.text or "").strip()


def synthesize_speech(text: str, voice: str | None = None, instructions: str | None = None) -> bytes:
    """Create MP3 speech for tutor feedback or listening practice."""
    text = text[:4000]
    ai_gateway.check("tts", len(text))
    started = time.monotonic()
    try:
        response = client.audio.speech.create(
            model=settings.openai_tts_model,
            voice=voice or settings.openai_tts_voice,
            input=text,
            instructions=instructions or "Speak clearly and naturally as a supportive English teacher. Use moderate speed.",
            response_format="mp3",
        )
    except Exception as exc:
        ai_gateway.log("tts", settings.openai_tts_model, ok=False, started=started, units=len(text), error=type(exc).__name__)
        raise
    ai_gateway.log("tts", settings.openai_tts_model, ok=True, started=started, units=len(text))
    if hasattr(response, "read"):
        return response.read()
    return response.content
