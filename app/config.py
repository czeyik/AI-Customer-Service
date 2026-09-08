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
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_graph_api_version: str = "v26.0"
    meta_send_enabled: bool = False
    meta_send_timeout_seconds: float = 10.0
    meta_send_max_attempts: int = 5

    llm_enabled: bool = False
    zai_api_key: str = ""
    llm_model: str = "glm-5.3-flash"
    llm_timeout_seconds: float = 8.0
    llm_max_input_chars: int = 8000
    llm_max_output_tokens: int = 300

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

        if self.meta_send_enabled:
            if len(self.meta_access_token) < 24:
                errors.append("META_ACCESS_TOKEN must be set when META_SEND_ENABLED is true")
            if not self.meta_phone_number_id.isdigit():
                errors.append("META_PHONE_NUMBER_ID must be numeric when META_SEND_ENABLED is true")
        if self.meta_graph_api_version != "v26.0":
            errors.append("META_GRAPH_API_VERSION must be the approved v26.0")
        if not 1 <= self.meta_send_max_attempts <= 10:
            errors.append("META_SEND_MAX_ATTEMPTS must be between 1 and 10")
        if not 1 <= self.meta_send_timeout_seconds <= 30:
            errors.append("META_SEND_TIMEOUT_SECONDS must be between 1 and 30")

        if not self.llm_enabled:
            errors.append("LLM_ENABLED must be true")
        if len(self.zai_api_key) < 16:
            errors.append("ZAI_API_KEY must be set")
        if self.llm_model != "glm-5.3-flash":
            errors.append("LLM_MODEL must be the approved glm-5.3-flash")
        if not 1 <= self.llm_timeout_seconds <= 8:
            errors.append("LLM_TIMEOUT_SECONDS must be between 1 and 8")
        if not 1 <= self.llm_max_input_chars <= 8000:
            errors.append("LLM_MAX_INPUT_CHARS must be between 1 and 8000")
        if not 1 <= self.llm_max_output_tokens <= 300:
            errors.append("LLM_MAX_OUTPUT_TOKENS must be between 1 and 300")

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
