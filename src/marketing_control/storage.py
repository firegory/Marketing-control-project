"""Local DuckDB connection lifecycle and schema migration support."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import duckdb

from marketing_control.settings import Settings

DATABASE_FILENAME = "marketing-control.duckdb"
_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]+)_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    """A versioned SQL migration applied once to a local database."""

    version: str
    sql: str


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
