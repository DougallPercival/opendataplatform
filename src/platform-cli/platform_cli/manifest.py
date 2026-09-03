"""`module.yaml` schema, validation, and Argo CD `Application`-manifest generation —
docs/architecture/module-lifecycle-plan.md's items 2 and 3 (platform-module-lifecycle branch,
2026-09-03). Two things happen here, deliberately kept in one file since they're two sides of the
same "turn a module.yaml into a running module" conversion `platform module install` performs:

1. `ModuleManifest`/`Placement`/`Toleration` — the same lightweight descriptor ARCHITECTURE.md §3
   already shows (`modules/notebook-jupyterhub/module.yaml`), plus §7's `placement` block, plus one
   additive field this branch adds: `namespace` (defaults to `id`) — something has to decide the
   generated Application's `destination.namespace`, and every existing core service already gets
   its own namespace named after itself, so adding it to the schema keeps module.yaml the one
   source of truth rather than inventing a second convention elsewhere.

   `model_config = ConfigDict(extra="forbid")` resolves the plan doc's "module.yaml validation
   failure mode" open question: a typo'd field name fails loudly at `install`/`scaffold` time with
   a specific field-level message (via `ManifestError`, caught by `errors.py`'s
   `handle_module_errors`), not silently ignored the way a plain dict-based YAML load would.

2. `render_application_manifest()` — turns a validated `ModuleManifest` into the literal YAML text
   `platform module install` writes to `src/modules-enabled/<id>.yaml`. Deliberately a hand-written
   string template, not `yaml.safe_dump(...)`: every other Application manifest in this repo
   (apps/core/*.yaml) carries a substantial header comment explaining what it is and why it's
   shaped the way it is — `yaml.safe_dump` can't produce that, and a generated file that looks
   nothing like every hand-authored one next to it would be its own small inconsistency. The
   generated Application is a complete, standalone manifest (not a lightweight pointer) — it's what
   `modules-root` (src/core/argocd/apps/core/modules-root.yaml) picks up directly, the same way
   root-app.yaml turns files in apps/core/ into core services (see that file's own header comment
   for the three-level app-of-apps structure this relies on).
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ManifestError(Exception):
    """A module.yaml failed to load or validate. Always carries a message that's already
    readable on its own — callers (module.py) don't need to inspect a wrapped Pydantic
    ValidationError themselves."""


class Toleration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    operator: str = "Equal"
    value: str | None = None
    effect: str


class Placement(BaseModel):
    """ARCHITECTURE.md §7: "a module's module.yaml carries an optional placement hint that the
    chart wrapper turns into the actual nodeSelector/tolerations block" — this is that hint,
    unchanged from what §7 shows."""

    model_config = ConfigDict(extra="forbid")

    role: str
    tolerations: list[Toleration] = Field(default_factory=list)


class ModuleManifest(BaseModel):
    """The `module.yaml` schema. Field set matches ARCHITECTURE.md §3's
    `modules/notebook-jupyterhub/module.yaml` example exactly, plus `namespace` (see this module's
    docstring) and `placement` (§7). `extra="forbid"` means an unknown field in module.yaml is a
    validation error, not a silently-ignored typo."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")  # used as k8s namespace + PVC label value
    displayName: str
    icon: str = "puzzle"
    navPath: str
    proxyTo: str
    healthCheck: str = "/healthz"
    requires: list[str] = Field(default_factory=list)
    optional: bool = True
    namespace: str | None = None
    placement: Placement | None = None

    @property
    def resolved_namespace(self) -> str:
        return self.namespace or self.id


def load_module_manifest(path: Path) -> ModuleManifest:
    """Load and validate `path` (a module.yaml). Raises ManifestError — never a raw
    yaml.YAMLError or pydantic.ValidationError — so callers can just print str(exc)."""
    if not path.is_file():
        raise ManifestError(f"no module.yaml found at {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} must be a YAML mapping at the top level, got {type(raw).__name__}")
    try:
        return ModuleManifest.model_validate(raw)
    except ValidationError as exc:
        # exc's own str() is already a readable, per-field message (pydantic v2's default
        # formatting) — no need to re-derive one field at a time here.
        raise ManifestError(f"{path} failed validation:\n{exc}") from exc


def _placement_values_block(manifest: ModuleManifest) -> str:
    """The `spec.source.helm.values` YAML text (decision 3: computed from module.yaml's own
    `placement`, empty when the module declares none — the chart's _helpers.tpl then renders no
    nodeSelector/tolerations at all)."""
    if manifest.placement is None:
        return "placement: {}\n"
    lines = ["placement:", f"  role: {manifest.placement.role}"]
    if manifest.placement.tolerations:
        lines.append("  tolerations:")
        for t in manifest.placement.tolerations:
            lines.append(f"    - key: {t.key}")
            lines.append(f"      operator: {t.operator}")
            if t.value is not None:
                lines.append(f"      value: {t.value}")
            lines.append(f"      effect: {t.effect}")
    else:
        lines.append("  tolerations: []")
    return "\n".join(lines) + "\n"


def render_application_manifest(manifest: ModuleManifest, *, repo_url: str, chart_path: str) -> str:
    """Renders the complete Argo CD `Application` YAML `platform module install` writes to
    `src/modules-enabled/<id>.yaml`. `repo_url` comes from `repo.discover_repo_url()` (git remote
    get-url origin) — the one place this improves on the hand-authored "self-referencing apps"
    convention (argocd/README.md), which hardcodes repoURL with a "forkers: edit this" comment;
    generated content doesn't need that caveat, it can just ask git. `targetRevision` still
    hardcodes `dev`, matching every other self-referencing Application in this repo."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    values_block = _placement_values_block(manifest)
    indented_values = "\n".join(
        f"        {line}" if line else "" for line in values_block.splitlines()
    )
    return f"""\
# GENERATED by `platform module install {manifest.id}` at {generated_at} — do not hand-edit.
# Source descriptor: src/modules/{manifest.id}/module.yaml. To change this module's placement,
# namespace, or chart, edit that file and re-run `platform module install {manifest.id}`
# (it's safe to run again — it overwrites this file in place and commits the diff).
#
# Picked up by modules-root (src/core/argocd/apps/core/modules-root.yaml), which watches
# src/modules-enabled/ the same way root-app.yaml watches apps/core/ — see that file's header
# comment for the three-level app-of-apps structure. `syncPolicy.automated.prune: true` plus the
# finalizer below mean `platform module uninstall {manifest.id}` (which just deletes this file)
# is enough to tear the whole module back down — except any PersistentVolumeClaim the chart marks
# `argocd.argoproj.io/sync-options: Delete=false`, which survives on purpose (ARCHITECTURE.md §3;
# see src/charts/{manifest.id}/templates/*.yaml for this module's own PVCs, if it has any).
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {manifest.id}
  namespace: argocd
  labels:
    platform.io/tier: module
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: {repo_url}
    targetRevision: dev
    path: {chart_path}
    helm:
      values: |
{indented_values}
  destination:
    server: https://kubernetes.default.svc
    namespace: {manifest.resolved_namespace}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
"""
