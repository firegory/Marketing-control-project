"""Source-level packaging guards for frozen application resources."""

from importlib.resources import files
from pathlib import Path


def test_runtime_templates_and_migrations_are_package_resources() -> None:
    package = files("marketing_control")

    assert package.joinpath("templates", "index.html").is_file()
    assert package.joinpath("migrations", "0001_create_schema_migrations.sql").is_file()


def test_pyinstaller_spec_collects_runtime_resources() -> None:
    spec = _project_root() / "packaging" / "marketing-control.spec"

    assert spec.is_file()
    contents = spec.read_text(encoding="utf-8")
    assert '"templates"' in contents
    assert '"migrations"' in contents
    assert 'collect_all("tzdata")' in contents
    assert 'name="Marketing Control"' in contents


def test_installer_is_per_user_and_does_not_remove_user_data() -> None:
    contents = (_project_root() / "packaging" / "marketing-control.iss").read_text(
        encoding="utf-8"
    )

    assert "PrivilegesRequired=lowest" in contents
    assert "DefaultDirName={localappdata}\\Programs" in contents
    assert "[UninstallDelete]" not in contents


def _project_root() -> Path:
    return Path(__file__).parent.parent
