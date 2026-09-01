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

## Error handling

Every non-2xx response raises `platform_sdk.PlatformAPIError` — `.status_code` and `.detail` are
pulled from the response body (catalog-service's consistent `{"detail": "..."}` shape), so callers
can branch on `.status_code` (e.g. `403` for a viewer trying to write) without parsing message
strings.
