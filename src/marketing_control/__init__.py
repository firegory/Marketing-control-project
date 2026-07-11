"""Marketing Control local application."""

__version__ = "0.1.0"


def main() -> None:
    """Run the local application server."""
    from marketing_control.launcher import run_local_server

    run_local_server()
