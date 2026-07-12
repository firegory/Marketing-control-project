"""DuckDB persistence for sync attempts, stored coverage, and history choices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import uuid4

import duckdb

from marketing_control.logging import redact_sensitive_values
from marketing_control.sync_diagnostics import FailureCategory

SyncStatus = Literal["running", "succeeded", "failed"]
ReportRunStatus = Literal["queued", "running", "succeeded", "failed", "skipped"]
HistoryPreferenceKind = Literal["initial", "backfill"]
_MAX_FAILURE_DETAIL_LENGTH = 1_000
_FAILURE_CATEGORIES = frozenset(
    {"authentication", "api", "range", "storage", "unexpected"}
)


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
class SyncReportRun:
    """Durable status, progress, and safe diagnostics for one report's work."""

    sync_run_id: str
    report_name: str
    status: ReportRunStatus
    total_units: int
    completed_units: int
    started_at: datetime | None
    ended_at: datetime | None
    failure_detail: str | None
    failure_category: FailureCategory | None


@dataclass(frozen=True)
class SyncRetryAudit:
    """A durable record of a failed-only retry request and its result."""

    id: str
    source_sync_run_id: str
    retry_sync_run_id: str | None
    outcome: Literal["running", "succeeded", "failed"]
    requested_at: datetime
    completed_at: datetime | None
    report_names: tuple[str, ...]


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

    def start_orchestration_run(
        self,
        requested_start_date: date,
        requested_end_date: date,
        report_names: list[str],
    ) -> SyncRun:
        """Atomically reserve the sole active run and queue ordered report work."""
        if len(report_names) != len(set(report_names)) or any(
            not name.strip() for name in report_names
        ):
            raise ValueError("report names must be non-empty and unique")
        _validate_date_range(requested_start_date, requested_end_date)
        self._connection.execute("BEGIN TRANSACTION")
        try:
            if self._connection.execute("SELECT 1 FROM sync_run_locks").fetchone():
                raise ValueError("a sync run is already running")
            run = self.start_run(requested_start_date, requested_end_date)
            self._connection.execute(
                "INSERT INTO sync_run_locks VALUES (1, ?)", [run.id]
            )
            for name in report_names:
                self._connection.execute(
                    "INSERT INTO sync_report_runs "
                    "(sync_run_id, report_name, status, total_units, completed_units, "
                    "started_at, ended_at, failure_detail) VALUES "
                    "(?, ?, 'queued', 0, 0, NULL, NULL, NULL)",
                    [run.id, name],
                )
            self._connection.execute("COMMIT")
            return run
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def start_report_run(
        self, sync_run_id: str, report_name: str, total_units: int
    ) -> None:
        """Transition queued work to running with its planned work count."""
        if total_units <= 0:
            raise ValueError("total_units must be positive")
        self._update_report_run(
            sync_run_id,
            report_name,
            "queued",
            "status = 'running', total_units = ?, started_at = ?",
            [total_units, _now()],
        )

    def complete_report_run(
        self, sync_run_id: str, report_name: str, completed_units: int
    ) -> None:
        """Complete all planned units for running report work."""
        self._update_report_run(
            sync_run_id,
            report_name,
            "running",
            "status = 'succeeded', completed_units = ?, ended_at = ?, "
            "failure_detail = NULL, failure_category = NULL",
            [completed_units, _now()],
        )

    def fail_report_run(
        self,
        sync_run_id: str,
        report_name: str,
        failure_detail: str,
        failure_category: FailureCategory,
    ) -> None:
        """Fail running report work with display-safe diagnostic text."""
        if failure_category not in _FAILURE_CATEGORIES:
            raise ValueError("failure_category is not supported")
        self._update_report_run(
            sync_run_id,
            report_name,
            "running",
            "status = 'failed', ended_at = ?, failure_detail = ?, failure_category = ?",
            [_now(), _safe_failure_detail(failure_detail), failure_category],
        )

    def skip_report_run(self, sync_run_id: str, report_name: str) -> None:
        """Mark queued work skipped when range planning finds no work."""
        self._update_report_run(
            sync_run_id,
            report_name,
            "queued",
            "status = 'skipped', ended_at = ?",
            [_now()],
        )

    def list_report_runs(self, sync_run_id: str) -> list[SyncReportRun]:
        """Return a run's report work in registry insertion order."""
        rows = self._connection.execute(
            "SELECT * FROM sync_report_runs WHERE sync_run_id = ? ORDER BY rowid",
            [sync_run_id],
        ).fetchall()
        return [SyncReportRun(*row) for row in rows]

    def list_failed_runs(self, limit: int = 20) -> list[SyncRun]:
        """Return a bounded newest-first history of failed runs for diagnostics."""
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self._connection.execute(
            "SELECT * FROM sync_runs WHERE status = 'failed' "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [SyncRun(*row) for row in rows]

    def start_retry_audit(
        self, source_sync_run_id: str, report_names: tuple[str, ...]
    ) -> SyncRetryAudit:
        """Persist the source run and exact failed report set before retrying it."""
        if not report_names or len(report_names) != len(set(report_names)):
            raise ValueError("retry audit requires unique failed report names")
        audit = SyncRetryAudit(
            str(uuid4()),
            source_sync_run_id,
            None,
            "running",
            _now(),
            None,
            report_names,
        )
        self._connection.execute(
            "INSERT INTO sync_retry_audits VALUES (?, ?, NULL, 'running', ?, NULL)",
            [audit.id, audit.source_sync_run_id, audit.requested_at],
        )
        self._connection.executemany(
            "INSERT INTO sync_retry_audit_reports VALUES (?, ?)",
            [(audit.id, report_name) for report_name in report_names],
        )
        return audit

    def finish_retry_audit(
        self,
        audit_id: str,
        retry_sync_run_id: str | None,
        outcome: Literal["succeeded", "failed"],
    ) -> None:
        """Record the retry run and terminal outcome without changing source history."""
        row = self._connection.execute(
            "UPDATE sync_retry_audits SET retry_sync_run_id = ?, outcome = ?, "
            "completed_at = ? WHERE id = ? AND outcome = 'running' RETURNING id",
            [retry_sync_run_id, outcome, _now(), audit_id],
        ).fetchone()
        if row is None:
            raise ValueError("retry audit is not running")

    def list_retry_audits(self, source_sync_run_id: str) -> list[SyncRetryAudit]:
        """Return audit records with their persisted source report sets."""
        rows = self._connection.execute(
            "SELECT id, source_sync_run_id, retry_sync_run_id, outcome, requested_at, "
            "completed_at FROM sync_retry_audits WHERE source_sync_run_id = ? "
            "ORDER BY requested_at DESC, id DESC",
            [source_sync_run_id],
        ).fetchall()
        return [
            SyncRetryAudit(
                id=row[0],
                source_sync_run_id=row[1],
                retry_sync_run_id=row[2],
                outcome=row[3],
                requested_at=row[4],
                completed_at=row[5],
                report_names=tuple(
                    item[0]
                    for item in self._connection.execute(
                        "SELECT report_name FROM sync_retry_audit_reports "
                        "WHERE retry_audit_id = ? ORDER BY report_name",
                        [row[0]],
                    ).fetchall()
                ),
            )
            for row in rows
        ]

    def latest_run(self) -> SyncRun | None:
        """Return the most recently started run, if any."""
        row = self._connection.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else SyncRun(*row)

    def finish_orchestration_run(self, sync_run_id: str) -> SyncRun:
        """Finalize terminal report work and release the active run lock."""
        work = self.list_report_runs(sync_run_id)
        if any(item.status in {"queued", "running"} for item in work):
            raise ValueError("sync run still has active report work")
        if any(item.status == "failed" for item in work):
            self.fail_run(
                sync_run_id, "One or more reports failed. Review report status."
            )
        else:
            run = self.get_run(sync_run_id)
            self.complete_run(
                sync_run_id, run.requested_start_date, run.requested_end_date
            )
        self._connection.execute(
            "DELETE FROM sync_run_locks WHERE sync_run_id = ?", [sync_run_id]
        )
        return self.get_run(sync_run_id)

    def abandon_orchestration_run(self, sync_run_id: str) -> None:
        """Fail and unlock an interrupted coordinator run without exposing internals."""
        run = self.get_run(sync_run_id)
        if run.status == "running":
            self.fail_run(
                sync_run_id, "The sync coordinator could not persist progress."
            )
        self._connection.execute(
            "DELETE FROM sync_run_locks WHERE sync_run_id = ?", [sync_run_id]
        )

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

    def list_report_names(self) -> list[str]:
        """Return every report with stored coverage in a stable display order."""
        rows = self._connection.execute(
            "SELECT DISTINCT report_name FROM report_coverage ORDER BY report_name"
        ).fetchall()
        return [row[0] for row in rows]

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

    def _update_report_run(
        self,
        sync_run_id: str,
        report_name: str,
        expected_status: ReportRunStatus,
        assignments: str,
        values: list[object],
    ) -> None:
        row = self._connection.execute(
            f"UPDATE sync_report_runs SET {assignments} "
            "WHERE sync_run_id = ? AND report_name = ? AND status = ? "
            "RETURNING report_name",
            [*values, sync_run_id, report_name, expected_status],
        ).fetchone()
        if row is not None:
            return
        exists = self._connection.execute(
            "SELECT 1 FROM sync_report_runs WHERE sync_run_id = ? AND report_name = ?",
            [sync_run_id, report_name],
        ).fetchone()
        if exists is None:
            raise KeyError(f"report work {report_name} does not exist")
        raise ValueError(f"report work {report_name} is not {expected_status}")


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
