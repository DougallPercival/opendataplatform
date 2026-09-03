# platform-cli

The `platform` command — ARCHITECTURE.md §4/§10. A thin wrapper over `platform-sdk`: every command
here does what `PlatformClient`'s matching method does, plus flag parsing and readable output/error
formatting. See `platform-sdk`'s own README for the client itself.

Lives at `src/platform-cli/`, same `src/`-wrapping convention as `platform-sdk` — see that package's
README for why.

## Phase 2, this pass (2026-09-01): the same minimal slice platform-sdk covers

```text
platform login
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

Not built yet: commands for the other three catalog-service resource types (pipelines, models,
lineage) — same "add it when platform-cli actually needs it" reasoning as everywhere else in this
repo. `platform module scaffold/install` from ARCHITECTURE.md §3/§7 is unrelated future scope
(module lifecycle, not catalog data) — not part of this package's current slice either; see
`docs/architecture/module-lifecycle-plan.md` for how that's actually scoped to get built.

## Functions: publish + promote (2026-09-03, platform-function-promote branch)

```text
platform function list
platform function create NAME [--visibility private|workspace|public] [--description TEXT] [--module-path TEXT]
platform function get FUNCTION_ID
platform function update FUNCTION_ID [--description TEXT] [--visibility ...]
platform function delete FUNCTION_ID
platform function versions FUNCTION_ID
platform function publish FUNCTION_ID --signature TEXT --module-path TEXT [--docstring TEXT] [--published-by TEXT]
platform function promote FUNCTION_ID
```

`publish`/`promote` were the two items ARCHITECTURE.md §11's Phase 2 row named explicitly
(`platform-cli function promote`) — catalog-service's backend for both already existed
(`app/routers/functions.py`); this branch is purely the CLI/SDK side. `promote` takes no flags —
the endpoint always sets visibility to `public` (one-directional by design); demoting back is
`function update FUNCTION_ID --visibility workspace`, the same generic `update` command every other
resource here already has.

## Real auth: `platform login` (2026-09-02, platform-gateway-auth branch)

```text
platform login
```

**Breaking change** — the flags every other command used to accept for identity/role are gone:
`--user`/`--role`/`PLATFORM_USER`/`PLATFORM_ROLE` no longer exist at all, and `--catalog-url`/
`PLATFORM_CATALOG_URL` are renamed to `--gateway-url`/`PLATFORM_GATEWAY_URL`. This is the actual
point of this branch, not an oversight: identity and role are no longer anything `platform-cli`
declares — `platform-gateway` derives both from a real, verified Keycloak token, and every command
that talks to catalog-service now goes through gateway instead of reaching it directly (see
`platform_sdk/client.py`'s and `../core/catalog-service/app/deps.py`'s module docstrings for the full
story). Running `platform --user alice ...` now fails with "no such option" rather than silently
doing nothing.

`platform login` is how you get that token: opens (or prints, if you're on a headless/SSH session) a
Keycloak verification URL, waits for you to approve it in a browser, and saves the result to
`~/.config/platform/credentials.json` for every other `platform` command to read back automatically.
No password ever touches this CLI. Safe to re-run any time — always replaces whatever was saved
before. Every other command now needs this run at least once first; without it, they fail fast with
"Not logged in — run `platform login` first" rather than a confusing 401 from gateway.

Uses a separate, public Keycloak client (`platform-cli-login`,
`bootstrap/keycloak-bootstrap-login-client.sh`) from the one `workspace invite` below uses — see that
script's header comment for why the two are deliberately never merged into one.

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

**Confirmed working end-to-end (2026-09-01)**, not just respx-mocked: `platform workspace invite
dougall --role editor` against a real user created by hand in Keycloak's admin console (see
`bootstrap/keycloak-bootstrap-cli-client.sh` for how `PLATFORM_KEYCLOAK_CLIENT_SECRET` gets set up)
correctly joined the already-seeded `/workspaces/personal/editor` group and printed `'dougall' added
to /workspaces/personal/editor (Reused that Keycloak group)`. That proves the "join an existing
group" path against a live cluster — the "self-heal a missing workspace/role group" path (invite into
a workspace besides `personal`, which `platform workspace create` never wires up in Keycloak) is
still only covered by `platform-sdk`'s mocked test suite, not yet exercised live. Worth trying once
there's a second workspace to invite into.

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

Then log in once and point the CLI at a running `platform-gateway` (see `../gateway/README.md` for
`uvicorn app.main:app --reload`, and that service's own env vars for pointing it at Keycloak +
catalog-service in turn) via env vars — no CLI flag or config file needed for the common case, though
`--workspace`/`--gateway-url` override them per-invocation:

```bash
export PLATFORM_GATEWAY_URL=http://localhost:8080
export PLATFORM_WORKSPACE=personal

platform login          # once — opens a browser verification URL, saves credentials
platform me
platform dataset create reddit-sentiment --visibility public --description "raw pulls"
platform dataset list
```

Verified working end-to-end against a real running `catalog-service` + Postgres during development,
before this branch's gateway/token-auth rewrite — `platform dataset create` / `list` / `get` /
`update` / `delete`, `platform workspace list`, and a `PLATFORM_ROLE=viewer` run correctly getting
rejected with `catalog-service error (403): Viewers cannot create entries.` (that specific env var no
longer exists — see "Real auth" above; a viewer-role rejection now depends on real Keycloak group
membership instead, confirmed the same way once `platform-gateway` is live — see this branch's plan
verification steps).

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

As of 2026-09-02's real-auth work, the same file also covers `platform login` the same way — a
`FakeLoginFlow` monkeypatched onto `platform_cli.login.KeycloakLoginFlow` (no real device flow, no
network) — verifying the verification-URL/code output, the success/failure paths, and, separately,
that `--user`/`--role`/`--catalog-url` now fail with Typer's own "no such option" rather than being
silently accepted and ignored (`test_removed_flags_are_rejected_not_silently_ignored`) — the actual
proof that this branch's breaking change took effect, not just that the new command works.
