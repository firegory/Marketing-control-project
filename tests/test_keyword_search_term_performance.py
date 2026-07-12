"""Tests for independent, privacy-aware keyword and search-term daily facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from marketing_control.keyword_search_term_performance import (
    KeywordSearchTermPerformanceImportError,
    import_keyword_search_term_performance,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection


class FakeSource:
    def __init__(self, responses: dict[str, tuple[object, ...]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        if "FROM keyword_view\n" in query:
            return self.responses["keyword"]
        if "FROM search_term_view\n" in query:
            return self.responses["search_term"]
        raise AssertionError(f"unexpected query: {query}")


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _metrics(*, clicks: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        impressions=10,
        clicks=clicks,
        cost_micros=1_250_000,
        conversions=1.25,
        conversions_value=12.5,
    )


def _keyword_result(
    report_date: str = "2026-01-02", *, clicks: int = 2
) -> SimpleNamespace:
    return SimpleNamespace(
        ad_group_criterion=SimpleNamespace(
            resource_name="customers/1/adGroupCriteria/3~4"
        ),
        segments=SimpleNamespace(date=report_date),
        metrics=_metrics(clicks=clicks),
    )


def _search_term_result(
    report_date: str = "2026-01-02",
    *,
    search_term: str | None = "red shoes",
    status: str = "NONE",
    resource_name: str = "customers/1/searchTermViews/2~3~key",
    clicks: int = 2,
) -> SimpleNamespace:
    return SimpleNamespace(
        search_term_view=SimpleNamespace(
            resource_name=resource_name,
            search_term=search_term,
            status=status,
        ),
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        ad_group=SimpleNamespace(resource_name="customers/1/adGroups/3"),
        segments=SimpleNamespace(date=report_date),
        metrics=_metrics(clicks=clicks),
    )


def _source(
    *,
    keyword_results: tuple[object, ...] | None = None,
    search_term_results: tuple[object, ...] | None = None,
    clicks: int = 2,
) -> FakeSource:
    return FakeSource(
        {
            "keyword": _batches(
                keyword_results, (_keyword_result(clicks=clicks),)
            ),
            "search_term": _batches(
                search_term_results, (_search_term_result(clicks=clicks),)
            ),
        }
    )


def _batches(
    results: tuple[object, ...] | None, default: tuple[object, ...]
) -> tuple[object, ...]:
    return (_batch(*(default if results is None else results)),)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_distinct_typed_grains_and_explicit_privacy_statuses(
    settings: Settings,
) -> None:
    source = _source(
        search_term_results=(
            _search_term_result(),
            _search_term_result(
                search_term=None,
                status="SEARCH_TERM_UNAVAILABLE",
                resource_name="customers/1/searchTermViews/2~3~unavailable",
            ),
            _search_term_result(
                search_term=None,
                resource_name="customers/1/searchTermViews/2~3~limited",
            ),
        )
    )

    with database_connection(settings) as connection:
        result = import_keyword_search_term_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        keyword = connection.execute(
            "SELECT * FROM keyword_daily_performance"
        ).fetchone()
        search_terms = connection.execute(
            "SELECT search_term, search_term_availability, "
            "conversions, conversions_value "
            "FROM search_term_daily_performance ORDER BY search_term_availability"
        ).fetchall()

    assert result.keyword_days == 1
    assert result.search_term_days == 3
    assert keyword == (
        "customers/1",
        "customers/1/adGroupCriteria/3~4",
        "keyword_day",
        date(2026, 1, 2),
        10,
        2,
        1_250_000,
        Decimal("1.250000"),
        Decimal("12.500000"),
    )
    assert search_terms == [
        ("red shoes", "available", Decimal("1.250000"), Decimal("12.500000")),
        (None, "privacy_limited", Decimal("1.250000"), Decimal("12.500000")),
        (None, "unavailable", Decimal("1.250000"), Decimal("12.500000")),
    ]
    assert all(
        "segments.date BETWEEN '2026-01-02' AND '2026-01-02'" in query
        for query in source.queries
    )
    assert ["FROM keyword_view\n" in query for query in source.queries] == [
        True,
        False,
    ]
    assert ["FROM search_term_view\n" in query for query in source.queries] == [
        False,
        True,
    ]


def test_replaces_only_requested_range_and_is_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_keyword_search_term_performance(
            connection,
            _source(),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )
        connection.execute(
            "INSERT INTO keyword_daily_performance VALUES "
            "('customers/1', 'customers/1/adGroupCriteria/3~4', "
            "'keyword_day', DATE '2026-01-01', 1, 1, 1, 1, 1)"
        )
        source = _source(clicks=20)
        import_keyword_search_term_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        first_rows = connection.execute(
            "SELECT report_date, clicks FROM keyword_daily_performance "
            "ORDER BY report_date"
        ).fetchall()
        import_keyword_search_term_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )

        assert first_rows == [(date(2026, 1, 1), 1), (date(2026, 1, 2), 20)]
        assert connection.execute(
            "SELECT report_date, clicks FROM keyword_daily_performance "
            "ORDER BY report_date"
        ).fetchall() == first_rows


def test_empty_response_preserves_existing_grain_data(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_keyword_search_term_performance(
            connection,
            _source(),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )

        with pytest.raises(
            KeywordSearchTermPerformanceImportError, match="refusing to clear"
        ):
            import_keyword_search_term_performance(
                connection,
                _source(keyword_results=()),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT clicks FROM keyword_daily_performance"
        ).fetchall() == [(2,)]


def test_failed_search_term_staging_rolls_back_its_range(settings: Settings) -> None:
    duplicate = _search_term_result()
    with database_connection(settings) as connection:
        import_keyword_search_term_performance(
            connection,
            _source(),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )

        with pytest.raises(duckdb.ConstraintException):
            import_keyword_search_term_performance(
                connection,
                _source(search_term_results=(duplicate, duplicate), clicks=20),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT clicks FROM search_term_daily_performance"
        ).fetchall() == [(2,)]
        assert connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE '_staging_%'"
        ).fetchall() == []
