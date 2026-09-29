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
