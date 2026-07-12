"""Tests for typed, attachment-level Google Ads daily performance imports."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from marketing_control.asset_attachment_daily_performance import (
    AssetAttachmentPerformanceImportError,
    import_asset_attachment_daily_performance,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection


class FakeSource:
    def __init__(
        self, campaign: tuple[object, ...], ad_group: tuple[object, ...]
    ) -> None:
        self.responses = {"campaign": campaign, "ad_group": ad_group}
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        if "FROM campaign_asset" in query:
            return self.responses["campaign"]
        if "FROM ad_group_asset" in query:
            return self.responses["ad_group"]
        raise AssertionError(f"unexpected query: {query}")


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _result(
    scope: str,
    *,
    field_type: str = "SITELINK",
    clicks: int | None = 2,
    report_date: str = "2026-01-02",
) -> SimpleNamespace:
    attachment = SimpleNamespace(
        resource_name=f"customers/1/{scope}Assets/3~4",
        asset="customers/1/assets/4",
        field_type=field_type,
        **(
            {"campaign": "customers/1/campaigns/3"}
            if scope == "campaign"
            else {"ad_group": "customers/1/adGroups/3"}
        ),
    )
    metrics: dict[str, object] = {
        "impressions": 10,
        "cost_micros": 1_250_000,
        "conversions": 1.25,
        "conversions_value": 12.5,
    }
    if clicks is not None:
        metrics["clicks"] = clicks
    return SimpleNamespace(
        **{
            f"{scope}_asset": attachment,
            "segments": SimpleNamespace(date=report_date),
            "metrics": SimpleNamespace(**metrics),
        }
    )


def _source(
    *,
    campaign: tuple[object, ...] | None = None,
    ad_group: tuple[object, ...] | None = None,
    clicks: int | None = 2,
) -> FakeSource:
    return FakeSource(
        (
            _batch(
                *(
                    campaign
                    if campaign is not None
                    else (_result("campaign", clicks=clicks),)
                )
            ),
        ),
        (
            _batch(
                *(
                    ad_group
                    if ad_group is not None
                    else (_result("ad_group", clicks=clicks),)
                )
            ),
        ),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_supported_attachment_scopes_at_the_explicit_grain(
    settings: Settings,
) -> None:
    source = _source()

    with database_connection(settings) as connection:
        result = import_asset_attachment_daily_performance(
            connection, source, "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        rows = connection.execute(
            "SELECT attachment_scope, attachment_type, parent_resource_name, "
            "report_grain, report_date, clicks FROM asset_attachment_daily_performance "
            "ORDER BY attachment_scope"
        ).fetchall()

    assert result.attachment_days == 2
    assert rows == [
        (
            "ad_group",
            "SITELINK",
            "customers/1/adGroups/3",
            "asset_attachment_day",
            date(2026, 1, 2),
            2,
        ),
        (
            "campaign",
            "SITELINK",
            "customers/1/campaigns/3",
            "asset_attachment_day",
            date(2026, 1, 2),
            2,
        ),
    ]
    assert all(
        "segments.date BETWEEN '2026-01-02' AND '2026-01-02'" in query
        for query in source.queries
    )


def test_rejects_unsupported_attachment_type_without_writing_metrics(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        with pytest.raises(
            AssetAttachmentPerformanceImportError,
            match="scope 'campaign' and type 'UNKNOWN'",
        ):
            import_asset_attachment_daily_performance(
                connection,
                _source(campaign=(_result("campaign", field_type="UNKNOWN"),)),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT count(*) FROM asset_attachment_daily_performance"
        ).fetchone() == (0,)


def test_rejects_missing_metric_instead_of_fabricating_a_value(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        with pytest.raises(AssetAttachmentPerformanceImportError, match="clicks"):
            import_asset_attachment_daily_performance(
                connection,
                _source(campaign=(_result("campaign", clicks=None),)),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT count(*) FROM asset_attachment_daily_performance"
        ).fetchone() == (0,)


def test_replaces_only_requested_range_and_is_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_asset_attachment_daily_performance(
            connection,
            _source(clicks=20),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )
        first_rows = connection.execute(
            "SELECT attachment_scope, clicks FROM asset_attachment_daily_performance "
            "ORDER BY attachment_scope"
        ).fetchall()
        import_asset_attachment_daily_performance(
            connection,
            _source(clicks=20),
            "customers/1",
            date(2026, 1, 2),
            date(2026, 1, 2),
        )

        assert (
            connection.execute(
                "SELECT attachment_scope, clicks "
                "FROM asset_attachment_daily_performance "
                "ORDER BY attachment_scope"
            ).fetchall()
            == first_rows
        )


def test_empty_response_preserves_existing_range(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_asset_attachment_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        with pytest.raises(
            AssetAttachmentPerformanceImportError, match="refusing to clear"
        ):
            import_asset_attachment_daily_performance(
                connection,
                _source(campaign=(), ad_group=()),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT count(*) FROM asset_attachment_daily_performance"
        ).fetchone() == (2,)


def test_failed_staging_rolls_back_the_entire_attachment_range(
    settings: Settings,
) -> None:
    first = _result("campaign", clicks=2)
    duplicate = _result("campaign", clicks=99)

    with database_connection(settings) as connection:
        import_asset_attachment_daily_performance(
            connection, _source(), "customers/1", date(2026, 1, 2), date(2026, 1, 2)
        )
        with pytest.raises(duckdb.ConstraintException):
            import_asset_attachment_daily_performance(
                connection,
                _source(campaign=(first, duplicate)),
                "customers/1",
                date(2026, 1, 2),
                date(2026, 1, 2),
            )

        assert connection.execute(
            "SELECT attachment_scope, clicks FROM asset_attachment_daily_performance "
            "ORDER BY attachment_scope"
        ).fetchall() == [("ad_group", 2), ("campaign", 2)]
