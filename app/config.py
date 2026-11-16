"""Application settings, loaded from environment variables (and `.env` when present)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # WhatsApp Cloud API
    wa_verify_token: str
    wa_app_secret: str
    wa_access_token: str
    wa_phone_number_id: str
    wa_api_version: str = "v23.0"

    # Postgres + PostGIS
    database_url: str

    # Key for HMAC-SHA256 of phone numbers (person_pii.phone_hash). Any long random string.
    # Changing it orphans every existing participant, so treat it like a database password.
    # min_length stops an app from silently hashing with an empty key from a half-filled .env.
    pii_hmac_key: str = Field(min_length=32)

    @property
    def graph_messages_url(self) -> str:
        return (
            f"https://graph.facebook.com/{self.wa_api_version}/{self.wa_phone_number_id}/messages"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
