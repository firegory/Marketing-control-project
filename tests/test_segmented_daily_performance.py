"""Tests for device, audience, and semantically distinct location fact imports."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from marketing_control.segmented_daily_performance import (
    SegmentedDailyPerformanceImportError,
    import_segmented_daily_performance,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection


class FakeSource:
    def __init__(self, responses: dict[str, tuple[object, ...]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        if "FROM campaign\n" in query:
            return self.responses["device"]
        if "FROM ad_group_audience_view\n" in query:
            return self.responses["audience"]
        if "FROM location_view\n" in query:
            return self.responses["targeting"]
        if "LOCATION_OF_PRESENCE" in query:
            return self.responses["presence"]
        if "AREA_OF_INTEREST" in query:
            return self.responses["interest"]
        raise AssertionError(f"unexpected query: {query}")


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _metrics(*, clicks: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        impressions=10,
        clicks=clicks,
        cost_micros=1_250_000,
        conversions="1.25",
        conversions_value="12.5",
    )


def _source(
    *,
    device_results: tuple[object, ...] | None = None,
    presence_results: tuple[object, ...] | None = None,
    clicks: int = 2,
) -> FakeSource:
    segments = SimpleNamespace(
        date="2026-01-02",
        device="MOBILE",
        geo_target_most_specific_location="geoTargetConstants/1000",
    )
    device = SimpleNamespace(
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        segments=segments,
        metrics=_metrics(clicks=clicks),
    )
    audience = SimpleNamespace(
        ad_group=SimpleNamespace(resource_name="customers/1/adGroups/3"),
        ad_group_criterion=SimpleNamespace(
            resource_name="customers/1/adGroupCriteria/3~4"
        ),
        segments=SimpleNamespace(date="2026-01-02"),
        metrics=_metrics(clicks=clicks),
    )
    targeting = SimpleNamespace(
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        segments=SimpleNamespace(
            date="2026-01-02",
            geo_target_most_specific_location="geoTargetConstants/1000",
        ),
        metrics=_metrics(clicks=clicks),
    )
    geographic = SimpleNamespace(
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        segments=SimpleNamespace(
            date="2026-01-02",
            geo_target_most_specific_location="geoTargetConstants/1000",
        ),
        metrics=_metrics(clicks=clicks),
    )
    return FakeSource(
        {
            "device": (
                _batch(*(device_results if device_results is not None else (device,))),
            ),
            "audience": (_batch(audience),),
            "targeting": (_batch(targeting),),
            "presence": (
                _batch(
                    *(
                        presence_results
                        if presence_results is not None
                        else (geographic,)
                    )
                ),
            ),
            "interest": (_batch(geographic),),
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_independent_grains_with_exact_metric_types_and_semantics(
    settings: Settings,
) -> None:
    source = _source()
    with database_connection(settings) as connection:
        result = import_segmented_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        device = connection.execute("SELECT * FROM device_daily_performance").fetchone()
        audience = connection.execute(
            "SELECT * FROM audience_daily_performance"
        ).fetchone()
        locations = connection.execute(
            "SELECT report_grain, location_semantics, conversions, conversions_value "
            "FROM location_daily_performance ORDER BY report_grain"
        ).fetchall()

    assert result.device_days == result.audience_days == 1
    assert result.location_targeting_days == result.user_presence_days == 1
    assert result.user_interest_days == 1
    assert device == (
        "customers/1",
        "customers/1/campaigns/2",
        "device_day",
        date(2026, 1, 2),
        "MOBILE",
        10,
        2,
        1_250_000,
        Decimal("1.250000"),
        Decimal("12.500000"),
    )
    assert audience is not None
    assert audience[1:5] == (
        "customers/1/adGroups/3",
        "customers/1/adGroupCriteria/3~4",
        "audience_day",
        date(2026, 1, 2),
    )
    assert locations == [
        (
            "location_targeting_day",
            "targeting",
            Decimal("1.250000"),
            Decimal("12.500000"),
        ),
        (
            "user_interest_day",
            "user_interest",
            Decimal("1.250000"),
            Decimal("12.500000"),
        ),
        (
            "user_presence_day",
            "user_presence",
            Decimal("1.250000"),
            Decimal("12.500000"),
        ),
    ]
    assert all(
        "segments.date BETWEEN '2026-01-02' AND '2026-01-02'" in query
        for query in source.queries
    )
    assert len(source.queries) == 5
    assert "FROM location_view" in source.queries[2]
    assert "LOCATION_OF_PRESENCE" in source.queries[3]
    assert "AREA_OF_INTEREST" in source.queries[4]


def test_replaces_only_requested_account_grain_and_date_range_idempotently(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        connection.execute(
            "INSERT INTO device_daily_performance VALUES "
            "('customers/1', 'customers/1/campaigns/2', 'device_day', "
            "DATE '2026-01-01', 'MOBILE', 1, 1, 1, 1, 1), "
            "('customers/1', 'customers/1/campaigns/2', 'device_day', "
            "DATE '2026-01-03', 'MOBILE', 3, 3, 3, 3, 3)"
        )
        source = _source(clicks=20)
        import_segmented_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        first_rows = connection.execute(
            "SELECT report_date, clicks FROM device_daily_performance "
            "ORDER BY report_date"
        ).fetchall()
        import_segmented_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )

        assert first_rows == [
            (date(2026, 1, 1), 1),
            (date(2026, 1, 2), 20),
            (date(2026, 1, 3), 3),
        ]
        assert (
            connection.execute(
                "SELECT report_date, clicks FROM device_daily_performance "
                "ORDER BY report_date"
            ).fetchall()
            == first_rows
        )


def test_empty_response_does_not_clear_existing_location_semantic_grain(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        import_segmented_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )

        with pytest.raises(
            SegmentedDailyPerformanceImportError, match="refusing to clear"
        ):
            import_segmented_daily_performance(
                connection,
                _source(presence_results=(), clicks=20),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT report_grain, location_semantics, clicks "
            "FROM location_daily_performance ORDER BY report_grain"
        ).fetchall() == [
            ("location_targeting_day", "targeting", 2),
            ("user_interest_day", "user_interest", 2),
            ("user_presence_day", "user_presence", 2),
        ]
        assert connection.execute(
            "SELECT clicks FROM device_daily_performance"
        ).fetchall() == [(2,)]


def test_replacing_location_grain_preserves_other_location_grains(
    settings: Settings,
) -> None:
    updated_presence = SimpleNamespace(
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        segments=SimpleNamespace(
            date="2026-01-02",
            geo_target_most_specific_location="geoTargetConstants/1000",
        ),
        metrics=_metrics(clicks=20),
    )
    with database_connection(settings) as connection:
        import_segmented_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        import_segmented_daily_performance(
            connection,
            _source(presence_results=(updated_presence,)),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )

        rows = connection.execute(
            "SELECT report_grain, clicks FROM location_daily_performance "
            "ORDER BY report_grain"
        ).fetchall()

    assert rows == [
        ("location_targeting_day", 2),
        ("user_interest_day", 2),
        ("user_presence_day", 20),
    ]


def test_failed_staging_rolls_back_the_requested_grain_and_cleans_up(
    settings: Settings,
) -> None:
    first = SimpleNamespace(
        campaign=SimpleNamespace(resource_name="customers/1/campaigns/2"),
        segments=SimpleNamespace(
            date="2026-01-02",
            geo_target_most_specific_location="geoTargetConstants/1000",
        ),
        metrics=_metrics(clicks=2),
    )
    duplicate = SimpleNamespace(
        campaign=first.campaign,
        segments=first.segments,
        metrics=_metrics(clicks=99),
    )
    with database_connection(settings) as connection:
        import_segmented_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        with pytest.raises(duckdb.ConstraintException):
            import_segmented_daily_performance(
                connection,
                _source(presence_results=(first, duplicate)),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT clicks FROM location_daily_performance "
            "WHERE report_grain = 'user_presence_day'"
        ).fetchall() == [(2,)]
        assert (
            connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '_staging_%'"
            ).fetchall()
            == []
        )
