"""Tests for fixed internal queries and safe local offline operations."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

from marketing_control import main
from marketing_control.data_access import (
    CatalogQuery,
    CatalogQueryService,
    create_backup,
    export_catalog_table,
    restore_backup,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection, database_path


def test_query_uses_fixed_projection_and_parameterized_predicates(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.executemany(
            "INSERT INTO campaign_daily_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "customers/1",
                    "campaigns/1",
                    "campaign_day",
                    date(2026, 1, 1),
                    1,
                    2,
                    3,
                    4,
                    5,
                ),
                (
                    "customers/2",
                    "campaigns/2",
                    "campaign_day",
                    date(2026, 1, 2),
                    6,
                    7,
                    8,
                    9,
                    10,
                ),
            ],
        )
        rows = CatalogQueryService(connection).fetch(
            CatalogQuery(
                "campaign_daily_performance",
                customer_resource_name="customers/1' OR TRUE --",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            )
        )

    assert rows == ()
    with pytest.raises(ValueError, match="supported catalog"):
        CatalogQuery("schema_migrations")
    with pytest.raises(ValueError, match="only for daily"):
        CatalogQueryService(duckdb.connect(":memory:")).fetch(
            CatalogQuery("customers", start_date=date(2026, 1, 1))
        )


@pytest.mark.parametrize("format", ["csv", "parquet"])
def test_export_writes_supported_table_to_unique_atomic_file(
    tmp_path: Path, format: str
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            ["customers/1", 1, "Ada", "USD", "UTC"],
        )

    output = export_catalog_table(settings, CatalogQuery("customers"), format)  # type: ignore[arg-type]

    assert output.parent == settings.paths.exports
    assert output.suffix == f".{format}"
    assert output.name.startswith("customers-")
    assert not list(output.parent.glob(".*.tmp"))
    assert duckdb.connect(":memory:").execute(
        "SELECT * FROM read_csv_auto(?)"
        if format == "csv"
        else "SELECT * FROM read_parquet(?)",
        [str(output)],
    ).fetchone() == ("customers/1", 1, "Ada", "USD", "UTC")


def test_export_removes_temporary_file_when_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        service = CatalogQueryService(connection)
        monkeypatch.setattr(
            "marketing_control.data_access.os.replace", _raise_permission_error
        )

        with pytest.raises(PermissionError):
            service.export(CatalogQuery("customers"), settings.paths.exports, "csv")

    assert not list(settings.paths.exports.glob("*"))


def test_backup_restores_into_new_database_without_touching_source(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            ["customers/1", 1, "Ada", "USD", "UTC"],
        )

    source = database_path(settings)
    backup = create_backup(settings)
    restored = tmp_path / "verified" / "restored.duckdb"
    restore_backup(backup, restored)

    assert backup.is_dir()
    assert (backup / "schema.sql").is_file()
    assert source != restored
    assert duckdb.connect(str(restored)).execute(
        "SELECT * FROM customers"
    ).fetchone() == (
        "customers/1",
        1,
        "Ada",
        "USD",
        "UTC",
    )


def test_restore_rejects_existing_database_without_destructive_restore(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    backup = create_backup(settings)
    destination = tmp_path / "existing.duckdb"
    duckdb.connect(str(destination)).execute(
        "CREATE TABLE sentinel (value INTEGER)"
    ).close()

    with pytest.raises(ValueError, match="must not already exist"):
        restore_backup(backup, destination)

    assert (
        duckdb.connect(str(destination)).execute("SELECT * FROM sentinel").fetchall()
        == []
    )


def test_cli_exports_only_allowlisted_catalog_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?)",
            ["customers/1", 1, "Ada", "USD", "UTC"],
        )
    monkeypatch.setattr(Settings, "load", lambda: settings)
    monkeypatch.setattr(
        sys, "argv", ["marketing-control", "export", "customers", "csv"]
    )

    main()

    output = Path(capsys.readouterr().out.strip())
    assert output.parent == settings.paths.exports
    assert output.suffix == ".csv"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def _raise_permission_error(_: Path, __: Path) -> None:
    raise PermissionError("simulated publication failure")
