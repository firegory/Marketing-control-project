"""Smoke tests for the package foundation."""

from pytest import CaptureFixture

from marketing_control import __version__, main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_entry_point(capsys: CaptureFixture[str]) -> None:
    main()

    assert capsys.readouterr().out == "Marketing Control foundation is ready.\n"
