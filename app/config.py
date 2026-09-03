from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DUDU Car Support AI"
    environment: Literal["development", "test", "production", "prod"] = "development"
    database_url: str = "sqlite:///./dudu_support.db"
    secret_key: str = "change-me-in-production"

    admin_username: str = "admin"
    admin_initial_password: str = "change-me-now"
    admin_totp_secret: str = ""
    admin_api_key: str = "dev-admin-api-key"

    meta_verify_token: str = "dev-verify-token"
    meta_app_secret: str = ""
    meta_send_enabled: bool = False

    rate_limit_messages_per_minute: int = 20
    retrieval_min_confidence: float = 0.12
    chat_log_retention_days: int = 90
    trusted_hosts: str = "*"
    cors_origins: str = ""

    @property
    def trusted_host_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_hosts.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if not self.is_production:
            return self

        errors = []
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            errors.append("DATABASE_URL must use PostgreSQL")

        unsafe_values = {
            "secret_key": {"", "change-me-in-production", "change-this-to-a-long-random-string"},
            "admin_initial_password": {"", "change-me-now"},
            "admin_totp_secret": {""},
            "admin_api_key": {"", "dev-admin-api-key", "change-this-admin-api-key"},
            "meta_verify_token": {"", "dev-verify-token", "change-this-meta-verify-token"},
            "meta_app_secret": {""},
        }
        for field, rejected in unsafe_values.items():
            if getattr(self, field) in rejected:
                errors.append(f"{field.upper()} must be set to a non-development value")

        minimum_lengths = {
            "secret_key": 32,
            "admin_initial_password": 12,
            "admin_api_key": 24,
            "meta_verify_token": 24,
            "meta_app_secret": 24,
        }
        for field, minimum in minimum_lengths.items():
            if len(getattr(self, field)) < minimum:
                errors.append(f"{field.upper()} must be at least {minimum} characters")

        if not self.trusted_host_list or "*" in self.trusted_host_list:
            errors.append("TRUSTED_HOSTS must contain explicit production hosts")
        if "*" in self.cors_origin_list:
            errors.append("CORS_ORIGINS cannot contain '*' in production")
        if errors:
            raise ValueError("Invalid production configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
