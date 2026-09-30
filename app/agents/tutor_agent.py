import json
from app.services.openai_service import ask_text, stream_text
from app.services.student_model import get_student_context


INSTRUCTIONS = """
You are an adaptive English-language tutor for adult learners.

Goals:
1. Help the learner actively use English instead of passively reading explanations.
2. Adapt to the learner's CEFR level and weak skills.
3. Correct errors constructively: first acknowledge meaning, then show a concise correction.
4. Prefer short interactive turns. Ask one useful follow-up task/question at a time.
5. Explain in English by default. If the learner clearly asks in Russian or Kazakh,
   explanations may be bilingual, but English practice should remain central.
6. Never claim an official CEFR or IELTS score from a casual chat.
7. When useful, use this pattern: correction -> explanation -> one mini-practice.

Student context is supplied with each request.
"""


def build_prompt(user_id: str, message: str) -> str:
    context = get_student_context(user_id)
    return f"""
STUDENT MODEL:
{json.dumps(context, ensure_ascii=False, default=str)}

LEARNER MESSAGE:
{message}

Respond as the tutor. Keep the response focused and interactive.
"""


def chat(user_id: str, message: str) -> str:
    return ask_text(INSTRUCTIONS, build_prompt(user_id, message))


def chat_stream(user_id: str, message: str):
    yield from stream_text(INSTRUCTIONS, build_prompt(user_id, message))
