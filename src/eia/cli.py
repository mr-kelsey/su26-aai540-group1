"""Top-level CLI: `eia <subcommand>`.

Subcommands:
    eia warehouse init|reset|info
    eia pull <source>
    eia phase0
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from eia.config import settings
from eia.sources import registry
from eia.warehouse import get_warehouse

app = typer.Typer(no_args_is_help=True, add_completion=False)
warehouse_app = typer.Typer(no_args_is_help=True, help="Warehouse operations.")
pull_app = typer.Typer(no_args_is_help=True, help="Pull a data source.")
app.add_typer(warehouse_app, name="warehouse")
app.add_typer(pull_app, name="pull")

console = Console()


# ---- warehouse subcommands ----

@warehouse_app.command("init")
def warehouse_init() -> None:
    """Initialize the warehouse with the schema."""
    settings.ensure_dirs()
    wh = get_warehouse()
    wh.migrate()
    console.print(f"[green]Warehouse initialized[/] ({settings.eia_warehouse_backend})")


@warehouse_app.command("reset")
def warehouse_reset(
    confirm: Annotated[bool, typer.Option("--yes", help="Skip confirmation")] = False,
) -> None:
    """Drop and re-create the warehouse. DESTRUCTIVE."""
    if not confirm and not typer.confirm(
        "This will drop all warehouse data. Continue?"
    ):
        raise typer.Abort()
    wh = get_warehouse()
    wh.reset()
    wh.migrate()
    console.print("[yellow]Warehouse reset complete.[/]")


@warehouse_app.command("info")
def warehouse_info() -> None:
    """Print warehouse metadata."""
    wh = get_warehouse()
    info = wh.info()
    table = Table(title="Warehouse")
    table.add_column("Key")
    table.add_column("Value")
    for k, v in info.items():
        table.add_row(k, str(v))
    console.print(table)


# ---- pull subcommands (one per source, dynamically registered) ----

def _register_pull_commands() -> None:
    """Auto-register a `pull` subcommand for every entry in the source registry."""
    # Triggers source modules to register themselves.
    importlib.import_module("eia.sources.bls_qcew")
    importlib.import_module("eia.sources.bea_io")
    importlib.import_module("eia.sources.census_acs")
    importlib.import_module("eia.sources.tiger")
    importlib.import_module("eia.sources.hud_crosswalk")
    importlib.import_module("eia.sources.ticketmaster")
    importlib.import_module("eia.sources.runsignup")
    importlib.import_module("eia.sources.setlistfm")

    for name, source_cls in registry.all_sources().items():

        def _make_pull(
            _name: str = name, _cls: type = source_cls
        ) -> Callable[[], None]:
            def _pull() -> None:
                """Pull this source through the full fetch -> clean -> load lifecycle."""
                src = _cls()
                console.print(f"[cyan]Pulling[/] {_name}…")
                raw = src.fetch()
                cleaned = src.to_cleaned(raw)
                wh = get_warehouse()
                src.load(cleaned, wh)
                console.print(f"[green]Done[/] {_name} → {cleaned}")

            return _pull

        pull_app.command(name=name)(_make_pull())


_register_pull_commands()


if __name__ == "__main__":
    app()
