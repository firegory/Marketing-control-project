"""Manual and once-per-account-day startup refresh trigger services."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from marketing_control.google_ads import GoogleAdsSettingsStore
from marketing_control.settings import Settings
from marketing_control.storage import database_connection
from marketing_control.sync_history import SyncRepository
from marketing_control.sync_orchestration import ReportTaskRegistry, SyncRunCoordinator
from marketing_control.sync_planning import DateRange


class StartupRefreshService:
    """Run one opted-in startup refresh at the application's lifespan boundary."""

    def __init__(
        self,
        settings: Settings,
        metadata_store: GoogleAdsSettingsStore,
        report_registry: ReportTaskRegistry,
        *,
        now: Callable[[], datetime] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._metadata_store = metadata_store
        self._report_registry = report_registry
        self._now = (lambda: datetime.now(UTC)) if now is None else now
        self._logger = logging.getLogger(__name__) if logger is None else logger

    def run(self) -> None:
        """Best-effort startup work; no failure may block application readiness."""
        try:
            self._run()
        except Exception:
            self._logger.exception("Startup refresh could not be completed.")

    def _run(self) -> None:
        metadata = self._metadata_store.load()
        if (
            metadata is None
            or not metadata.time_zone
            or not self._report_registry.tasks
        ):
            return
        try:
            local_date = self._now().astimezone(  # type: ignore[no-untyped-call]
                ZoneInfo(metadata.time_zone)
            ).date()
        except ZoneInfoNotFoundError:
            self._logger.warning(
                "Saved Google Ads timezone is invalid; skipping startup refresh."
            )
            return

        with database_connection(self._settings) as connection:
            repository = SyncRepository(connection)
            preference = repository.get_history_preference()
            if not repository.startup_refresh_enabled() or preference is None:
                return
            if not repository.reserve_startup_refresh(metadata.customer_id, local_date):
                return
            try:
                run = SyncRunCoordinator(repository, self._report_registry).start(
                    DateRange(
                        preference.requested_start_date, preference.requested_end_date
                    )
                )
                repository.complete_startup_refresh(
                    metadata.customer_id,
                    local_date,
                    failure_detail=(
                        "One or more reports failed. Review report status."
                        if run.status == "failed"
                        else None
                    ),
                )
            except Exception as error:
                repository.complete_startup_refresh(
                    metadata.customer_id, local_date, failure_detail=str(error)
                )
