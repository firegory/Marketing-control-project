"""Controlled, injectable orchestration for independently persisted report work.

The registry order is the execution order. Tasks receive planned date ranges only;
adapters and network clients remain outside this module.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from marketing_control.sync_diagnostics import classify_failure
from marketing_control.sync_history import SyncRepository, SyncRun
from marketing_control.sync_planning import DateRange, ReportRangePlanner


class ReportTask(Protocol):
    """Execute one named report for already-planned inclusive date ranges."""

    name: str

    def execute(self, ranges: Sequence[DateRange]) -> None: ...


@dataclass(frozen=True)
class ReportTaskRegistry:
    """Ordered boundary containing only supported host-provided report tasks."""

    tasks: tuple[ReportTask, ...]

    def __post_init__(self) -> None:
        names = [task.name for task in self.tasks]
        if any(not name.strip() for name in names) or len(names) != len(set(names)):
            raise ValueError("report task names must be non-empty and unique")


class SyncRunCoordinator:
    """Run ordered report tasks while isolating failures and persisting progress."""

    def __init__(
        self, repository: SyncRepository, registry: ReportTaskRegistry
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._planner = ReportRangePlanner(repository)

    def start(self, requested_range: DateRange) -> SyncRun:
        """Create and execute a fresh run, continuing after each task failure."""
        return self._execute(requested_range, self._registry.tasks)

    def retry_failed(self, previous_run_id: str) -> SyncRun:
        """Create a run containing only report work failed by a prior run."""
        previous = self._repository.get_run(previous_run_id)
        failed_names = tuple(
            work.report_name
            for work in self._repository.list_report_runs(previous.id)
            if work.status == "failed"
        )
        if not failed_names:
            raise ValueError("sync run has no failed report work to retry")
        tasks = tuple(
            task for task in self._registry.tasks if task.name in failed_names
        )
        if len(tasks) != len(failed_names):
            raise ValueError("a failed report is no longer registered")
        audit = self._repository.start_retry_audit(previous.id, failed_names)
        try:
            retry = self._execute(
                DateRange(
                    previous.requested_start_date, previous.requested_end_date
                ),
                tasks,
            )
        except Exception:
            self._repository.finish_retry_audit(audit.id, None, "failed")
            raise
        self._repository.finish_retry_audit(
            audit.id,
            retry.id,
            "succeeded" if retry.status == "succeeded" else "failed",
        )
        return retry

    def _execute(
        self, requested_range: DateRange, tasks: Sequence[ReportTask]
    ) -> SyncRun:
        run = self._repository.start_orchestration_run(
            requested_range.start_date,
            requested_range.end_date,
            [task.name for task in tasks],
        )
        try:
            for task in tasks:
                ranges = self._planner.plan(task.name, requested_range)
                if not ranges:
                    self._repository.skip_report_run(run.id, task.name)
                    continue
                self._repository.start_report_run(run.id, task.name, len(ranges))
                try:
                    task.execute(ranges)
                except Exception as error:
                    category = classify_failure(error)
                    logging.getLogger("marketing_control.sync").exception(
                        "sync report failed run_id=%s report=%s category=%s",
                        run.id,
                        task.name,
                        category,
                    )
                    self._repository.fail_report_run(
                        run.id, task.name, str(error), category
                    )
                    continue
                self._planner.record_coverage(task.name, ranges)
                self._repository.complete_report_run(run.id, task.name, len(ranges))
            return self._repository.finish_orchestration_run(run.id)
        except Exception:
            self._repository.abandon_orchestration_run(run.id)
            raise
