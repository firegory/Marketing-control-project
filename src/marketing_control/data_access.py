"""Internal, allowlisted read and offline data operations for DuckDB."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import duckdb

from marketing_control.imported_data_preview import CATALOG, CatalogEntry
from marketing_control.settings import Settings

ExportFormat = Literal["csv", "parquet"]
_EXPORT_FORMATS: tuple[ExportFormat, ...] = ("csv", "parquet")
_CATALOG_BY_TABLE = {entry.table: entry for entry in CATALOG}


@dataclass(frozen=True)
class CatalogQuery:
    """A fixed catalog query with optional, parameterized common predicates."""

    table: str
    customer_resource_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if self.table not in _CATALOG_BY_TABLE:
            raise ValueError("table is not in the supported catalog")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")


class CatalogQueryService:
    """Read only fixed catalog projections for in-process application modules."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection

    def fetch(self, query: CatalogQuery) -> tuple[tuple[object, ...], ...]:
        """Return the fixed projection after applying supported bound parameters."""
        statement, parameters = self._select(query)
        return tuple(self._connection.execute(statement, parameters).fetchall())

    def export(
        self, query: CatalogQuery, destination: Path, format: ExportFormat
    ) -> Path:
        """Write a catalog projection atomically to an application-controlled path."""
        if format not in _EXPORT_FORMATS:
            raise ValueError("format must be csv or parquet")
        destination.mkdir(parents=True, exist_ok=True)
        final_path = destination / _filename(query.table, format)
        temporary_path = destination / f".{final_path.name}.{uuid4().hex}.tmp"
        statement, parameters = self._select(query)
        options = "FORMAT csv, HEADER true" if format == "csv" else "FORMAT parquet"
        try:
            self._connection.execute(
                f"COPY ({statement}) TO ? ({options})",
                [*parameters, str(temporary_path)],
            )
            os.replace(temporary_path, final_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return final_path

    def _select(self, query: CatalogQuery) -> tuple[str, list[object]]:
        entry = _CATALOG_BY_TABLE[query.table]
        conditions: list[str] = []
        parameters: list[object] = []
        if query.customer_resource_name is not None:
            conditions.append('"customer_resource_name" = ?')
            parameters.append(query.customer_resource_name)
        if query.start_date is not None:
            _require_fact(entry, "start_date")
            conditions.append('"report_date" >= ?')
            parameters.append(query.start_date)
        if query.end_date is not None:
            _require_fact(entry, "end_date")
            conditions.append('"report_date" <= ?')
            parameters.append(query.end_date)
        projection = ", ".join(_quote(column) for column in entry.columns)
        statement = f"SELECT {projection} FROM {_quote(entry.table)}"
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        return statement, parameters


def export_catalog_table(
    settings: Settings, query: CatalogQuery, format: ExportFormat
) -> Path:
    """Export a supported catalog table to the configured local exports directory."""
    from marketing_control.storage import database_connection

    with database_connection(settings) as connection:
        return CatalogQueryService(connection).export(
            query, settings.paths.exports, format
        )


def create_backup(settings: Settings) -> Path:
    """Create an atomically published, transaction-consistent DuckDB export package."""
    from marketing_control.storage import database_connection

    settings.paths.backups.mkdir(parents=True, exist_ok=True)
    final_path = settings.paths.backups / _backup_name()
    temporary_path = settings.paths.backups / f".{final_path.name}.{uuid4().hex}.tmp"
    try:
        with database_connection(settings) as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    f"EXPORT DATABASE {_sql_string(temporary_path)} "
                    "(FORMAT parquet, COMPRESSION zstd)"
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        os.replace(temporary_path, final_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise
    return final_path


def restore_backup(backup: Path, destination: Path) -> None:
    """Restore a backup package into a new, empty database for verification tools."""
    if not backup.is_dir():
        raise ValueError("backup must be an export package directory")
    if destination.exists():
        raise ValueError("restore destination must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(destination))
    try:
        connection.execute(f"IMPORT DATABASE {_sql_string(backup)}")
    except Exception:
        connection.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        connection.close()


def supported_tables() -> tuple[str, ...]:
    """Return catalog table names for trusted local command-line choices."""
    return tuple(entry.table for entry in CATALOG)


def _require_fact(entry: CatalogEntry, predicate: str) -> None:
    if entry.kind != "fact":
        raise ValueError(f"{predicate} is supported only for daily performance tables")


def _filename(table: str, format: ExportFormat) -> str:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return f"{table}-{timestamp}-{uuid4().hex[:12]}.{format}"


def _backup_name() -> str:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return f"marketing-control-backup-{timestamp}-{uuid4().hex[:12]}"


def _quote(identifier: str) -> str:
    return f'"{identifier}"'


def _sql_string(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"
