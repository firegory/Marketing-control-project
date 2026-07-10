"""Tests for safe local log output."""

import logging
import stat
from pathlib import Path

from marketing_control.logging import configure_logging, redact_sensitive_values
from marketing_control.settings import Settings


def test_redact_sensitive_values_removes_supported_credentials() -> None:
    message = (
        "token=oauth-value password: hunter2 developer_token dev-value "
        'authorization="Bearer secret-value" {"client_secret": "json-secret"}'
    )

    redacted = redact_sensitive_values(message)

    assert "oauth-value" not in redacted
    assert "hunter2" not in redacted
    assert "dev-value" not in redacted
    assert "secret-value" not in redacted
    assert "json-secret" not in redacted
    assert redacted.count("[REDACTED]") == 5


def test_redact_sensitive_values_removes_bearer_tokens() -> None:
    redacted = redact_sensitive_values("Authorization: Bearer access-token")

    assert "access-token" not in redacted
    assert redacted == "Authorization: [REDACTED]"


def test_configure_logging_preserves_other_handlers(tmp_path: Path) -> None:
    settings = Settings.load(
        "MarketingControl", environment={"HOME": str(tmp_path)}, platform="linux"
    )
    logger_name = "marketing_control.handler_ownership"
    logger = logging.getLogger(logger_name)
    other_handler = logging.NullHandler()
    logger.addHandler(other_handler)

    configure_logging(settings, logger_name=logger_name)
    configure_logging(settings, logger_name=logger_name)

    assert other_handler in logger.handlers
    file_handler_count = sum(
        isinstance(handler, logging.FileHandler) for handler in logger.handlers
    )
    assert file_handler_count == 1

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def test_configured_log_redacts_messages_and_exceptions(tmp_path: Path) -> None:
    settings = Settings.load(
        "MarketingControl", environment={"HOME": str(tmp_path)}, platform="linux"
    )
    logger = configure_logging(settings, logger_name="marketing_control.test")

    logger.info("OAuth token=message-token")
    try:
        credential = "exception-credential"
        raise RuntimeError(f"credential={credential}")
    except RuntimeError:
        logger.exception("request failed")

    for handler in logger.handlers:
        handler.close()
    logging.getLogger("marketing_control.test").handlers.clear()

    output = (settings.paths.logs / "marketing-control.log").read_text(encoding="utf-8")
    assert "message-token" not in output
    assert "exception-credential" not in output
    assert "[REDACTED]" in output
    assert stat.S_IMODE(settings.paths.logs.stat().st_mode) == 0o700
    assert (
        stat.S_IMODE((settings.paths.logs / "marketing-control.log").stat().st_mode)
        == 0o600
    )
