"""Import the configured Google Ads account's non-reporting dimensions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import duckdb

CUSTOMER_QUERY = """SELECT
    customer.resource_name, customer.id, customer.descriptive_name,
    customer.currency_code, customer.time_zone
FROM customer"""
CAMPAIGN_BUDGET_QUERY = """SELECT
    campaign_budget.resource_name, campaign_budget.id, campaign_budget.name,
    campaign_budget.amount_micros, campaign_budget.explicitly_shared
FROM campaign_budget"""
CAMPAIGN_QUERY = """SELECT
    campaign.resource_name, campaign.id, campaign.name, campaign.status,
    campaign.campaign_budget
FROM campaign"""
AD_GROUP_QUERY = """SELECT
    ad_group.resource_name, ad_group.id, ad_group.name, ad_group.status,
    ad_group.campaign
FROM ad_group"""
AD_DIMENSION_QUERY = """SELECT
    ad_group_ad.resource_name, ad_group_ad.status, ad_group_ad.ad.id,
    ad_group_ad.ad.name, ad_group_ad.ad.type, ad_group_ad.ad_group
FROM ad_group_ad"""

_ENTITY_COLUMNS = {
    "customers": (
        "customer_resource_name",
        "customer_id",
        "descriptive_name",
        "currency_code",
        "time_zone",
    ),
    "campaign_budgets": (
        "campaign_budget_resource_name",
        "campaign_budget_id",
        "customer_resource_name",
        "name",
        "amount_micros",
        "explicitly_shared",
    ),
    "campaigns": (
        "campaign_resource_name", "campaign_id", "customer_resource_name",
        "campaign_budget_resource_name", "name", "status",
    ),
    "ad_groups": (
        "ad_group_resource_name", "ad_group_id", "customer_resource_name",
        "campaign_resource_name", "name", "status",
    ),
    "ad_dimensions": (
        "ad_group_ad_resource_name", "ad_id", "customer_resource_name",
        "ad_group_resource_name", "status", "ad_type", "name",
    ),
}


class CoreAccountSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one query."""


class CoreAccountImportError(ValueError):
    """Google Ads returned incomplete core account dimension data."""


@dataclass(frozen=True)
class CoreAccountImportResult:
    """Counts of the dimension snapshots committed by an import."""

    customers: int
    campaign_budgets: int
    campaigns: int
    ad_groups: int
    ad_dimensions: int


def import_core_account(
    connection: duckdb.DuckDBPyConnection, source: CoreAccountSource
) -> CoreAccountImportResult:
    """Fetch and atomically replace each core dimension for the configured account."""
    customers = _parse_customers(source.search_stream(CUSTOMER_QUERY))
    if len(customers) != 1:
        raise CoreAccountImportError("Google Ads must return exactly one customer.")
    customer_resource_name = str(customers[0]["customer_resource_name"])
    campaign_budgets = _parse_campaign_budgets(
        source.search_stream(CAMPAIGN_BUDGET_QUERY), customer_resource_name
    )
    campaigns = _parse_campaigns(
        source.search_stream(CAMPAIGN_QUERY), customer_resource_name
    )
    ad_groups = _parse_ad_groups(
        source.search_stream(AD_GROUP_QUERY), customer_resource_name
    )
    ad_dimensions = _parse_ad_dimensions(
        source.search_stream(AD_DIMENSION_QUERY), customer_resource_name
    )
    _validate_relationships(campaign_budgets, campaigns, ad_groups, ad_dimensions)

    replace_core_account_snapshots(
        connection,
        customer_resource_name,
        {
            "customers": customers,
            "campaign_budgets": campaign_budgets,
            "campaigns": campaigns,
            "ad_groups": ad_groups,
            "ad_dimensions": ad_dimensions,
        },
    )

    return CoreAccountImportResult(
        len(customers),
        len(campaign_budgets),
        len(campaigns),
        len(ad_groups),
        len(ad_dimensions),
    )


