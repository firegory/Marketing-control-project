"""Tests for supporting Google Ads dimension ingestion."""

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
    import_core_account,
)
from marketing_control.core_account_import import (
    CUSTOMER_QUERY as CORE_CUSTOMER_QUERY,
)
from marketing_control.settings import AppPaths, Settings
from marketing_control.storage import database_connection
from marketing_control.supporting_entities_import import (
    AD_GROUP_ASSET_QUERY,
    AD_GROUP_CRITERIA_QUERY,
    ASSET_QUERY,
    CAMPAIGN_ASSET_QUERY,
    CAMPAIGN_CRITERIA_QUERY,
    CUSTOMER_ASSET_QUERY,
    CUSTOMER_QUERY,
    GEO_TARGET_QUERY,
    KEYWORD_QUERY,
    SupportingEntitiesImportError,
    import_supporting_entities,
)


class FakeSource:
    def __init__(self, responses: dict[str, tuple[object, ...]]) -> None:
        self.responses = responses
        self.queries: list[str] = []

    def search_stream(self, query: str) -> tuple[object, ...]:
        self.queries.append(query)
        return self.responses[query]


def _batch(*results: object) -> SimpleNamespace:
    return SimpleNamespace(results=results)


def _core_source() -> FakeSource:
    customer = SimpleNamespace(
        resource_name="customers/1",
        id=1,
        descriptive_name="Example",
        currency_code="USD",
        time_zone="UTC",
    )
    budget = SimpleNamespace(
        resource_name="customers/1/campaignBudgets/2",
        id=2,
        name="Budget",
        amount_micros=1,
        explicitly_shared=False,
    )
    campaign = SimpleNamespace(
        resource_name="customers/1/campaigns/3",
        id=3,
        name="Campaign",
        status="ENABLED",
        campaign_budget=budget.resource_name,
    )
    ad_group = SimpleNamespace(
        resource_name="customers/1/adGroups/4",
        id=4,
        name="Group",
        status="ENABLED",
        campaign=campaign.resource_name,
    )
    ad = SimpleNamespace(id=5, name="Ad", type="RESPONSIVE_SEARCH_AD")
    ad_group_ad = SimpleNamespace(
        resource_name="customers/1/adGroupAds/4~5",
        ad=ad,
        ad_group=ad_group.resource_name,
        status="ENABLED",
    )
    return FakeSource(
        {
            CORE_CUSTOMER_QUERY: (_batch(SimpleNamespace(customer=customer)),),
            CAMPAIGN_BUDGET_QUERY: (_batch(SimpleNamespace(campaign_budget=budget)),),
            CAMPAIGN_QUERY: (_batch(SimpleNamespace(campaign=campaign)),),
            AD_GROUP_QUERY: (_batch(SimpleNamespace(ad_group=ad_group)),),
            AD_DIMENSION_QUERY: (_batch(SimpleNamespace(ad_group_ad=ad_group_ad)),),
        }
    )


