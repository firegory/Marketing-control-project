"""Local DuckDB connection lifecycle and schema migration support."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import duckdb

from marketing_control.settings import Settings

DATABASE_FILENAME = "marketing-control.duckdb"
_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]+)_[a-z0-9_]+\.sql$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Migration:
    """A versioned SQL migration applied once to a local database."""

    version: str
    sql: str


@dataclass(frozen=True)
class ReportRange:
    """The account, report grain, and inclusive date range being replaced."""

    account_id: str
    report_grain: str
    start_date: date
    end_date: date
    account_column: str = "account_id"
    report_grain_column: str = "report_grain"
    report_date_column: str = "report_date"
    _columns: tuple[str, str, str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.account_id or not self.report_grain:
            raise ValueError("account_id and report_grain must not be empty")
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")

        columns = (
            self.account_column,
            self.report_grain_column,
            self.report_date_column,
        )
        _validate_identifiers(columns)
        if len(set(columns)) != len(columns):
            raise ValueError("range scope columns must be distinct")
        object.__setattr__(self, "_columns", columns)


def replace_report_range(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    report_range: ReportRange,
) -> None:
    """Atomically replace a report range with validated rows from temporary staging.

    Every row must carry the supplied range scope. An empty row set intentionally
    clears the requested range. Staging is temporary and removed on success or
    failure, so it can never become visible as committed reporting data.
    """
    _validate_identifiers((table, *columns))
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("columns must be non-empty and unique")
    if not set(report_range._columns).issubset(columns):
        raise ValueError("columns must include the report range scope")

    expected_columns = set(columns)
    values: list[tuple[object, ...]] = []
    for row in rows:
        if set(row) != expected_columns:
            raise ValueError("each row must contain exactly the supplied columns")
        values.append(tuple(row[column] for column in columns))

    quoted_table = _quote_identifier(table)
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    scope_columns = tuple(_quote_identifier(column) for column in report_range._columns)
    staging_table = f"_staging_{uuid4().hex}"
    quoted_staging_table = _quote_identifier(staging_table)

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            f"CREATE TEMP TABLE {quoted_staging_table} AS "
            f"SELECT {quoted_columns} FROM {quoted_table} WHERE FALSE"
        )
        if values:
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {quoted_staging_table} ({quoted_columns}) "
                f"VALUES ({placeholders})",
                values,
            )

        invalid_rows = connection.execute(
            f"SELECT count(*) FROM {quoted_staging_table} "
            f"WHERE {scope_columns[0]} IS DISTINCT FROM ? "
            f"OR {scope_columns[1]} IS DISTINCT FROM ? "
            f"OR {scope_columns[2]} IS NULL OR {scope_columns[2]} < ? "
            f"OR {scope_columns[2]} > ?",
            [
                report_range.account_id,
                report_range.report_grain,
                report_range.start_date,
                report_range.end_date,
            ],
        ).fetchone()
        if invalid_rows is None or invalid_rows[0] != 0:
            raise ValueError("staged rows must match the requested report range")

        connection.execute(
            f"DELETE FROM {quoted_table} WHERE {scope_columns[0]} = ? "
            f"AND {scope_columns[1]} = ? AND {scope_columns[2]} BETWEEN ? AND ?",
            [
                report_range.account_id,
                report_range.report_grain,
                report_range.start_date,
                report_range.end_date,
            ],
        )
        connection.execute(
            f"INSERT INTO {quoted_table} ({quoted_columns}) "
            f"SELECT {quoted_columns} FROM {quoted_staging_table}"
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute(f"DROP TABLE IF EXISTS {quoted_staging_table}")


def database_path(settings: Settings) -> Path:
    """Return the database path located in the configured application data root."""
    return settings.paths.data / DATABASE_FILENAME


@contextmanager
def database_connection(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a migrated database connection and close it when the scope exits."""
    path = database_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    try:
        run_migrations(connection)
        yield connection
    finally:
        connection.close()


def run_migrations(
    connection: duckdb.DuckDBPyConnection,
    migrations: Sequence[Migration] | None = None,
) -> None:
    """Apply each unapplied migration in version order within one transaction."""
    migrations = load_migrations() if migrations is None else migrations
    _validate_migrations(migrations)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT current_timestamp
        )
        """
    )
    applied_versions = {
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    }

    connection.execute("BEGIN TRANSACTION")
    try:
        for migration in migrations:
            if migration.version not in applied_versions:
                connection.execute(migration.sql)
                connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    [migration.version],
                )
        connection.execute("COMMIT")
    except duckdb.Error:
        connection.execute("ROLLBACK")
        raise


def load_migrations() -> tuple[Migration, ...]:
    """Load bundled SQL migrations in lexical version order."""
    directory = files("marketing_control.migrations")
    migrations = [
        Migration(match.group("version"), resource.read_text(encoding="utf-8"))
        for resource in directory.iterdir()
        if (match := _MIGRATION_NAME.fullmatch(resource.name)) is not None
    ]
    return tuple(sorted(migrations, key=lambda migration: migration.version))


def _validate_migrations(migrations: Sequence[Migration]) -> None:
    versions = [migration.version for migration in migrations]
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise ValueError("migrations must have unique versions in ascending order")


def _validate_identifiers(identifiers: Sequence[str]) -> None:
    if any(_IDENTIFIER.fullmatch(identifier) is None for identifier in identifiers):
        raise ValueError(
            "identifiers must contain only letters, numbers, and underscores"
        )


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier}"'
