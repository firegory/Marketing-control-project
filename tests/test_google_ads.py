"""Tests for Google Ads onboarding validation and secret handling."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.credentials import CredentialStoreError
from marketing_control.google_ads import (
    DEVELOPER_TOKEN_NAME,
    OAUTH_CLIENT_SECRET_NAME,
    GoogleAdsSettingsStore,
    normalize_customer_id,
    normalize_optional_customer_id,
)
from marketing_control.settings import AppPaths, Settings


class FakeCredentialStore:
    """Records stored credentials without requiring a platform keyring."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def save(self, name: str, secret: str) -> None:
        self.values[name] = secret

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FailingCredentialStore(FakeCredentialStore):
    def save(self, name: str, secret: str) -> None:
        raise CredentialStoreError(secret)


@pytest.fixture
def app_parts(tmp_path: Path) -> tuple[TestClient, FakeCredentialStore, Settings]:
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    store = FakeCredentialStore()
    client = TestClient(create_app(settings=settings, credential_store=store))
    return client, store, settings


def _csrf_token(client: TestClient) -> str:
    response = client.get("/settings/google-ads")
    assert response.status_code == 200
    token = client.cookies.get("google_ads_csrf")
    assert token is not None
    return cast(str, token)


def _valid_form(csrf_token: str) -> dict[str, str]:
    return {
        "csrf_token": csrf_token,
        "oauth_client_id": "test-client-id.apps.googleusercontent.com",
        "oauth_client_secret": "client-secret-value",
        "customer_id": "123-456-7890",
        "login_customer_id": "987 654 3210",
        "developer_token": "developer-token-value",
    }


def test_onboarding_saves_normalized_metadata_and_only_secrets_in_credential_store(
    app_parts: tuple[TestClient, FakeCredentialStore, Settings],
) -> None:
    client, credentials, settings = app_parts

    response = client.post(
        "/settings/google-ads", data=_valid_form(_csrf_token(client))
    )

    assert response.status_code == 200
    assert "client-secret-value" not in response.text
    assert "developer-token-value" not in response.text
    assert credentials.values == {
        OAUTH_CLIENT_SECRET_NAME: "client-secret-value",
        DEVELOPER_TOKEN_NAME: "developer-token-value",
    }
    saved_settings = GoogleAdsSettingsStore(settings).load()
    assert saved_settings is not None
    assert saved_settings.customer_id == "1234567890"
    assert saved_settings.login_customer_id == "9876543210"
    saved_metadata = (settings.paths.config / "google-ads.json").read_text()
    assert "client-secret-value" not in saved_metadata
    assert "developer-token-value" not in saved_metadata


@pytest.mark.parametrize("value", ["123456789", "12345678901", "123-abc-7890"])
def test_customer_id_requires_ten_digits(value: str) -> None:
    with pytest.raises(ValueError, match="exactly 10 digits"):
        normalize_customer_id(value)


def test_optional_customer_id_accepts_blank_value() -> None:
    assert normalize_optional_customer_id("  ") is None


def test_onboarding_rejects_invalid_customer_id_before_saving(
    app_parts: tuple[TestClient, FakeCredentialStore, Settings],
) -> None:
    client, credentials, _ = app_parts
    form = _valid_form(_csrf_token(client))
    form["customer_id"] = "not-a-customer-id"

    response = client.post("/settings/google-ads", data=form)

    assert response.status_code == 422
    assert credentials.values == {}


def test_onboarding_rejects_missing_or_invalid_csrf_token(
    app_parts: tuple[TestClient, FakeCredentialStore, Settings],
) -> None:
    client, _, _ = app_parts

    response = client.post(
        "/settings/google-ads", data=_valid_form("not-the-cookie-token")
    )

    assert response.status_code == 403


def test_credential_store_errors_do_not_expose_submitted_secrets(
    tmp_path: Path,
) -> None:
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    client = TestClient(
        create_app(settings=settings, credential_store=FailingCredentialStore())
    )

    response = client.post(
        "/settings/google-ads", data=_valid_form(_csrf_token(client))
    )

    assert response.status_code == 503
    assert "client-secret-value" not in response.text
    assert "developer-token-value" not in response.text
