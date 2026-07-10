"""Tests for cross-platform settings and filesystem path resolution."""

from pathlib import Path

import pytest

from marketing_control.settings import AppPaths, Settings


def test_windows_paths_use_per_user_roaming_and_local_directories() -> None:
    paths = AppPaths.for_user(
        "MarketingControl",
        environment={
            "APPDATA": r"C:\Users\Ada\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\Ada\AppData\Local",
        },
        platform="win32",
    )

    roaming = Path(r"C:\Users\Ada\AppData\Roaming")
    local = Path(r"C:\Users\Ada\AppData\Local")
    assert paths.config == roaming / "MarketingControl"
    assert paths.data == local / "MarketingControl" / "Data"
    assert paths.logs == local / "MarketingControl" / "Logs"


@pytest.mark.parametrize("variable", ["APPDATA", "LOCALAPPDATA"])
def test_windows_paths_require_known_folder_environment(variable: str) -> None:
    environment = {"APPDATA": "C:/Roaming", "LOCALAPPDATA": "C:/Local"}
    del environment[variable]

    with pytest.raises(ValueError, match=variable):
        AppPaths.for_user("MarketingControl", environment=environment, platform="win32")


def test_settings_validate_application_name_and_expose_paths() -> None:
    with pytest.raises(ValueError, match="application_name"):
        Settings.load(" ", environment={"HOME": "/home/ada"}, platform="linux")

    settings = Settings.load(
        "MarketingControl", environment={"HOME": "/home/ada"}, platform="linux"
    )

    assert settings.paths.config == Path("/home/ada/.config/MarketingControl")
