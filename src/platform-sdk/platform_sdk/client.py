"""PlatformClient — a thin, synchronous wrapper over catalog-service's REST
API. "Thin" on purpose: this does exactly what a `curl` command hitting the
same endpoint would do, plus typed request/response shapes and one
consistent error type (PlatformAPIError) instead of raw httpx exceptions —
no caching, no retries, no batching. Add those only once something using
this actually needs them, not speculatively.

Synchronous, not async: platform-cli (this SDK's first real consumer) is a
one-shot-command-then-exit CLI, where async buys nothing — every command
does one thing and quits. Revisit if something long-lived and concurrent
(e.g. a future TUI, or platform-sdk's own @platform.dataset decorators
doing background registration) ever needs it; httpx supports both, so this
isn't a one-way door.

Auth is the same placeholder shape catalog-service's own app/deps.py
expects on the other end: X-Workspace/X-User/X-Role headers, sent plainly,
no verification either side. Real auth replaces what THIS file sends the
same way it'll replace what deps.py reads — see that module's docstring.
"""
from __future__ import annotations

import getpass
from typing import Any
from uuid import UUID

import httpx

from platform_sdk.config import SDKSettings
from platform_sdk.exceptions import PlatformAPIError
from platform_sdk.models import Dataset, Principal, Visibility, Workspace


class PlatformClient:
    def __init__(
        self,
        *,
        catalog_url: str | None = None,
        workspace: str | None = None,
        user: str | None = None,
        role: str | None = None,
        settings: SDKSettings | None = None,
        timeout: float = 10.0,
    ) -> None:
        # Explicit constructor args win over settings (env vars) over
        # hardcoded fallbacks — same precedence order a CLI flag / env var /
        # default chain usually takes, so platform-cli's own --workspace
        # flag (once it has one) can override PLATFORM_WORKSPACE without
        # this class needing to know CLI flags exist.
        settings = settings or SDKSettings()
        self._base_url = (catalog_url or settings.catalog_url).rstrip("/")
        self._workspace = workspace or settings.workspace
        self._user = user or settings.user or getpass.getuser()
        self._role = role or settings.role
        self._http = httpx.Client(base_url=self._base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PlatformClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"X-Workspace": self._workspace, "X-User": self._user}
        # Omitted, not sent-empty, when unset — an empty X-Role header
        # would fail app/deps.py's Role(x_role.lower()) parse (empty
        # string isn't a valid role) instead of falling through to its
        # own DEFAULT_ROLE the way a genuinely absent header does. See
        # config.py's role field docstring for why unset is the default.
        if self._role:
            headers["X-Role"] = self._role
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:  # body wasn't JSON at all (e.g. a raw 502 from something in front)
                detail = response.text
            raise PlatformAPIError(response.status_code, detail, method=method, url=str(response.url))
        return response

    # ---- Principal ---------------------------------------------------
    def me(self) -> Principal:
        return Principal.model_validate(self._request("GET", "/me").json())

    # ---- Workspaces ----------------------------------------------------
    def list_workspaces(self) -> list[Workspace]:
        return [Workspace.model_validate(w) for w in self._request("GET", "/workspaces").json()]

    def create_workspace(self, name: str, display_name: str) -> Workspace:
        body = {"name": name, "display_name": display_name}
        return Workspace.model_validate(self._request("POST", "/workspaces", json=body).json())

    def get_workspace(self, workspace_id: UUID | str) -> Workspace:
        return Workspace.model_validate(self._request("GET", f"/workspaces/{workspace_id}").json())

    # ---- Datasets --------------------------------------------------------
    def list_datasets(self) -> list[Dataset]:
        return [Dataset.model_validate(d) for d in self._request("GET", "/datasets").json()]

    def create_dataset(
        self,
        name: str,
        *,
        visibility: Visibility = Visibility.PRIVATE,
        description: str | None = None,
        location_uri: str | None = None,
    ) -> Dataset:
        body = {
            "name": name,
            "visibility": visibility.value,
            "description": description,
            "location_uri": location_uri,
        }
        return Dataset.model_validate(self._request("POST", "/datasets", json=body).json())

    def get_dataset(self, dataset_id: UUID | str) -> Dataset:
        return Dataset.model_validate(self._request("GET", f"/datasets/{dataset_id}").json())

    def update_dataset(self, dataset_id: UUID | str, **fields: Any) -> Dataset:
        return Dataset.model_validate(self._request("PATCH", f"/datasets/{dataset_id}", json=fields).json())

    def delete_dataset(self, dataset_id: UUID | str) -> None:
        self._request("DELETE", f"/datasets/{dataset_id}")
