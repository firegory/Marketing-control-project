"""Secret-safe access to Google Ads SearchStream for the configured account."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from typing import Protocol

import grpc
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.api_core.exceptions import (
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
)

from marketing_control.credentials import CredentialStore, CredentialStoreError
from marketing_control.google_ads import (
    DEVELOPER_TOKEN_NAME,
    OAUTH_CLIENT_SECRET_NAME,
    REFRESH_TOKEN_NAME,
    GoogleAdsSettings,
    GoogleAdsSettingsStore,
)

_RETRYABLE_STATUS_CODES = frozenset(
    {
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.UNAVAILABLE,
    }
)
_RETRYABLE_TRANSPORT_ERRORS = (
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
)


class GoogleAdsSearchStreamAdapterError(RuntimeError):
    """An actionable Google Ads adapter error that never contains secrets."""


class GoogleAdsService(Protocol):
    """The minimal Google Ads service interface used by this adapter."""

    def search_stream(self, *, customer_id: str, query: str) -> Iterable[object]:
        """Start a SearchStream request for one customer."""


class GoogleAdsClientFactory(Protocol):
    """Create a configured Google Ads client."""

    def __call__(self, config: dict[str, object]) -> GoogleAdsClient:
        """Create a client from its non-persisted runtime configuration."""


class GoogleAdsSearchStreamAdapter:
    """Execute SearchStream requests for exactly the configured customer account."""

    def __init__(
        self,
        metadata_store: GoogleAdsSettingsStore,
        credential_store: CredentialStore,
        *,
        client_factory: GoogleAdsClientFactory = GoogleAdsClient.load_from_dict,
        max_attempts: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 8.0,
        jitter: Callable[[float], float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure bounded retry dependencies without contacting Google Ads."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if base_delay_seconds <= 0 or max_delay_seconds <= 0:
            raise ValueError("retry delays must be positive")
        self._metadata_store = metadata_store
        self._credential_store = credential_store
        self._client_factory = client_factory
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._jitter: Callable[[float], float] = (
            _full_jitter if jitter is None else jitter
        )
        self._sleep = sleep

    def search_stream(self, query: str) -> tuple[object, ...]:
        """Return all SearchStream batches, retrying only known transient failures."""
        if not query.strip():
            raise ValueError("Google Ads SearchStream query must not be empty")

        client, metadata = self._create_client()
        service: GoogleAdsService = client.get_service("GoogleAdsService")
        for attempt in range(self._max_attempts):
            try:
                return tuple(
                    service.search_stream(customer_id=metadata.customer_id, query=query)
                )
            except Exception as error:
                if not _is_transient_google_ads_error(error):
                    raise GoogleAdsSearchStreamAdapterError(
                        "Google Ads rejected the request. Check the customer ID, "
                        "credentials, developer token, and query."
                    ) from None
                if attempt == self._max_attempts - 1:
                    raise GoogleAdsSearchStreamAdapterError(
                        "Google Ads remained temporarily unavailable after "
                        f"{self._max_attempts} attempts. Try again later."
                    ) from None
                self._sleep(self._retry_delay(attempt))

        raise AssertionError("unreachable")

    def _create_client(self) -> tuple[GoogleAdsClient, GoogleAdsSettings]:
        metadata = self._metadata_store.load()
        if metadata is None:
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads is not configured for this application."
            )
        try:
            client_secret = self._credential_store.get(OAUTH_CLIENT_SECRET_NAME)
            developer_token = self._credential_store.get(DEVELOPER_TOKEN_NAME)
            refresh_token = self._credential_store.get(REFRESH_TOKEN_NAME)
        except CredentialStoreError:
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads credentials could not be loaded from secure storage."
            ) from None
        if not client_secret or not developer_token:
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads credentials are incomplete in secure storage."
            )

        config: dict[str, object] = {
            "developer_token": developer_token,
            "client_id": metadata.oauth_client_id,
            "client_secret": client_secret,
            "use_proto_plus": True,
        }
        if metadata.login_customer_id is not None:
            config["login_customer_id"] = metadata.login_customer_id
        if refresh_token:
            config["refresh_token"] = refresh_token
        try:
            return self._client_factory(config), metadata
        except Exception:
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads client configuration is invalid. Check the stored settings "
                "and credentials."
            ) from None

    def _retry_delay(self, attempt: int) -> float:
        cap = min(self._max_delay_seconds, self._base_delay_seconds * (2**attempt))
        delay = self._jitter(cap)
        if delay < 0:
            return 0.0
        return cap if delay > cap else delay


def _is_transient_google_ads_error(error: Exception) -> bool:
    """Recognize only explicit Google Ads and transport retry conditions."""
    if isinstance(error, GoogleAdsException):
        return _rpc_status_is_transient(error.error)
    if isinstance(error, grpc.RpcError):
        return _rpc_status_is_transient(error)
    return isinstance(error, _RETRYABLE_TRANSPORT_ERRORS)


def _rpc_status_is_transient(error: grpc.RpcError) -> bool:
    try:
        return error.code() in _RETRYABLE_STATUS_CODES
    except Exception:
        return False


def _full_jitter(delay_cap: float) -> float:
    return random.uniform(0, delay_cap)
