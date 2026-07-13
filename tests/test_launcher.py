"""Tests for local server lifecycle coordination."""

import logging
import os
from pathlib import Path

from marketing_control.launcher import (
    SingleInstance,
    _bind_loopback_listener,
    _launch_browser_after_readiness,
)


def test_listener_is_bound_to_an_ephemeral_loopback_port() -> None:
    listener = _bind_loopback_listener()
    try:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        listener.close()


def test_second_instance_cannot_acquire_the_local_server_lock(tmp_path: Path) -> None:
    first = SingleInstance(tmp_path)
    second = SingleInstance(tmp_path)
    url = "http://127.0.0.1:51234"

    assert first.acquire(url)
    assert not second.acquire("http://127.0.0.1:51235")
    if os.name != "nt":
        assert second.running_url() == url

    first.close()
    assert second.acquire("http://127.0.0.1:51235")
    second.close()


def test_browser_launch_waits_for_health_readiness() -> None:
    events: list[str] = []

    def wait_for_ready(health_url: str) -> bool:
        events.append(health_url)
        return True

    def browser_open(url: str) -> bool:
        events.append(url)
        return True

    _launch_browser_after_readiness(
        "http://127.0.0.1:51234",
        logging.getLogger("test"),
        wait_for_ready=wait_for_ready,
        browser_open=browser_open,
    )

    assert events == ["http://127.0.0.1:51234/health", "http://127.0.0.1:51234"]
