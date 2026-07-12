"""Tests for durable ordered sync coordination and retry behavior."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry, SyncRunCoordinator
from marketing_control.sync_planning import DateRange


class FakeTask:
    def __init__(
        self, name: str, events: list[str], error: Exception | None = None
    ) -> None:
        self.name = name
        self._events = events
        self._error = error

    def execute(self, ranges: Sequence[DateRange]) -> None:
        self._events.append(self.name)
        if self._error:
            raise self._error


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_coordinator_orders_tasks_continues_after_failure_and_records_work(
    settings: Settings,
) -> None:
    events: list[str] = []
    registry = ReportTaskRegistry(
        (
            FakeTask("account", events),
            FakeTask("daily", events, RuntimeError("token=top-secret unavailable")),
            FakeTask("terms", events),
        )
    )
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        run = SyncRunCoordinator(repository, registry).start(
            DateRange(date(2026, 1, 1), date(2026, 1, 2))
        )
        work = repository.list_report_runs(run.id)

    assert events == ["account", "daily", "terms"]
    assert run.status == "failed"
    assert [
        (item.report_name, item.status, item.completed_units, item.total_units)
        for item in work
    ] == [
        ("account", "succeeded", 1, 1),
        ("daily", "failed", 0, 1),
        ("terms", "succeeded", 1, 1),
    ]
    assert work[1].failure_detail == "token=[REDACTED] unavailable"
    assert all(item.ended_at is not None for item in work)


def test_retry_runs_only_prior_failed_report(settings: Settings) -> None:
    first_events: list[str] = []
    retry_events: list[str] = []
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        first = SyncRunCoordinator(
            repository,
            ReportTaskRegistry(
                (
                    FakeTask("one", first_events),
                    FakeTask("two", first_events, ValueError("bad")),
                )
            ),
        ).start(DateRange(date(2026, 1, 1), date(2026, 1, 1)))
        retry = SyncRunCoordinator(
            repository,
            ReportTaskRegistry(
                (FakeTask("one", retry_events), FakeTask("two", retry_events))
            ),
        ).retry_failed(first.id)

    assert first_events == ["one", "two"]
    assert retry_events == ["two"]
    assert retry.status == "succeeded"


def test_repository_rejects_concurrent_run_and_invalid_report_transition(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        run = repository.start_orchestration_run(
            date(2026, 1, 1), date(2026, 1, 1), ["daily"]
        )
        with pytest.raises(ValueError, match="already running"):
            repository.start_orchestration_run(
                date(2026, 1, 1), date(2026, 1, 1), ["other"]
            )
        with pytest.raises(ValueError, match="not running"):
            repository.complete_report_run(run.id, "daily", 0)
        repository.skip_report_run(run.id, "daily")
        completed = repository.finish_orchestration_run(run.id)

    assert completed.status == "succeeded"
