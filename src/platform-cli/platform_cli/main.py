"""The `platform` command's entry point. Root callback builds exactly one
PlatformClient per invocation and hangs it on the Click/Typer context
(`ctx.obj`) — every subcommand (in workspace.py, dataset.py, ...) pulls it
back out rather than constructing its own, so there's one place auth/config
resolution happens, not one per command.

CLI flags here are deliberately thin passthroughs — None by default, letting
PlatformClient's own constructor precedence (explicit arg > PLATFORM_* env
var > built-in default; see platform_sdk/config.py and client.py) do the
actual resolution. This file doesn't re-implement env var reading itself,
so there's exactly one definition of "what does unset mean," not two that
could disagree.
"""
from __future__ import annotations

import typer
from platform_sdk import PlatformClient

from platform_cli.dataset import app as dataset_app
from platform_cli.errors import handle_api_errors
from platform_cli.workspace import app as workspace_app

app = typer.Typer(
    name="platform",
    help="ARCHITECTURE.md §4/§10's platform CLI — talks to catalog-service via platform-sdk.",
    no_args_is_help=True,
)
app.add_typer(workspace_app, name="workspace", help="Manage workspaces.")
app.add_typer(dataset_app, name="dataset", help="Manage datasets.")


@app.callback()
def main(
    ctx: typer.Context,
    catalog_url: str | None = typer.Option(
        None, "--catalog-url", help="Overrides PLATFORM_CATALOG_URL / the built-in default."
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Overrides PLATFORM_WORKSPACE / the built-in default."
    ),
    user: str | None = typer.Option(
        None, "--user", "-u", help="Overrides PLATFORM_USER / the current OS user."
    ),
    role: str | None = typer.Option(
        None,
        "--role",
        help="Overrides PLATFORM_ROLE. Leave unset to run as catalog-service's own default (owner).",
    ),
) -> None:
    client = PlatformClient(catalog_url=catalog_url, workspace=workspace, user=user, role=role)
    # Click's context teardown hook, not a try/finally wrapped around every
    # single command — one registration here covers all of them, and fires
    # whether the command succeeded, failed, or raised typer.Exit.
    ctx.call_on_close(client.close)
    ctx.obj = client


@app.command()
@handle_api_errors
def me(ctx: typer.Context) -> None:
    """Who does catalog-service think I am, given my current headers."""
    client: PlatformClient = ctx.obj
    principal = client.me()
    typer.echo(f"workspace : {principal.workspace_name} ({principal.workspace_id})")
    typer.echo(f"user      : {principal.user_id}")
    typer.echo(f"role      : {principal.role}")
