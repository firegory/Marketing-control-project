# Marketing Control Project

Minimal Python 3.12 foundation for the Marketing Control project.

## Sync orchestration

`ReportTaskRegistry` order is the documented, controlled report order. Tasks are injected by the host and receive only planned `DateRange` values, so the coordinator creates neither network clients nor report queries. Each run persists queued, running, succeeded, failed, or skipped work per report, including timestamps, progress, bounded redacted diagnostic text, and a stable failure category. Failures do not prevent later independent tasks; a retry queues only the prior run's failed reports and records its source report set, resulting run, and outcome. `/sync/diagnostics` is a local server-rendered view of failed runs, reports, retry audits, and bounded relevant log excerpts; it is not analytics.

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
- Exports: `%LOCALAPPDATA%\MarketingControl\Exports`
- Backups: `%LOCALAPPDATA%\MarketingControl\Backups`
- Logs: `%LOCALAPPDATA%\MarketingControl\Logs\marketing-control.log`

On macOS, per-user files are stored in:

- Configuration and application data: `~/Library/Application Support/MarketingControl`
- Logs: `~/Library/Logs/MarketingControl/marketing-control.log`

On Linux and other POSIX platforms, per-user files are stored in:

- Configuration: `~/.config/MarketingControl`
- Application data: `~/.local/share/MarketingControl`
- Exports: `~/.local/share/MarketingControl/Exports`
- Backups: `~/.local/share/MarketingControl/Backups`
- Logs: `~/.local/state/MarketingControl/log/marketing-control.log`

Logs rotate locally after 1 MB and retain up to three backups. Log messages and
exception output redact secrets, passwords, OAuth and developer tokens,
credentials, authorization values, and API keys as `[REDACTED]`.

## Offline data operations

Marketing Control has no public data API: it exposes no REST data endpoints,
access tokens, or OpenAPI contract. Internal Python modules can use the fixed
catalog query service only. It permits the product's allowlisted imported tables
and fixed projections, with bound customer and report-date predicates. It does
not accept SQL, table names, column names, joins, or other identifiers from users.

Use the local command line to create files from supported catalog tables:

```bash
# Writes a unique, atomically completed CSV or Parquet file under Exports.
uv run marketing-control export customers csv
uv run marketing-control export campaign_daily_performance parquet \
  --customer-resource-name customers/123 --start-date 2026-01-01 --end-date 2026-01-31

# Writes a consistent DuckDB export package under Backups.
uv run marketing-control backup
```

CSV files are portable text exports. Parquet files retain typed column values.
Backups are DuckDB `EXPORT DATABASE` packages containing schema and compressed
Parquet data; they are restored into a new empty DuckDB database with `IMPORT
DATABASE`. The application never backs up by copying a live `.duckdb` file.
Exports and backups are first written to a private temporary path and published
only on successful completion.

Direct DuckDB access is supported only for offline, read-only inspection. Close
Marketing Control first and ensure it is the sole writer before opening
`marketing-control.duckdb` with DuckDB or another tool. Do not modify its schema
or run the application's migrations yourself: application-owned migrations are
the only supported schema upgrade path. Restore verification should always import
to a new database file, never replace the active application database in place.

## Windows packaging

The pinned PyInstaller development dependency builds a Windows `onedir`
distribution using `packaging/marketing-control.spec`; templates and SQL
migrations are bundled. `packaging/marketing-control.iss` compiles that directory
into a per-user Inno Setup installer. It installs only under the current user's
local Programs directory and deliberately has no uninstall directives for
`%APPDATA%` or `%LOCALAPPDATA%`, preserving credentials, database, exports,
backups, and logs on uninstall.

## Credentials

On Windows, credentials are stored through the operating system's Credential
Manager using `keyring`. Other platforms fail safely: the application does not
fall back to plaintext files, configuration, or application data.
