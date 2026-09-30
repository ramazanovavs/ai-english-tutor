import json
import re
from typing import Any
from openai import OpenAI

from app.config import get_settings

settings = get_settings()
client = OpenAI(api_key=settings.openai_api_key)


def ask_text(instructions: str, user_input: str) -> str:
    response = client.responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=user_input,
    )
    return response.output_text.strip()


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
    result = client.audio.transcriptions.create(
        model=settings.openai_transcribe_model,
        file=file_arg,
        language="en",
    )
    return (result.text or "").strip()


def synthesize_speech(text: str, voice: str | None = None, instructions: str | None = None) -> bytes:
    """Create MP3 speech for tutor feedback or listening practice."""
    response = client.audio.speech.create(
        model=settings.openai_tts_model,
        voice=voice or settings.openai_tts_voice,
        input=text[:4000],
        instructions=instructions or "Speak clearly and naturally as a supportive English teacher. Use moderate speed.",
        response_format="mp3",
    )
    if hasattr(response, "read"):
        return response.read()
    return response.content