def _supporting_source(*, attachment_asset: str = "customers/1/assets/7") -> FakeSource:
    customer = SimpleNamespace(resource_name="customers/1")
    keyword = SimpleNamespace(
        resource_name="customers/1/adGroupCriteria/4~6",
        criterion_id=6,
        ad_group="customers/1/adGroups/4",
        status="ENABLED",
        keyword=SimpleNamespace(text="marketing control", match_type="EXACT"),
    )
    audience = SimpleNamespace(
        resource_name="customers/1/adGroupCriteria/4~8",
        criterion_id=8,
        ad_group="customers/1/adGroups/4",
        type="USER_LIST",
        status="PAUSED",
    )
    campaign_criterion = SimpleNamespace(
        resource_name="customers/1/campaignCriteria/3~9",
        criterion_id=9,
        campaign="customers/1/campaigns/3",
        type="LOCATION",
        status="ENABLED",
        location=SimpleNamespace(geo_target_constant="geoTargetConstants/1000"),
    )
    asset = SimpleNamespace(
        resource_name="customers/1/assets/7", id=7, name="Logo", type="IMAGE"
    )
    customer_asset = SimpleNamespace(
        resource_name="customers/1/customerAssets/7~LOGO",
        asset=attachment_asset,
        field_type="LOGO",
        status="ENABLED",
    )
    campaign_asset = SimpleNamespace(
        resource_name="customers/1/campaignAssets/3~7",
        campaign="customers/1/campaigns/3",
        asset=attachment_asset,
        field_type="MARKETING_IMAGE",
        status="ENABLED",
    )
    ad_group_asset = SimpleNamespace(
        resource_name="customers/1/adGroupAssets/4~7",
        ad_group="customers/1/adGroups/4",
        asset=attachment_asset,
        field_type="MARKETING_IMAGE",
        status="PAUSED",
    )
    geo = SimpleNamespace(
        resource_name="geoTargetConstants/1000",
        id=1000,
        name="New York",
        canonical_name="New York, New York, United States",
        country_code="US",
        target_type="CITY",
        status="ENABLED",
    )
    return FakeSource(
        {
            CUSTOMER_QUERY: (_batch(SimpleNamespace(customer=customer)),),
            KEYWORD_QUERY: (_batch(SimpleNamespace(ad_group_criterion=keyword)),),
            AD_GROUP_CRITERIA_QUERY: (
                _batch(SimpleNamespace(ad_group_criterion=audience)),
            ),
            CAMPAIGN_CRITERIA_QUERY: (
                _batch(SimpleNamespace(campaign_criterion=campaign_criterion)),
            ),
            ASSET_QUERY: (_batch(SimpleNamespace(asset=asset)),),
            CUSTOMER_ASSET_QUERY: (
                _batch(SimpleNamespace(customer_asset=customer_asset)),
            ),
            CAMPAIGN_ASSET_QUERY: (
                _batch(SimpleNamespace(campaign_asset=campaign_asset)),
            ),
            AD_GROUP_ASSET_QUERY: (
                _batch(SimpleNamespace(ad_group_asset=ad_group_asset)),
            ),
            GEO_TARGET_QUERY: (_batch(SimpleNamespace(geo_target_constant=geo)),),
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )


def test_imports_normalized_supporting_entities_with_focused_queries(
    settings: Settings,
) -> None:
    source = _supporting_source()
    with database_connection(settings) as connection:
        import_core_account(connection, _core_source())
        result = import_supporting_entities(connection, source)
        relationships = connection.execute(
            """SELECT k.criterion_id, agc.source_type, cc.criterion_id, g.name,
                      aa.attachment_scope, a.asset_id
               FROM keyword_criteria k
               JOIN ad_group_criteria agc
                   USING (customer_resource_name, ad_group_resource_name)
               JOIN ad_groups ag
                   USING (customer_resource_name, ad_group_resource_name)
               JOIN campaign_criteria cc USING (customer_resource_name)
               JOIN campaigns c ON c.customer_resource_name = cc.customer_resource_name
                   AND c.campaign_resource_name = cc.campaign_resource_name
               JOIN geo_target_constants g
                   USING (customer_resource_name, geo_target_constant_resource_name)
               JOIN asset_attachments aa USING (customer_resource_name)
               JOIN assets a USING (customer_resource_name, asset_resource_name)
               ORDER BY aa.attachment_scope"""
        ).fetchall()

    assert (
        result.keyword_criteria
        == result.ad_group_criteria
        == result.campaign_criteria
        == 1
    )
    assert result.assets == result.geo_target_constants == 1
    assert result.asset_attachments == 3
    assert source.queries == [
        CUSTOMER_QUERY,
        KEYWORD_QUERY,
        AD_GROUP_CRITERIA_QUERY,
        CAMPAIGN_CRITERIA_QUERY,
        ASSET_QUERY,
        CUSTOMER_ASSET_QUERY,
        CAMPAIGN_ASSET_QUERY,
        AD_GROUP_ASSET_QUERY,
        GEO_TARGET_QUERY,
    ]
    assert relationships == [
        (6, "USER_LIST", 9, "New York", "ad_group", 7),
        (6, "USER_LIST", 9, "New York", "campaign", 7),
        (6, "USER_LIST", 9, "New York", "customer", 7),
    ]


def test_rejects_invalid_relationship_without_replacing_prior_snapshot(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        import_core_account(connection, _core_source())
        import_supporting_entities(connection, _supporting_source())

        with pytest.raises(SupportingEntitiesImportError, match="missing asset"):
            import_supporting_entities(
                connection,
                _supporting_source(attachment_asset="customers/1/assets/unknown"),
            )

        assert connection.execute(
            "SELECT asset_resource_name FROM assets"
        ).fetchall() == [("customers/1/assets/7",)]
        assert connection.execute(
            "SELECT count(*) FROM asset_attachments"
        ).fetchone() == (3,)


def test_database_failure_rolls_back_entire_supporting_snapshot(
    settings: Settings,
) -> None:
    with database_connection(settings) as connection:
        import_core_account(connection, _core_source())
        import_supporting_entities(connection, _supporting_source())
        duplicate_asset = SimpleNamespace(
            resource_name="customers/1/assets/duplicate",
            id=7,
            name="Duplicate",
            type="IMAGE",
        )
        invalid = _supporting_source()
        invalid.responses[ASSET_QUERY] = (
            _batch(
                SimpleNamespace(
                    asset=SimpleNamespace(
                        resource_name="customers/1/assets/7",
                        id=7,
                        name="Logo",
                        type="IMAGE",
                    )
                ),
                SimpleNamespace(asset=duplicate_asset),
            ),
        )

        with pytest.raises(duckdb.ConstraintException):
            import_supporting_entities(connection, invalid)

        assert connection.execute(
            "SELECT asset_resource_name FROM assets"
        ).fetchall() == [("customers/1/assets/7",)]
        assert connection.execute(
            "SELECT count(*) FROM geo_target_constants"
        ).fetchone() == (1,)
        assert (
            connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE '_staging_%'"
            ).fetchall()
            == []
        )


def test_unchanged_import_is_idempotent(settings: Settings) -> None:
    with database_connection(settings) as connection:
        import_core_account(connection, _core_source())
        import_supporting_entities(connection, _supporting_source())
        first_rows = connection.execute(
            "SELECT * FROM asset_attachments ORDER BY asset_attachment_resource_name"
        ).fetchall()

        import_supporting_entities(connection, _supporting_source())

        assert (
            connection.execute(
                "SELECT * FROM asset_attachments "
                "ORDER BY asset_attachment_resource_name"
            ).fetchall()
            == first_rows
        )
        assert connection.execute(
            "SELECT count(*) FROM keyword_criteria"
        ).fetchone() == (1,)
