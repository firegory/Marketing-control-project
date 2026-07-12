"""Import typed Google Ads asset-attachment performance at a daily grain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import duckdb

from marketing_control.storage import ReportRange, replace_report_range

CAMPAIGN_ASSET_DAILY_QUERY = """SELECT
    campaign_asset.resource_name, campaign_asset.asset, campaign_asset.field_type,
    campaign_asset.campaign, segments.date, metrics.impressions, metrics.clicks,
    metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM campaign_asset
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
AD_GROUP_ASSET_DAILY_QUERY = """SELECT
    ad_group_asset.resource_name, ad_group_asset.asset, ad_group_asset.field_type,
    ad_group_asset.ad_group, segments.date, metrics.impressions, metrics.clicks,
    metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM ad_group_asset
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""

ATTACHMENT_PERFORMANCE_GRAIN = "asset_attachment_day"
SUPPORTED_ATTACHMENT_METRICS = frozenset(
    {
        "impressions",
        "clicks",
        "cost_micros",
        "conversions",
        "conversions_value",
    }
)
# Google Ads serves these asset field types through the attachment resources above.
SUPPORTED_ATTACHMENT_TYPES = {
    "campaign": frozenset(
        {
            "SITELINK",
            "CALLOUT",
            "STRUCTURED_SNIPPET",
            "CALL",
            "IMAGE",
            "LEAD_FORM",
            "PROMOTION",
            "PRICE",
            "APP",
            "BUSINESS_NAME",
            "BUSINESS_LOGO",
        }
    ),
    "ad_group": frozenset(
        {
            "SITELINK",
            "CALLOUT",
            "STRUCTURED_SNIPPET",
            "CALL",
            "IMAGE",
            "LEAD_FORM",
            "PROMOTION",
            "PRICE",
            "APP",
            "BUSINESS_NAME",
            "BUSINESS_LOGO",
        }
    ),
}

_COLUMNS = (
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
)


class AssetAttachmentDailyPerformanceSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one query."""


class AssetAttachmentPerformanceImportError(ValueError):
    """Google Ads returned unsupported or incomplete attachment performance data."""


@dataclass(frozen=True)
class AssetAttachmentPerformanceImportResult:
    """Count of committed asset-attachment daily performance rows."""

    attachment_days: int


def import_asset_attachment_daily_performance(
    connection: duckdb.DuckDBPyConnection,
    source: AssetAttachmentDailyPerformanceSource,
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> AssetAttachmentPerformanceImportResult:
    """Atomically replace one customer's requested attachment-performance range."""
    if not customer_resource_name:
        raise ValueError("customer_resource_name must not be empty")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    rows = _parse_rows(
        source.search_stream(_query(CAMPAIGN_ASSET_DAILY_QUERY, start_date, end_date)),
        customer_resource_name,
        "campaign",
        "campaign_asset",
        "campaign",
    ) + _parse_rows(
        source.search_stream(_query(AD_GROUP_ASSET_DAILY_QUERY, start_date, end_date)),
        customer_resource_name,
        "ad_group",
        "ad_group_asset",
        "ad_group",
    )
    if not rows and _range_row_count(
        connection, customer_resource_name, start_date, end_date
    ):
        raise AssetAttachmentPerformanceImportError(
            "Google Ads returned no asset attachment performance rows; "
            "refusing to clear existing data."
        )
    replace_report_range(
        connection,
        "asset_attachment_daily_performance",
        _COLUMNS,
        rows,
        ReportRange(
            customer_resource_name,
            ATTACHMENT_PERFORMANCE_GRAIN,
            start_date,
            end_date,
            account_column="customer_resource_name",
        ),
    )
    return AssetAttachmentPerformanceImportResult(len(rows))


def _query(template: str, start_date: date, end_date: date) -> str:
    return template.format(
        start_date=start_date.isoformat(), end_date=end_date.isoformat()
    )


def _range_row_count(
    connection: duckdb.DuckDBPyConnection,
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> int:
    row = connection.execute(
        "SELECT count(*) FROM asset_attachment_daily_performance "
        "WHERE customer_resource_name = ? AND report_grain = ? "
        "AND report_date BETWEEN ? AND ?",
        [customer_resource_name, ATTACHMENT_PERFORMANCE_GRAIN, start_date, end_date],
    ).fetchone()
    return 0 if row is None else int(row[0])


def _parse_rows(
    batches: Iterable[object],
    customer_resource_name: str,
    scope: str,
    attachment_name: str,
    parent_name: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in _results(batches):
        attachment = getattr(result, attachment_name, None)
        attachment_type = _required_text(attachment, "field_type")
        _validate_supported_combination(scope, attachment_type)
        metrics = getattr(result, "metrics", None)
        rows.append(
            {
                "customer_resource_name": customer_resource_name,
                "asset_resource_name": _required_text(attachment, "asset"),
                "asset_attachment_resource_name": _required_text(
                    attachment, "resource_name"
                ),
                "attachment_scope": scope,
                "attachment_type": attachment_type,
                "parent_resource_name": _required_text(attachment, parent_name),
                "report_grain": ATTACHMENT_PERFORMANCE_GRAIN,
                "report_date": _required_date(getattr(result, "segments", None)),
                "impressions": _required_int(metrics, "impressions"),
                "clicks": _required_int(metrics, "clicks"),
                "cost_micros": _required_int(metrics, "cost_micros"),
                "conversions": _required_decimal(metrics, "conversions"),
                "conversions_value": _required_decimal(metrics, "conversions_value"),
            }
        )
    return rows


def _validate_supported_combination(scope: str, attachment_type: str) -> None:
    if attachment_type not in SUPPORTED_ATTACHMENT_TYPES.get(scope, frozenset()):
        raise AssetAttachmentPerformanceImportError(
            "Google Ads does not support daily performance for attachment "
            f"scope '{scope}' and type '{attachment_type}'."
        )


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required_text(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if item is None or not str(item):
        raise AssetAttachmentPerformanceImportError(
            f"Google Ads returned no valid attachment {name}."
        )
    return str(item)


def _required_date(segments: object) -> date:
    value = getattr(segments, "date", None)
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise AssetAttachmentPerformanceImportError(
            "Google Ads returned no valid attachment performance date."
        ) from None


def _required_int(value: object, name: str) -> int:
    item = getattr(value, name, None)
    try:
        return int(str(item))
    except (TypeError, ValueError):
        raise AssetAttachmentPerformanceImportError(
            f"Google Ads returned no valid attachment performance {name}."
        ) from None


def _required_decimal(value: object, name: str) -> Decimal:
    item = getattr(value, name, None)
    try:
        result = Decimal(str(item))
    except (InvalidOperation, TypeError, ValueError):
        raise AssetAttachmentPerformanceImportError(
            f"Google Ads returned no valid attachment performance {name}."
        ) from None
    if not result.is_finite():
        raise AssetAttachmentPerformanceImportError(
            f"Google Ads returned no valid attachment performance {name}."
        )
    return result
