import pytest
from pydantic import ValidationError

from app.config import Settings


PRODUCTION_SETTINGS = {
    "environment": "production",
    "database_url": "postgresql+psycopg://app:password@db/dudu_support",
    "secret_key": "production-session-secret-at-least-32",
    "admin_initial_password": "production-admin-password",
    "admin_totp_secret": "JBSWY3DPEHPK3PXP",
    "admin_api_key": "production-admin-api-key-long",
    "meta_verify_token": "production-meta-verify-token-long",
    "meta_app_secret": "production-meta-app-secret-long",
    "trusted_hosts": "support.example.com",
}


def test_production_configuration_accepts_explicit_safe_values() -> None:
    settings = Settings(_env_file=None, **PRODUCTION_SETTINGS)

    assert settings.is_production


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="prodution")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_url", "sqlite:///production.db"),
        ("secret_key", "change-me-in-production"),
        ("admin_initial_password", "change-me-now"),
        ("admin_totp_secret", ""),
        ("admin_api_key", "dev-admin-api-key"),
        ("meta_verify_token", "dev-verify-token"),
        ("meta_app_secret", ""),
        ("trusted_hosts", "*"),
        ("cors_origins", "*"),
    ],
)
def test_production_configuration_rejects_unsafe_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="Invalid production configuration"):
        Settings(_env_file=None, **(PRODUCTION_SETTINGS | {field: value}))
