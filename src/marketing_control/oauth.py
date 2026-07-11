"""Google desktop OAuth authorization isolated from the local UI."""

from __future__ import annotations

from typing import Protocol, cast

from google.auth.exceptions import RefreshError
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from oauthlib.oauth2.rfc6749.errors import (  # type: ignore[import-untyped]
    AccessDeniedError,
    MismatchingStateError,
    OAuth2Error,
)

GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"


class DesktopOAuthAuthorizer(Protocol):
    """Authorize the configured desktop client and return its refresh token."""

    def authorize(self, *, client_id: str, client_secret: str) -> str:
        """Run the browser and loopback authorization flow."""


class OAuthAuthorizationError(RuntimeError):
    """Base class for safe, categorized authorization failures."""


class OAuthAuthorizationDeniedError(OAuthAuthorizationError):
    """The user declined the Google consent request."""


class OAuthCallbackError(OAuthAuthorizationError):
    """The local callback could not be trusted or completed."""


class OAuthTokenExchangeError(OAuthAuthorizationError):
    """Google did not return usable credentials for the authorization code."""


class OAuthCancelledError(OAuthAuthorizationError):
    """The authorization did not complete before its local listener timed out."""


class GoogleDesktopOAuthAuthorizer:
    """Use Google's supported installed-app loopback authorization flow."""

    def authorize(self, *, client_id: str, client_secret: str) -> str:
        """Open the system browser, validate state, and exchange the callback code."""
        try:
            flow = InstalledAppFlow.from_client_config(
                {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                },
                scopes=[GOOGLE_ADS_SCOPE],
            )
        except Exception:
            raise OAuthTokenExchangeError from None
        try:
            credentials = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                authorization_prompt_message="",
                success_message="Authorization completed. You may close this window.",
                access_type="offline",
                prompt="consent",
                timeout_seconds=300,
            )
        except AccessDeniedError:
            raise OAuthAuthorizationDeniedError from None
        except MismatchingStateError:
            raise OAuthCallbackError from None
        except TimeoutError:
            raise OAuthCancelledError from None
        except RefreshError:
            raise OAuthTokenExchangeError from None
        except OAuth2Error:
            raise OAuthTokenExchangeError from None
        except Exception:
            raise OAuthCallbackError from None

        if not credentials.refresh_token:
            raise OAuthTokenExchangeError
        return cast(str, credentials.refresh_token)
