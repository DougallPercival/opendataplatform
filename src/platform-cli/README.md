# platform-cli

The `platform` command — ARCHITECTURE.md §4/§10. A thin wrapper over `platform-sdk`: every command
here does what `PlatformClient`'s matching method does, plus flag parsing and readable output/error
formatting. See `platform-sdk`'s own README for the client itself.

Lives at `src/platform-cli/`, same `src/`-wrapping convention as `platform-sdk` — see that package's
README for why.

## Phase 2, this pass (2026-09-01): the same minimal slice platform-sdk covers

```text
platform me
platform workspace list
platform workspace create NAME [--display-name TEXT]
platform workspace get WORKSPACE_ID
platform dataset list
platform dataset create NAME [--visibility private|workspace|public] [--description TEXT] [--location-uri TEXT]
platform dataset get DATASET_ID
platform dataset update DATASET_ID [--description TEXT] [--visibility ...] [--location-uri TEXT]
platform dataset delete DATASET_ID
```

Not built yet: commands for the other four catalog-service resource types (pipelines, models,
functions incl. publish/promote, lineage) — same "add it when platform-cli actually needs it"
reasoning as everywhere else in this repo. `platform module scaffold/install` from ARCHITECTURE.md
§3/§7 is unrelated future scope (module lifecycle, not catalog data) — not part of this package's
current slice either.

## Workspace invites (2026-09-01)

```text
platform workspace invite USERNAME [--workspace NAME] [--role owner|editor|viewer]
```

Adds an EXISTING Keycloak user to a workspace's `owner`/`editor`/`viewer` group. Defaults: `--role
viewer` (least privilege), `--workspace` from `PLATFORM_WORKSPACE`/`personal` like every other
command. Doesn't create the user — see `platform_sdk`'s README ("Workspace invites") for why, and for
the required `PLATFORM_KEYCLOAK_CLIENT_SECRET` setup via `bootstrap/keycloak-bootstrap-cli-client.sh`.

Different from every other command here in one way worth knowing before you run it: it does NOT talk
to catalog-service or need `PLATFORM_CATALOG_URL` at all — it talks to Keycloak directly, and manages
its own `kubectl port-forward` to do it (needs `kubectl` + the same `sudo` access every bootstrap
script in this repo assumes). No separate port-forward to set up first.

Confirmed against Admin REST API calls verified against Keycloak's own docs (see
`platform_sdk/keycloak_admin.py`'s module docstring) and a full respx-mocked test suite, but — like
`bootstrap/keycloak-bootstrap-cli-client.sh` itself — not yet run against a live Keycloak from this
package's side. Whoever runs `platform workspace invite` for real first should treat that the same
way the bootstrap script's own first run was treated: tell me what happens.

## Requirements

**Python 3.12+** (`requires-python` in `pyproject.toml`, matching `platform-sdk` and
`catalog-service`). Confirmed the hard way, not hypothetically: installing this on `homelab-dev`
(Rocky Linux 9.4) against its default `python3` (3.9.19) failed with `requires a different Python`.
Rocky/Alma/RHEL 9.x ship Python 3.12 directly from AppStream, no EPEL needed:

```bash
sudo dnf install python3.12
python3.12 -m venv ~/.venvs/platform
source ~/.venvs/platform/bin/activate
```

(Ubuntu 22.04 ships 3.10, same problem, different fix — `sudo apt install python3.12` or the
deadsnakes PPA; macOS — `brew install python@3.12`. See `catalog-service/README.md`'s "Running
locally" section for the full per-OS list; not repeated here to avoid the two copies drifting.)

**The venv isn't active by default in a new shell** — `source ~/.venvs/platform/bin/activate`
first, every time, or `platform: command not found` even though it's installed. Easy to forget
after switching terminals/reconnecting SSH; if `platform` isn't found and you're sure it's
installed, this is almost always why.

## Local dev setup

Two editable installs, in this order — `platform-cli` depends on `platform-sdk` by name with no
version pin (see this package's `pyproject.toml` for why: no published package to resolve against,
just a sibling in this monorepo):

```bash
pip install -e ../platform-sdk
pip install -e ".[dev]"
```

Then point it at a running `catalog-service` (see that service's own README for `uvicorn
app.main:app --reload`) via env vars — no CLI flag or config file needed for the common case, though
`--workspace`/`--user`/`--role`/`--catalog-url` override them per-invocation:

```bash
export PLATFORM_CATALOG_URL=http://localhost:8000
export PLATFORM_WORKSPACE=personal
export PLATFORM_USER=alice   # optional — defaults to your OS username

platform me
platform dataset create reddit-sentiment --visibility public --description "raw pulls"
platform dataset list
```

Verified working end-to-end against a real running `catalog-service` + Postgres during development —
`platform dataset create` / `list` / `get` / `update` / `delete`, `platform workspace list`, and a
`PLATFORM_ROLE=viewer` run correctly getting rejected with `catalog-service error (403): Viewers
cannot create entries.` — not just the mocked test suite below.

## Running its tests

```bash
pip install -e ../platform-sdk
pip install -e ".[dev]"
ruff check .
pytest -v
```

`tests/test_cli.py` uses Typer's `CliRunner` with `platform_cli.main.PlatformClient` monkeypatched to
a fake — no network, no live `catalog-service` needed, same reasoning as `platform-sdk`'s own
transport-mocked tests (see that package's README): this suite's job is "does the CLI wire flags,
output, and errors correctly around *a* client," not "does `PlatformClient` talk HTTP correctly"
(that's `platform-sdk`'s job) or "does `catalog-service` work" (that's `catalog-service`'s job).
