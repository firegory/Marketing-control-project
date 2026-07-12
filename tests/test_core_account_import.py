"""Tests for one-account Google Ads dimension ingestion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from marketing_control.core_account_import import (
    AD_DIMENSION_QUERY,
    AD_GROUP_QUERY,
    CAMPAIGN_BUDGET_QUERY,
    CAMPAIGN_QUERY,
    CUSTOMER_QUERY,
    CoreAccountImportError,
    import_core_account,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection


class FakeSource:
    def __init__(self, responses: dict[str, tuple[object, ...]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        return self.responses[query]


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _source(*, campaign_budget: str = "customers/1/campaignBudgets/2") -> FakeSource:
    customer = SimpleNamespace(
        resource_name="customers/1",
        id=1,
        descriptive_name="Example customer",
        currency_code="USD",
        time_zone="America/New_York",
    )
    budget = SimpleNamespace(
        resource_name="customers/1/campaignBudgets/2",
        id=2,
        name="Daily budget",
        amount_micros=10_000_000,
        explicitly_shared=False,
    )
    campaign = SimpleNamespace(
        resource_name="customers/1/campaigns/3",
        id=3,
        name="Brand campaign",
        status="ENABLED",
        campaign_budget=campaign_budget,
    )
    ad_group = SimpleNamespace(
        resource_name="customers/1/adGroups/4",
        id=4,
        name="Brand ad group",
        status="PAUSED",
        campaign="customers/1/campaigns/3",
    )
    ad = SimpleNamespace(id=5, name="Responsive ad", type="RESPONSIVE_SEARCH_AD")
    ad_group_ad = SimpleNamespace(
        resource_name="customers/1/adGroupAds/4~5",
        ad=ad,
        ad_group="customers/1/adGroups/4",
        status="ENABLED",
    )
    return FakeSource(
        {
            CUSTOMER_QUERY: (_batch(SimpleNamespace(customer=customer)),),
            CAMPAIGN_BUDGET_QUERY: (_batch(SimpleNamespace(campaign_budget=budget)),),
            CAMPAIGN_QUERY: (_batch(SimpleNamespace(campaign=campaign)),),
            AD_GROUP_QUERY: (_batch(SimpleNamespace(ad_group=ad_group)),),
            AD_DIMENSION_QUERY: (_batch(SimpleNamespace(ad_group_ad=ad_group_ad)),),
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_normalized_account_dimensions_and_uses_focused_queries(
    settings: Settings,
) -> None:
    source = _source()

    with database_connection(settings) as connection:
        result = import_core_account(connection, source)
        relationships = connection.execute(
            """
            SELECT c.customer_id, b.campaign_budget_id, p.campaign_id,
                   g.ad_group_id, a.ad_id
            FROM customers c
            JOIN campaign_budgets b USING (customer_resource_name)
            JOIN campaigns p
                USING (customer_resource_name, campaign_budget_resource_name)
            JOIN ad_groups g USING (customer_resource_name, campaign_resource_name)
            JOIN ad_dimensions a USING (customer_resource_name, ad_group_resource_name)
            """
        ).fetchall()

    assert result.customers == result.campaign_budgets == result.campaigns == 1
    assert result.ad_groups == result.ad_dimensions == 1
    assert source.queries == [
        CUSTOMER_QUERY,
        CAMPAIGN_BUDGET_QUERY,
        CAMPAIGN_QUERY,
        AD_GROUP_QUERY,
        AD_DIMENSION_QUERY,
    ]
    assert relationships == [(1, 2, 3, 4, 5)]


def test_failed_staged_snapshot_preserves_prior_committed_account_data(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        import_core_account(connection, _source())
        invalid_source = _source()
        original_budget = SimpleNamespace(
            resource_name="customers/1/campaignBudgets/2",
            id=2,
            name="Daily budget",
            amount_micros=10_000_000,
            explicitly_shared=False,
        )
        invalid_budget = SimpleNamespace(
            resource_name="customers/1/campaignBudgets/duplicate",
            id=2,
            name="Duplicate budget ID",
            amount_micros=5_000_000,
            explicitly_shared=False,
        )
        invalid_source.responses[CAMPAIGN_BUDGET_QUERY] = (
            _batch(
                SimpleNamespace(campaign_budget=original_budget),
                SimpleNamespace(campaign_budget=invalid_budget),
            ),
        )

        with pytest.raises(duckdb.ConstraintException):
            import_core_account(connection, invalid_source)

        assert connection.execute(
            "SELECT campaign_budget_resource_name FROM campaigns"
        ).fetchall() == [("customers/1/campaignBudgets/2",)]
        assert (
            connection.execute("SELECT count(*) FROM ad_dimensions").fetchone() == (1,)
        )
        assert connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE '_staging_%'"
        ).fetchall() == []


def test_unchanged_import_is_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_core_account(connection, _source())
        first_rows = connection.execute(
            "SELECT * FROM ad_dimensions"
        ).fetchall()

        import_core_account(connection, _source())

        assert (
            connection.execute("SELECT * FROM ad_dimensions").fetchall() == first_rows
        )
        assert connection.execute("SELECT count(*) FROM customers").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM campaigns").fetchone() == (1,)


def test_rejects_a_response_without_exactly_one_customer(settings: Settings) -> None:
    source = _source()
    source.responses[CUSTOMER_QUERY] = ()

    with database_connection(settings) as connection:
        with pytest.raises(CoreAccountImportError, match="exactly one customer"):
            import_core_account(connection, source)

        assert connection.execute("SELECT count(*) FROM customers").fetchone() == (0,)
