"""Tests for local DuckDB startup and migration behavior."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import (
    DATABASE_FILENAME,
    Migration,
    ReportRange,
    database_connection,
    database_path,
    replace_report_range,
    run_migrations,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_first_run_creates_migrated_database_in_configured_data_directory(
    settings: Settings,
) -> None:
    path = database_path(settings)

    with database_connection(settings) as connection:
        applied_versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert path == settings.paths.data / DATABASE_FILENAME
    assert path.is_file()
    assert applied_versions == [("0001",), ("0002",), ("0003",)]


def test_restart_does_not_reapply_migrations(settings: Settings) -> None:
    with database_connection(settings) as connection:
        first_applied_at = connection.execute(
            "SELECT applied_at FROM schema_migrations WHERE version = '0001'"
        ).fetchone()

    with database_connection(settings) as connection:
        migration_count = connection.execute(
            "SELECT count(*) FROM schema_migrations"
        ).fetchone()
        second_applied_at = connection.execute(
            "SELECT applied_at FROM schema_migrations WHERE version = '0001'"
        ).fetchone()

    assert migration_count == (3,)
    assert second_applied_at == first_applied_at


def test_database_connection_closes_after_its_lifecycle(settings: Settings) -> None:
    with database_connection(settings) as connection:
        connection.execute("SELECT 1")

    with pytest.raises(duckdb.ConnectionException, match="closed"):
        connection.execute("SELECT 1")


def test_upgrade_applies_only_pending_migrations(tmp_path: Path) -> None:
    connection = duckdb.connect(str(tmp_path / DATABASE_FILENAME))
    connection.execute("CREATE TABLE schema_migrations (version VARCHAR PRIMARY KEY)")
    connection.execute("INSERT INTO schema_migrations VALUES ('0001')")

    migrations = (
        Migration("0001", "CREATE TABLE legacy_table (id INTEGER)"),
        Migration("0002", "CREATE TABLE upgraded_table (id INTEGER)"),
    )
    run_migrations(connection, migrations)

    tables = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    applied_versions = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    connection.close()

    assert ("legacy_table",) not in tables
    assert ("upgraded_table",) in tables
    assert applied_versions == [("0001",), ("0002",)]


def test_replace_report_range_replaces_only_requested_account_grain_and_dates() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE reports ("
        "account_id VARCHAR, report_grain VARCHAR, report_date DATE, value INTEGER)"
    )
    connection.executemany(
        "INSERT INTO reports VALUES (?, ?, ?, ?)",
        [
            ("account-a", "daily", date(2026, 1, 1), 10),
            ("account-a", "daily", date(2026, 1, 2), 20),
            ("account-a", "daily", date(2026, 1, 3), 30),
            ("account-a", "weekly", date(2026, 1, 2), 40),
            ("account-b", "daily", date(2026, 1, 2), 50),
        ],
    )

    replace_report_range(
        connection,
        "reports",
        ("account_id", "report_grain", "report_date", "value"),
        [
            {
                "account_id": "account-a",
                "report_grain": "daily",
                "report_date": date(2026, 1, 2),
                "value": 200,
            }
        ],
        ReportRange("account-a", "daily", date(2026, 1, 2), date(2026, 1, 2)),
    )

    assert connection.execute(
        "SELECT * FROM reports ORDER BY account_id, report_grain, report_date"
    ).fetchall() == [
        ("account-a", "daily", date(2026, 1, 1), 10),
        ("account-a", "daily", date(2026, 1, 2), 200),
        ("account-a", "daily", date(2026, 1, 3), 30),
        ("account-a", "weekly", date(2026, 1, 2), 40),
        ("account-b", "daily", date(2026, 1, 2), 50),
    ]


def test_replace_report_range_rejects_out_of_scope_rows_and_cleans_staging() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE reports ("
        "account_id VARCHAR, report_grain VARCHAR, report_date DATE, value INTEGER)"
    )
    connection.execute(
        "INSERT INTO reports VALUES ('account-a', 'daily', DATE '2026-01-02', 20)"
    )

    with pytest.raises(ValueError, match="requested report range"):
        replace_report_range(
            connection,
            "reports",
            ("account_id", "report_grain", "report_date", "value"),
            [
                {
                    "account_id": None,
                    "report_grain": "daily",
                    "report_date": date(2026, 1, 2),
                    "value": 30,
                }
            ],
            ReportRange("account-a", "daily", date(2026, 1, 2), date(2026, 1, 2)),
        )

    assert connection.execute("SELECT * FROM reports").fetchall() == [
        ("account-a", "daily", date(2026, 1, 2), 20)
    ]
    assert connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE '_staging_%'"
    ).fetchall() == []


def test_replace_report_range_rolls_back_delete_when_committed_insert_fails() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE reports (account_id VARCHAR, report_grain VARCHAR, "
        "report_date DATE, value INTEGER NOT NULL)"
    )
    connection.execute(
        "INSERT INTO reports VALUES ('account-a', 'daily', DATE '2026-01-02', 20)"
    )

    with pytest.raises(duckdb.ConstraintException):
        replace_report_range(
            connection,
            "reports",
            ("account_id", "report_grain", "report_date", "value"),
            [
                {
                    "account_id": "account-a",
                    "report_grain": "daily",
                    "report_date": date(2026, 1, 2),
                    "value": None,
                }
            ],
            ReportRange("account-a", "daily", date(2026, 1, 2), date(2026, 1, 2)),
        )

    assert connection.execute("SELECT * FROM reports").fetchall() == [
        ("account-a", "daily", date(2026, 1, 2), 20)
    ]
    assert connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE '_staging_%'"
    ).fetchall() == []


def test_replace_report_range_is_idempotent_for_identical_retries() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE reports ("
        "account_id VARCHAR, report_grain VARCHAR, report_date DATE, value INTEGER)"
    )
    report_range = ReportRange("account-a", "daily", date(2026, 1, 2), date(2026, 1, 2))
    rows = [
        {
            "account_id": "account-a",
            "report_grain": "daily",
            "report_date": date(2026, 1, 2),
            "value": 200,
        }
    ]

    replace_report_range(
        connection,
        "reports",
        ("account_id", "report_grain", "report_date", "value"),
        rows,
        report_range,
    )
    first_result = connection.execute("SELECT * FROM reports").fetchall()
    replace_report_range(
        connection,
        "reports",
        ("account_id", "report_grain", "report_date", "value"),
        rows,
        report_range,
    )

    assert connection.execute("SELECT * FROM reports").fetchall() == first_result
