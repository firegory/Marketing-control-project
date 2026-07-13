"""Marketing Control local application."""

import argparse
from datetime import date

__version__ = "0.1.0"


def main() -> None:
    """Run the local application or a safe local data operation."""
    parser = argparse.ArgumentParser(prog="marketing-control")
    subcommands = parser.add_subparsers(dest="command")
    export = subcommands.add_parser("export", help="export a supported catalog table")
    export.add_argument("table", choices=_supported_tables())
    export.add_argument("format", choices=("csv", "parquet"))
    export.add_argument("--customer-resource-name")
    export.add_argument("--start-date", type=date.fromisoformat)
    export.add_argument("--end-date", type=date.fromisoformat)
    subcommands.add_parser("backup", help="create a consistent local DuckDB backup")
    arguments = parser.parse_args()

    if arguments.command == "export":
        from marketing_control.data_access import CatalogQuery, export_catalog_table
        from marketing_control.settings import Settings

        output = export_catalog_table(
            Settings.load(),
            CatalogQuery(
                arguments.table,
                customer_resource_name=arguments.customer_resource_name,
                start_date=arguments.start_date,
                end_date=arguments.end_date,
            ),
            arguments.format,
        )
        print(output)
        return
    if arguments.command == "backup":
        from marketing_control.data_access import create_backup
        from marketing_control.settings import Settings

        print(create_backup(Settings.load()))
        return

    from marketing_control.launcher import run_local_server

    run_local_server()


def _supported_tables() -> tuple[str, ...]:
    from marketing_control.data_access import supported_tables

    return supported_tables()
