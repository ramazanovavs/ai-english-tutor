from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI English Tutor"
    app_env: str = "development"

    openai_api_key: str
    openai_model: str = "gpt-6-luna"
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "marin"
    openai_audio_model: str = "gpt-audio-1.5"  # listens to Speaking answers for Pronunciation

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # Admins: Supabase user IDs (preferred). Emails count only once the address is confirmed in Supabase.
    admin_user_ids: str = ""
    admin_emails: str = ""

    # AI gateway limits (protect the OpenAI bill); tune in the Render environment.
    ai_user_calls_per_10min: int = 60
    ai_user_calls_per_day: int = 400
    ai_user_tts_chars_per_day: int = 60000
    ai_daily_calls_limit: int = 6000
    # Approximate prices in USD for the cost column of the AI journal (check current OpenAI prices).
    ai_price_input_per_m: float = 1.25
    ai_price_output_per_m: float = 10.0
    ai_price_tts_per_m_chars: float = 15.0
    ai_price_stt_per_minute: float = 0.003
    # Requests per minute per user to the whole API (all endpoints).
    api_requests_per_minute: int = 240

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
