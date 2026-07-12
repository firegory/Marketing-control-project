"""Import device, audience, and location Google Ads performance at separate grains."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import duckdb

from marketing_control.storage import ReportRange, replace_report_range

DEVICE_DAILY_QUERY = """SELECT
    campaign.resource_name, segments.date, segments.device, metrics.impressions,
    metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value
FROM campaign
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
AUDIENCE_DAILY_QUERY = """SELECT
    ad_group.resource_name, ad_group_criterion.resource_name, segments.date,
    metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
    metrics.conversions_value
FROM ad_group_audience_view
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
LOCATION_TARGETING_DAILY_QUERY = """SELECT
    campaign.resource_name, segments.geo_target_most_specific_location, segments.date,
    metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
    metrics.conversions_value
FROM location_view
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
USER_PRESENCE_DAILY_QUERY = """SELECT
    campaign.resource_name, segments.geo_target_most_specific_location, segments.date,
    metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
    metrics.conversions_value
FROM geographic_view
WHERE geographic_view.location_type = LOCATION_OF_PRESENCE
  AND segments.date BETWEEN '{start_date}' AND '{end_date}'"""
USER_INTEREST_DAILY_QUERY = """SELECT
    campaign.resource_name, segments.geo_target_most_specific_location, segments.date,
    metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
    metrics.conversions_value
FROM geographic_view
WHERE geographic_view.location_type = AREA_OF_INTEREST
  AND segments.date BETWEEN '{start_date}' AND '{end_date}'"""

_METRIC_COLUMNS = (
    "impressions",
    "clicks",
    "cost_micros",
    "conversions",
    "conversions_value",
)
_FACT_COLUMNS = {
    "device_daily_performance": (
        "customer_resource_name",
        "campaign_resource_name",
        "report_grain",
        "report_date",
        "device",
        *_METRIC_COLUMNS,
    ),
    "audience_daily_performance": (
        "customer_resource_name",
        "ad_group_resource_name",
        "ad_group_criterion_resource_name",
        "report_grain",
        "report_date",
        *_METRIC_COLUMNS,
    ),
    "location_daily_performance": (
        "customer_resource_name",
        "campaign_resource_name",
        "geo_target_constant_resource_name",
        "report_grain",
        "location_semantics",
        "report_date",
        *_METRIC_COLUMNS,
    ),
}


class SegmentedDailyPerformanceSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one focused query."""


class SegmentedDailyPerformanceImportError(ValueError):
    """Google Ads returned incomplete or suspicious segmented performance data."""


@dataclass(frozen=True)
class SegmentedDailyPerformanceImportResult:
    """Counts of rows committed to the independent segmented fact tables."""

    device_days: int
    audience_days: int
    location_targeting_days: int
    user_presence_days: int
    user_interest_days: int


def import_segmented_daily_performance(
    connection: duckdb.DuckDBPyConnection,
    source: SegmentedDailyPerformanceSource,
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> SegmentedDailyPerformanceImportResult:
    """Replace exactly one account, grain, and inclusive date range per fact grain."""
    if not customer_resource_name:
        raise ValueError("customer_resource_name must not be empty")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    imports = (
        (
            "device_daily_performance",
            "device_day",
            DEVICE_DAILY_QUERY,
            _parse_device_rows,
        ),
        (
            "audience_daily_performance",
            "audience_day",
            AUDIENCE_DAILY_QUERY,
            _parse_audience_rows,
        ),
        (
            "location_daily_performance",
            "location_targeting_day",
            LOCATION_TARGETING_DAILY_QUERY,
            _parse_targeting_location_rows,
        ),
        (
            "location_daily_performance",
            "user_presence_day",
            USER_PRESENCE_DAILY_QUERY,
            _parse_presence_location_rows,
        ),
        (
            "location_daily_performance",
            "user_interest_day",
            USER_INTEREST_DAILY_QUERY,
            _parse_interest_location_rows,
        ),
    )
    parsed_imports = [
        (
            table,
            grain,
            parser(
                source.search_stream(_query(query, start_date, end_date)),
                customer_resource_name,
                grain,
            ),
        )
        for table, grain, query, parser in imports
    ]
    for table, grain, rows in parsed_imports:
        if not rows and _range_row_count(
            connection, table, customer_resource_name, grain, start_date, end_date
        ):
            raise SegmentedDailyPerformanceImportError(
                f"Google Ads returned no {grain} rows; refusing to clear existing data."
            )

    counts: list[int] = []
    for table, grain, rows in parsed_imports:
        replace_report_range(
            connection,
            table,
            _FACT_COLUMNS[table],
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
    return SegmentedDailyPerformanceImportResult(*counts)


def _query(template: str, start_date: date, end_date: date) -> str:
    return template.format(
        start_date=start_date.isoformat(), end_date=end_date.isoformat()
    )


def _range_row_count(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    customer: str,
    grain: str,
    start_date: date,
    end_date: date,
) -> int:
    row = connection.execute(
        f"SELECT count(*) FROM {table} WHERE customer_resource_name = ? "
        "AND report_grain = ? AND report_date BETWEEN ? AND ?",
        [customer, grain, start_date, end_date],
    ).fetchone()
    return 0 if row is None else int(row[0])


def _parse_device_rows(
    batches: Iterable[object], customer: str, grain: str
) -> list[dict[str, object]]:
    return [
        _metrics(row, customer, grain)
        | {
            "campaign_resource_name": _required_text(row.campaign, "resource_name"),
            "device": _required_text(row.segments, "device"),
        }
        for row in _results(batches)
    ]


def _parse_audience_rows(
    batches: Iterable[object], customer: str, grain: str
) -> list[dict[str, object]]:
    return [
        _metrics(row, customer, grain)
        | {
            "ad_group_resource_name": _required_text(row.ad_group, "resource_name"),
            "ad_group_criterion_resource_name": _required_text(
                row.ad_group_criterion, "resource_name"
            ),
        }
        for row in _results(batches)
    ]


def _parse_targeting_location_rows(
    batches: Iterable[object], customer: str, grain: str
) -> list[dict[str, object]]:
    return _parse_location_rows(batches, customer, grain, "targeting")


def _parse_presence_location_rows(
    batches: Iterable[object], customer: str, grain: str
) -> list[dict[str, object]]:
    return _parse_location_rows(batches, customer, grain, "user_presence")


def _parse_interest_location_rows(
    batches: Iterable[object], customer: str, grain: str
) -> list[dict[str, object]]:
    return _parse_location_rows(batches, customer, grain, "user_interest")


def _parse_location_rows(
    batches: Iterable[object],
    customer: str,
    grain: str,
    semantics: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in _results(batches):
        location_name = _required_text(
            getattr(row, "segments", None), "geo_target_most_specific_location"
        )
        rows.append(
            _metrics(row, customer, grain)
            | {
                "campaign_resource_name": _required_text(row.campaign, "resource_name"),
                "geo_target_constant_resource_name": location_name,
                "location_semantics": semantics,
            }
        )
    return rows


def _metrics(row: object, customer: str, grain: str) -> dict[str, object]:
    metrics = getattr(row, "metrics", None)
    return {
        "customer_resource_name": customer,
        "report_grain": grain,
        "report_date": _required_date(getattr(row, "segments", None)),
        "impressions": _required_int(metrics, "impressions"),
        "clicks": _required_int(metrics, "clicks"),
        "cost_micros": _required_int(metrics, "cost_micros"),
        "conversions": _required_decimal(metrics, "conversions"),
        "conversions_value": _required_decimal(metrics, "conversions_value"),
    }


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required_text(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if item is None or not str(item):
        raise SegmentedDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        )
    return str(item)


def _required_date(segments: object) -> date:
    try:
        return date.fromisoformat(str(getattr(segments, "date", None)))
    except (TypeError, ValueError):
        raise SegmentedDailyPerformanceImportError(
            "Google Ads returned no valid date."
        ) from None


def _required_int(value: object, name: str) -> int:
    try:
        return int(str(getattr(value, name, None)))
    except (TypeError, ValueError):
        raise SegmentedDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None


def _required_decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(getattr(value, name, None)))
    except (InvalidOperation, TypeError, ValueError):
        raise SegmentedDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None
    if not result.is_finite():
        raise SegmentedDailyPerformanceImportError(
            f"Google Ads returned no valid {name}."
        )
    return result
