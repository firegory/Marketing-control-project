"""Import supporting Google Ads dimensions for one core account snapshot."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import duckdb

CUSTOMER_QUERY = "SELECT customer.resource_name FROM customer"
KEYWORD_QUERY = """SELECT
    ad_group_criterion.resource_name, ad_group_criterion.criterion_id,
    ad_group_criterion.ad_group, ad_group_criterion.status,
    ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type
FROM keyword_view"""
AD_GROUP_CRITERIA_QUERY = """SELECT
    ad_group_criterion.resource_name, ad_group_criterion.criterion_id,
    ad_group_criterion.ad_group, ad_group_criterion.type, ad_group_criterion.status
FROM ad_group_criterion
WHERE ad_group_criterion.type != KEYWORD"""
CAMPAIGN_CRITERIA_QUERY = """SELECT
    campaign_criterion.resource_name, campaign_criterion.criterion_id,
    campaign_criterion.campaign, campaign_criterion.type, campaign_criterion.status,
    campaign_criterion.location.geo_target_constant
FROM campaign_criterion"""
ASSET_QUERY = """SELECT asset.resource_name, asset.id, asset.name, asset.type
FROM asset"""
CUSTOMER_ASSET_QUERY = """SELECT
    customer_asset.resource_name, customer_asset.asset, customer_asset.field_type,
    customer_asset.status
FROM customer_asset"""
CAMPAIGN_ASSET_QUERY = """SELECT
    campaign_asset.resource_name, campaign_asset.campaign, campaign_asset.asset,
    campaign_asset.field_type, campaign_asset.status
FROM campaign_asset"""
AD_GROUP_ASSET_QUERY = """SELECT
    ad_group_asset.resource_name, ad_group_asset.ad_group, ad_group_asset.asset,
    ad_group_asset.field_type, ad_group_asset.status
FROM ad_group_asset"""
GEO_TARGET_QUERY = """SELECT
    geo_target_constant.resource_name, geo_target_constant.id, geo_target_constant.name,
    geo_target_constant.canonical_name, geo_target_constant.country_code,
    geo_target_constant.target_type, geo_target_constant.status
FROM geo_target_constant"""

_ENTITY_COLUMNS = {
    "keyword_criteria": (
        "ad_group_criterion_resource_name",
        "criterion_id",
        "customer_resource_name",
        "ad_group_resource_name",
        "source_status",
        "keyword_text",
        "match_type",
    ),
    "ad_group_criteria": (
        "ad_group_criterion_resource_name",
        "criterion_id",
        "customer_resource_name",
        "ad_group_resource_name",
        "source_type",
        "source_status",
    ),
    "campaign_criteria": (
        "campaign_criterion_resource_name",
        "criterion_id",
        "customer_resource_name",
        "campaign_resource_name",
        "source_type",
        "source_status",
        "geo_target_constant_resource_name",
    ),
    "assets": (
        "asset_resource_name",
        "asset_id",
        "customer_resource_name",
        "name",
        "source_type",
    ),
    "asset_attachments": (
        "asset_attachment_resource_name",
        "customer_resource_name",
        "attachment_scope",
        "attached_to_resource_name",
        "asset_resource_name",
        "field_type",
        "source_status",
    ),
    "geo_target_constants": (
        "customer_resource_name",
        "geo_target_constant_resource_name",
        "criterion_id",
        "name",
        "canonical_name",
        "country_code",
        "target_type",
        "source_status",
    ),
}


class SupportingEntitiesSource(Protocol):
    """Fetch Google Ads SearchStream response batches for the configured account."""

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return SearchStream batches for one query."""


class SupportingEntitiesImportError(ValueError):
    """Google Ads returned incomplete or unlinked supporting dimension data."""


@dataclass(frozen=True)
class SupportingEntitiesImportResult:
    """Counts of supporting dimension snapshots committed by an import."""

    keyword_criteria: int
    ad_group_criteria: int
    campaign_criteria: int
    assets: int
    asset_attachments: int
    geo_target_constants: int