def replace_core_account_snapshots(
    connection: duckdb.DuckDBPyConnection,
    customer_resource_name: str,
    snapshots: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Stage all related dimensions and replace them as one coherent snapshot."""
    if set(snapshots) != set(_ENTITY_COLUMNS):
        raise ValueError("snapshots must contain every core account entity")
    staged: dict[str, str] = {}
    connection.execute("BEGIN TRANSACTION")
    try:
        for table, rows in snapshots.items():
            staged[table] = _stage_snapshot(
                connection, table, _ENTITY_COLUMNS[table], rows, customer_resource_name
            )
        for table in (
            "ad_dimensions",
            "ad_groups",
            "campaigns",
            "campaign_budgets",
            "customers",
        ):
            connection.execute(
                f"DELETE FROM {_quote(table)} WHERE customer_resource_name = ?",
                [customer_resource_name],
            )
        for table in (
            "customers",
            "campaign_budgets",
            "campaigns",
            "ad_groups",
            "ad_dimensions",
        ):
            columns = ", ".join(_quote(column) for column in _ENTITY_COLUMNS[table])
            connection.execute(
                f"INSERT INTO {_quote(table)} ({columns}) SELECT {columns} "
                f"FROM {_quote(staged[table])}"
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        for staging_table in staged.values():
            connection.execute(f"DROP TABLE IF EXISTS {_quote(staging_table)}")


def _stage_snapshot(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    customer_resource_name: str,
) -> str:
    """Validate and populate a temporary, schema-identical dimension snapshot."""
    expected_columns = set(columns)
    values: list[tuple[object, ...]] = []
    for row in rows:
        if set(row) != expected_columns:
            raise ValueError("each row must contain exactly the supplied columns")
        if row["customer_resource_name"] != customer_resource_name:
            raise ValueError("staged rows must match the requested customer")
        values.append(tuple(row[column] for column in columns))
    staging_table = f"_staging_{uuid4().hex}"
    quoted_columns = ", ".join(_quote(column) for column in columns)
    connection.execute(
        f"CREATE TEMP TABLE {_quote(staging_table)} AS "
        f"SELECT {quoted_columns} FROM {_quote(table)} WHERE FALSE"
    )
    if values:
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {_quote(staging_table)} ({quoted_columns}) "
            f"VALUES ({placeholders})",
            values,
        )
    return staging_table


def _parse_customers(batches: Iterable[object]) -> list[dict[str, object]]:
    return [
        {
            "customer_resource_name": _required(row.customer, "resource_name"),
            "customer_id": _required_int(row.customer, "id"),
            "descriptive_name": _required(row.customer, "descriptive_name"),
            "currency_code": _required(row.customer, "currency_code"),
            "time_zone": _required(row.customer, "time_zone"),
        }
        for row in _results(batches)
    ]


def _parse_campaign_budgets(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    return [
        {
            "campaign_budget_resource_name": _required(
                row.campaign_budget, "resource_name"
            ),
            "campaign_budget_id": _required_int(row.campaign_budget, "id"),
            "customer_resource_name": customer_resource_name,
            "name": _required(row.campaign_budget, "name"),
            "amount_micros": _optional_int(row.campaign_budget, "amount_micros"),
            "explicitly_shared": bool(row.campaign_budget.explicitly_shared),
        }
        for row in _results(batches)
    ]


def _parse_campaigns(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    return [
        {
            "campaign_resource_name": _required(row.campaign, "resource_name"),
            "campaign_id": _required_int(row.campaign, "id"),
            "customer_resource_name": customer_resource_name,
            "campaign_budget_resource_name": _required(row.campaign, "campaign_budget"),
            "name": _required(row.campaign, "name"),
            "status": _required(row.campaign, "status"),
        }
        for row in _results(batches)
    ]


def _parse_ad_groups(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    return [
        {
            "ad_group_resource_name": _required(row.ad_group, "resource_name"),
            "ad_group_id": _required_int(row.ad_group, "id"),
            "customer_resource_name": customer_resource_name,
            "campaign_resource_name": _required(row.ad_group, "campaign"),
            "name": _required(row.ad_group, "name"),
            "status": _required(row.ad_group, "status"),
        }
        for row in _results(batches)
    ]


def _parse_ad_dimensions(
    batches: Iterable[object], customer_resource_name: str
) -> list[dict[str, object]]:
    return [
        {
            "ad_group_ad_resource_name": _required(row.ad_group_ad, "resource_name"),
            "ad_id": _required_int(row.ad_group_ad.ad, "id"),
            "customer_resource_name": customer_resource_name,
            "ad_group_resource_name": _required(row.ad_group_ad, "ad_group"),
            "status": _required(row.ad_group_ad, "status"),
            "ad_type": _required(row.ad_group_ad.ad, "type"),
            "name": _optional_text(row.ad_group_ad.ad, "name"),
        }
        for row in _results(batches)
    ]


def _validate_relationships(
    campaign_budgets: Sequence[Mapping[str, object]],
    campaigns: Sequence[Mapping[str, object]],
    ad_groups: Sequence[Mapping[str, object]],
    ad_dimensions: Sequence[Mapping[str, object]],
) -> None:
    """Ensure staged relationship keys refer to the same complete snapshot."""
    budget_names = {row["campaign_budget_resource_name"] for row in campaign_budgets}
    campaign_names = {row["campaign_resource_name"] for row in campaigns}
    ad_group_names = {row["ad_group_resource_name"] for row in ad_groups}
    if any(
        row["campaign_budget_resource_name"] not in budget_names for row in campaigns
    ):
        raise CoreAccountImportError("Campaign refers to a missing campaign budget.")
    if any(row["campaign_resource_name"] not in campaign_names for row in ad_groups):
        raise CoreAccountImportError("Ad group refers to a missing campaign.")
    if any(
        row["ad_group_resource_name"] not in ad_group_names for row in ad_dimensions
    ):
        raise CoreAccountImportError("Ad refers to a missing ad group.")


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required(value: object, name: str) -> str:
    text = _optional_text(value, name)
    if text is None:
        raise CoreAccountImportError(f"Google Ads returned no valid {name}.")
    return text


def _optional_text(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    if item is None or not str(item):
        return None
    return str(item)


def _required_int(value: object, name: str) -> int:
    item = _optional_int(value, name)
    if item is None:
        raise CoreAccountImportError(f"Google Ads returned no valid {name}.")
    return item


def _optional_int(value: object, name: str) -> int | None:
    item = getattr(value, name, None)
    try:
        return None if item is None else int(item)
    except (TypeError, ValueError):
        return None


def _quote(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(
            "identifiers must contain only letters, numbers, and underscores"
        )
    return f'"{identifier}"'
