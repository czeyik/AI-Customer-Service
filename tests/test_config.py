import pytest
from pydantic import ValidationError

from app.config import Settings


PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": "postgresql+psycopg://app:password@db/dudu_support",
    "secret_key": "production-session-secret-at-least-32",
    "admin_totp_secrets": {"admin/czeyik/totp": "JBSWY3DPEHPK3PXP"},
    "admin_api_key": "production-admin-api-key-long",
    "meta_verify_token": "production-meta-verify-token-long",
    "meta_app_secret": "production-meta-app-secret-long",
    "llm_enabled": True,
    "zai_api_key": "production-zai-api-key",
    "trusted_hosts": "support.example.com",
}


def test_production_configuration_accepts_explicit_safe_values() -> None:
    settings = Settings(_env_file=None, **PRODUCTION_SETTINGS)

    assert settings.is_production


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
                "meta_access_token": "production-meta-access-token-long",
                "meta_phone_number_id": "123456789",
            }
        ),
    )
    assert settings.meta_graph_api_version == "v26.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///production.db"),
        ("secret_key", "change-me-in-production"),
        ("admin_totp_secrets", {}),
        ("admin_api_key", "dev-admin-api-key"),
        ("meta_verify_token", "dev-verify-token"),
        ("meta_app_secret", ""),
        ("llm_enabled", False),
        ("zai_api_key", ""),
        ("llm_model", "glm-5.3"),
        ("llm_timeout_seconds", 9),
        ("llm_max_input_chars", 8001),
        ("llm_max_output_tokens", 301),
        ("trusted_hosts", "*"),
        ("cors_origins", "*"),
    ],
)
def test_production_configuration_rejects_unsafe_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="Invalid production configuration"):
        Settings(_env_file=None, **(PRODUCTION_SETTINGS | {field: value}))
