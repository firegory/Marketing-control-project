"""Tests for Data History's report-specific read model."""

from datetime import date
from pathlib import Path

from marketing_control.data_history import data_history
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_planning import DateRange


def test_data_history_merges_retained_ranges_and_plans_each_report_independently(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.record_coverage("campaign", date(2026, 1, 1), date(2026, 1, 3))
        repository.record_coverage("campaign", date(2026, 1, 4), date(2026, 1, 5))
        repository.record_coverage(
            "search_terms", date(2026, 1, 1), date(2026, 1, 2)
        )

        history = data_history(
            repository, DateRange(date(2026, 1, 1), date(2026, 1, 7))
        )

    assert [(item.report_name, item.retained_ranges) for item in history] == [
        ("campaign", (DateRange(date(2026, 1, 1), date(2026, 1, 5)),)),
        ("search_terms", (DateRange(date(2026, 1, 1), date(2026, 1, 2)),)),
    ]
    assert history[0].planned_ranges == (DateRange(date(2026, 1, 6), date(2026, 1, 7)),)
    assert history[1].planned_ranges == (DateRange(date(2026, 1, 3), date(2026, 1, 7)),)


def test_data_history_without_a_request_only_shows_retained_coverage(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        repository.record_coverage("campaign", date(2026, 1, 1), date(2026, 1, 1))

        history = data_history(repository)

    assert history[0].planned_ranges == ()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
