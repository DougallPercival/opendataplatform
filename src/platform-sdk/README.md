# platform-sdk

The typed Python client for `catalog-service` — ARCHITECTURE.md §4/§10's "connectors, `@platform`
decorators, catalog client." First real consumer of `catalog-service`'s API outside its own test
suite.

Lives at `src/platform-sdk/`, not the top-level `platform-sdk/` ARCHITECTURE.md's original repo
layout diagram shows — this repo already wraps everything under `src/` (`src/core/`, `src/modules/`,
`src/charts/`, `src/examples/`), and this follows that same convention for consistency rather than
matching the diagram literally. Same divergence-for-consistency call as elsewhere in this repo.

## Phase 2, this pass (2026-09-01): workspaces + datasets only

Not a placeholder — real, tested code — but deliberately the minimal slice: `PlatformClient` covers
`/me`, `/workspaces` (list/create/get), and `/datasets` (list/create/get/update/delete). Pipelines,
models, functions (+publish/promote), and lineage are the exact same shape of work again, once
`platform-cli` or a real `@platform.*` decorator actually needs them — see
`platform_sdk/models.py`'s module docstring for why they're not speculatively built now.

The `@platform.dataset` / `@platform.pipeline` / `@platform.function` decorators ARCHITECTURE.md §4
describes (self-registration for *code*, the counterpart to the module self-registration pattern in
§3) also aren't built yet — this pass is the client those decorators will eventually call into, not
the decorators themselves.

## Workspace invites (2026-09-01): `KeycloakAdminClient`

A second, separate client — `platform_sdk.keycloak_admin.KeycloakAdminClient` — talks to Keycloak's
own Admin REST API directly, not to catalog-service, to add an existing user to a workspace's
`owner`/`editor`/`viewer` group (`src/core/auth/realm-platform.yaml`'s `/workspaces/<name>/<role>`
model). This is `platform workspace invite`'s entire implementation; see `platform_sdk/keycloak_admin.py`'s
module docstring for the full design, especially *why* it manages its own `kubectl port-forward` and
reproduces curl's `--resolve` trick in Python (Keycloak's hostname provider — see
`docs/known-issues.md`'s 2026-08-31 entry — makes a plain port-forward-to-localhost not work the way
it does for catalog-service).

Requires:

- `PLATFORM_KEYCLOAK_CLIENT_SECRET` set — from `bootstrap/keycloak-bootstrap-cli-client.sh`'s printed
  `export` line, or its read-back command if you've already run that script once.
- `kubectl` reachable the same way every other script in this repo expects (`sudo`, `/usr/local/bin/kubectl`).
- `jq` is NOT needed here (that's the bootstrap script's dependency, for its own bash+curl approach) —
  this is plain Python/httpx.

Self-heals workspaces that only exist in catalog-service's database so far: `platform workspace
create` never touches Keycloak (see that service's `app/routers/workspaces.py` docstring), so the
first `invite` into a workspace besides the seeded `personal` one creates the missing Keycloak group
path (and maps the matching realm role onto it) automatically rather than requiring a manual
admin-console step first.

Does **not** create Keycloak users — `realm-platform.yaml` sets `registrationAllowed: false` and
seeds no users on purpose (see that file's header comment). `invite()` raises a clear
`KeycloakAdminError` naming this if the username doesn't already exist, rather than a bare 404.

## Requirements

**Python 3.12+** (`requires-python` in `pyproject.toml`) — most systems' default `python3` is
older (RHEL-family 9.x ships 3.9, Ubuntu 22.04 ships 3.10), so `pip install -e ".[dev]"` fails with
`requires a different Python` against a bare `python3`/`pip`. See `catalog-service/README.md`'s
"Running locally" section for per-OS install commands (same requirement, same fix, verified there
against a real Rocky 9.4 box) — not repeated here to avoid the two copies drifting apart.

## Using it

```python
from platform_sdk import PlatformClient, Visibility

with PlatformClient(workspace="personal", user="alice") as client:
    me = client.me()
    dataset = client.create_dataset("reddit-sentiment", visibility=Visibility.PRIVATE)
    print(client.list_datasets())
```

```python
from platform_sdk import KeycloakAdminClient, Role

# Needs PLATFORM_KEYCLOAK_CLIENT_SECRET set — see "Workspace invites" above.
with KeycloakAdminClient() as admin:
    result = admin.invite("alice", workspace="personal", role=Role.EDITOR)
    print(result.group_path, result.group_created)
```

Config resolution (constructor arg > `PLATFORM_*` env var / `.env` > built-in default) is
`platform_sdk/config.py` — see its docstring, especially for why `PLATFORM_ROLE` defaults to unset
rather than `"owner"`.

## Running its tests

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
```

No live `catalog-service` or Postgres needed — `tests/test_client.py` mocks the HTTP transport
directly with `respx`. That's a real difference from `catalog-service`'s own test suite (which
insists on a real Postgres — see that service's `tests/conftest.py`), not an inconsistency: this
package's job is "does the client send/parse the right thing," not "does the service work."

`tests/test_keycloak_admin.py` does the same for `KeycloakAdminClient` — respx-mocked Admin API
calls, `KeycloakAdminClient`'s private `_client=` constructor arg bypassing the real
port-forward/kubectl/hostname-patch plumbing entirely (that plumbing isn't unit-testable without a
live cluster — see that test file's own docstring, and the real end-to-end confirmation noted in
`platform-cli`'s README once `workspace invite` has actually been run against a live Keycloak).

## Error handling

Every non-2xx response from `PlatformClient` raises `platform_sdk.PlatformAPIError` — `.status_code`
and `.detail` are pulled from the response body (catalog-service's consistent `{"detail": "..."}`
shape), so callers can branch on `.status_code` (e.g. `403` for a viewer trying to write) without
parsing message strings.

`KeycloakAdminClient` raises `platform_sdk.KeycloakAdminError` instead — a single exception type
covering everything from "no client secret configured" to "port-forward never came up" to "no such
Keycloak user" to an unexpected Admin API status, each with a message that says which.
