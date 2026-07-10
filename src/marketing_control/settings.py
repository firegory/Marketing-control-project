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

    @classmethod
    def for_user(
        cls,
        application_name: str,
        *,
        environment: Mapping[str, str] | None = None,
        platform: str | None = None,
    ) -> AppPaths:
        """Resolve per-user paths without creating them."""
        if not application_name or application_name.isspace():
            raise ValueError("application_name must not be empty")

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

        home = Path(environment.get("HOME", str(Path.home())))
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
        if not self.application_name or self.application_name.isspace():
            raise ValueError("application_name must not be empty")

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
        raise ValueError(f"{name} must be set on Windows")
    return Path(value)
