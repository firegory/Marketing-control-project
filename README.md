# Marketing Control Project

Minimal Python 3.12 foundation for the Marketing Control project.

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
