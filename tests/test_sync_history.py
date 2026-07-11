"""Tests for durable sync execution history and report coverage."""

from datetime import date
from pathlib import Path

import pytest

from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_sync_run_records_requested_and_completed_coverage(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        started = repository.start_run(date(2026, 1, 1), date(2026, 1, 31))
        completed = repository.complete_run(
            started.id, date(2026, 1, 3), date(2026, 1, 30)
        )

    assert started.status == "running"
    assert started.started_at.tzinfo is None
    assert completed.status == "succeeded"
    assert completed.requested_start_date == date(2026, 1, 1)
    assert completed.requested_end_date == date(2026, 1, 31)
    assert completed.completed_start_date == date(2026, 1, 3)
    assert completed.completed_end_date == date(2026, 1, 30)
    assert completed.ended_at is not None


def test_failed_sync_run_redacts_and_bounds_failure_detail(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        started = repository.start_run(date(2026, 1, 1), date(2026, 1, 1))
        failed = repository.fail_run(
            started.id, "Google Ads rejected developer_token=super-secret\nTry again."
        )

    assert failed.status == "failed"
    assert (
        failed.failure_detail
        == "Google Ads rejected developer_token=[REDACTED] Try again."
    )
    assert failed.ended_at is not None


def test_report_coverage_is_separate_for_each_report(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.record_coverage("campaign", date(2026, 1, 1), date(2026, 1, 31))
        repository.record_coverage("search_terms", date(2026, 1, 1), date(2026, 1, 15))
        repository.record_coverage("campaign", date(2026, 2, 1), date(2026, 2, 28))

        campaign_coverage = repository.list_coverage("campaign")
        search_term_coverage = repository.list_coverage("search_terms")

    assert [item.covered_start_date for item in campaign_coverage] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]
    assert [item.covered_end_date for item in search_term_coverage] == [
        date(2026, 1, 15)
    ]


def test_history_preference_survives_a_database_restart(settings: Settings) -> None:
    with database_connection(settings) as connection:
        SyncRepository(connection).save_history_preference(
            "initial", date(2025, 1, 1), date(2026, 1, 1)
        )

    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        saved_preference = repository.get_history_preference()
        repository.save_history_preference(
            "backfill", date(2024, 1, 1), date(2024, 12, 31)
        )

    assert saved_preference is not None
    assert saved_preference.kind == "initial"
    assert saved_preference.requested_start_date == date(2025, 1, 1)

    with database_connection(settings) as connection:
        updated_preference = SyncRepository(connection).get_history_preference()

    assert updated_preference is not None
    assert updated_preference.kind == "backfill"
    assert updated_preference.requested_end_date == date(2024, 12, 31)


def test_only_running_sync_runs_can_transition(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        started = repository.start_run(date(2026, 1, 1), date(2026, 1, 1))
        repository.fail_run(started.id, "Connection could not be established")

        with pytest.raises(ValueError, match="not running"):
            repository.complete_run(started.id, date(2026, 1, 1), date(2026, 1, 1))
