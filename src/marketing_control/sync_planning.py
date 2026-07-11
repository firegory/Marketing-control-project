"""Pure report coverage planning without an external Ads implementation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from marketing_control.sync_history import ReportCoverage, SyncRepository


@dataclass(frozen=True, order=True)
class DateRange:
    """An inclusive calendar-date range."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")


class GoogleAdsReportSource(Protocol):
    """Minimal boundary a future Google Ads adapter must satisfy."""

    def fetch_report(
        self, report_name: str, date_range: DateRange
    ) -> Sequence[Mapping[str, object]]: ...


def merge_ranges(ranges: Iterable[DateRange]) -> list[DateRange]:
    """Merge overlapping or adjacent inclusive date ranges."""
    merged: list[DateRange] = []
    for current in sorted(ranges):
        if not merged or current.start_date > merged[-1].end_date + timedelta(days=1):
            merged.append(current)
            continue
        merged[-1] = DateRange(
            merged[-1].start_date, max(merged[-1].end_date, current.end_date)
        )
    return merged


def calculate_missing_ranges(
    requested_range: DateRange, coverage: Iterable[DateRange]
) -> list[DateRange]:
    """Return the inclusive uncovered gaps within a requested range."""
    gaps: list[DateRange] = []
    next_start = requested_range.start_date
    for covered_range in merge_ranges(coverage):
        if covered_range.end_date < requested_range.start_date:
            continue
        if covered_range.start_date > requested_range.end_date:
            break
        if covered_range.start_date > next_start:
            gaps.append(
                DateRange(next_start, covered_range.start_date - timedelta(days=1))
            )
        next_start = max(next_start, covered_range.end_date + timedelta(days=1))
        if next_start > requested_range.end_date:
            break
    if next_start <= requested_range.end_date:
        gaps.append(DateRange(next_start, requested_range.end_date))
    return gaps


def plan_ranges(
    requested_range: DateRange,
    coverage: Iterable[DateRange],
    *,
    refresh_window_days: int = 0,
) -> list[DateRange]:
    """Plan uncovered dates plus the requested range's recent mutable dates.

    A nonzero refresh window includes the requested range's final N dates, even
    when already covered. The result is normalized to prevent duplicate work.
    """
    if refresh_window_days < 0:
        raise ValueError("refresh_window_days must not be negative")
    refresh_ranges: list[DateRange] = []
    if refresh_window_days:
        refresh_ranges.append(
            DateRange(
                max(
                    requested_range.start_date,
                    requested_range.end_date
                    - timedelta(days=refresh_window_days - 1),
                ),
                requested_range.end_date,
            )
        )
    return merge_ranges(
        [*calculate_missing_ranges(requested_range, coverage), *refresh_ranges]
    )


class ReportRangePlanner:
    """Plan and persist coverage for one repository without fetching reports."""

    def __init__(self, repository: SyncRepository) -> None:
        self._repository = repository

    def plan(
        self,
        report_name: str,
        requested_range: DateRange,
        *,
        refresh_window_days: int = 0,
    ) -> list[DateRange]:
        """Return missing and mutable ranges for this report only."""
        coverage = (
            DateRange(item.covered_start_date, item.covered_end_date)
            for item in self._repository.list_coverage(report_name)
        )
        return plan_ranges(
            requested_range, coverage, refresh_window_days=refresh_window_days
        )

    def record_coverage(
        self,
        report_name: str,
        completed_ranges: Iterable[DateRange],
        *,
        sync_run_id: str | None = None,
    ) -> list[ReportCoverage]:
        """Record completed intervals while retaining all prior report coverage."""
        return [
            self._repository.record_coverage(
                report_name,
                completed_range.start_date,
                completed_range.end_date,
                sync_run_id=sync_run_id,
            )
            for completed_range in merge_ranges(completed_ranges)
        ]
