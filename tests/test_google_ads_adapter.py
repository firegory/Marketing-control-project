"""Tests for Google Ads SearchStream access and retry behavior."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import grpc
import pytest
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from marketing_control.credentials import CredentialStoreError
from marketing_control.google_ads import (
    DEVELOPER_TOKEN_NAME,
    OAUTH_CLIENT_SECRET_NAME,
    REFRESH_TOKEN_NAME,
    GoogleAdsSettings,
    GoogleAdsSettingsStore,
)
from marketing_control.google_ads_adapter import (
    GoogleAdsSearchStreamAdapter,
    GoogleAdsSearchStreamAdapterError,
)
from marketing_control.settings import AppPaths, Settings


class FakeCredentialStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = {} if values is None else values

    def save(self, name: str, secret: str) -> None:
        self.values[name] = secret

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class FailingCredentialStore(FakeCredentialStore):
    def get(self, name: str) -> str | None:
        raise CredentialStoreError("credential-secret")


class FakeService:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str]] = []

    def search_stream(self, *, customer_id: str, query: str) -> Iterable[object]:
        self.calls.append((customer_id, query))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return cast(Iterable[object], outcome)


class FakeClient:
    def __init__(self, service: FakeService) -> None:
        self.service = service

    def get_service(self, name: str) -> FakeService:
        assert name == "GoogleAdsService"
        return self.service


class FakeRpcError(grpc.RpcError):
    def __init__(self, status: grpc.StatusCode) -> None:
        self._status = status

    def code(self) -> grpc.StatusCode:
        return self._status


def _google_ads_error(status: grpc.StatusCode) -> GoogleAdsException:
    error = GoogleAdsException.__new__(GoogleAdsException)
    error.error = FakeRpcError(status)
    return error


def _adapter(
    tmp_path: Path,
    service: FakeService,
    *,
    credentials: FakeCredentialStore | None = None,
    sleeps: list[float] | None = None,
    jitter: float = 0.0,
) -> tuple[GoogleAdsSearchStreamAdapter, list[dict[str, object]]]:
    settings = Settings(
        "MarketingControl",
        AppPaths(tmp_path / "data", tmp_path / "config", tmp_path / "logs"),
    )
    metadata = GoogleAdsSettingsStore(settings)
    metadata.save(
        GoogleAdsSettings(
            oauth_client_id="client-id.apps.googleusercontent.com",
            customer_id="1234567890",
            login_customer_id="9876543210",
        )
    )
    captured_configs: list[dict[str, object]] = []

    def make_client(config: dict[str, object]) -> GoogleAdsClient:
        captured_configs.append(config)
        return cast(GoogleAdsClient, FakeClient(service))

    adapter = GoogleAdsSearchStreamAdapter(
        metadata,
        credentials
        or FakeCredentialStore(
            {
                OAUTH_CLIENT_SECRET_NAME: "client-secret",
                DEVELOPER_TOKEN_NAME: "developer-token",
                REFRESH_TOKEN_NAME: "refresh-token",
            }
        ),
        client_factory=make_client,
        base_delay_seconds=2,
        max_delay_seconds=3,
        jitter=lambda _: jitter,
        sleep=(sleeps if sleeps is not None else []).append,
    )
    return adapter, captured_configs


def test_search_stream_uses_only_configured_customer_and_secure_credentials(
    tmp_path: Path,
) -> None:
    service = FakeService([["first", "second"]])
    adapter, configs = _adapter(tmp_path, service)

    assert adapter.search_stream("SELECT customer.id FROM customer") == (
        "first",
        "second",
    )
    assert configs == [
        {
            "developer_token": "developer-token",
            "client_id": "client-id.apps.googleusercontent.com",
            "client_secret": "client-secret",
            "use_proto_plus": True,
            "login_customer_id": "9876543210",
            "refresh_token": "refresh-token",
        }
    ]
    assert service.calls == [("1234567890", "SELECT customer.id FROM customer")]


def test_search_stream_omits_unavailable_refresh_token(tmp_path: Path) -> None:
    credentials = FakeCredentialStore(
        {
            OAUTH_CLIENT_SECRET_NAME: "client-secret",
            DEVELOPER_TOKEN_NAME: "developer-token",
        }
    )
    adapter, configs = _adapter(tmp_path, FakeService([[]]), credentials=credentials)

    assert adapter.search_stream("SELECT customer.id FROM customer") == ()
    assert "refresh_token" not in configs[0]


def test_search_stream_retries_transient_google_ads_errors_with_bounded_jitter(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []
    adapter, _ = _adapter(
        tmp_path,
        FakeService(
            [
                _google_ads_error(grpc.StatusCode.UNAVAILABLE),
                _google_ads_error(grpc.StatusCode.RESOURCE_EXHAUSTED),
                ["success"],
            ]
        ),
        sleeps=sleeps,
        jitter=10,
    )

    assert adapter.search_stream("SELECT customer.id FROM customer") == ("success",)
    assert sleeps == [2, 3]


@pytest.mark.parametrize(
    "status", [grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.INVALID_ARGUMENT]
)
def test_search_stream_does_not_retry_auth_or_validation_errors(
    tmp_path: Path, status: grpc.StatusCode
) -> None:
    service = FakeService([_google_ads_error(status)])
    sleeps: list[float] = []
    adapter, _ = _adapter(tmp_path, service, sleeps=sleeps)

    with pytest.raises(
        GoogleAdsSearchStreamAdapterError, match="Check the customer ID"
    ):
        adapter.search_stream("SELECT customer.id FROM customer")

    assert len(service.calls) == 1
    assert sleeps == []


def test_search_stream_does_not_retry_unknown_errors_or_expose_them(
    tmp_path: Path,
) -> None:
    service = FakeService([RuntimeError("secret-value")])
    adapter, _ = _adapter(tmp_path, service)

    with pytest.raises(GoogleAdsSearchStreamAdapterError) as raised:
        adapter.search_stream("SELECT customer.id FROM customer")

    assert "secret-value" not in str(raised.value)
    assert len(service.calls) == 1


def test_search_stream_reports_safe_final_error_after_retry_limit(
    tmp_path: Path,
) -> None:
    service = FakeService([FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 3)
    adapter, _ = _adapter(tmp_path, service)

    with pytest.raises(GoogleAdsSearchStreamAdapterError, match="after 3 attempts"):
        adapter.search_stream("SELECT customer.id FROM customer")

    assert len(service.calls) == 3


def test_search_stream_reports_safe_credential_store_error(tmp_path: Path) -> None:
    adapter, _ = _adapter(
        tmp_path, FakeService([[]]), credentials=FailingCredentialStore()
    )

    with pytest.raises(GoogleAdsSearchStreamAdapterError) as raised:
        adapter.search_stream("SELECT customer.id FROM customer")

    assert "credential-secret" not in str(raised.value)
