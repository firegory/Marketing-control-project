"""Tests for persisted manual and once-per-local-day refresh triggers."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.google_ads import GoogleAdsSettings, GoogleAdsSettingsStore
from marketing_control.refresh_triggers import StartupRefreshService
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry
from marketing_control.sync_planning import DateRange


class RecordingTask:
    name = "daily"

    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self._events = events
        self._error = error

    def execute(self, ranges: Sequence[DateRange]) -> None:
        self._events.append(self.name)
        if self._error:
            raise self._error


def test_startup_refresh_is_opt_in_and_persists_terminal_outcome(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    events: list[str] = []
    _save_connected_metadata(settings, "America/New_York")
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.save_history_preference(
            "initial", date(2026, 7, 1), date(2026, 7, 2)
        )
        repository.save_startup_refresh_enabled(True)

    _service(settings, events).run()

    with database_connection(settings) as connection:
        outcome = SyncRepository(connection).get_startup_refresh_outcome(
            "1234567890", date(2026, 7, 11)
        )
    assert events == ["daily"]
    assert outcome is not None
    assert outcome.status == "succeeded"
    assert outcome.completed_at is not None


def test_startup_refresh_skips_without_opt_in_connected_metadata_history_or_tasks(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    events: list[str] = []
    _service(settings, events).run()
    _save_connected_metadata(settings, "America/New_York")
    _service(settings, events).run()
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.save_history_preference(
            "initial", date(2026, 7, 1), date(2026, 7, 2)
        )
    _service(settings, events).run()
    with database_connection(settings) as connection:
        SyncRepository(connection).save_startup_refresh_enabled(True)
    StartupRefreshService(
        settings,
        GoogleAdsSettingsStore(settings),
        ReportTaskRegistry(()),
        now=_now,
    ).run()

    assert events == []


def test_startup_refresh_reservation_survives_restart_and_records_failure(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    first_events: list[str] = []
    retry_events: list[str] = []
    _save_connected_metadata(settings, "America/New_York")
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.save_history_preference(
            "initial", date(2026, 7, 1), date(2026, 7, 2)
        )
        repository.save_startup_refresh_enabled(True)

    _service(settings, first_events, RuntimeError("unavailable")).run()
    _service(settings, retry_events).run()

    with database_connection(settings) as connection:
        outcome = SyncRepository(connection).get_startup_refresh_outcome(
            "1234567890", date(2026, 7, 11)
        )
    assert first_events == ["daily"]
    assert retry_events == []
    assert outcome is not None
    assert outcome.status == "failed"
    assert outcome.failure_detail == "One or more reports failed. Review report status."


def test_concurrent_instances_reserve_only_one_startup_refresh(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings):
        pass

    def reserve() -> bool:
        with database_connection(settings) as connection:
            return SyncRepository(connection).reserve_startup_refresh(
                "1234567890", date(2026, 7, 11)
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(lambda _: reserve(), range(2)))

    assert reservations.count(True) == 1
    assert reservations.count(False) == 1


def test_manual_refresh_is_csrf_protected_and_does_not_change_startup_marker(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    events: list[str] = []
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.save_history_preference(
            "initial", date(2026, 7, 1), date(2026, 7, 2)
        )
        repository.reserve_startup_refresh("1234567890", date(2026, 7, 11))
    client = TestClient(
        create_app(
            settings=settings,
            report_registry=ReportTaskRegistry((RecordingTask(events),)),
        )
    )

    blocked = client.post("/sync/runs", data={"csrf_token": "invalid"})
    page = client.get("/sync/runs")
    refreshed = client.post("/sync/runs", data={"csrf_token": _csrf_token(page.text)})

    with database_connection(settings) as connection:
        outcome = SyncRepository(connection).get_startup_refresh_outcome(
            "1234567890", date(2026, 7, 11)
        )
    assert blocked.status_code == 403
    assert refreshed.status_code == 200
    assert events == ["daily"]
    assert outcome is not None
    assert outcome.status == "attempted"


def test_startup_refresh_opt_in_is_csrf_protected_and_persisted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings=settings))
    page = client.get("/sync/runs")

    blocked = client.post(
        "/sync/startup-refresh", data={"csrf_token": "invalid", "enabled": "true"}
    )
    saved = client.post(
        "/sync/startup-refresh",
        data={"csrf_token": _csrf_token(page.text), "enabled": "true"},
    )

    with database_connection(settings) as connection:
        enabled = SyncRepository(connection).startup_refresh_enabled()
    assert blocked.status_code == 403
    assert saved.status_code == 200
    assert "checked" in saved.text
    assert enabled is True


def test_startup_refresh_invalid_timezone_and_failure_do_not_block_lifespan(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _save_connected_metadata(settings, "Not/A_Timezone")
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.save_history_preference(
            "initial", date(2026, 7, 1), date(2026, 7, 2)
        )
        repository.save_startup_refresh_enabled(True)
    app = create_app(settings=settings, report_registry=ReportTaskRegistry(()))

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def _save_connected_metadata(settings: Settings, time_zone: str) -> None:
    GoogleAdsSettingsStore(settings).save(
        GoogleAdsSettings(
            oauth_client_id="client-id.apps.googleusercontent.com",
            customer_id="1234567890",
            login_customer_id=None,
            time_zone=time_zone,
        )
    )


def _service(
    settings: Settings, events: list[str], error: Exception | None = None
) -> StartupRefreshService:
    return StartupRefreshService(
        settings,
        GoogleAdsSettingsStore(settings),
        ReportTaskRegistry((RecordingTask(events, error),)),
        now=_now,
    )


def _now() -> datetime:
    return datetime(2026, 7, 12, 3, 0, tzinfo=UTC)


def _csrf_token(page: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)
