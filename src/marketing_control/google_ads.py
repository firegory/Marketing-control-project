"""Google Ads onboarding validation and safe local metadata storage."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass

from marketing_control.settings import Settings

OAUTH_CLIENT_SECRET_NAME = "google_ads_oauth_client_secret"
DEVELOPER_TOKEN_NAME = "google_ads_developer_token"
REFRESH_TOKEN_NAME = "google_ads_refresh_token"
_CUSTOMER_ID = re.compile(r"^\d{10}$")


@dataclass(frozen=True)
class GoogleAdsSettings:
    """Non-secret configuration for the single Google Ads account."""

    oauth_client_id: str
    customer_id: str
    login_customer_id: str | None


class GoogleAdsSettingsStore:
    """Persist safe Google Ads metadata in the application's config directory."""

    def __init__(self, settings: Settings) -> None:
        self._path = settings.paths.config / "google-ads.json"

    def save(self, value: GoogleAdsSettings) -> None:
        """Write safe metadata atomically with owner-only file permissions."""
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(asdict(value)), encoding="utf-8")
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(self._path)

    def load(self) -> GoogleAdsSettings | None:
        """Load saved metadata without ever reading stored credentials."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        return GoogleAdsSettings(
            oauth_client_id=_required_text(data, "oauth_client_id"),
            customer_id=normalize_customer_id(_required_text(data, "customer_id")),
            login_customer_id=normalize_optional_customer_id(
                _optional_text(data, "login_customer_id")
            ),
        )


def normalize_customer_id(value: str) -> str:
    """Return a Google Ads customer ID as exactly ten digits."""
    normalized = value.replace("-", "").replace(" ", "")
    if _CUSTOMER_ID.fullmatch(normalized) is None:
        raise ValueError("Customer ID must contain exactly 10 digits.")
    return normalized


def normalize_optional_customer_id(value: str) -> str | None:
    """Normalize an optional login customer ID, preserving an omitted value."""
    return None if not value.strip() else normalize_customer_id(value)


def _required_text(data: object, name: str) -> str:
    if not isinstance(data, dict) or not isinstance(value := data.get(name), str):
        raise ValueError(f"Saved Google Ads settings have no valid {name}.")
    return value


def _optional_text(data: object, name: str) -> str:
    if not isinstance(data, dict):
        raise ValueError(f"Saved Google Ads settings have no valid {name}.")
    value = data.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Saved Google Ads settings have no valid {name}.")
    return value
