"""Tests for report-specific missing-range planning."""

from datetime import date
from pathlib import Path

import pytest

from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_planning import (
    DateRange,
    ReportRangePlanner,
    calculate_missing_ranges,
    merge_ranges,
    plan_ranges,
)


def date_range(start_day: int, end_day: int) -> DateRange:
    return DateRange(date(2026, 1, start_day), date(2026, 1, end_day))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


@pytest.mark.parametrize(
    ("ranges", "expected"),
    [
        ([], []),
        ([date_range(3, 5)], [date_range(3, 5)]),
        ([date_range(3, 5), date_range(1, 2)], [date_range(1, 5)]),
        ([date_range(1, 5), date_range(3, 7)], [date_range(1, 7)]),
        ([date_range(1, 10), date_range(3, 5)], [date_range(1, 10)]),
    ],
)
def test_merge_ranges_normalizes_adjacent_overlapping_and_nested_intervals(
    ranges: list[DateRange], expected: list[DateRange]
) -> None:
    assert merge_ranges(ranges) == expected


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        ([], [date_range(1, 10)]),
        ([date_range(1, 10)], []),
        ([date_range(3, 7)], [date_range(1, 2), date_range(8, 10)]),
        ([date_range(1, 3), date_range(8, 10)], [date_range(4, 7)]),
        ([date_range(1, 2), date_range(3, 5), date_range(6, 10)], []),
        ([date_range(1, 1), date_range(10, 10)], [date_range(2, 9)]),
        ([date_range(1, 12)], []),
        ([date_range(3, 8)], [date_range(1, 2), date_range(9, 10)]),
    ],
)
def test_calculate_missing_ranges_handles_all_inclusive_boundaries(
    coverage: list[DateRange], expected: list[DateRange]
) -> None:
    assert calculate_missing_ranges(date_range(1, 10), coverage) == expected


def test_plan_ranges_refreshes_only_the_configured_recent_dates() -> None:
    assert plan_ranges(
        date_range(1, 10), [date_range(1, 10)], refresh_window_days=3
    ) == [date_range(8, 10)]


def test_plan_ranges_merges_missing_and_refresh_work() -> None:
    assert plan_ranges(
        date_range(1, 10), [date_range(1, 7), date_range(9, 10)], refresh_window_days=3
    ) == [date_range(8, 10)]


def test_plan_ranges_refresh_window_larger_than_request_refreshes_all_dates() -> None:
    assert plan_ranges(
        date_range(1, 10), [date_range(1, 10)], refresh_window_days=11
    ) == [date_range(1, 10)]


def test_plan_ranges_rejects_negative_refresh_window() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        plan_ranges(date_range(1, 10), [], refresh_window_days=-1)


def test_planner_expands_history_without_replanning_persisted_coverage(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        planner = ReportRangePlanner(repository)
        planner.record_coverage("campaign", [date_range(3, 7)])

        assert planner.plan("campaign", date_range(1, 10)) == [
            date_range(1, 2),
            date_range(8, 10),
        ]


def test_planner_shrinking_history_does_not_delete_coverage(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        planner = ReportRangePlanner(repository)
        planner.record_coverage("campaign", [date_range(1, 10)])

        assert planner.plan("campaign", date_range(4, 6)) == []
        stored_coverage = repository.list_coverage("campaign")[0]
        assert stored_coverage.covered_start_date == date(2026, 1, 1)
        assert stored_coverage.covered_end_date == date(2026, 1, 10)


def test_planner_is_report_specific_and_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        planner = ReportRangePlanner(repository)
        planner.record_coverage("campaign", [date_range(1, 10)])
        planner.record_coverage("campaign", [date_range(1, 10)])

        assert planner.plan("campaign", date_range(1, 10)) == []
        assert planner.plan("search_terms", date_range(1, 10)) == [date_range(1, 10)]
        assert len(repository.list_coverage("campaign")) == 1


def test_record_coverage_normalizes_completed_adjacent_ranges(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        planner = ReportRangePlanner(repository)

        recorded = planner.record_coverage(
            "campaign", [date_range(1, 3), date_range(4, 5), date_range(2, 4)]
        )

    assert [(item.covered_start_date, item.covered_end_date) for item in recorded] == [
        (date(2026, 1, 1), date(2026, 1, 5))
    ]
