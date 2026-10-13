from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    gemini_api_key: SecretStr
    database_url: SecretStr

    gemini_model: str = "gemini-3.5-flash-lite"

    booking_resource_id: int = Field(
        default=1,
        ge=1,
    )

    allowed_telegram_user_ids: str = ""

    resend_api_key: SecretStr
    email_from: str = "AgendaAI <onboarding@resend.dev>"
    email_verification_secret: SecretStr

    @property
    def allowed_users(self) -> set[int]:
        raw = self.allowed_telegram_user_ids.strip()

        if not raw:
            return set()

        return {int(value.strip()) for value in raw.split(",") if value.strip()}
