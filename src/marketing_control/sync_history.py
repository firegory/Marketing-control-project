"""DuckDB persistence for sync attempts, stored coverage, and history choices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4

import duckdb

from marketing_control.logging import redact_sensitive_values

SyncStatus = Literal["running", "succeeded", "failed"]
HistoryPreferenceKind = Literal["initial", "backfill"]
_MAX_FAILURE_DETAIL_LENGTH = 1_000


@dataclass(frozen=True)
class SyncRun:
    """A persisted attempt to synchronize one requested range."""

    id: str
    status: SyncStatus
    requested_start_date: date
    requested_end_date: date
    completed_start_date: date | None
    completed_end_date: date | None
    failure_detail: str | None
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class ReportCoverage:
    """A date interval stored for one report."""

    report_name: str
    covered_start_date: date
    covered_end_date: date
    sync_run_id: str | None
    recorded_at: datetime


@dataclass(frozen=True)
class HistoryPreference:
    """The latest date range selected for an initial import or backfill."""

    kind: HistoryPreferenceKind
    requested_start_date: date
    requested_end_date: date
    updated_at: datetime


class SyncRepository:
    """Focused persistence API; it does not plan or execute synchronization."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection

    def start_run(
        self, requested_start_date: date, requested_end_date: date
    ) -> SyncRun:
        """Record a newly started sync attempt and return its durable identity."""
        _validate_date_range(requested_start_date, requested_end_date)
        now = _now()
        sync_run = SyncRun(
            id=str(uuid4()),
            status="running",
            requested_start_date=requested_start_date,
            requested_end_date=requested_end_date,
            completed_start_date=None,
            completed_end_date=None,
            failure_detail=None,
            started_at=now,
            ended_at=None,
        )
        self._connection.execute(
            """
            INSERT INTO sync_runs (
                id, status, requested_start_date, requested_end_date, started_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                sync_run.id,
                sync_run.status,
                sync_run.requested_start_date,
                sync_run.requested_end_date,
                sync_run.started_at,
            ],
        )
        return sync_run

    def complete_run(
        self, sync_run_id: str, completed_start_date: date, completed_end_date: date
    ) -> SyncRun:
        """Mark a running attempt successful with the coverage it completed."""
        _validate_date_range(completed_start_date, completed_end_date)
        self._update_running_run(
            sync_run_id,
            """
            status = 'succeeded', completed_start_date = ?, completed_end_date = ?,
            failure_detail = NULL, ended_at = ?
            """,
            [completed_start_date, completed_end_date, _now()],
        )
        return self.get_run(sync_run_id)

    def fail_run(self, sync_run_id: str, failure_detail: str) -> SyncRun:
        """Mark a running attempt failed with display-safe diagnostic detail."""
        self._update_running_run(
            sync_run_id,
            "status = 'failed', failure_detail = ?, ended_at = ?",
            [_safe_failure_detail(failure_detail), _now()],
        )
        return self.get_run(sync_run_id)

    def get_run(self, sync_run_id: str) -> SyncRun:
        """Return one recorded sync run."""
        row = self._connection.execute(
            "SELECT * FROM sync_runs WHERE id = ?", [sync_run_id]
        ).fetchone()
        if row is None:
            raise KeyError(f"sync run {sync_run_id} does not exist")
        return SyncRun(*row)

    def record_coverage(
        self,
        report_name: str,
        covered_start_date: date,
        covered_end_date: date,
        *,
        sync_run_id: str | None = None,
    ) -> ReportCoverage:
        """Record one report-specific stored interval without deleting prior data."""
        if not report_name.strip():
            raise ValueError("report_name must not be empty")
        _validate_date_range(covered_start_date, covered_end_date)
        coverage = ReportCoverage(
            report_name=report_name,
            covered_start_date=covered_start_date,
            covered_end_date=covered_end_date,
            sync_run_id=sync_run_id,
            recorded_at=_now(),
        )
        self._connection.execute(
            """
            INSERT INTO report_coverage VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (report_name, covered_start_date, covered_end_date)
            DO UPDATE SET
                sync_run_id = excluded.sync_run_id,
                recorded_at = excluded.recorded_at
            """,
            [
                coverage.report_name,
                coverage.covered_start_date,
                coverage.covered_end_date,
                coverage.sync_run_id,
                coverage.recorded_at,
            ],
        )
        return coverage

    def list_coverage(self, report_name: str) -> list[ReportCoverage]:
        """Return stored coverage intervals for one report in date order."""
        rows = self._connection.execute(
            """
            SELECT * FROM report_coverage WHERE report_name = ?
            ORDER BY covered_start_date, covered_end_date
            """,
            [report_name],
        ).fetchall()
        return [ReportCoverage(*row) for row in rows]

    def save_history_preference(
        self,
        kind: HistoryPreferenceKind,
        requested_start_date: date,
        requested_end_date: date,
    ) -> HistoryPreference:
        """Persist the user's current initial-history or backfill selection."""
        if kind not in {"initial", "backfill"}:
            raise ValueError("kind must be 'initial' or 'backfill'")
        _validate_date_range(requested_start_date, requested_end_date)
        preference = HistoryPreference(
            kind, requested_start_date, requested_end_date, _now()
        )
        self._connection.execute(
            """
            INSERT INTO history_preferences VALUES (1, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                kind = excluded.kind,
                requested_start_date = excluded.requested_start_date,
                requested_end_date = excluded.requested_end_date,
                updated_at = excluded.updated_at
            """,
            [
                preference.kind,
                preference.requested_start_date,
                preference.requested_end_date,
                preference.updated_at,
            ],
        )
        return preference

    def get_history_preference(self) -> HistoryPreference | None:
        """Return the latest selected history range, if the user has chosen one."""
        row = self._connection.execute("SELECT * FROM history_preferences").fetchone()
        return None if row is None else HistoryPreference(*row[1:])

    def _update_running_run(
        self, sync_run_id: str, assignments: str, values: list[object]
    ) -> None:
        row = self._connection.execute(
            f"UPDATE sync_runs SET {assignments} "
            "WHERE id = ? AND status = 'running' RETURNING id",
            [*values, sync_run_id],
        ).fetchone()
        if row is None:
            self.get_run(sync_run_id)
            raise ValueError(f"sync run {sync_run_id} is not running")


def _validate_date_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")


def _safe_failure_detail(value: str) -> str:
    detail = " ".join(value.split())
    if not detail:
        raise ValueError("failure_detail must not be empty")
    return redact_sensitive_values(detail)[:_MAX_FAILURE_DETAIL_LENGTH]


def _now() -> datetime:
    """Return a timezone-naive timestamp normalized to UTC for DuckDB."""
    return datetime.now(UTC).replace(tzinfo=None)
