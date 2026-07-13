"""Read model for the local Google Ads integration status page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marketing_control.google_ads_adapter import GoogleAdsConnectionStatus
from marketing_control.sync_history import ReportCoverage, SyncReportRun, SyncRun
from marketing_control.sync_orchestration import ReportTaskRegistry
from marketing_control.sync_planning import (
    DateRange,
    calculate_missing_ranges,
    merge_ranges,
)

IntegrationState = Literal["current", "stale", "incomplete", "failed"]


@dataclass(frozen=True)
class ReportIntegrationStatus:
    """Coverage, freshness, and latest work state for one known report."""

    report_name: str
    state: IntegrationState
    coverage: tuple[DateRange, ...]
    latest_work: SyncReportRun | None


@dataclass(frozen=True)
class IntegrationStatus:
    """Display-ready local state without initiating validation or synchronization."""

    connection: GoogleAdsConnectionStatus
    configured_customer_id: str | None
    account_date: date
    account_time_zone: str
    latest_run: SyncRun | None
    latest_run_time: datetime | None
    reports: tuple[ReportIntegrationStatus, ...]


def integration_status(
    *,
    connection: GoogleAdsConnectionStatus,
    configured_customer_id: str | None,
    latest_run: SyncRun | None,
    latest_work: list[SyncReportRun],
    coverage_by_report: dict[str, list[ReportCoverage]],
    registry: ReportTaskRegistry,
    now: datetime,
) -> IntegrationStatus:
    """Build deterministic report states from the latest durable sync metadata.

    Failed latest work takes precedence. Incomplete means latest work is active or
    its successful range is not fully retained. Stale means retained coverage does
    not reach the account's current local date; otherwise coverage is current.
    """
    time_zone = _account_time_zone(connection.time_zone)
    local_now = _as_utc(now).astimezone(time_zone)
    work_by_report = {item.report_name: item for item in latest_work}
    report_names = sorted(
        {task.name for task in registry.tasks}
        | set(coverage_by_report)
        | set(work_by_report)
    )
    reports = tuple(
        _report_status(
            name,
            coverage_by_report.get(name, []),
            work_by_report.get(name),
            latest_run,
            local_now.date(),
        )
        for name in report_names
    )
    latest_run_time = None
    if latest_run is not None:
        completed_at = latest_run.ended_at or latest_run.started_at
        latest_run_time = _as_utc(completed_at).astimezone(time_zone)
    return IntegrationStatus(
        connection=connection,
        configured_customer_id=configured_customer_id,
        account_date=local_now.date(),
        account_time_zone=str(time_zone),
        latest_run=latest_run,
        latest_run_time=latest_run_time,
        reports=reports,
    )


def _report_status(
    report_name: str,
    coverage: list[ReportCoverage],
    latest_work: SyncReportRun | None,
    latest_run: SyncRun | None,
    account_date: date,
) -> ReportIntegrationStatus:
    ranges = tuple(
        merge_ranges(
            DateRange(item.covered_start_date, item.covered_end_date)
            for item in coverage
        )
    )
    if latest_work is not None and latest_work.status == "failed":
        state: IntegrationState = "failed"
    elif not ranges or (
        latest_work is not None and latest_work.status in {"queued", "running"}
    ):
        state = "incomplete"
    elif len(ranges) > 1:
        state = "incomplete"
    elif (
        latest_work is not None
        and latest_work.status == "succeeded"
        and latest_run is not None
        and calculate_missing_ranges(
            DateRange(
                latest_run.requested_start_date,
                latest_run.requested_end_date,
            ),
            ranges,
        )
    ):
        state = "incomplete"
    elif ranges[-1].end_date < account_date:
        state = "stale"
    else:
        state = "current"
    return ReportIntegrationStatus(report_name, state, ranges, latest_work)


def _account_time_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
