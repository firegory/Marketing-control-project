"""Import independent Google Ads keyword-day and search-term-day facts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import duckdb

from marketing_control.storage import ReportRange, replace_report_range

KEYWORD_DAILY_QUERY = """SELECT
    ad_group_criterion.resource_name, segments.date, metrics.impressions,
    metrics.clicks, metrics.cost_micros, metrics.conversions,
    metrics.conversions_value
FROM keyword_view
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""
SEARCH_TERM_DAILY_QUERY = """SELECT
    search_term_view.resource_name, search_term_view.search_term,
    search_term_view.status, campaign.resource_name, ad_group.resource_name,
    segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros,
    metrics.conversions, metrics.conversions_value
FROM search_term_view
WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'"""

_METRIC_COLUMNS = (
    "impressions",
    "clicks",
    "cost_micros",
    "conversions",
    "conversions_value",
)


class KeywordSearchTermPerformanceSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one query."""


class KeywordSearchTermPerformanceImportError(ValueError):
    """Google Ads returned incomplete or invalid keyword/search-term data."""


@dataclass(frozen=True)
class KeywordSearchTermPerformanceImportResult:
    """Counts of committed rows in the independent keyword and search-term facts."""

    keyword_days: int
    search_term_days: int


def import_keyword_search_term_performance(
    connection: duckdb.DuckDBPyConnection,
    source: KeywordSearchTermPerformanceSource,
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> KeywordSearchTermPerformanceImportResult:
    """Atomically replace each requested account/grain/date range from SearchStream."""
    if not customer_resource_name:
        raise ValueError("customer_resource_name must not be empty")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    keyword_rows = _parse_keyword_rows(
        source.search_stream(_query(KEYWORD_DAILY_QUERY, start_date, end_date)),
        customer_resource_name,
    )
    search_term_rows = _parse_search_term_rows(
        source.search_stream(_query(SEARCH_TERM_DAILY_QUERY, start_date, end_date)),
        customer_resource_name,
    )
    _reject_suspicious_empty_response(
        connection,
        "keyword_daily_performance",
        "keyword_day",
        keyword_rows,
        customer_resource_name,
        start_date,
        end_date,
    )
    _reject_suspicious_empty_response(
        connection,
        "search_term_daily_performance",
        "search_term_day",
        search_term_rows,
        customer_resource_name,
        start_date,
        end_date,
    )

    _replace(
        connection,
        "keyword_daily_performance",
        (
            "customer_resource_name",
            "ad_group_criterion_resource_name",
            "report_grain",
            "report_date",
            *_METRIC_COLUMNS,
        ),
        keyword_rows,
        customer_resource_name,
        "keyword_day",
        start_date,
        end_date,
    )
    _replace(
        connection,
        "search_term_daily_performance",
        (
            "customer_resource_name",
            "search_term_view_resource_name",
            "campaign_resource_name",
            "ad_group_resource_name",
            "report_grain",
            "report_date",
            "search_term",
            "search_term_availability",
            *_METRIC_COLUMNS,
        ),
        search_term_rows,
        customer_resource_name,
        "search_term_day",
        start_date,
        end_date,
    )
    return KeywordSearchTermPerformanceImportResult(
        keyword_days=len(keyword_rows), search_term_days=len(search_term_rows)
    )


def _query(template: str, start_date: date, end_date: date) -> str:
    return template.format(
        start_date=start_date.isoformat(), end_date=end_date.isoformat()
    )


def _reject_suspicious_empty_response(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    grain: str,
    rows: list[dict[str, object]],
    customer_resource_name: str,
    start_date: date,
    end_date: date,
) -> None:
    if rows or not _range_row_count(
        connection, table, customer_resource_name, grain, start_date, end_date
    ):
        return
    raise KeywordSearchTermPerformanceImportError(
        f"Google Ads returned no {grain} rows; refusing to clear existing data."
    )


def _range_row_count(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    customer_resource_name: str,
    grain: str,
    start_date: date,
    end_date: date,
) -> int:
    row = connection.execute(
        f"SELECT count(*) FROM {table} "
        "WHERE customer_resource_name = ? AND report_grain = ? "
        "AND report_date BETWEEN ? AND ?",
        [customer_resource_name, grain, start_date, end_date],
    ).fetchone()
    return 0 if row is None else int(row[0])


def _replace(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict[str, object]],
    customer_resource_name: str,
    grain: str,
    start_date: date,
    end_date: date,
) -> None:
    replace_report_range(
        connection,
        table,
        columns,
        rows,
        ReportRange(
            customer_resource_name,
            grain,
            start_date,
            end_date,
            account_column="customer_resource_name",
        ),
    )


def _parse_keyword_rows(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in _results(batches):
        rows.append(
            _fact_metrics(
                result,
                customer_resource_name,
                "keyword_day",
                ad_group_criterion_resource_name=_required_text(
                    getattr(result, "ad_group_criterion", None), "resource_name"
                ),
            )
        )
    return rows


def _parse_search_term_rows(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in _results(batches):
        view = getattr(result, "search_term_view", None)
        search_term = _optional_text(view, "search_term")
        availability = _search_term_availability(
            search_term, getattr(view, "status", None)
        )
        rows.append(
            _fact_metrics(
                result,
                customer_resource_name,
                "search_term_day",
                search_term_view_resource_name=_required_text(view, "resource_name"),
                campaign_resource_name=_required_text(
                    getattr(result, "campaign", None), "resource_name"
                ),
                ad_group_resource_name=_required_text(
                    getattr(result, "ad_group", None), "resource_name"
                ),
                search_term=search_term,
                search_term_availability=availability,
            )
        )
    return rows


def _fact_metrics(
    result: Any, customer_resource_name: str, grain: str, **dimensions: object
) -> dict[str, object]:
    metrics = getattr(result, "metrics", None)
    return {
        "customer_resource_name": customer_resource_name,
        "report_grain": grain,
        "report_date": _required_date(getattr(result, "segments", None)),
        "impressions": _required_int(metrics, "impressions"),
        "clicks": _required_int(metrics, "clicks"),
        "cost_micros": _required_int(metrics, "cost_micros"),
        "conversions": _required_decimal(metrics, "conversions"),
        "conversions_value": _required_decimal(metrics, "conversions_value"),
        **dimensions,
    }


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required_text(value: object, name: str) -> str:
    item = _optional_text(value, name)
    if item is None:
        raise KeywordSearchTermPerformanceImportError(
            f"Google Ads returned no valid {name}."
        )
    return item


def _optional_text(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    if item is None or not str(item).strip():
        return None
    return str(item)


def _search_term_availability(search_term: str | None, status: object) -> str:
    status_name = str(status).rsplit(".", maxsplit=1)[-1]
    if search_term is not None:
        return "available"
    if status_name == "SEARCH_TERM_UNAVAILABLE":
        return "unavailable"
    return "privacy_limited"


def _required_date(segments: object) -> date:
    value = getattr(segments, "date", None)
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise KeywordSearchTermPerformanceImportError(
            "Google Ads returned no valid date."
        ) from None


def _required_int(value: object, name: str) -> int:
    item = getattr(value, name, None)
    try:
        return int(str(item))
    except (TypeError, ValueError):
        raise KeywordSearchTermPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None


def _required_decimal(value: object, name: str) -> Decimal:
    item = getattr(value, name, None)
    try:
        result = Decimal(str(item))
    except (InvalidOperation, TypeError, ValueError):
        raise KeywordSearchTermPerformanceImportError(
            f"Google Ads returned no valid {name}."
        ) from None
    if not result.is_finite():
        raise KeywordSearchTermPerformanceImportError(
            f"Google Ads returned no valid {name}."
        )
    return result
