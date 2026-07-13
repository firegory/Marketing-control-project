"""Safe, bounded read models for locally imported Google Ads data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import duckdb

from marketing_control.logging import redact_sensitive_values
from marketing_control.sync_history import SyncReportRun

PreviewState = Literal["populated", "empty", "unavailable", "failed"]
_SAMPLE_LIMIT = 5
_CELL_LIMIT = 160
_SENSITIVE_COLUMN_PARTS = (
    "secret",
    "password",
    "token",
    "credential",
    "authorization",
    "api_key",
)


@dataclass(frozen=True)
class CatalogEntry:
    """One fixed, displayable imported-data table and its safe query shape."""

    table: str
    kind: Literal["dimension", "fact"]
    description: str
    grain: str | None
    columns: tuple[str, ...]
    stable_ids: tuple[str, ...]
    report_name: str


@dataclass(frozen=True)
class ImportedDataPreview:
    """Rendered-safe, bounded result for one catalog entry."""

    entry: CatalogEntry
    state: PreviewState
    row_count: int | None
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


# This is deliberately the complete product allowlist, not a database discovery list.
CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        "customers",
        "dimension",
        "Configured Google Ads customer snapshots.",
        None,
        (
            "customer_resource_name",
            "customer_id",
            "descriptive_name",
            "currency_code",
            "time_zone",
        ),
        ("customer_id",),
        "customers",
    ),
    CatalogEntry(
        "campaign_budgets",
        "dimension",
        "Campaign budget snapshots.",
        None,
        (
            "campaign_budget_resource_name",
            "campaign_budget_id",
            "customer_resource_name",
            "name",
            "amount_micros",
            "explicitly_shared",
        ),
        ("campaign_budget_id",),
        "campaign_budgets",
    ),
    CatalogEntry(
        "campaigns",
        "dimension",
        "Campaign snapshots.",
        None,
        (
            "campaign_resource_name",
            "campaign_id",
            "customer_resource_name",
            "campaign_budget_resource_name",
            "name",
            "status",
        ),
        ("campaign_id",),
        "campaigns",
    ),
    CatalogEntry(
        "ad_groups",
        "dimension",
        "Ad group snapshots.",
        None,
        (
            "ad_group_resource_name",
            "ad_group_id",
            "customer_resource_name",
            "campaign_resource_name",
            "name",
            "status",
        ),
        ("ad_group_id",),
        "ad_groups",
    ),
    CatalogEntry(
        "ad_dimensions",
        "dimension",
        "Ad snapshots.",
        None,
        (
            "ad_group_ad_resource_name",
            "ad_id",
            "customer_resource_name",
            "ad_group_resource_name",
            "status",
            "ad_type",
            "name",
        ),
        ("ad_id",),
        "ad_dimensions",
    ),
    CatalogEntry(
        "keyword_criteria",
        "dimension",
        "Keyword criterion snapshots.",
        None,
        (
            "ad_group_criterion_resource_name",
            "criterion_id",
            "customer_resource_name",
            "ad_group_resource_name",
            "source_status",
            "keyword_text",
            "match_type",
        ),
        ("criterion_id",),
        "keyword_criteria",
    ),
    CatalogEntry(
        "ad_group_criteria",
        "dimension",
        "Non-keyword ad group criterion snapshots.",
        None,
        (
            "ad_group_criterion_resource_name",
            "criterion_id",
            "customer_resource_name",
            "ad_group_resource_name",
            "source_type",
            "source_status",
        ),
        ("criterion_id",),
        "ad_group_criteria",
    ),
    CatalogEntry(
        "campaign_criteria",
        "dimension",
        "Campaign criterion snapshots.",
        None,
        (
            "campaign_criterion_resource_name",
            "criterion_id",
            "customer_resource_name",
            "campaign_resource_name",
            "source_type",
            "source_status",
            "geo_target_constant_resource_name",
        ),
        ("criterion_id",),
        "campaign_criteria",
    ),
    CatalogEntry(
        "assets",
        "dimension",
        "Asset snapshots.",
        None,
        (
            "asset_resource_name",
            "asset_id",
            "customer_resource_name",
            "name",
            "source_type",
        ),
        ("asset_id",),
        "assets",
    ),
    CatalogEntry(
        "asset_attachments",
        "dimension",
        "Asset attachment snapshots.",
        None,
        (
            "asset_attachment_resource_name",
            "customer_resource_name",
            "attachment_scope",
            "attached_to_resource_name",
            "asset_resource_name",
            "field_type",
            "source_status",
        ),
        ("asset_attachment_resource_name",),
        "asset_attachments",
    ),
    CatalogEntry(
        "geo_target_constants",
        "dimension",
        "Geographic target constant snapshots.",
        None,
        (
            "customer_resource_name",
            "geo_target_constant_resource_name",
            "criterion_id",
            "name",
            "canonical_name",
            "country_code",
            "target_type",
            "source_status",
        ),
        ("criterion_id",),
        "geo_target_constants",
    ),
    CatalogEntry(
        "campaign_daily_performance",
        "fact",
        "Campaign daily imported rows; metrics are shown only as stored sample values.",
        "campaign_day",
        (
            "customer_resource_name",
            "campaign_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("campaign_resource_name",),
        "campaign_daily_performance",
    ),
    CatalogEntry(
        "ad_group_daily_performance",
        "fact",
        "Ad group daily imported rows; metrics are shown only as stored sample values.",
        "ad_group_day",
        (
            "customer_resource_name",
            "ad_group_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("ad_group_resource_name",),
        "ad_group_daily_performance",
    ),
    CatalogEntry(
        "ad_daily_performance",
        "fact",
        "Ad daily imported rows; metrics are shown only as stored sample values.",
        "ad_day",
        (
            "customer_resource_name",
            "ad_group_ad_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("ad_group_ad_resource_name",),
        "ad_daily_performance",
    ),
    CatalogEntry(
        "keyword_daily_performance",
        "fact",
        "Keyword daily imported rows; metrics are shown only as stored sample values.",
        "keyword_day",
        (
            "customer_resource_name",
            "ad_group_criterion_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("ad_group_criterion_resource_name",),
        "keyword_daily_performance",
    ),
    CatalogEntry(
        "search_term_daily_performance",
        "fact",
        "Search-term daily imported rows; metrics are shown only as stored "
        "sample values.",
        "search_term_day",
        (
            "customer_resource_name",
            "search_term_view_resource_name",
            "campaign_resource_name",
            "ad_group_resource_name",
            "report_grain",
            "report_date",
            "search_term",
            "search_term_availability",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("search_term_view_resource_name",),
        "search_term_daily_performance",
    ),
    CatalogEntry(
        "device_daily_performance",
        "fact",
        "Campaign device daily imported rows; metrics are shown only as stored "
        "sample values.",
        "device_day",
        (
            "customer_resource_name",
            "campaign_resource_name",
            "report_grain",
            "report_date",
            "device",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("campaign_resource_name", "device"),
        "device_daily_performance",
    ),
    CatalogEntry(
        "audience_daily_performance",
        "fact",
        "Audience daily imported rows; metrics are shown only as stored sample values.",
        "audience_day",
        (
            "customer_resource_name",
            "ad_group_resource_name",
            "ad_group_criterion_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        ("ad_group_criterion_resource_name",),
        "audience_daily_performance",
    ),
    CatalogEntry(
        "location_daily_performance",
        "fact",
        "Location daily imported rows; metrics are shown only as stored sample values.",
        "location_targeting_day, user_presence_day, or user_interest_day",
        (
            "customer_resource_name",
            "campaign_resource_name",
            "geo_target_constant_resource_name",
            "report_grain",
            "location_semantics",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        (
            "campaign_resource_name",
            "geo_target_constant_resource_name",
            "location_semantics",
        ),
        "location_daily_performance",
    ),
    CatalogEntry(
        "asset_attachment_daily_performance",
        "fact",
        "Asset attachment daily imported rows; metrics are shown only as stored "
        "sample values.",
        "asset_attachment_day",
        (
            "customer_resource_name",
            "asset_resource_name",
            "asset_attachment_resource_name",
            "attachment_scope",
            "attachment_type",
            "parent_resource_name",
            "report_grain",
            "report_date",
            "impressions",
            "clicks",
            "cost_micros",
            "conversions",
            "conversions_value",
        ),
        (
            "asset_attachment_resource_name",
            "attachment_scope",
            "attachment_type",
            "parent_resource_name",
        ),
        "asset_attachment_daily_performance",
    ),
)


def imported_data_preview(
    connection: duckdb.DuckDBPyConnection, latest_work: tuple[SyncReportRun, ...]
) -> tuple[ImportedDataPreview, ...]:
    """Read every allowlisted table with bounded deterministic display-safe samples."""
    failed_reports = {
        work.report_name for work in latest_work if work.status == "failed"
    }
    return tuple(
        _preview_entry(connection, entry, entry.report_name in failed_reports)
        for entry in CATALOG
    )


def _preview_entry(
    connection: duckdb.DuckDBPyConnection, entry: CatalogEntry, failed: bool
) -> ImportedDataPreview:
    if not _table_exists(connection, entry.table):
        return ImportedDataPreview(entry, "unavailable", None, entry.columns, ())
    try:
        count = connection.execute(
            f"SELECT count(*) FROM {_quote(entry.table)}"
        ).fetchone()
        row_count = 0 if count is None else int(count[0])
        if row_count == 0:
            return ImportedDataPreview(
                entry, "failed" if failed else "empty", 0, entry.columns, ()
            )
        columns = ", ".join(_quote(column) for column in entry.columns)
        ordering = _sample_order(entry)
        rows = connection.execute(
            f"SELECT {columns} FROM {_quote(entry.table)} ORDER BY {ordering} LIMIT ?",
            [_SAMPLE_LIMIT],
        ).fetchall()
        return ImportedDataPreview(
            entry,
            "failed" if failed else "populated",
            row_count,
            entry.columns,
            tuple(_safe_row(entry.columns, row) for row in rows),
        )
    except duckdb.Error:
        # A corrupt or incompatible local table should not make the status page fail.
        return ImportedDataPreview(entry, "unavailable", None, entry.columns, ())


def _table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ? LIMIT 1",
            [table],
        ).fetchone()
        is not None
    )


def _sample_order(entry: CatalogEntry) -> str:
    stable = ", ".join(f"{_quote(column)} ASC" for column in entry.stable_ids)
    if entry.kind == "fact":
        return f"{_quote('report_date')} DESC, {stable}"
    return stable


def _safe_row(columns: tuple[str, ...], row: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(
        _safe_cell(column, value) for column, value in zip(columns, row, strict=True)
    )


def _safe_cell(column: str, value: object) -> str:
    if any(part in column.casefold() for part in _SENSITIVE_COLUMN_PARTS):
        return "[REDACTED]"
    text = redact_sensitive_values("" if value is None else str(value))
    return text[:_CELL_LIMIT]


def _quote(identifier: str) -> str:
    return f'"{identifier}"'
