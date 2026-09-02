"""Shared low-level Keycloak connection plumbing — `_PortForward` and
`_ResolvePatch`, moved here verbatim from `keycloak_admin.py` (mechanical
extraction, no behavior change) so `KeycloakAdminClient` and the new
device-flow login code in `keycloak_login.py` use the exact same,
already-tested mechanism instead of two copies that could drift apart.

Both classes exist for the same reason, described in full in
`keycloak_admin.py`'s module docstring (not repeated here to avoid the two
copies drifting): Keycloak's hostname provider strictly enforces
`spec.hostname.hostname: keycloak.platform.local` on every request once it's
set, so a plain `kubectl port-forward` to `localhost`/a raw IP doesn't work
the way it does for catalog-service. `_PortForward` manages the background
`kubectl port-forward` + extracted CA cert; `_ResolvePatch` reproduces curl's
`--resolve HOST:PORT:IP` trick so the TLS SNI and Host header still say
`keycloak.platform.local` while the actual TCP connection goes to
`127.0.0.1`.

Deliberately NOT used by the in-cluster `gateway` service — `_ResolvePatch`
monkeypatches `socket.getaddrinfo` process-wide, which is fine for a
short-lived, single-purpose CLI invocation (get one token, exit) but unsafe
in a long-running, concurrently-serving FastAPI Deployment. Gateway instead
connects to Keycloak's real in-cluster Service DNS name directly — see
`src/core/gateway/app/config.py` and
`src/core/argocd/manifests/keycloak-instance.yaml`'s Certificate SAN
addition for that side of the story.
"""
from __future__ import annotations

import base64
import contextlib
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from platform_sdk.exceptions import KeycloakAdminError


class _ResolvePatch:
    """Reproduces curl's `--resolve HOST:PORT:IP` for Python's stdlib socket
    resolution. httpx's sync transport (via httpcore) resolves hosts through
    `socket.getaddrinfo` like everything else in the stdlib, so patching
    that one function has the same effect curl's --resolve flag has — TLS
    SNI and the Host header still say `keycloak.platform.local` (that's
    still the URL host), only the actual TCP connection goes to `target_ip`
    — without editing /etc/hosts. Scoped to one hostname and undone via
    `undo()`, not left patched process-wide forever.
    """

    def __init__(self, hostname: str, target_ip: str) -> None:
        self._hostname = hostname
        self._target_ip = target_ip
        self._original = socket.getaddrinfo

    def apply(self) -> None:
        original = self._original
        hostname = self._hostname
        target_ip = self._target_ip

        def patched(host, *args, **kwargs):
            if host == hostname:
                host = target_ip
            return original(host, *args, **kwargs)

        socket.getaddrinfo = patched

    def undo(self) -> None:
        socket.getaddrinfo = self._original


class _PortForward:
    """Manages a background `kubectl port-forward` to Keycloak's Service
    plus the CA cert `--cacert` needs, the same way
    bootstrap/keycloak-bootstrap-cli-client.sh does it — see that script's
    header comment for the original design this mirrors, and
    keycloak_admin.py's own module docstring for why a client needs one of
    its own at all.
    """

    def __init__(
        self, *, kubectl_cmd: str, namespace: str, service_name: str, service_port: int, local_port: int
    ) -> None:
        self._kubectl_cmd = kubectl_cmd.split()
        self._namespace = namespace
        self._service_name = service_name
        self._service_port = service_port
        self._local_port = local_port
        self._process: subprocess.Popen | None = None
        self._ca_cert_path: Path | None = None

    def start(self) -> str:
        ca_cert_path = self._extract_ca_cert()
        self._process = subprocess.Popen(
            [
                *self._kubectl_cmd,
                "port-forward",
                "-n",
                self._namespace,
                f"svc/{self._service_name}",
                f"{self._local_port}:{self._service_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()
        return ca_cert_path

    def _extract_ca_cert(self) -> str:
        # Same Secret bootstrap/keycloak-bootstrap-cli-client.sh reads —
        # see that script's header comment for where it comes from
        # (cert-manager's platform-ca ClusterIssuer).
        try:
            result = subprocess.run(
                [
                    *self._kubectl_cmd,
                    "get",
                    "secret",
                    "platform-ca-secret",
                    "-n",
                    "cert-manager",
                    "-o",
                    r"jsonpath={.data.ca\.crt}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise KeycloakAdminError(
                f"Couldn't read platform-ca-secret's ca.crt via kubectl: {exc.stderr.strip()}"
            ) from exc
        ca_bytes = base64.b64decode(result.stdout)
        if not ca_bytes:
            raise KeycloakAdminError(
                "platform-ca-secret's ca.crt came back empty — check it exists: "
                "kubectl get secret platform-ca-secret -n cert-manager -o yaml"
            )
        fd = tempfile.NamedTemporaryFile(prefix="platform-ca-", suffix=".crt", delete=False)
        fd.write(ca_bytes)
        fd.close()
        self._ca_cert_path = Path(fd.name)
        return fd.name

    def _wait_ready(self) -> None:
        # Poll rather than a fixed sleep — same reasoning as the bootstrap
        # script's own readiness loop.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", self._local_port), timeout=0.5):
                    return
            time.sleep(0.3)
        raise KeycloakAdminError(
            f"kubectl port-forward to {self._service_name}:{self._service_port} never came up on "
            f"127.0.0.1:{self._local_port} within 10s. Is the 'keycloak' namespace's platform-service "
            "Service up? (sudo kubectl get svc -n keycloak)"
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            with contextlib.suppress(Exception):
                self._process.wait(timeout=5)
            self._process = None
        if self._ca_cert_path is not None:
            with contextlib.suppress(OSError):
                self._ca_cert_path.unlink()
            self._ca_cert_path = None
