"""Safely start one loopback server and open it after readiness."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from threading import Thread
from typing import TextIO
from urllib.parse import urlparse

import uvicorn

from marketing_control.app import create_app
from marketing_control.logging import configure_logging
from marketing_control.settings import Settings

_HOST = "127.0.0.1"
_READINESS_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.1


class SingleInstance:
    """An advisory lock and local URL record owned by one application process."""

    def __init__(self, config_directory: Path) -> None:
        self._path = config_directory / "server.lock"
        self._handle: TextIO | None = None

    def acquire(self, url: str) -> bool:
        """Acquire the process lock and record its loopback URL."""
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        handle = self._path.open("a+", encoding="utf-8")
        os.chmod(self._path, 0o600)
        try:
            self._lock(handle)
        except OSError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        json.dump({"url": url}, handle)
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return True

    def running_url(self) -> str | None:
        """Return a valid recorded loopback URL, if an instance owns the lock."""
        try:
            value = json.loads(self._path.read_text(encoding="utf-8")).get("url")
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, str) and _is_loopback_url(value) else None

    def close(self) -> None:
        """Release the lock held by this process."""
        if self._handle is None:
            return
        self._unlock(self._handle)
        self._handle.close()
        self._handle = None

    @staticmethod
    def _lock(handle: TextIO) -> None:
        if os.name == "nt":
            import msvcrt

            # msvcrt locks an existing byte. Do not overwrite a record another
            # instance owns while preparing a newly created lock file.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(" ")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: TextIO) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_local_server(settings: Settings | None = None) -> None:
    """Run the local server, or focus the already-running local instance."""
    settings = Settings.load() if settings is None else settings
    logger = configure_logging(settings)
    listener = _bind_loopback_listener()
    port = int(listener.getsockname()[1])
    url = f"http://{_HOST}:{port}"
    instance = SingleInstance(settings.paths.config)

    if not instance.acquire(url):
        listener.close()
        existing_url = instance.running_url()
        if existing_url is not None:
            _launch_browser_after_readiness(existing_url, logger)
        else:
            logger.warning("Another Marketing Control instance is starting.")
        return

    try:
        Thread(
            target=_launch_browser_after_readiness,
            args=(url, logger),
            daemon=True,
        ).start()
        config = uvicorn.Config(create_app(), host=_HOST, port=port, log_config=None)
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()
        instance.close()


def _bind_loopback_listener() -> socket.socket:
    """Bind and retain an ephemeral loopback socket for Uvicorn to serve."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((_HOST, 0))
    return listener


def _launch_browser_after_readiness(
    url: str,
    logger: logging.Logger,
    *,
    wait_for_ready: Callable[[str], bool] | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> None:
    """Open the browser only after the local health endpoint responds."""
    if wait_for_ready is None:
        wait_for_ready = _wait_for_ready
    if not wait_for_ready(f"{url}/health"):
        logger.error("Local server did not become ready before the timeout.")
        return
    if not browser_open(url):
        logger.warning("Could not open the default browser for the local server.")


def _wait_for_ready(health_url: str) -> bool:
    """Poll a local health endpoint until it is ready or times out."""
    deadline = time.monotonic() + _READINESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(_POLL_INTERVAL_SECONDS)
    return False


def _is_loopback_url(value: str) -> bool:
    """Prevent a stale lock file from directing a browser to a remote URL."""
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname == _HOST and port is not None
