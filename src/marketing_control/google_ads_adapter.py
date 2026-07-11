"""Secret-safe access to Google Ads SearchStream for the configured account."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
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
from google.auth.exceptions import RefreshError

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


class GoogleAdsConnectionState(StrEnum):
    """The safe, user-actionable state of the configured Ads connection."""

    USABLE = "usable"
    INVALID_AUTHORIZATION = "invalid_authorization"
    NOT_CONFIGURED = "not_configured"
    TEMPORARY_FAILURE = "temporary_failure"


@dataclass(frozen=True)
class GoogleAdsConnectionStatus:
    """Connection validation result, containing no credentials or error details."""

    state: GoogleAdsConnectionState
    customer_id: str | None = None
    customer_name: str | None = None
    currency_code: str | None = None
    time_zone: str | None = None


class GoogleAdsConnectionValidator(Protocol):
    """Validate the configured Google Ads customer connection."""

    def connection_status(self) -> GoogleAdsConnectionStatus:
        """Return a safe, typed connection validation result."""


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

    def connection_status(self) -> GoogleAdsConnectionStatus:
        """Validate authorization and load the configured customer's safe identity."""
        try:
            client, metadata = self._create_client(require_refresh_token=True)
        except (_NotConfiguredError, ValueError):
            return GoogleAdsConnectionStatus(GoogleAdsConnectionState.NOT_CONFIGURED)
        except _TemporaryConnectionError:
            return GoogleAdsConnectionStatus(GoogleAdsConnectionState.TEMPORARY_FAILURE)

        try:
            service: GoogleAdsService = client.get_service("GoogleAdsService")
            batches = service.search_stream(
                customer_id=metadata.customer_id,
                query=(
                    "SELECT customer.id, customer.descriptive_name, "
                    "customer.currency_code, customer.time_zone FROM customer"
                ),
            )
            return _connection_status_from_batches(batches)
        except Exception as error:
            if _is_authorization_error(error):
                return GoogleAdsConnectionStatus(
                    GoogleAdsConnectionState.INVALID_AUTHORIZATION
                )
            return GoogleAdsConnectionStatus(GoogleAdsConnectionState.TEMPORARY_FAILURE)

    def _create_client(
        self, *, require_refresh_token: bool = False
    ) -> tuple[GoogleAdsClient, GoogleAdsSettings]:
        metadata = self._metadata_store.load()
        if metadata is None:
            if require_refresh_token:
                raise _NotConfiguredError
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads is not configured for this application."
            )
        try:
            client_secret = self._credential_store.get(OAUTH_CLIENT_SECRET_NAME)
            developer_token = self._credential_store.get(DEVELOPER_TOKEN_NAME)
            refresh_token = self._credential_store.get(REFRESH_TOKEN_NAME)
        except CredentialStoreError:
            if require_refresh_token:
                raise _TemporaryConnectionError from None
            raise GoogleAdsSearchStreamAdapterError(
                "Google Ads credentials could not be loaded from secure storage."
            ) from None
        if not client_secret or not developer_token or (
            require_refresh_token and not refresh_token
        ):
            if require_refresh_token:
                raise _NotConfiguredError
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
            if require_refresh_token:
                raise _NotConfiguredError from None
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


class _NotConfiguredError(RuntimeError):
    """Internal signal for a validation request missing required local setup."""


class _TemporaryConnectionError(RuntimeError):
    """Internal signal for a local failure that may recover without user action."""


def _is_authorization_error(error: Exception) -> bool:
    """Recognize only explicit authorization failures without exposing their details."""
    if isinstance(error, RefreshError):
        return True
    if isinstance(error, GoogleAdsException):
        return _rpc_status_is_authorization_failure(error.error)
    if isinstance(error, grpc.RpcError):
        return _rpc_status_is_authorization_failure(error)
    return False


def _rpc_status_is_authorization_failure(error: grpc.RpcError) -> bool:
    try:
        return error.code() == grpc.StatusCode.UNAUTHENTICATED
    except Exception:
        return False


def _connection_status_from_batches(
    batches: Iterable[object],
) -> GoogleAdsConnectionStatus:
    """Extract one customer row while treating malformed responses as temporary."""
    for batch in batches:
        results = getattr(batch, "results", ())
        for result in results:
            customer = getattr(result, "customer", None)
            customer_id = _customer_value(customer, "id")
            customer_name = _customer_value(customer, "descriptive_name")
            currency_code = _customer_value(customer, "currency_code")
            time_zone = _customer_value(customer, "time_zone")
            if all((customer_id, customer_name, currency_code, time_zone)):
                return GoogleAdsConnectionStatus(
                    GoogleAdsConnectionState.USABLE,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    currency_code=currency_code,
                    time_zone=time_zone,
                )
    return GoogleAdsConnectionStatus(GoogleAdsConnectionState.TEMPORARY_FAILURE)


def _customer_value(customer: object, name: str) -> str | None:
    value = getattr(customer, name, None)
    if isinstance(value, (str, int)) and str(value):
        return str(value)
    return None


def _rpc_status_is_transient(error: grpc.RpcError) -> bool:
    try:
        return error.code() in _RETRYABLE_STATUS_CODES
    except Exception:
        return False


def _full_jitter(delay_cap: float) -> float:
    return random.uniform(0, delay_cap)