def import_supporting_entities(
    connection: duckdb.DuckDBPyConnection, source: SupportingEntitiesSource
) -> SupportingEntitiesImportResult:
    """Fetch and atomically replace supporting dimensions for one core account."""
    customer_resource_name = _customer_resource_name(
        source.search_stream(CUSTOMER_QUERY)
    )
    snapshots = {
        "keyword_criteria": _parse_keywords(
            source.search_stream(KEYWORD_QUERY), customer_resource_name
        ),
        "ad_group_criteria": _parse_ad_group_criteria(
            source.search_stream(AD_GROUP_CRITERIA_QUERY), customer_resource_name
        ),
        "campaign_criteria": _parse_campaign_criteria(
            source.search_stream(CAMPAIGN_CRITERIA_QUERY), customer_resource_name
        ),
        "assets": _parse_assets(
            source.search_stream(ASSET_QUERY), customer_resource_name
        ),
        "asset_attachments": _parse_asset_attachments(
            source.search_stream(CUSTOMER_ASSET_QUERY),
            customer_resource_name,
            "customer",
        )
        + _parse_asset_attachments(
            source.search_stream(CAMPAIGN_ASSET_QUERY),
            customer_resource_name,
            "campaign",
        )
        + _parse_asset_attachments(
            source.search_stream(AD_GROUP_ASSET_QUERY),
            customer_resource_name,
            "ad_group",
        ),
        "geo_target_constants": _parse_geo_targets(
            source.search_stream(GEO_TARGET_QUERY), customer_resource_name
        ),
    }
    _validate_snapshot_relationships(snapshots)
    replace_supporting_entity_snapshots(connection, customer_resource_name, snapshots)
    return SupportingEntitiesImportResult(
        *(len(snapshots[name]) for name in _ENTITY_COLUMNS)
    )


