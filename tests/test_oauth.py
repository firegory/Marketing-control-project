"""Tests for the mocked Google desktop OAuth authorization flow."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.credentials import CredentialStoreError
from marketing_control.google_ads import REFRESH_TOKEN_NAME
from marketing_control.oauth import (
    OAuthAuthorizationDeniedError,
    OAuthCallbackError,
    OAuthCancelledError,
    OAuthTokenExchangeError,
)
from marketing_control.settings import AppPaths, Settings


class FakeCredentialStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def save(self, name: str, secret: str) -> None:
        self.values[name] = secret

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FakeAuthorizer:
    def __init__(self, result: str | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def authorize(self, *, client_id: str, client_secret: str) -> str:
        self.calls.append((client_id, client_secret))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _csrf_token(client: TestClient) -> str:
    response = client.get("/settings/google-ads")
    assert response.status_code == 200
    return cast(str, client.cookies.get("google_ads_csrf"))


def _save_settings(client: TestClient) -> None:
    response = client.post(
        "/settings/google-ads",
        data={
            "csrf_token": _csrf_token(client),
            "oauth_client_id": "test-client-id.apps.googleusercontent.com",
            "oauth_client_secret": "client-secret-value",
            "customer_id": "123-456-7890",
            "login_customer_id": "987-654-3210",
            "developer_token": "developer-token-value",
        },
    )
    assert response.status_code == 200


def _client(
    tmp_path: Path, authorizer: FakeAuthorizer, store: FakeCredentialStore | None = None
) -> tuple[TestClient, FakeCredentialStore]:
    credentials = FakeCredentialStore() if store is None else store
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    return (
        TestClient(
            create_app(
                settings=settings,
                credential_store=credentials,
                oauth_authorizer=authorizer,
            )
        ),
        credentials,
    )


def test_authorization_stores_only_refresh_token_after_mocked_success(
    tmp_path: Path,
) -> None:
    authorizer = FakeAuthorizer("refresh-token-value")
    client, credentials = _client(tmp_path, authorizer)
    _save_settings(client)

    response = client.post(
        "/settings/google-ads/authorize", data={"csrf_token": _csrf_token(client)}
    )

    assert response.status_code == 200
    assert "Google authorization completed" in response.text
    assert "refresh-token-value" not in response.text
    assert credentials.values[REFRESH_TOKEN_NAME] == "refresh-token-value"
    assert authorizer.calls == [
        ("test-client-id.apps.googleusercontent.com", "client-secret-value")
    ]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (OAuthAuthorizationDeniedError(), "Google authorization was denied"),
        (OAuthCancelledError(), "Google authorization timed out or was cancelled"),
        (OAuthCallbackError(), "Google authorization callback could not be verified"),
        (OAuthTokenExchangeError(), "Google could not issue a refresh token"),
    ],
)
def test_authorization_failure_messages_are_clear_and_secret_safe(
    tmp_path: Path, error: Exception, message: str
) -> None:
    client, credentials = _client(tmp_path, FakeAuthorizer(error))
    _save_settings(client)

    response = client.post(
        "/settings/google-ads/authorize", data={"csrf_token": _csrf_token(client)}
    )

    assert response.status_code == 200
    assert message in response.text
    assert "client-secret-value" not in response.text
    assert "developer-token-value" not in response.text
    assert REFRESH_TOKEN_NAME not in credentials.values


def test_authorization_rejects_invalid_csrf_before_starting_flow(
    tmp_path: Path,
) -> None:
    authorizer = FakeAuthorizer("refresh-token-value")
    client, _ = _client(tmp_path, authorizer)
    _save_settings(client)

    response = client.post(
        "/settings/google-ads/authorize", data={"csrf_token": "invalid"}
    )

    assert response.status_code == 403
    assert authorizer.calls == []


def test_authorization_reports_secure_storage_error_without_secrets(
    tmp_path: Path,
) -> None:
    class FailingStore(FakeCredentialStore):
        def save(self, name: str, secret: str) -> None:
            if name == REFRESH_TOKEN_NAME:
                raise CredentialStoreError(secret)
            super().save(name, secret)

    client, _ = _client(
        tmp_path, FakeAuthorizer("refresh-token-value"), FailingStore()
    )
    _save_settings(client)

    response = client.post(
        "/settings/google-ads/authorize", data={"csrf_token": _csrf_token(client)}
    )

    assert response.status_code == 200
    assert "Secure credential storage is unavailable" in response.text
    assert "refresh-token-value" not in response.text


def test_google_authorizer_uses_loopback_flow_and_returns_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_control import oauth

    calls: dict[str, object] = {}

    class FakeFlow:
        def run_local_server(self, **kwargs: object) -> object:
            calls["run"] = kwargs
            return type("Credentials", (), {"refresh_token": "refresh-token-value"})()

    class FakeInstalledAppFlow:
        @staticmethod
        def from_client_config(*args: object, **kwargs: object) -> FakeFlow:
            calls["config_args"] = args
            calls["config"] = kwargs
            return FakeFlow()

    monkeypatch.setattr(oauth, "InstalledAppFlow", FakeInstalledAppFlow)

    assert oauth.GoogleDesktopOAuthAuthorizer().authorize(
        client_id="client-id", client_secret="client-secret"
    ) == "refresh-token-value"
    assert calls["config_args"] == (
        {
            "installed": {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
    )
    assert calls["config"] == {
        "scopes": ["https://www.googleapis.com/auth/adwords"],
    }
    assert calls["run"] == {
        "host": "127.0.0.1",
        "port": 0,
        "open_browser": True,
        "authorization_prompt_message": "",
        "success_message": "Authorization completed. You may close this window.",
        "access_type": "offline",
        "prompt": "consent",
        "timeout_seconds": 300,
    }
