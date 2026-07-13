"""Tests for persisted local synchronization diagnostics."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.credentials import CredentialStoreError
from marketing_control.logging import diagnostic_log_excerpt, redact_sensitive_values
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_diagnostics import classify_failure
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry


class SuccessfulTask:
    name = "daily"

    def execute(self, ranges: object) -> None:
        pass


def test_classification_uses_typed_and_conservative_failure_signals() -> None:
    assert classify_failure(CredentialStoreError("unavailable")) == "storage"
    assert classify_failure(ValueError("start date is invalid")) == "range"
    assert (
        classify_failure(RuntimeError("OAuth authorization expired"))
        == "authentication"
    )
    assert classify_failure(RuntimeError("unknown failure")) == "unexpected"


def test_repository_rejects_failure_categories_outside_the_stable_set(
    tmp_path: Path,
) -> None:
    with database_connection(_settings(tmp_path)) as connection:
        repository = SyncRepository(connection)
        run = repository.start_orchestration_run(
            date(2026, 1, 1), date(2026, 1, 1), ["daily"]
        )
        repository.start_report_run(run.id, "daily", 1)

        with pytest.raises(ValueError, match="not supported"):
            repository.fail_report_run(run.id, "daily", "failed", "other")  # type: ignore[arg-type]


def test_redaction_and_log_excerpt_are_bounded_relevant_and_tolerate_missing_logs(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    assert diagnostic_log_excerpt(logs, ("daily",)) == ()
    logs.mkdir()
    (logs / "marketing-control.log").write_text(
        "irrelevant token=not-shown\n"
        "daily oauth_code=never-show customer_secret: also-never\n",
        encoding="utf-8",
    )

    excerpt = diagnostic_log_excerpt(logs, ("daily",))

    assert excerpt == ("daily oauth_code=[REDACTED] customer_secret: [REDACTED]",)
    assert "secret-value" not in redact_sensitive_values("secret=secret-value")


def test_diagnostics_route_shows_failed_reports_redacted_logs_and_retry_audit(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        run = repository.start_orchestration_run(
            date(2026, 1, 1), date(2026, 1, 1), ["daily"]
        )
        repository.start_report_run(run.id, "daily", 1)
        repository.fail_report_run(
            run.id, "daily", "oauth_code=never-show", "authentication"
        )
        repository.finish_orchestration_run(run.id)
    settings.paths.logs.mkdir()
    (settings.paths.logs / "marketing-control.log").write_text(
        f"daily run_id={run.id} token=never-show\nother token=also-never\n",
        encoding="utf-8",
    )
    client = TestClient(
        create_app(
            settings=settings,
            report_registry=ReportTaskRegistry((SuccessfulTask(),)),
        )
    )

    page = client.get("/sync/diagnostics")
    retry = client.post(
        f"/sync/runs/{run.id}/retry",
        data={"csrf_token": _csrf_token(page.text)},
    )

    assert page.status_code == 200
    assert "daily</strong>: authentication - oauth_code=[REDACTED]" in page.text
    assert "never-show" not in page.text
    assert "also-never" not in page.text
    assert retry.status_code == 200
    with database_connection(settings) as connection:
        audits = SyncRepository(connection).list_retry_audits(run.id)
    assert len(audits) == 1
    assert audits[0].report_names == ("daily",)
    assert audits[0].retry_sync_run_id is not None
    assert audits[0].outcome == "succeeded"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def _csrf_token(page: str) -> str:
    marker = 'name="csrf_token" value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]
