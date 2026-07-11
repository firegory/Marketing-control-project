"""Tests for the local FastAPI shell."""

from fastapi.testclient import TestClient

from marketing_control.app import create_app


def test_root_renders_the_server_side_application_shell() -> None:
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "<h1>Marketing Control</h1>" in response.text


def test_health_reports_ready() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
