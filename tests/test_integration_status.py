"""Tests for the deterministic local integration status read model."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.google_ads_adapter import (
    GoogleAdsConnectionState,
    GoogleAdsConnectionStatus,
)
from marketing_control.integration_status import integration_status
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import ReportCoverage, SyncReportRun, SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry
from marketing_control.sync_planning import DateRange


class FakeConnectionValidator:
    def __init__(self, status: GoogleAdsConnectionStatus) -> None:
        self._status = status

    def connection_status(self) -> GoogleAdsConnectionStatus:
        return self._status


class FakeReportTask:
    name = "daily"

    def execute(self, ranges: Sequence[DateRange]) -> None:
        pass


def _coverage(end_date: date) -> ReportCoverage:
    return ReportCoverage(
        "daily",
        end_date,
        end_date,
        None,
        datetime(2026, 1, 2, tzinfo=UTC),
    )


def _work(status: Literal["failed"]) -> SyncReportRun:
    return SyncReportRun("run", "daily", status, 1, 0, None, None, "safe detail", None)


def test_integration_status_route_shows_disconnected_connection() -> None:
    response = TestClient(
        create_app(
            connection_validator=FakeConnectionValidator(
                GoogleAdsConnectionStatus(GoogleAdsConnectionState.NOT_CONFIGURED)
            )
        )
    ).get("/integration-status")

    assert response.status_code == 200
    assert "Status: <strong>not_configured</strong>." in response.text
    assert "Review Google Ads connection settings" in response.text


@pytest.mark.parametrize(
    ("name", "coverage", "work", "expected"),
    [
        ("stale", [_coverage(date(2025, 12, 31))], None, "stale"),
        (
            "incomplete",
            [_coverage(date(2025, 12, 30)), _coverage(date(2026, 1, 1))],
            None,
            "incomplete",
        ),
        (
            "failed",
            [_coverage(date(2026, 1, 2))],
            _work("failed"),
            "failed",
        ),
        ("healthy", [_coverage(date(2026, 1, 1))], None, "current"),
    ],
)
def test_integration_status_classifies_report_coverage(
    name: str,
    coverage: list[ReportCoverage],
    work: SyncReportRun | None,
    expected: str,
) -> None:
    status = integration_status(
        connection=GoogleAdsConnectionStatus(
            GoogleAdsConnectionState.USABLE, time_zone="America/New_York"
        ),
        configured_customer_id="1234567890",
        latest_run=None,
        latest_work=[] if work is None else [work],
        coverage_by_report={"daily": coverage},
        registry=ReportTaskRegistry((FakeReportTask(),)),
        now=datetime(2026, 1, 2, 1, tzinfo=UTC),
    )

    assert status.account_date == date(2026, 1, 1)
    assert status.reports[0].state == expected


def test_integration_status_route_shows_current_coverage_and_latest_sync(
    tmp_path: Path,
) -> None:
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    with database_connection(settings) as database:
        repository = SyncRepository(database)
        run = repository.start_orchestration_run(
            date(2026, 1, 1), date(2026, 1, 1), ["daily"]
        )
        repository.start_report_run(run.id, "daily", 1)
        repository.record_coverage("daily", date(2026, 1, 1), date(2026, 1, 1))
        repository.complete_report_run(run.id, "daily", 1)
        repository.finish_orchestration_run(run.id)
    response = TestClient(
        create_app(
            settings=settings,
            connection_validator=FakeConnectionValidator(
                GoogleAdsConnectionStatus(
                    GoogleAdsConnectionState.USABLE,
                    customer_id="1234567890",
                    customer_name="Example Account",
                    time_zone="America/New_York",
                )
            ),
            report_registry=ReportTaskRegistry((FakeReportTask(),)),
            now=lambda: datetime(2026, 1, 2, 1, tzinfo=UTC),
        )
    )

    response = response.get("/integration-status")

    assert response.status_code == 200
    assert "Account: Example Account (1234567890)." in response.text
    assert "Reference date: 2026-01-01 (America/New_York)." in response.text
    assert "Outcome: <strong>succeeded</strong>." in response.text
    assert "Time: " in response.text
    assert "daily</strong>: <strong>current</strong>." in response.text
