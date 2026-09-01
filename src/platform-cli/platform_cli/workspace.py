"""`platform workspace {list,create,get}` — mirrors PlatformClient's
workspace methods 1:1; no logic of its own beyond formatting output and
mapping errors, same "thin" principle platform-sdk's own client.py states
up front.
"""
from __future__ import annotations

import typer
from platform_sdk import PlatformClient

from platform_cli.errors import handle_api_errors

app = typer.Typer(no_args_is_help=True)


@app.command("list")
@handle_api_errors
def list_workspaces(ctx: typer.Context) -> None:
    client: PlatformClient = ctx.obj
    workspaces = client.list_workspaces()
    if not workspaces:
        typer.echo("No workspaces.")
        return
    for w in workspaces:
        typer.echo(f"{w.id}  {w.name:<20} {w.display_name}")


@app.command("create")
@handle_api_errors
def create_workspace(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Short name, e.g. 'nfl-betting'."),
    display_name: str = typer.Option(None, "--display-name", help="Defaults to NAME.title()."),
) -> None:
    client: PlatformClient = ctx.obj
    workspace = client.create_workspace(name, display_name or name.title())
    typer.echo(f"Created workspace {workspace.name!r} ({workspace.id}).")


@app.command("get")
@handle_api_errors
def get_workspace(ctx: typer.Context, workspace_id: str) -> None:
    client: PlatformClient = ctx.obj
    workspace = client.get_workspace(workspace_id)
    typer.echo(f"id           : {workspace.id}")
    typer.echo(f"name         : {workspace.name}")
    typer.echo(f"display_name : {workspace.display_name}")
    typer.echo(f"created_at   : {workspace.created_at}")
