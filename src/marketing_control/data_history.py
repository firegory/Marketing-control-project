"""Read models for the server-rendered Data History settings page."""

from __future__ import annotations

from dataclasses import dataclass

from marketing_control.sync_history import SyncRepository
from marketing_control.sync_planning import DateRange, ReportRangePlanner, merge_ranges


@dataclass(frozen=True)
class ReportHistory:
    """Retained and planned ranges for one independently tracked report."""

    report_name: str
    retained_ranges: tuple[DateRange, ...]
    planned_ranges: tuple[DateRange, ...]


def data_history(
    repository: SyncRepository, requested_range: DateRange | None = None
) -> list[ReportHistory]:
    """Describe every known report without recording coverage or starting a sync."""
    planner = ReportRangePlanner(repository)
    reports: list[ReportHistory] = []
    for report_name in repository.list_report_names():
        retained_ranges = tuple(
            merge_ranges(
                DateRange(item.covered_start_date, item.covered_end_date)
                for item in repository.list_coverage(report_name)
            )
        )
        planned_ranges = (
            tuple(planner.plan(report_name, requested_range))
            if requested_range is not None
            else ()
        )
        reports.append(ReportHistory(report_name, retained_ranges, planned_ranges))
    return reports
