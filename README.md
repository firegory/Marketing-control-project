# Marketing Control Project

Minimal Python 3.12 foundation for the Marketing Control project.

## Sync orchestration

`ReportTaskRegistry` order is the documented, controlled report order. Tasks are injected by the host and receive only planned `DateRange` values, so the coordinator creates neither network clients nor report queries. Each run persists queued, running, succeeded, failed, or skipped work per report, including timestamps, progress, and bounded redacted diagnostic text. Failures do not prevent later independent tasks; a retry queues only the prior run's failed reports.

## Local setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then create
the locked development environment:

```bash
uv sync --all-groups --locked
```

## Commands

```bash
# Run the package smoke entry point
uv run marketing-control

# Run all required checks
uv run ruff check .
uv run mypy
uv run pytest
```

GitHub Actions runs the same locked install and checks for pushes and pull
requests.

## Local files and logging

On Windows, Marketing Control stores per-user files in the following locations:

- Configuration: `%APPDATA%\MarketingControl`
- Application data: `%LOCALAPPDATA%\MarketingControl\Data`
- Logs: `%LOCALAPPDATA%\MarketingControl\Logs\marketing-control.log`

On macOS, per-user files are stored in:

- Configuration and application data: `~/Library/Application Support/MarketingControl`
- Logs: `~/Library/Logs/MarketingControl/marketing-control.log`

On Linux and other POSIX platforms, per-user files are stored in:

- Configuration: `~/.config/MarketingControl`
- Application data: `~/.local/share/MarketingControl`
- Logs: `~/.local/state/MarketingControl/log/marketing-control.log`

Logs rotate locally after 1 MB and retain up to three backups. Log messages and
exception output redact secrets, passwords, OAuth and developer tokens,
credentials, authorization values, and API keys as `[REDACTED]`.

## Credentials

On Windows, credentials are stored through the operating system's Credential
Manager using `keyring`. Other platforms fail safely: the application does not
fall back to plaintext files, configuration, or application data.
