"""FastAPI application factory for the local UI."""

import hmac
import secrets
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Annotated, Callable, cast

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from marketing_control.credentials import (
    CredentialStore,
    CredentialStoreError,
    create_credential_store,
)
from marketing_control.data_history import ReportHistory, data_history
from marketing_control.google_ads import (
    DEVELOPER_TOKEN_NAME,
    OAUTH_CLIENT_SECRET_NAME,
    REFRESH_TOKEN_NAME,
    GoogleAdsSettings,
    GoogleAdsSettingsStore,
    normalize_customer_id,
    normalize_optional_customer_id,
)
from marketing_control.google_ads_adapter import (
    GoogleAdsConnectionValidator,
    GoogleAdsSearchStreamAdapter,
)
from marketing_control.initial_history import (
    HistoryPreset,
    ads_history_boundary,
    select_initial_history,
)
from marketing_control.oauth import (
    DesktopOAuthAuthorizer,
    GoogleDesktopOAuthAuthorizer,
    OAuthAuthorizationDeniedError,
    OAuthCallbackError,
    OAuthCancelledError,
    OAuthTokenExchangeError,
)
from marketing_control.settings import Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import HistoryPreference, SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry, SyncRunCoordinator
from marketing_control.sync_planning import DateRange

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    *,
    settings: Settings | None = None,
    credential_store: CredentialStore | None = None,
    oauth_authorizer: DesktopOAuthAuthorizer | None = None,
    connection_validator: GoogleAdsConnectionValidator | None = None,
    report_registry: ReportTaskRegistry | None = None,
    today: Callable[[], date] | None = None,
) -> FastAPI:
    """Create the loopback-only application's HTTP interface."""
    app = FastAPI(title="Marketing Control", docs_url=None, redoc_url=None)
    settings = Settings.load() if settings is None else settings
    metadata_store = GoogleAdsSettingsStore(settings)
    credential_store = (
        create_credential_store() if credential_store is None else credential_store
    )
    oauth_authorizer = (
        GoogleDesktopOAuthAuthorizer() if oauth_authorizer is None else oauth_authorizer
    )
    connection_validator = (
        GoogleAdsSearchStreamAdapter(metadata_store, credential_store)
        if connection_validator is None
        else connection_validator
    )
    today = date.today if today is None else today
    report_registry = (
        ReportTaskRegistry(()) if report_registry is None else report_registry
    )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        """Render the initial local application shell."""
        return _templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report that the local process can serve requests."""
        return {"status": "ok"}

    @app.get("/sync/initial-history", response_class=HTMLResponse)
    def initial_history(request: Request) -> HTMLResponse:
        """Render the pre-sync initial-history selection screen."""
        with database_connection(settings) as connection:
            preference = SyncRepository(connection).get_history_preference()
        return _initial_history_response(
            request,
            today=today(),
            saved_preference=preference
            if preference and preference.kind == "initial"
            else None,
        )

    @app.post("/sync/initial-history", response_class=HTMLResponse)
    def save_initial_history(
        request: Request,
        csrf_token: Annotated[str, Form()],
        preset: Annotated[str, Form()],
        custom_start_date: Annotated[str, Form()] = "",
        custom_end_date: Annotated[str, Form()] = "",
    ) -> Response:
        """Persist a valid initial range without starting synchronization."""
        if not hmac.compare_digest(csrf_token, request.cookies.get("history_csrf", "")):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        form = {
            "preset": preset,
            "custom_start_date": custom_start_date,
            "custom_end_date": custom_end_date,
        }
        current_date = today()
        try:
            selection = select_initial_history(
                cast(HistoryPreset, preset),
                today=current_date,
                custom_start_date=(
                    _parse_date(custom_start_date, "Start date")
                    if preset == "custom"
                    else None
                ),
                custom_end_date=(
                    _parse_date(custom_end_date, "End date")
                    if preset == "custom"
                    else None
                ),
            )
        except ValueError as error:
            return _initial_history_response(
                request,
                today=current_date,
                error=str(error),
                form=form,
                status_code=422,
            )

        with database_connection(settings) as connection:
            SyncRepository(connection).save_history_preference(
                "initial", selection.start_date, selection.end_date
            )
        return RedirectResponse(
            "/sync/initial-history", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/settings/data-history", response_class=HTMLResponse)
    def data_history_settings(request: Request) -> HTMLResponse:
        """Render retained coverage and backfill planning without synchronizing."""
        with database_connection(settings) as connection:
            repository = SyncRepository(connection)
            preference = repository.get_history_preference()
            requested_range = (
                DateRange(
                    preference.requested_start_date, preference.requested_end_date
                )
                if preference and preference.kind == "backfill"
                else None
            )
            reports = data_history(repository, requested_range)
        return _data_history_response(
            request,
            today=today(),
            saved_preference=(
                preference if preference and preference.kind == "backfill" else None
            ),
            reports=reports,
        )

    @app.post("/settings/data-history", response_class=HTMLResponse)
    def save_data_history(
        request: Request,
        csrf_token: Annotated[str, Form()],
        preset: Annotated[str, Form()],
        custom_start_date: Annotated[str, Form()] = "",
        custom_end_date: Annotated[str, Form()] = "",
    ) -> Response:
        """Save a validated backfill preference without deleting or syncing data."""
        if not hmac.compare_digest(
            csrf_token, request.cookies.get("data_history_csrf", "")
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        form = {
            "preset": preset,
            "custom_start_date": custom_start_date,
            "custom_end_date": custom_end_date,
        }
        current_date = today()
        try:
            selection = select_initial_history(
                cast(HistoryPreset, preset),
                today=current_date,
                custom_start_date=(
                    _parse_date(custom_start_date, "Start date")
                    if preset == "custom"
                    else None
                ),
                custom_end_date=(
                    _parse_date(custom_end_date, "End date")
                    if preset == "custom"
                    else None
                ),
            )
        except ValueError as error:
            with database_connection(settings) as connection:
                reports = data_history(SyncRepository(connection))
            return _data_history_response(
                request,
                today=current_date,
                reports=reports,
                error=str(error),
                form=form,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        with database_connection(settings) as connection:
            SyncRepository(connection).save_history_preference(
                "backfill", selection.start_date, selection.end_date
            )
        return RedirectResponse(
            "/settings/data-history", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.get("/sync/runs", response_class=HTMLResponse)
    def sync_runs(request: Request) -> HTMLResponse:
        """Render persisted latest-run progress without initiating any work."""
        with database_connection(settings) as connection:
            repository = SyncRepository(connection)
            run = repository.latest_run()
            work_items = [] if run is None else repository.list_report_runs(run.id)
        return _sync_runs_response(request, run, work_items, len(report_registry.tasks))

    @app.post("/sync/runs")
    def start_sync_run(
        request: Request, csrf_token: Annotated[str, Form()]
    ) -> RedirectResponse:
        """Synchronously start the saved history range through injected report tasks."""
        _verify_sync_csrf(request, csrf_token)
        if not report_registry.tasks:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No report tasks are configured.",
            )
        with database_connection(settings) as connection:
            repository = SyncRepository(connection)
            preference = repository.get_history_preference()
            if preference is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        "Save an initial history or backfill range "
                        "before starting sync."
                    ),
                )
            try:
                SyncRunCoordinator(repository, report_registry).start(
                    DateRange(
                        preference.requested_start_date, preference.requested_end_date
                    )
                )
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from None
        return RedirectResponse("/sync/runs", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/sync/runs/{sync_run_id}/retry")
    def retry_sync_run(
        request: Request, sync_run_id: str, csrf_token: Annotated[str, Form()]
    ) -> RedirectResponse:
        """Synchronously retry only report work that failed in the selected run."""
        _verify_sync_csrf(request, csrf_token)
        with database_connection(settings) as connection:
            try:
                SyncRunCoordinator(
                    SyncRepository(connection), report_registry
                ).retry_failed(sync_run_id)
            except KeyError:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(error)
                ) from None
        return RedirectResponse("/sync/runs", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/settings/google-ads", response_class=HTMLResponse)
    def google_ads_settings(
        request: Request, oauth_status: str | None = None
    ) -> HTMLResponse:
        """Render the one-account Google Ads credential onboarding form."""
        csrf_token = secrets.token_urlsafe()
        response = _templates.TemplateResponse(
            request=request,
            name="google_ads_onboarding.html",
            context={
                "csrf_token": csrf_token,
                "configured": metadata_store.load() is not None,
                "connection": connection_validator.connection_status(),
                "oauth_message": _oauth_message(oauth_status),
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

    @app.post("/settings/google-ads/authorize")
    def authorize_google_ads(
        request: Request, csrf_token: Annotated[str, Form()]
    ) -> RedirectResponse:
        """Start a browser-based desktop OAuth flow for saved Google Ads settings."""
        cookie_token = request.cookies.get("google_ads_csrf", "")
        if not hmac.compare_digest(csrf_token, cookie_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        configured = metadata_store.load()
        if configured is None:
            return _oauth_redirect("not-configured")
        try:
            client_secret = credential_store.get(OAUTH_CLIENT_SECRET_NAME)
        except CredentialStoreError:
            return _oauth_redirect("storage-error")
        if not client_secret:
            return _oauth_redirect("not-configured")

        try:
            refresh_token = oauth_authorizer.authorize(
                client_id=configured.oauth_client_id, client_secret=client_secret
            )
            credential_store.save(REFRESH_TOKEN_NAME, refresh_token)
        except OAuthAuthorizationDeniedError:
            return _oauth_redirect("denied")
        except OAuthCancelledError:
            return _oauth_redirect("cancelled")
        except OAuthCallbackError:
            return _oauth_redirect("callback-error")
        except OAuthTokenExchangeError:
            return _oauth_redirect("token-error")
        except CredentialStoreError:
            return _oauth_redirect("storage-error")
        return _oauth_redirect("authorized")

    return app


def _oauth_redirect(oauth_status: str) -> RedirectResponse:
    return RedirectResponse(
        f"/settings/google-ads?oauth_status={oauth_status}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _initial_history_response(
    request: Request,
    *,
    today: date,
    saved_preference: HistoryPreference | None = None,
    error: str | None = None,
    form: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    csrf_token = secrets.token_urlsafe()
    response = _templates.TemplateResponse(
        request=request,
        name="initial_history.html",
        context={
            "csrf_token": csrf_token,
            "today": today,
            "ads_history_start": ads_history_boundary(today),
            "saved_preference": saved_preference,
            "saved_days": (
                (
                    saved_preference.requested_end_date
                    - saved_preference.requested_start_date
                ).days
                + 1
                if saved_preference
                else None
            ),
            "error": error,
            "form": form
            or {"preset": "30", "custom_start_date": "", "custom_end_date": ""},
        },
        status_code=status_code,
    )
    response.set_cookie("history_csrf", csrf_token, httponly=True, samesite="strict")
    return response


def _data_history_response(
    request: Request,
    *,
    today: date,
    reports: list[ReportHistory],
    saved_preference: HistoryPreference | None = None,
    error: str | None = None,
    form: dict[str, str] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    csrf_token = secrets.token_urlsafe()
    response = _templates.TemplateResponse(
        request=request,
        name="data_history.html",
        context={
            "csrf_token": csrf_token,
            "today": today,
            "ads_history_start": ads_history_boundary(today),
            "reports": reports,
            "saved_preference": saved_preference,
            "error": error,
            "form": form
            or {"preset": "30", "custom_start_date": "", "custom_end_date": ""},
        },
        status_code=status_code,
    )
    response.set_cookie(
        "data_history_csrf", csrf_token, httponly=True, samesite="strict"
    )
    return response


def _sync_runs_response(
    request: Request, run: object | None, work_items: Sequence[object], task_count: int
) -> HTMLResponse:
    csrf_token = secrets.token_urlsafe()
    response = _templates.TemplateResponse(
        request=request,
        name="sync_runs.html",
        context={
            "csrf_token": csrf_token,
            "run": run,
            "work_items": work_items,
            "task_count": task_count,
        },
    )
    response.set_cookie("sync_runs_csrf", csrf_token, httponly=True, samesite="strict")
    return response


def _verify_sync_csrf(request: Request, csrf_token: str) -> None:
    if not hmac.compare_digest(csrf_token, request.cookies.get("sync_runs_csrf", "")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _parse_date(value: str, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} must be a valid date.") from None


def _oauth_message(oauth_status: str | None) -> tuple[str, bool] | None:
    messages = {
        "authorized": (
            "Google authorization completed. The refresh token is stored securely.",
            True,
        ),
        "denied": ("Google authorization was denied. You can try again.", False),
        "cancelled": (
            "Google authorization timed out or was cancelled. You can try again.",
            False,
        ),
        "callback-error": (
            "Google authorization callback could not be verified. You can try again.",
            False,
        ),
        "token-error": (
            "Google could not issue a refresh token. Check the OAuth client "
            "configuration and try again.",
            False,
        ),
        "storage-error": ("Secure credential storage is unavailable.", False),
        "not-configured": ("Save the Google Ads settings before authorizing.", False),
    }
    return None if oauth_status is None else messages.get(oauth_status)
