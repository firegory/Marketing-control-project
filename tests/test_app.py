"""Tests for the local FastAPI shell."""

import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.google_ads import GoogleAdsSettings, GoogleAdsSettingsStore
from marketing_control.google_ads_adapter import (
    GoogleAdsConnectionState,
    GoogleAdsConnectionStatus,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository


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
                GoogleAdsConnectionStatus(
                    GoogleAdsConnectionState.INVALID_AUTHORIZATION
                )
            ),
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


def test_initial_history_route_displays_boundary_workload_and_readiness(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(settings=_settings(tmp_path), today=lambda: date(2026, 7, 12))
    )

    response = client.get("/sync/initial-history")

    assert response.status_code == 200
    assert "2015-07-12 through 2026-07-12" in response.text
    assert (
        "estimated workload is the number of inclusive reporting days" in response.text
    )
    assert "does not delete data already retained locally" in response.text
    assert "before the initial sync can be started" in response.text


def test_initial_history_route_persists_a_valid_choice_without_starting_sync(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings=settings, today=lambda: date(2026, 7, 12)))
    page = client.get("/sync/initial-history")

    response = client.post(
        "/sync/initial-history",
        data={
            "csrf_token": _csrf_token(page.text),
            "preset": "90",
            "custom_start_date": "",
            "custom_end_date": "",
        },
    )

    assert response.status_code == 200
    assert (
        "Ready to sync 2026-04-14 through 2026-07-12 (90 days). No sync has started."
        in response.text
    )
    with database_connection(settings) as connection:
        preference = SyncRepository(connection).get_history_preference()
        run_count = connection.execute("SELECT count(*) FROM sync_runs").fetchone()
    assert preference is not None
    assert preference.kind == "initial"
    assert preference.requested_start_date == date(2026, 4, 14)
    assert run_count == (0,)


def test_initial_history_route_rejects_invalid_custom_dates(tmp_path: Path) -> None:
    client = TestClient(
        create_app(settings=_settings(tmp_path), today=lambda: date(2026, 7, 12))
    )
    page = client.get("/sync/initial-history")

    response = client.post(
        "/sync/initial-history",
        data={
            "csrf_token": _csrf_token(page.text),
            "preset": "custom",
            "custom_start_date": "2026-07-13",
            "custom_end_date": "2026-07-12",
        },
    )

    assert response.status_code == 422
    assert "Start date must be on or before end date." in response.text


def test_initial_history_route_rejects_future_and_malformed_custom_dates(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(settings=_settings(tmp_path), today=lambda: date(2026, 7, 12))
    )
    page = client.get("/sync/initial-history")
    future_response = client.post(
        "/sync/initial-history",
        data={
            "csrf_token": _csrf_token(page.text),
            "preset": "custom",
            "custom_start_date": "2026-07-12",
            "custom_end_date": "2026-07-13",
        },
    )
    malformed_response = client.post(
        "/sync/initial-history",
        data={
            "csrf_token": _csrf_token(future_response.text),
            "preset": "custom",
            "custom_start_date": "not-a-date",
            "custom_end_date": "2026-07-12",
        },
    )

    assert future_response.status_code == 422
    assert "End date cannot be in the future." in future_response.text
    assert malformed_response.status_code == 422
    assert "Start date must be a valid date." in malformed_response.text


def test_initial_history_route_rejects_missing_or_invalid_csrf(tmp_path: Path) -> None:
    client = TestClient(
        create_app(settings=_settings(tmp_path), today=lambda: date(2026, 7, 12))
    )

    response = client.post(
        "/sync/initial-history", data={"csrf_token": "invalid", "preset": "7"}
    )

    assert response.status_code == 403


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def _csrf_token(page: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)
