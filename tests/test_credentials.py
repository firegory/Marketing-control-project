"""Tests for secure credential storage without using real secrets."""

from __future__ import annotations

import pytest

from marketing_control.credentials import (
    CredentialStoreError,
    UnavailableCredentialStore,
    WindowsCredentialStore,
    create_credential_store,
)


class FakeKeyring:
    """In-memory keyring backend for credential-store contract tests."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, secret: str) -> None:
        self.values[service, name] = secret

    def get_password(self, service: str, name: str) -> str | None:
        return self.values.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        self.values.pop((service, name), None)


class FailingKeyring(FakeKeyring):
    def set_password(self, service: str, name: str, secret: str) -> None:
        raise RuntimeError(secret)


def test_windows_store_retrieves_replaces_and_deletes_credentials() -> None:
    store = WindowsCredentialStore(backend=FakeKeyring())  # type: ignore[arg-type]

    assert store.get("google_ads_refresh_token") is None

    store.save("google_ads_refresh_token", "first-test-token")
    assert store.get("google_ads_refresh_token") == "first-test-token"

    store.save("google_ads_refresh_token", "replacement-test-token")
    assert store.get("google_ads_refresh_token") == "replacement-test-token"

    store.delete("google_ads_refresh_token")
    assert store.get("google_ads_refresh_token") is None


def test_windows_store_never_exposes_backend_secret_in_errors() -> None:
    store = WindowsCredentialStore(backend=FailingKeyring())  # type: ignore[arg-type]

    with pytest.raises(
        CredentialStoreError, match="Unable to save credential"
    ) as error:
        store.save("google_ads_developer_token", "test-secret-value")

    assert "test-secret-value" not in str(error.value)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_factory_never_falls_back_to_plaintext(platform: str) -> None:
    store = create_credential_store(platform=platform)

    assert isinstance(store, UnavailableCredentialStore)
    with pytest.raises(CredentialStoreError, match="unavailable"):
        store.save("google_ads_refresh_token", "test-secret-value")


def test_windows_factory_selects_windows_credential_store() -> None:
    assert isinstance(create_credential_store(platform="win32"), WindowsCredentialStore)


@pytest.mark.parametrize("name", ["", "   "])
def test_credential_names_must_not_be_empty(name: str) -> None:
    store = WindowsCredentialStore(backend=FakeKeyring())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="credential name"):
        store.get(name)
