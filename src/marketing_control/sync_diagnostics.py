"""Safe, stable classifications for synchronization failures."""

from __future__ import annotations

import re
from typing import Literal

import duckdb
from google.auth.exceptions import RefreshError

from marketing_control.credentials import CredentialStoreError
from marketing_control.google_ads_adapter import GoogleAdsSearchStreamAdapterError
from marketing_control.oauth import OAuthCallbackError, OAuthTokenExchangeError

FailureCategory = Literal[
    "authentication", "api", "range", "storage", "unexpected"
]

_MESSAGE_CATEGORIES: tuple[tuple[FailureCategory, re.Pattern[str]], ...] = (
    (
        "authentication",
        re.compile(
            r"\b(authentication|authorization|oauth|refresh token|access token|"
            r"unauthenticated)\b",
            re.I,
        ),
    ),
    (
        "storage",
        re.compile(r"\b(storage|database|disk|file system|permission denied)\b", re.I),
    ),
    ("range", re.compile(r"\b(date range|start date|end date|date interval)\b", re.I)),
    ("api", re.compile(r"\b(api|google ads|grpc|rpc|http request)\b", re.I)),
)


def classify_failure(error: Exception) -> FailureCategory:
    """Return a deliberately small category without depending on error text alone."""
    if isinstance(error, (RefreshError, OAuthCallbackError, OAuthTokenExchangeError)):
        return "authentication"
    if isinstance(error, (CredentialStoreError, OSError, duckdb.Error)):
        return "storage"
    if isinstance(error, GoogleAdsSearchStreamAdapterError):
        return "api"
    for category, pattern in _MESSAGE_CATEGORIES:
        if pattern.search(str(error)):
            return category
    return "unexpected"
