import pytest
from pydantic import ValidationError

from app.config import Settings


PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": "postgresql+psycopg://app:password@db/dudu_support",
    "secret_key": "production-session-secret-at-least-32",
    "secret_manager": "aws-secrets-manager",
    "admin_totp_secrets": {"admin/czeyik/totp": "JBSWY3DPEHPK3PXP"},
    "meta_verify_token": "production-meta-verify-token-long",
    "meta_app_secret": "production-meta-app-secret-long",
    "meta_access_token": "production-meta-access-token-long",
    "meta_phone_number_id": "123456789",
    "media_processing_enabled": True,
    "media_bucket": "dudu-private-media",
    "llm_enabled": True,
    "zai_api_key": "production-zai-api-key",
    "trusted_hosts": "support.example.com",
}


@pytest.mark.parametrize("llm_enabled", [True, False])
def test_production_configuration_accepts_explicit_safe_values(llm_enabled: bool) -> None:
    settings = Settings(_env_file=None, **(PRODUCTION_SETTINGS | {"llm_enabled": llm_enabled}))

    assert settings.is_production
    assert settings.llm_enabled is llm_enabled
    assert settings.llm_timeout_seconds == 30.0
    assert settings.llm_max_input_chars == 15000


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prodution")


def test_production_send_requires_meta_transport_credentials() -> None:
    with pytest.raises(ValidationError, match="META_ACCESS_TOKEN"):
        Settings(
            _env_file=None,
            **(
                PRODUCTION_SETTINGS
                | {"meta_send_enabled": True, "meta_access_token": "", "meta_phone_number_id": ""}
            ),
        )

    settings = Settings(
        _env_file=None,
        **(
            PRODUCTION_SETTINGS
            | {
                "meta_send_enabled": True,
                "public_beta_enabled": True,
                "meta_access_token": "production-meta-access-token-long",
                "meta_phone_number_id": "123456789",
            }
        ),
    )
    assert settings.meta_graph_api_version == "v26.0"


def test_production_configuration_accepts_extended_beta_window_with_sending() -> None:
    settings = Settings(
        _env_file=None,
        **(
            PRODUCTION_SETTINGS
            | {
                "meta_send_enabled": True,
                "public_beta_enabled": True,
                "public_beta_end_date": "2026-09-30",
            }
        ),
    )

    assert settings.meta_send_enabled is True
    assert settings.public_beta_enabled is True
    assert settings.public_beta_end_date.isoformat() == "2026-09-30"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///production.db"),
        ("secret_key", "change-me-in-production"),
        ("secret_manager", "local"),
        ("admin_totp_secrets", {}),
        ("meta_verify_token", "dev-verify-token"),
        ("meta_app_secret", ""),
        ("media_processing_enabled", False),
        ("media_bucket", ""),
        ("media_region", "ap-southeast-1"),
        ("media_signed_url_seconds", 3600),
        ("zai_api_key", ""),
        ("llm_model", "glm-5.3"),
        ("llm_timeout_seconds", 31),
        ("llm_max_input_chars", 15001),
        ("llm_max_output_tokens", 301),
        ("public_beta_start_date", "2026-09-11"),
        ("public_beta_end_date", "2026-09-09"),
        ("public_beta_messages_per_user_day", 201),
        ("public_beta_messages_per_day", 2001),
        ("public_beta_messages_total", 10001),
        ("trusted_hosts", "*"),
        ("cors_origins", "*"),
        ("chat_log_retention_days", 91),
        ("ticket_retention_months", 35),
        ("backup_retention_days", 36),
        ("backup_interval_minutes", 61),
        ("backup_prefix", "postgresql"),
        ("privacy_owner_username", "shared-admin"),
        ("retention_batch_size", 0),
    ],
)
def test_production_configuration_rejects_unsafe_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="Invalid production configuration"):
        Settings(_env_file=None, **(PRODUCTION_SETTINGS | {field: value}))
