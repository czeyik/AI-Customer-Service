from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DUDU Car Support AI"
    environment: str = "development"
    database_url: str = "sqlite:///./dudu_support.db"
    secret_key: str = "change-me-in-production"

    admin_username: str = "admin"
    admin_initial_password: str = "change-me-now"
    admin_totp_secret: str = ""
    admin_api_key: str = "dev-admin-api-key"

    llm_provider: str = "ollama"
    llm_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()

