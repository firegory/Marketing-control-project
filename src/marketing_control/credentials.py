"""Secure storage for application credentials.

This module intentionally has no plaintext fallback. On supported Windows
installations, ``keyring`` uses Windows Credential Manager.
"""

from __future__ import annotations

import sys
from typing import Protocol

import keyring
from keyring.backend import KeyringBackend

_SERVICE_NAME = "MarketingControl"


class CredentialStore(Protocol):
    """Store named application secrets without exposing their values."""

    def save(self, name: str, secret: str) -> None:
        """Create or replace the secret identified by ``name``."""

    def get(self, name: str) -> str | None:
        """Retrieve a secret, or ``None`` when it does not exist."""

    def delete(self, name: str) -> None:
        """Delete a stored secret if it exists."""


class CredentialStoreError(RuntimeError):
    """A safe-to-display credential-store failure."""


class WindowsCredentialStore:
    """Credential store backed by Windows Credential Manager via keyring."""

    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        service_name: str = _SERVICE_NAME,
    ) -> None:
        self._backend = keyring.get_keyring() if backend is None else backend
        self._service_name = service_name

    def save(self, name: str, secret: str) -> None:
        """Create or replace a named credential."""
        _validate_name(name)
        try:
            self._backend.set_password(self._service_name, name, secret)
        except Exception:
            raise CredentialStoreError("Unable to save credential.") from None

    def get(self, name: str) -> str | None:
        """Retrieve a named credential without logging its value."""
        _validate_name(name)
        try:
            return self._backend.get_password(self._service_name, name)
        except Exception:
            raise CredentialStoreError("Unable to retrieve credential.") from None

    def delete(self, name: str) -> None:
        """Delete a named credential without exposing its value."""
        _validate_name(name)
        try:
            self._backend.delete_password(self._service_name, name)
        except Exception:
            raise CredentialStoreError("Unable to delete credential.") from None


class UnavailableCredentialStore:
    """Safe failure mode for platforms without the Windows credential provider."""

    def save(self, name: str, secret: str) -> None:
        """Reject credential storage when no secure provider is available."""
        _validate_name(name)
        raise CredentialStoreError("Secure credential storage is unavailable.")

    def get(self, name: str) -> str | None:
        """Reject credential retrieval when no secure provider is available."""
        _validate_name(name)
        raise CredentialStoreError("Secure credential storage is unavailable.")

    def delete(self, name: str) -> None:
        """Reject credential deletion when no secure provider is available."""
        _validate_name(name)
        raise CredentialStoreError("Secure credential storage is unavailable.")


def create_credential_store(*, platform: str | None = None) -> CredentialStore:
    """Create the platform-appropriate secure credential store."""
    if (sys.platform if platform is None else platform) == "win32":
        return WindowsCredentialStore()
    return UnavailableCredentialStore()


def _validate_name(name: str) -> None:
    if not name or name.isspace():
        raise ValueError("credential name must not be empty")
