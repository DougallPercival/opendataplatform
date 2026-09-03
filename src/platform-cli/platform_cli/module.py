"""`platform module {install,uninstall,scaffold}` — docs/architecture/module-lifecycle-plan.md's
items 4/5 (platform-module-lifecycle branch, 2026-09-03). Unlike every other command in this
package, these don't go through `ctx.obj`'s `PlatformClient` at all — no login, no gateway, no
workspace. They read/write files in the git checkout they're invoked from and talk to `git`
directly (`repo.py`), per the user's own decision this branch: install/uninstall should
auto-commit and auto-push, "operating on whatever local checkout the CLI is invoked from."

install/uninstall are the only two of these three that touch git — `scaffold` deliberately
doesn't commit anything (see its own docstring below). Both install and uninstall:
1. Resolve the repo root from the CWD (`repo.find_repo_root`).
2. Refuse to run against a dirty working tree (`repo.require_clean_worktree`) — this is the first
   platform-cli surface that commits on the operator's behalf, so it shouldn't ever sweep up
   unrelated in-progress changes into its own commit.
3. Do their actual work (validate + render, or just locate the file to remove).
4. `--dry-run` stops here, printing what *would* happen.
5. Otherwise, `repo.commit_and_push` — and print the resulting commit hash, so every run is
   self-auditing in its own terminal output without needing a separate `git log` check.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer
import yaml

from platform_cli.errors import handle_module_errors
from platform_cli.manifest import ManifestError, load_module_manifest, render_application_manifest
from platform_cli.repo import commit_and_push, discover_repo_url, find_repo_root, require_clean_worktree

app = typer.Typer(no_args_is_help=True)

MODULES_DIR = "src/modules"
MODULES_ENABLED_DIR = "src/modules-enabled"
CHARTS_DIR = "src/charts"


def _run_helm_template(chart_dir: Path, values_yaml: str) -> None:
    """Decision 5's optional pre-commit safety check: if `helm` is on PATH, actually render the
    chart with the computed values and abort (before anything is written or committed) if it
    fails. If `helm` isn't found, print one warning and move on — confirmed via this sandbox that
    it can't be assumed present everywhere, and install shouldn't hard-block on a box that
    doesn't have it. `helm`'s presence on homelab-dev is a live-verification item, not assumed."""
    helm = shutil.which("helm")
    if helm is None:
        typer.secho(
            "warning: `helm` not found on PATH — skipping the helm-template safety check. "
            "The generated Application will still be written and pushed; if the chart is "
            "actually broken, Argo CD will surface that as a degraded sync instead of this "
            "command catching it up front.",
            fg=typer.colors.YELLOW,
        )
        return
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(values_yaml)
        values_path = f.name
    try:
        result = subprocess.run(
            [helm, "template", str(chart_dir), "-f", values_path],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(values_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise ManifestError(f"`helm template {chart_dir}` failed:\n{result.stderr.strip()}")


@app.command("install")
@handle_module_errors
def install(
    name: str,
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and render, but write/commit nothing."),
) -> None:
    repo_root = find_repo_root(Path.cwd())
    manifest_path = repo_root / MODULES_DIR / name / "module.yaml"
    manifest = load_module_manifest(manifest_path)

    chart_dir = repo_root / CHARTS_DIR / manifest.id
    if not chart_dir.is_dir():
        raise ManifestError(
            f"{manifest_path} validates, but its chart ({chart_dir}) doesn't exist — "
            f"run `platform module scaffold {name}` first, or write the chart by hand."
        )

    repo_url = discover_repo_url(repo_root)
    chart_path = f"{CHARTS_DIR}/{manifest.id}"
    application_yaml = render_application_manifest(manifest, repo_url=repo_url, chart_path=chart_path)

    # Re-derive just the values block for the helm-template check, so what's checked is exactly
    # what will be pushed, not a second independent computation of it.
    values_yaml = yaml.safe_load(application_yaml)["spec"]["source"]["helm"]["values"]
    _run_helm_template(chart_dir, values_yaml)

    if dry_run:
        typer.echo(f"--dry-run: would write {MODULES_ENABLED_DIR}/{manifest.id}.yaml:\n")
        typer.echo(application_yaml)
        return

    require_clean_worktree(repo_root)

    enabled_dir = repo_root / MODULES_ENABLED_DIR
    enabled_dir.mkdir(parents=True, exist_ok=True)
    target = enabled_dir / f"{manifest.id}.yaml"
    target.write_text(application_yaml)

    commit_hash = commit_and_push(repo_root, [target], f"install module: {manifest.id}")
    rel_target = target.relative_to(repo_root)
    typer.echo(f"Installed {manifest.id!r} — wrote and pushed {rel_target} ({commit_hash}).")
    typer.echo("Argo CD (via modules-root) will pick it up on its next reconcile.")


@app.command("uninstall")
@handle_module_errors
def uninstall(
    name: str,
    purge_data: bool = typer.Option(
        False, "--purge-data", help="Also print the kubectl command to delete this module's PVCs."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen, but change nothing."),
) -> None:
    repo_root = find_repo_root(Path.cwd())
    target = repo_root / MODULES_ENABLED_DIR / f"{name}.yaml"
    if not target.is_file():
        raise ManifestError(
            f"{target.relative_to(repo_root)} doesn't exist — {name!r} isn't installed "
            "(`platform module install` writes this file; nothing to remove)."
        )

    if dry_run:
        typer.echo(f"--dry-run: would remove {target.relative_to(repo_root)} and push that removal.")
        if purge_data:
            _print_purge_command(name)
        return

    require_clean_worktree(repo_root)
    target.unlink()

    message = f"uninstall module: {name}" + (" (data purge requested)" if purge_data else "")
    commit_hash = commit_and_push(repo_root, [target], message)
    rel_target = target.relative_to(repo_root)
    typer.echo(f"Uninstalled {name!r} — removed and pushed the removal of {rel_target} ({commit_hash}).")
    typer.echo(
        "Argo CD will prune the Deployment/Service on its next reconcile. Any PersistentVolumeClaim "
        "the chart marked `argocd.argoproj.io/sync-options: Delete=false` survives on purpose "
        "(ARCHITECTURE.md §3) — reinstalling gets its data back."
    )
    if purge_data:
        _print_purge_command(name)


def _print_purge_command(name: str) -> None:
    # Deliberately printed, not run: platform-cli only ever talks to git and gateway today, never
    # straight to the cluster, and PVC deletion has no undo — see this branch's plan file, decision
    # 4, and the AskUserQuestion this was confirmed with. The `sudo` prefix matches how cluster
    # access has actually worked in this session so far, not an assumption this command can verify.
    typer.echo("")
    typer.secho("--purge-data: run this yourself once the uninstall above has synced:", bold=True)
    typer.echo(f"  sudo kubectl delete pvc -n {name} -l platform.io/module={name}")
    typer.echo("(platform-cli doesn't run this for you — no cluster credentials, and no undo.)")


@app.command("scaffold")
@handle_module_errors
def scaffold(name: str) -> None:
    """Generates `src/modules/<name>/module.yaml` (from src/modules/_template/) and
    `src/charts/<name>/` (from src/charts/_template/) — ARCHITECTURE.md §3: "generates the
    modules/<name>/ skeleton (chart + module.yaml), you write the actual service." Deliberately
    does NOT commit or push, unlike install/uninstall: those two only ever toggle a
    machine-generated pointer file, the exact mechanical operation the user asked to automate:
    a scaffold is a skeleton the operator is expected to actually edit before it means anything,
    and auto-committing an empty one would be premature."""
    if not re.match(r"^[a-z0-9-]+$", name):
        raise ManifestError(
            f"module names must match ^[a-z0-9-]+$ (got {name!r}) — used as a namespace and label value."
        )

    repo_root = find_repo_root(Path.cwd())
    module_dir = repo_root / MODULES_DIR / name
    chart_dir = repo_root / CHARTS_DIR / name
    if module_dir.exists():
        raise ManifestError(f"{module_dir.relative_to(repo_root)} already exists.")
    if chart_dir.exists():
        raise ManifestError(f"{chart_dir.relative_to(repo_root)} already exists.")

    display_name = name.replace("-", " ").replace("_", " ").title()
    module_replacements = {
        "__MODULE_ID__": name,
        "__Display Name__": display_name,
        "__module_id__": name,
        "__service__": name,
        "__namespace__": name,
    }
    chart_replacements = {
        "__MODULE_ID__": name,
        "__DISPLAY_NAME__": display_name,
    }

    _copy_and_substitute(repo_root / MODULES_DIR / "_template", module_dir, module_replacements)
    _copy_and_substitute(repo_root / CHARTS_DIR / "_template", chart_dir, chart_replacements)

    typer.echo(f"Scaffolded {module_dir.relative_to(repo_root)} and {chart_dir.relative_to(repo_root)}.")
    typer.echo("Next: edit the chart to actually do something, review module.yaml, then:")
    typer.echo(f"  platform module install {name}")


def _copy_and_substitute(src: Path, dst: Path, replacements: dict[str, str]) -> None:
    for src_file in src.rglob("*"):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(src)
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        text = src_file.read_text()
        for token, value in replacements.items():
            text = text.replace(token, value)
        dst_file.write_text(text)
