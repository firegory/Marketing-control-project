"""Tests for independent, typed daily Google Ads fact imports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from marketing_control.core_daily_performance import import_core_daily_performance
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection


class FakeSource:
    def __init__(self, responses: dict[str, tuple[object, ...]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        if "FROM campaign\n" in query:
            return self.responses["campaign"]
        if "FROM ad_group\n" in query:
            return self.responses["ad_group"]
        if "FROM ad_group_ad\n" in query:
            return self.responses["ad"]
        raise AssertionError(f"unexpected query: {query}")


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _result(
    entity_name: str, resource_name: str, report_date: str, *, clicks: int = 2
) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            entity_name: SimpleNamespace(resource_name=resource_name),
            "segments": SimpleNamespace(date=report_date),
            "metrics": SimpleNamespace(
                impressions=10,
                clicks=clicks,
                cost_micros=1_250_000,
                conversions=1.25,
                conversions_value=12.5,
            ),
        }
    )


def _source(
    *, campaign_results: tuple[object, ...] | None = None, clicks: int = 2
) -> FakeSource:
    campaign = _result(
        "campaign", "customers/1/campaigns/2", "2026-01-02", clicks=clicks
    )
    return FakeSource(
        {
            "campaign": (_batch(*(campaign_results or (campaign,))),),
            "ad_group": (
                _batch(
                    _result(
                        "ad_group",
                        "customers/1/adGroups/3",
                        "2026-01-02",
                        clicks=clicks,
                    )
                ),
            ),
            "ad": (
                _batch(
                    _result(
                        "ad_group_ad",
                        "customers/1/adGroupAds/3~4",
                        "2026-01-02",
                        clicks=clicks,
                    )
                ),
            ),
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_each_daily_grain_separately_with_exact_metric_types(
    settings: Settings,
) -> None:
    source = _source()

    with database_connection(settings) as connection:
        result = import_core_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        campaign = connection.execute(
            "SELECT * FROM campaign_daily_performance"
        ).fetchone()
        ad_group = connection.execute(
            "SELECT * FROM ad_group_daily_performance"
        ).fetchone()
        ad = connection.execute("SELECT * FROM ad_daily_performance").fetchone()

    assert result.campaign_days == result.ad_group_days == result.ad_days == 1
    assert ad_group is not None
    assert ad is not None
    assert campaign == (
        "customers/1",
        "customers/1/campaigns/2",
        "campaign_day",
        date(2026, 1, 2),
        10,
        2,
        1_250_000,
        Decimal("1.250000"),
        Decimal("12.500000"),
    )
    assert ad_group[1:4] == ("customers/1/adGroups/3", "ad_group_day", date(2026, 1, 2))
    assert ad[1:4] == ("customers/1/adGroupAds/3~4", "ad_day", date(2026, 1, 2))
    assert all(
        "segments.date BETWEEN '2026-01-02' AND '2026-01-02'" in query
        for query in source.queries
    )
    assert ["FROM campaign\n" in query for query in source.queries] == [
        True,
        False,
        False,
    ]
    assert ["FROM ad_group\n" in query for query in source.queries] == [
        False,
        True,
        False,
    ]
    assert ["FROM ad_group_ad\n" in query for query in source.queries] == [
        False,
        False,
        True,
    ]


def test_replaces_only_requested_range_and_is_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO campaign_daily_performance VALUES "
            "('customers/1', 'customers/1/campaigns/2', "
            "'campaign_day', DATE '2026-01-01', 1, 1, 1, 1, 1), "
            "('customers/1', 'customers/1/campaigns/2', "
            "'campaign_day', DATE '2026-01-03', 3, 3, 3, 3, 3)"
        )
        source = _source(clicks=20)
        import_core_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        first_rows = connection.execute(
            "SELECT report_date, clicks FROM campaign_daily_performance "
            "ORDER BY report_date"
        ).fetchall()
        import_core_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )

        assert first_rows == [
            (date(2026, 1, 1), 1),
            (date(2026, 1, 2), 20),
            (date(2026, 1, 3), 3),
        ]
        assert (
            connection.execute(
                "SELECT report_date, clicks FROM campaign_daily_performance "
                "ORDER BY report_date"
            ).fetchall()
            == first_rows
        )


def test_failed_campaign_staging_preserves_previously_committed_rows(
    settings: Settings,
) -> None:
    first_campaign = _result(
        "campaign", "customers/1/campaigns/2", "2026-01-02", clicks=2
    )
    duplicate_campaign = _result(
        "campaign", "customers/1/campaigns/2", "2026-01-02", clicks=99
    )

    with database_connection(settings) as connection:
        import_core_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        with pytest.raises(duckdb.ConstraintException):
            import_core_daily_performance(
                connection,
                _source(campaign_results=(first_campaign, duplicate_campaign)),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT clicks FROM campaign_daily_performance"
        ).fetchall() == [(2,)]
        assert (
            connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '_staging_%'"
            ).fetchall()
            == []
        )
