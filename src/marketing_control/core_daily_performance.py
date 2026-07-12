"""Import separate typed Google Ads performance facts for each daily grain."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import duckdb

from marketing_control.storage import ReportRange, replace_report_range

CAMPAIGN_DAILY_QUERY = """SELECT
    campaign.resource_name, segments.date, metrics.impressions, metrics.clicks,
    metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM campaign
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
AD_GROUP_DAILY_QUERY = """SELECT
    ad_group.resource_name, segments.date, metrics.impressions, metrics.clicks,
    metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM ad_group
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
AD_DAILY_QUERY = """SELECT
    ad_group_ad.resource_name, segments.date, metrics.impressions, metrics.clicks,
    metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM ad_group_ad
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""

_FACTS = (
    (
        "campaign_daily_performance",
        "campaign_day",
        "campaign_resource_name",
        CAMPAIGN_DAILY_QUERY,
        "campaign",
    ),
    (
        "ad_group_daily_performance",
        "ad_group_day",
        "ad_group_resource_name",
        AD_GROUP_DAILY_QUERY,
        "ad_group",
    ),
    (
        "ad_daily_performance",
        "ad_day",
        "ad_group_ad_resource_name",
        AD_DAILY_QUERY,
        "ad_group_ad",
    ),
)
_METRIC_COLUMNS = (
    "impressions",
    "clicks",
    "cost_micros",
    "conversions",
    "conversions_value",
)


class CoreDailyPerformanceSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one query."""


class CoreDailyPerformanceImportError(ValueError):
    """Google Ads returned incomplete or invalid daily performance data."""


@dataclass(frozen=True)
class CoreDailyPerformanceImportResult:
    """Counts of committed rows for the three independent daily fact tables."""

    campaign_days: int
    ad_group_days: int
    ad_days: int


def import_core_daily_performance(
    connection: duckdb.DuckDBPyConnection,
    source: CoreDailyPerformanceSource,
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> CoreDailyPerformanceImportResult:
    """Replace one account's requested date range in each separate fact table."""
    if not customer_resource_name:
        raise ValueError("customer_resource_name must not be empty")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    counts: list[int] = []
    for table, grain, resource_column, template, entity_name in _FACTS:
        rows = _parse_daily_rows(
            source.search_stream(_query(template, start_date, end_date)),
            customer_resource_name,
            grain,
            resource_column,
            entity_name,
        )
        replace_report_range(
            connection,
            table,
            (
                "customer_resource_name",
                resource_column,
                "report_grain",
                "report_date",
                *_METRIC_COLUMNS,
            ),
            rows,
            ReportRange(
                customer_resource_name,
                grain,
                start_date,
                end_date,
                account_column="customer_resource_name",
            ),
        )
        counts.append(len(rows))

    return CoreDailyPerformanceImportResult(*counts)


def _query(template: str, start_date: date, end_date: date) -> str:
    return template.format(
        start_date=start_date.isoformat(), end_date=end_date.isoformat()
    )


def _parse_daily_rows(
    batches: Iterable[object],
    customer_resource_name: str,
    grain: str,
    resource_column: str,
    entity_name: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in _results(batches):
        entity = getattr(result, entity_name, None)
        metrics = getattr(result, "metrics", None)
        resource_name = _required_text(entity, "resource_name")
        report_date = _required_date(getattr(result, "segments", None))
        rows.append(
            {
                "customer_resource_name": customer_resource_name,
                resource_column: resource_name,
                "report_grain": grain,
                "report_date": report_date,
                "impressions": _required_int(metrics, "impressions"),
                "clicks": _required_int(metrics, "clicks"),
                "cost_micros": _required_int(metrics, "cost_micros"),
                "conversions": _required_decimal(metrics, "conversions"),
                "conversions_value": _required_decimal(metrics, "conversions_value"),
            }
        )
    return rows


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required_text(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if item is None or not str(item):
        raise CoreDailyPerformanceImportError(f"Google Ads returned no valid {name}.")
    return str(item)


def _required_date(segments: object) -> date:
    value = getattr(segments, "date", None)
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise CoreDailyPerformanceImportError(
            "Google Ads returned no valid date."
        ) from None


def _required_int(value: object, name: str) -> int:
    item = getattr(value, name, None)
    try:
        return int(str(item))
    except (TypeError, ValueError):
        raise CoreDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None


def _required_decimal(value: object, name: str) -> Decimal:
    item = getattr(value, name, None)
    try:
        result = Decimal(str(item))
    except (InvalidOperation, TypeError, ValueError):
        raise CoreDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None
    if not result.is_finite():
        raise CoreDailyPerformanceImportError(f"Google Ads returned no valid {name}.")
    return result
