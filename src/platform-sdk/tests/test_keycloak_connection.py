"""Unit tests for two narrow, genuinely-unit-testable slices of
`_PortForward` — `_wait_ready()`'s readiness logic and `start()`'s
constructed kubectl command — NOT the rest of `_PortForward`/`_ResolvePatch`,
which still need a real kubectl and a live cluster and stay
confirmed-live-only (see test_keycloak_admin.py's own docstring for that
reasoning, unchanged here).

Both bugs covered here were found the same way: live testing on
2026-09-02, `platform login`'s actual first run against a real cluster
(see `_keycloak_connection.py`'s own comments on `_wait_ready` and
`bind_address` for the full story in each case, and docs/known-issues.md
for the live symptoms).

`_wait_ready()` is pure socket-readiness logic with no kubectl/cluster
dependency of its own, so it's fully testable against a throwaway local
TCP/TLS listener spun up in-process. The original check only confirmed a
bare TCP accept, which wasn't enough — `kubectl port-forward` can open its
local listener slightly before the reverse tunnel to the pod is actually
usable, so an early connection gets accepted and then reset
mid-TLS-handshake.

`start()`'s command construction is also pure (a list of strings), testable
by monkeypatching `subprocess.Popen`/`subprocess.run` to capture the argv
instead of actually shelling out — confirms `bind_address` reaches the
`--address` flag `kubectl port-forward` actually reads.
"""
from __future__ import annotations

import contextlib
import socket
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from platform_sdk._keycloak_connection import _PortForward
from platform_sdk.exceptions import KeycloakAdminError


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _self_signed_context():
    """A throwaway self-signed cert/key pair, generated via the `openssl`
    CLI rather than adding a `cryptography` dev-dependency just for this
    one test file — this repo's bootstrap scripts already assume `openssl`
    is present everywhere (see keycloak-bootstrap-cli-client.sh's header),
    so this doesn't introduce a new tool, just a new use of one already
    relied on. The cert's contents don't matter at all — this test only
    needs *something* that completes a TLS handshake, the same
    "liveness, not identity" distinction `_wait_ready`'s own comment draws
    (the real request right after `_wait_ready` returns is the one that
    actually verifies against the real `platform-ca` CA).
    """
    with tempfile.TemporaryDirectory() as tmp:
        keyfile = Path(tmp) / "key.pem"
        certfile = Path(tmp) / "cert.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(keyfile),
                "-out",
                str(certfile),
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=test",
            ],
            check=True,
            capture_output=True,
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(certfile), str(keyfile))
        yield ctx


def _serve_once_with_tls(port: int, stop: threading.Event) -> None:
    """Accepts connections and completes a real TLS handshake on each,
    standing in for what a working kubectl port-forward tunnel eventually
    presents once it's actually up.
    """
    with _self_signed_context() as ctx:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(5)
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    tls_conn.do_handshake()
            except (OSError, ssl.SSLError):
                pass
        listener.close()


def _serve_once_tcp_only_no_tls(port: int, stop: threading.Event) -> None:
    """Accepts raw TCP connections but never completes (or even attempts)
    a TLS handshake — the exact shape of the bug this test guards against:
    a bare TCP accept succeeding while the tunnel underneath isn't actually
    usable yet.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(5)
    listener.settimeout(0.2)
    conns = []
    while not stop.is_set():
        try:
            conn, _ = listener.accept()
            conns.append(conn)  # held open, never spoken to — TLS handshake just hangs/never starts
        except TimeoutError:
            continue
    for conn in conns:
        conn.close()
    listener.close()


def _port_forward_stub(port: int, ready_timeout_seconds: float) -> _PortForward:
    # kubectl_cmd/namespace/service_name/service_port are never used by
    # _wait_ready itself (only by start()/_extract_ca_cert(), which this
    # test deliberately never calls) — placeholder values are fine.
    return _PortForward(
        kubectl_cmd="true",
        namespace="keycloak",
        service_name="platform-service",
        service_port=8443,
        local_port=port,
        ready_timeout_seconds=ready_timeout_seconds,
    )


def test_wait_ready_succeeds_once_a_real_tls_handshake_completes() -> None:
    port = _free_port()
    stop = threading.Event()
    thread = threading.Thread(target=_serve_once_with_tls, args=(port, stop), daemon=True)
    thread.start()
    try:
        pf = _port_forward_stub(port, ready_timeout_seconds=5.0)
        pf._wait_ready()  # should return normally, not raise
    finally:
        stop.set()
        thread.join(timeout=2)


def test_wait_ready_raises_when_tcp_accepts_but_tls_never_completes() -> None:
    # This is the regression test for the actual bug: a listener that
    # accepts TCP connections (so the OLD bare-connect check would have
    # returned immediately) but never speaks TLS, matching what an
    # early/not-yet-ready kubectl port-forward tunnel looks like from the
    # client's side.
    port = _free_port()
    stop = threading.Event()
    thread = threading.Thread(target=_serve_once_tcp_only_no_tls, args=(port, stop), daemon=True)
    thread.start()
    try:
        pf = _port_forward_stub(port, ready_timeout_seconds=1.0)
        with pytest.raises(KeycloakAdminError, match="never came up"):
            pf._wait_ready()
    finally:
        stop.set()
        thread.join(timeout=2)


def test_wait_ready_raises_when_nothing_is_listening_at_all() -> None:
    port = _free_port()  # deliberately never bound by anything
    pf = _port_forward_stub(port, ready_timeout_seconds=1.0)
    with pytest.raises(KeycloakAdminError, match="never came up"):
        pf._wait_ready()


def test_start_defaults_to_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **_kwargs):  # stands in for the `kubectl get secret` call
        class _Result:
            stdout = "dGVzdA=="  # base64("test") — just needs to decode to something non-empty

        return _Result()

    class _FakePopen:
        def __init__(self, cmd, **_kwargs) -> None:
            captured["cmd"] = cmd

        def terminate(self) -> None:
            pass

        def wait(self, timeout=None) -> None:
            pass

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(_PortForward, "_wait_ready", lambda self: None)  # skip the real socket wait

    pf = _PortForward(
        kubectl_cmd="kubectl",
        namespace="keycloak",
        service_name="platform-service",
        service_port=8443,
        local_port=18443,
    )
    pf.start()
    assert "--address" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--address") + 1] == "127.0.0.1"


def test_start_honors_an_explicit_bind_address(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test for the actual bug: KeycloakLoginFlow's forward needs
    # to be reachable by whatever machine's browser the human uses, not
    # just the machine `platform login` runs on — see keycloak_login.py's
    # own comment on why it passes bind_address="0.0.0.0".
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **_kwargs):
        class _Result:
            stdout = "dGVzdA=="

        return _Result()

    class _FakePopen:
        def __init__(self, cmd, **_kwargs) -> None:
            captured["cmd"] = cmd

        def terminate(self) -> None:
            pass

        def wait(self, timeout=None) -> None:
            pass

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(_PortForward, "_wait_ready", lambda self: None)

    pf = _PortForward(
        kubectl_cmd="kubectl",
        namespace="keycloak",
        service_name="platform-service",
        service_port=8443,
        local_port=18444,
        bind_address="0.0.0.0",
    )
    pf.start()
    assert captured["cmd"][captured["cmd"].index("--address") + 1] == "0.0.0.0"
