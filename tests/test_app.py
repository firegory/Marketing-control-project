"""Tests for the local FastAPI shell."""

from pathlib import Path

from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.google_ads import GoogleAdsSettings, GoogleAdsSettingsStore
from marketing_control.google_ads_adapter import (
    GoogleAdsConnectionState,
    GoogleAdsConnectionStatus,
)
from marketing_control.settings import AppPaths, Settings


class FakeConnectionValidator:
    def __init__(self, status: GoogleAdsConnectionStatus) -> None:
        self.status = status

    def connection_status(self) -> GoogleAdsConnectionStatus:
        return self.status


def test_root_renders_the_server_side_application_shell() -> None:
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "<h1>Marketing Control</h1>" in response.text


def test_health_reports_ready() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_google_ads_route_displays_connected_customer_metadata() -> None:
    client = TestClient(
        create_app(
            connection_validator=FakeConnectionValidator(
                GoogleAdsConnectionStatus(
                    GoogleAdsConnectionState.USABLE,
                    customer_id="1234567890",
                    customer_name="Example Account",
                    currency_code="USD",
                    time_zone="America/New_York",
                )
            )
        )
    )

    response = client.get("/settings/google-ads")

    assert response.status_code == 200
    assert "Connected to Example Account (1234567890)." in response.text
    assert "Currency</dt><dd>USD" in response.text
    assert "Timezone</dt><dd>America/New_York" in response.text


def test_google_ads_route_shows_secret_safe_reauthorization_recovery(
    tmp_path: Path,
) -> None:
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    GoogleAdsSettingsStore(settings).save(
        GoogleAdsSettings(
            oauth_client_id="client-id.apps.googleusercontent.com",
            customer_id="1234567890",
            login_customer_id=None,
        )
    )
    client = TestClient(
        create_app(
            settings=settings,
            connection_validator=FakeConnectionValidator(
                GoogleAdsConnectionStatus(GoogleAdsConnectionState.INVALID_AUTHORIZATION)
            )
        )
    )

    response = client.get("/settings/google-ads")

    assert response.status_code == 200
    assert "Google authorization is no longer valid" in response.text
    assert "Reauthorize Google Ads" in response.text


def test_google_ads_route_displays_safe_temporary_failure() -> None:
    client = TestClient(
        create_app(
            connection_validator=FakeConnectionValidator(
                GoogleAdsConnectionStatus(GoogleAdsConnectionState.TEMPORARY_FAILURE)
            )
        )
    )

    response = client.get("/settings/google-ads")

    assert response.status_code == 200
    assert "Google Ads could not be reached. Try again later." in response.text
