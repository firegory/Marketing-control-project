"""Application settings and per-user filesystem locations."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AppPaths:
    """Resolved locations for application-owned local files."""

    data: Path
    config: Path
    logs: Path

    @property
    def exports(self) -> Path:
        """Return the configured local directory for completed data exports."""
        return self.data.parent / "Exports"

    @property
    def backups(self) -> Path:
        """Return the configured local directory for completed backup packages."""
        return self.data.parent / "Backups"

    @classmethod
    def for_user(
        cls,
        application_name: str,
        *,
        environment: Mapping[str, str] | None = None,
        platform: str | None = None,
    ) -> AppPaths:
        """Resolve per-user paths without creating them."""
        _validate_application_name(application_name)

        environment = os.environ if environment is None else environment
        platform = sys.platform if platform is None else platform

        if platform == "win32":
            app_data = _required_directory(environment, "APPDATA")
            local_app_data = _required_directory(environment, "LOCALAPPDATA")
            return cls(
                data=local_app_data / application_name / "Data",
                config=app_data / application_name,
                logs=local_app_data / application_name / "Logs",
            )

        home = _required_directory(environment, "HOME")
        if platform == "darwin":
            library = home / "Library"
            return cls(
                data=library / "Application Support" / application_name,
                config=library / "Application Support" / application_name,
                logs=library / "Logs" / application_name,
            )

        return cls(
            data=home / ".local" / "share" / application_name,
            config=home / ".config" / application_name,
            logs=home / ".local" / "state" / application_name / "log",
        )


@dataclass(frozen=True)
class Settings:
    """Validated runtime settings with centrally resolved filesystem paths."""

    application_name: str
    paths: AppPaths

    def __post_init__(self) -> None:
        _validate_application_name(self.application_name)

    @classmethod
    def load(
        cls,
        application_name: str = "MarketingControl",
        *,
        environment: Mapping[str, str] | None = None,
        platform: str | None = None,
    ) -> Settings:
        """Validate application settings and resolve their runtime paths."""
        return cls(
            application_name=application_name,
            paths=AppPaths.for_user(
                application_name, environment=environment, platform=platform
            ),
        )


def _required_directory(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name)
    if not value:
        raise ValueError(f"{name} must be set")
    return Path(value)


def _validate_application_name(application_name: str) -> None:
    if not application_name or application_name.isspace():
        raise ValueError("application_name must not be empty")
    if (
        application_name in {".", ".."}
        or "/" in application_name
        or "\\" in application_name
    ):
        raise ValueError("application_name must be a single path segment")
