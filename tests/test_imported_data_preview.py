"""Tests for the fixed, local imported-data preview."""

from datetime import date
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from marketing_control.app import create_app
from marketing_control.imported_data_preview import CATALOG, imported_data_preview
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository


def test_catalog_is_a_fixed_allowlist_of_only_imported_tables() -> None:
    assert [entry.table for entry in CATALOG] == [
        "customers",
        "campaign_budgets",
        "campaigns",
        "ad_groups",
        "ad_dimensions",
        "keyword_criteria",
        "ad_group_criteria",
        "campaign_criteria",
        "assets",
        "asset_attachments",
        "geo_target_constants",
        "campaign_daily_performance",
        "ad_group_daily_performance",
        "ad_daily_performance",
        "keyword_daily_performance",
        "search_term_daily_performance",
        "device_daily_performance",
        "audience_daily_performance",
        "location_daily_performance",
        "asset_attachment_daily_performance",
    ]
    assert not {"schema_migrations", "sync_runs", "sync_report_runs"} & {
        entry.table for entry in CATALOG
    }


def test_preview_uses_exact_count_and_newest_then_stable_fact_sample(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.executemany(
            "INSERT INTO campaign_daily_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "customers/1",
                    "campaigns/2",
                    "campaign_day",
                    date(2026, 1, 2),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/1",
                    "campaigns/1",
                    "campaign_day",
                    date(2026, 1, 2),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/1",
                    "campaigns/3",
                    "campaign_day",
                    date(2026, 1, 1),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/1",
                    "campaigns/4",
                    "campaign_day",
                    date(2026, 1, 1),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/1",
                    "campaigns/5",
                    "campaign_day",
                    date(2026, 1, 1),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/1",
                    "campaigns/6",
                    "campaign_day",
                    date(2026, 1, 1),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
            ],
        )
        preview = imported_data_preview(connection, ())

    campaign = next(
        item for item in preview if item.entry.table == "campaign_daily_performance"
    )
    assert campaign.state == "populated"
    assert campaign.row_count == 6
    assert [row[1] for row in campaign.rows] == [
        "campaigns/1",
        "campaigns/2",
        "campaigns/3",
        "campaigns/4",
        "campaigns/5",
    ]


def test_preview_redacts_sensitive_values_bounds_cells_and_templates_escape_them(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            ["customers/1", 1, "token=top-secret", "USD", "UTC"],
        )
        connection.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?)",
            ["assets/1", 1, "customers/1", "<script>" + "x" * 200, "IMAGE"],
        )
        preview = imported_data_preview(connection, ())
    customer = next(item for item in preview if item.entry.table == "customers")
    assert customer.rows[0][2] == "token=[REDACTED]"
    assert len(customer.rows[0][2]) <= 160

    response = TestClient(create_app(settings=settings)).get("/imported-data")

    assert response.status_code == 200
    assert "token=[REDACTED]" in response.text
    assert "top-secret" not in response.text
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_preview_marks_empty_unavailable_and_failed_tables(tmp_path: Path) -> None:
    unavailable = imported_data_preview(duckdb.connect(":memory:"), ())
    assert {item.state for item in unavailable} == {"unavailable"}

    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        repository = SyncRepository(connection)
        run = repository.start_orchestration_run(
            date(2026, 1, 1), date(2026, 1, 1), ["campaign_daily_performance"]
        )
        repository.start_report_run(run.id, "campaign_daily_performance", 1)
        repository.fail_report_run(
            run.id, "campaign_daily_performance", "safe failure", "storage"
        )
        preview = imported_data_preview(
            connection, tuple(repository.list_report_runs(run.id))
        )

    campaign = next(
        item for item in preview if item.entry.table == "campaign_daily_performance"
    )
    customer = next(item for item in preview if item.entry.table == "customers")
    assert campaign.state == "failed"
    assert campaign.row_count == 0
    assert customer.state == "empty"


def test_imported_data_route_has_navigation_and_no_query_parameters(
    tmp_path: Path,
) -> None:
    response = TestClient(create_app(settings=_settings(tmp_path))).get(
        "/imported-data?table=sync_runs&sql=DROP%20TABLE%20customers"
    )

    assert response.status_code == 200
    assert 'href="/"' in response.text
    assert 'href="/integration-status"' in response.text
    assert "schema_migrations" not in response.text
    assert "sync_runs" not in response.text
    assert "campaign_daily_performance" in response.text


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