def replace_supporting_entity_snapshots(
    connection: duckdb.DuckDBPyConnection,
    customer_resource_name: str,
    snapshots: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    """Stage and replace a complete supporting-entity account snapshot atomically."""
    if set(snapshots) != set(_ENTITY_COLUMNS):
        raise ValueError("snapshots must contain every supporting entity")
    staged: dict[str, str] = {}
    connection.execute("BEGIN TRANSACTION")
    try:
        for table, rows in snapshots.items():
            staged[table] = _stage_snapshot(
                connection, table, _ENTITY_COLUMNS[table], rows, customer_resource_name
            )
        _validate_core_relationships(connection, customer_resource_name, snapshots)
        for table in _ENTITY_COLUMNS:
            connection.execute(
                f"DELETE FROM {_quote(table)} WHERE customer_resource_name = ?",
                [customer_resource_name],
            )
        for table, columns in _ENTITY_COLUMNS.items():
            quoted_columns = ", ".join(_quote(column) for column in columns)
            connection.execute(
                f"INSERT INTO {_quote(table)} ({quoted_columns}) "
                f"SELECT {quoted_columns} FROM {_quote(staged[table])}"
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        for staging_table in staged.values():
            connection.execute(f"DROP TABLE IF EXISTS {_quote(staging_table)}")


def _customer_resource_name(batches: Iterable[object]) -> str:
    customers = [_required(row.customer, "resource_name") for row in _results(batches)]
    if len(customers) != 1:
        raise SupportingEntitiesImportError(
            "Google Ads must return exactly one customer."
        )
    return customers[0]


def _parse_keywords(
    batches: Iterable[object], customer: str
) -> list[dict[str, object]]:
    return [
        {
            "ad_group_criterion_resource_name": _required(
                row.ad_group_criterion, "resource_name"
            ),
            "criterion_id": _required_int(row.ad_group_criterion, "criterion_id"),
            "customer_resource_name": customer,
            "ad_group_resource_name": _required(row.ad_group_criterion, "ad_group"),
            "source_status": _required(row.ad_group_criterion, "status"),
            "keyword_text": _required(row.ad_group_criterion.keyword, "text"),
            "match_type": _required(row.ad_group_criterion.keyword, "match_type"),
        }
        for row in _results(batches)
    ]


def _parse_ad_group_criteria(
    batches: Iterable[object], customer: str
) -> list[dict[str, object]]:
    return [
        {
            "ad_group_criterion_resource_name": _required(
                row.ad_group_criterion, "resource_name"
            ),
            "criterion_id": _required_int(row.ad_group_criterion, "criterion_id"),
            "customer_resource_name": customer,
            "ad_group_resource_name": _required(row.ad_group_criterion, "ad_group"),
            "source_type": _required(row.ad_group_criterion, "type"),
            "source_status": _required(row.ad_group_criterion, "status"),
        }
        for row in _results(batches)
    ]


def _parse_campaign_criteria(
    batches: Iterable[object], customer: str
) -> list[dict[str, object]]:
    return [
        {
            "campaign_criterion_resource_name": _required(
                row.campaign_criterion, "resource_name"
            ),
            "criterion_id": _required_int(row.campaign_criterion, "criterion_id"),
            "customer_resource_name": customer,
            "campaign_resource_name": _required(row.campaign_criterion, "campaign"),
            "source_type": _required(row.campaign_criterion, "type"),
            "source_status": _required(row.campaign_criterion, "status"),
            "geo_target_constant_resource_name": _optional_text(
                getattr(row.campaign_criterion, "location", None), "geo_target_constant"
            ),
        }
        for row in _results(batches)
    ]


def _parse_assets(batches: Iterable[object], customer: str) -> list[dict[str, object]]:
    return [
        {
            "asset_resource_name": _required(row.asset, "resource_name"),
            "asset_id": _required_int(row.asset, "id"),
            "customer_resource_name": customer,
            "name": _optional_text(row.asset, "name"),
            "source_type": _required(row.asset, "type"),
        }
        for row in _results(batches)
    ]


def _parse_asset_attachments(
    batches: Iterable[object], customer: str, scope: str
) -> list[dict[str, object]]:
    parent_field = {"customer": None, "campaign": "campaign", "ad_group": "ad_group"}[
        scope
    ]
    return [
        {
            "asset_attachment_resource_name": _required(
                getattr(row, f"{scope}_asset"), "resource_name"
            ),
            "customer_resource_name": customer,
            "attachment_scope": scope,
            "attached_to_resource_name": customer
            if parent_field is None
            else _required(getattr(row, f"{scope}_asset"), parent_field),
            "asset_resource_name": _required(getattr(row, f"{scope}_asset"), "asset"),
            "field_type": _required(getattr(row, f"{scope}_asset"), "field_type"),
            "source_status": _required(getattr(row, f"{scope}_asset"), "status"),
        }
        for row in _results(batches)
    ]


def _parse_geo_targets(
    batches: Iterable[object], customer: str
) -> list[dict[str, object]]:
    return [
        {
            "customer_resource_name": customer,
            "geo_target_constant_resource_name": _required(
                row.geo_target_constant, "resource_name"
            ),
            "criterion_id": _required_int(row.geo_target_constant, "id"),
            "name": _required(row.geo_target_constant, "name"),
            "canonical_name": _required(row.geo_target_constant, "canonical_name"),
            "country_code": _required(row.geo_target_constant, "country_code"),
            "target_type": _required(row.geo_target_constant, "target_type"),
            "source_status": _required(row.geo_target_constant, "status"),
        }
        for row in _results(batches)
    ]


def _validate_snapshot_relationships(
    snapshots: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    asset_names = {row["asset_resource_name"] for row in snapshots["assets"]}
    geo_names = {
        row["geo_target_constant_resource_name"]
        for row in snapshots["geo_target_constants"]
    }
    if any(
        row["asset_resource_name"] not in asset_names
        for row in snapshots["asset_attachments"]
    ):
        raise SupportingEntitiesImportError(
            "Asset attachment refers to a missing asset."
        )
    if any(
        row["geo_target_constant_resource_name"] not in geo_names
        for row in snapshots["campaign_criteria"]
        if row["geo_target_constant_resource_name"] is not None
    ):
        raise SupportingEntitiesImportError(
            "Campaign criterion refers to missing geographic metadata."
        )


def _validate_core_relationships(
    connection: duckdb.DuckDBPyConnection,
    customer: str,
    snapshots: Mapping[str, Sequence[Mapping[str, object]]],
) -> None:
    customer_rows = connection.execute(
        "SELECT customer_resource_name FROM customers WHERE customer_resource_name = ?",
        [customer],
    ).fetchall()
    if len(customer_rows) != 1:
        raise SupportingEntitiesImportError(
            "Supporting entities require an imported core customer."
        )
    ad_groups = _resource_names(
        connection, "ad_groups", "ad_group_resource_name", customer
    )
    campaigns = _resource_names(
        connection, "campaigns", "campaign_resource_name", customer
    )
    if any(
        row["ad_group_resource_name"] not in ad_groups
        for name in ("keyword_criteria", "ad_group_criteria")
        for row in snapshots[name]
    ):
        raise SupportingEntitiesImportError(
            "Ad group criterion refers to a missing ad group."
        )
    if any(
        row["campaign_resource_name"] not in campaigns
        for row in snapshots["campaign_criteria"]
    ):
        raise SupportingEntitiesImportError(
            "Campaign criterion refers to a missing campaign."
        )
    for row in snapshots["asset_attachments"]:
        scope = row["attachment_scope"]
        parent = row["attached_to_resource_name"]
        if (
            (scope == "customer" and parent != customer)
            or (scope == "campaign" and parent not in campaigns)
            or (scope == "ad_group" and parent not in ad_groups)
        ):
            raise SupportingEntitiesImportError(
                "Asset attachment refers to a missing core entity."
            )


def _resource_names(
    connection: duckdb.DuckDBPyConnection, table: str, column: str, customer: str
) -> set[object]:
    return {
        row[0]
        for row in connection.execute(
            f"SELECT {_quote(column)} FROM {_quote(table)} "
            "WHERE customer_resource_name = ?",
            [customer],
        ).fetchall()
    }


def _stage_snapshot(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
    customer: str,
) -> str:
    values: list[tuple[object, ...]] = []
    for row in rows:
        if set(row) != set(columns):
            raise ValueError("each row must contain exactly the supplied columns")
        if row["customer_resource_name"] != customer:
            raise ValueError("staged rows must match the requested customer")
        values.append(tuple(row[column] for column in columns))
    staging_table = f"_staging_{uuid4().hex}"
    quoted_columns = ", ".join(_quote(column) for column in columns)
    connection.execute(
        f"CREATE TEMP TABLE {_quote(staging_table)} AS "
        f"SELECT {quoted_columns} FROM {_quote(table)} WHERE FALSE"
    )
    if values:
        connection.executemany(
            f"INSERT INTO {_quote(staging_table)} ({quoted_columns}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
    return staging_table


def _results(batches: Iterable[object]) -> Iterable[Any]:
    for batch in batches:
        yield from getattr(batch, "results", ())


def _required(value: object, name: str) -> str:
    text = _optional_text(value, name)
    if text is None:
        raise SupportingEntitiesImportError(f"Google Ads returned no valid {name}.")
    return text


def _optional_text(value: object, name: str) -> str | None:
    item = getattr(value, name, None)
    return None if item is None or not str(item) else str(item)


def _required_int(value: object, name: str) -> int:
    try:
        return int(getattr(value, name))
    except (TypeError, ValueError):
        raise SupportingEntitiesImportError(
            f"Google Ads returned no valid {name}."
        ) from None


def _quote(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise ValueError(
            "identifiers must contain only letters, numbers, and underscores"
        )
    return f'"{identifier}"'
