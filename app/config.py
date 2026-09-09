from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "DUDU Car Support AI"
    environment: Literal["development", "test", "production", "prod"] = "development"
    database_url: str = "sqlite:///./dudu_support.db"
    secret_key: str = "change-me-in-production"
    secret_manager: Literal["local", "aws-secrets-manager"] = "local"

    admin_totp_secrets: dict[str, str] = Field(default_factory=dict)
    notification_send_enabled: bool = False
    notification_max_attempts: int = 5
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    notification_template_names: dict[str, str] = Field(default_factory=dict)

    meta_verify_token: str = "dev-verify-token"
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_graph_api_version: str = "v26.0"
    meta_send_enabled: bool = False
    meta_send_timeout_seconds: float = 10.0
    meta_send_max_attempts: int = 5

    media_processing_enabled: bool = False
    media_bucket: str = ""
    media_region: str = "ap-southeast-5"
    media_s3_endpoint_url: str = ""
    media_signed_url_seconds: int = 300
    media_max_attempts: int = 3
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 20.0

    llm_enabled: bool = False
    zai_api_key: str = ""
    llm_model: str = "glm-5.3-flash"
    llm_timeout_seconds: float = 8.0
    llm_max_input_chars: int = 8000
    llm_max_output_tokens: int = 300

    rate_limit_messages_per_minute: int = 20
    rate_limit_admin_attempts: int = 5
    rate_limit_admin_window_seconds: int = 900
    rate_limit_max_keys: int = 10_000
    retrieval_min_confidence: float = 0.12
    chat_log_retention_days: int = 90
    ticket_retention_months: int = 36
    backup_retention_days: int = 35
    backup_interval_minutes: int = 60
    backup_prefix: str = "backups/postgresql"
    privacy_owner_username: str = "czeyik"
    retention_batch_size: int = 100
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
            "meta_verify_token": {"", "dev-verify-token", "change-this-meta-verify-token"},
            "meta_app_secret": {""},
        }
        for field, rejected in unsafe_values.items():
            if getattr(self, field) in rejected:
                errors.append(f"{field.upper()} must be set to a non-development value")

        minimum_lengths = {
            "secret_key": 32,
            "meta_verify_token": 24,
            "meta_app_secret": 24,
        }
        for field, minimum in minimum_lengths.items():
            if len(getattr(self, field)) < minimum:
                errors.append(f"{field.upper()} must be at least {minimum} characters")

        if not self.admin_totp_secrets:
            errors.append("ADMIN_TOTP_SECRETS must contain per-user secret references")
        if self.secret_manager != "aws-secrets-manager":
            errors.append("SECRET_MANAGER must be aws-secrets-manager")
        if self.notification_send_enabled:
            if not all(
                (self.smtp_host, self.smtp_username, self.smtp_password, self.smtp_from_address)
            ):
                errors.append("SMTP settings must be set when NOTIFICATION_SEND_ENABLED is true")
            required_templates = {
                "ticket_status_changed.en",
                "ticket_status_changed.ms",
                "ticket_status_changed.zh",
                "urgent_ticket_created.en",
            }
            if not required_templates <= self.notification_template_names.keys():
                errors.append("NOTIFICATION_TEMPLATE_NAMES must contain all launch templates")
        if not 1 <= self.notification_max_attempts <= 10:
            errors.append("NOTIFICATION_MAX_ATTEMPTS must be between 1 and 10")

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

        if not self.media_processing_enabled:
            errors.append("MEDIA_PROCESSING_ENABLED must be true")
        if self.media_processing_enabled and len(self.meta_access_token) < 24:
            errors.append("META_ACCESS_TOKEN must be set when media processing is enabled")
        if self.media_processing_enabled and not self.meta_phone_number_id.isdigit():
            errors.append("META_PHONE_NUMBER_ID must be numeric when media processing is enabled")
        if not self.media_bucket:
            errors.append("MEDIA_BUCKET must be set")
        if self.media_region != "ap-southeast-5":
            errors.append("MEDIA_REGION must be the approved ap-southeast-5")
        if self.media_s3_endpoint_url:
            errors.append("MEDIA_S3_ENDPOINT_URL cannot override AWS in production")
        if self.media_signed_url_seconds != 300:
            errors.append("MEDIA_SIGNED_URL_SECONDS must be the approved 300 seconds")
        if not 1 <= self.media_max_attempts <= 5:
            errors.append("MEDIA_MAX_ATTEMPTS must be between 1 and 5")
        if not 1 <= self.clamav_timeout_seconds <= 30:
            errors.append("CLAMAV_TIMEOUT_SECONDS must be between 1 and 30")

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
        if not 1 <= self.rate_limit_messages_per_minute <= 120:
            errors.append("RATE_LIMIT_MESSAGES_PER_MINUTE must be between 1 and 120")
        if not 1 <= self.rate_limit_admin_attempts <= 20:
            errors.append("RATE_LIMIT_ADMIN_ATTEMPTS must be between 1 and 20")
        if not 60 <= self.rate_limit_admin_window_seconds <= 3600:
            errors.append("RATE_LIMIT_ADMIN_WINDOW_SECONDS must be between 60 and 3600")
        if not 100 <= self.rate_limit_max_keys <= 100_000:
            errors.append("RATE_LIMIT_MAX_KEYS must be between 100 and 100000")
        if self.chat_log_retention_days != 90:
            errors.append("CHAT_LOG_RETENTION_DAYS must be the approved 90 days")
        if self.ticket_retention_months != 36:
            errors.append("TICKET_RETENTION_MONTHS must be the approved 36 months")
        if self.backup_retention_days != 35:
            errors.append("BACKUP_RETENTION_DAYS must be the approved 35 days")
        if self.backup_interval_minutes != 60:
            errors.append("BACKUP_INTERVAL_MINUTES must be the approved 60 minutes")
        if not self.backup_prefix.startswith("backups/") or ".." in self.backup_prefix:
            errors.append("BACKUP_PREFIX must be below backups/")
        if self.privacy_owner_username != "czeyik":
            errors.append("PRIVACY_OWNER_USERNAME must be the approved privacy owner")
        if not 1 <= self.retention_batch_size <= 1000:
            errors.append("RETENTION_BATCH_SIZE must be between 1 and 1000")
        if errors:
            raise ValueError("Invalid production configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
