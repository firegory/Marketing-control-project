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
