"""Tests for local DuckDB startup and migration behavior."""

from pathlib import Path

import duckdb
import pytest

from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import (
    DATABASE_FILENAME,
    Migration,
    database_connection,
    database_path,
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
    assert applied_versions == [("0001",)]


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

    assert migration_count == (1,)
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
