"""FastAPI application factory for the local UI."""

import hmac
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from marketing_control.credentials import (
    CredentialStore,
    CredentialStoreError,
    create_credential_store,
)
from marketing_control.google_ads import (
    DEVELOPER_TOKEN_NAME,
    OAUTH_CLIENT_SECRET_NAME,
    GoogleAdsSettings,
    GoogleAdsSettingsStore,
    normalize_customer_id,
    normalize_optional_customer_id,
)
from marketing_control.settings import Settings

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    *,
    settings: Settings | None = None,
    credential_store: CredentialStore | None = None,
) -> FastAPI:
    """Create the loopback-only application's HTTP interface."""
    app = FastAPI(title="Marketing Control", docs_url=None, redoc_url=None)
    settings = Settings.load() if settings is None else settings
    metadata_store = GoogleAdsSettingsStore(settings)
    credential_store = (
        create_credential_store() if credential_store is None else credential_store
    )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        """Render the initial local application shell."""
        return _templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report that the local process can serve requests."""
        return {"status": "ok"}

    @app.get("/settings/google-ads", response_class=HTMLResponse)
    def google_ads_settings(request: Request) -> HTMLResponse:
        """Render the one-account Google Ads credential onboarding form."""
        csrf_token = secrets.token_urlsafe()
        response = _templates.TemplateResponse(
            request=request,
            name="google_ads_onboarding.html",
            context={
                "csrf_token": csrf_token,
                "configured": metadata_store.load() is not None,
            },
        )
        response.set_cookie(
            "google_ads_csrf", csrf_token, httponly=True, samesite="strict"
        )
        return response

    @app.post("/settings/google-ads")
    def save_google_ads_settings(
        request: Request,
        csrf_token: Annotated[str, Form()],
        oauth_client_id: Annotated[str, Form()],
        oauth_client_secret: Annotated[str, Form()],
        customer_id: Annotated[str, Form()],
        login_customer_id: Annotated[str, Form()],
        developer_token: Annotated[str, Form()],
    ) -> RedirectResponse:
        """Validate and securely persist onboarding details without authorizing Ads."""
        cookie_token = request.cookies.get("google_ads_csrf", "")
        if not hmac.compare_digest(csrf_token, cookie_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        if (
            not oauth_client_id.strip()
            or not oauth_client_secret.strip()
            or not developer_token.strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "OAuth client ID, client secret, and developer token are required."
                ),
            )
        try:
            normalized_customer_id = normalize_customer_id(customer_id)
            normalized_login_customer_id = normalize_optional_customer_id(
                login_customer_id
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from None

        try:
            credential_store.save(OAUTH_CLIENT_SECRET_NAME, oauth_client_secret)
            credential_store.save(DEVELOPER_TOKEN_NAME, developer_token)
            metadata_store.save(
                GoogleAdsSettings(
                    oauth_client_id=oauth_client_id.strip(),
                    customer_id=normalized_customer_id,
                    login_customer_id=normalized_login_customer_id,
                )
            )
        except CredentialStoreError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secure credential storage is unavailable.",
            ) from None

        return RedirectResponse(
            "/settings/google-ads", status_code=status.HTTP_303_SEE_OTHER
        )

    return app
