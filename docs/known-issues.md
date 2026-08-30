# Known issues & host prerequisites

Running notes from testing `bootstrap/install.sh` on a real box, kept separate from
[`ARCHITECTURE.md`](architecture/ARCHITECTURE.md) because this is host-specific troubleshooting,
not design. First target: RHEL/Rocky/AlmaLinux 9. Add to this as new hosts turn up new gotchas.

## Still on you — host-level decisions a script shouldn't make for you

### MetalLB's IP pool is network-specific — update it on any new host/network

`src/core/argocd/manifests/metallb-pool.yaml` is committed with a real, concrete IP range
(currently `192.168.4.240-192.168.4.250`, homelab-dev's eero LAN), not a placeholder. Unlike
`REPO_URL`/`REVISION`, there's no way for `install.sh` to derive "which slice of this network is
safe to hand out" on its own — that always needs a human check (see the ping-loop approach below).

This means the committed value is only correct for the network it was set up on. Run this repo
against different hardware or a different network — new server, router swap, a cloud VM — and it
silently carries over the old range. MetalLB's `L2Advertisement` mode ARPs on the local subnet, so
a mismatched pool doesn't just risk a conflict, it doesn't work at all: ingress-nginx's external IP
just sits on `<pending>` forever, with nothing pointing at "wrong subnet" as the cause.

`install.sh` now checks this automatically — it compares the pool's subnet against the host's
detected subnet and warns (doesn't block) on a mismatch. If you see that warning, or you're setting
this up fresh on new hardware, find a safe range with a quick reachability check rather than
guessing at your router's DHCP boundary:

```bash
for i in 240 241 242 243 244 245; do ping -c1 -W1 192.168.X.$i >/dev/null && echo "192.168.X.$i is IN USE" || echo "192.168.X.$i looks free"; done
```

(swap `192.168.X` for your actual subnet — check any device's own IP to find it, no router login
needed). Update `metallb-pool.yaml` with whatever range comes back clean.

### firewalld

**What happened:** k3s installed and the `k3s` service ran, but the node sat `NotReady`
indefinitely — no crash, no clear error, just stuck. `firewalld` (on by default on RHEL 9) was
blocking traffic k3s/Flannel need. `sudo systemctl stop firewalld` unblocked it immediately.

**Status right now:** only *stopped*, not disabled — it'll come back on the next reboot and block
things again. Pick one:

- **Disable it.** Reasonable for a homelab box sitting behind your own router, not directly
  internet-facing:

  ```bash
  sudo systemctl disable firewalld
  ```

- **Keep it, add rules instead.** Better if this box is more exposed (a cloud VM, a shared
  network) — open exactly what k3s needs rather than turning the firewall off. Per
  [k3s's own port requirements](https://docs.k3s.io/installation/requirements), a single-node
  server needs:

  | Port | Protocol | For |
  |---|---|---|
  | 6443 | TCP | K3s supervisor / Kubernetes API server |
  | 10250 | TCP | Kubelet metrics and API |
  | 8472 | UDP | Flannel VXLAN — k3s's own docs say this one specifically should never be exposed to the open internet |

  ```bash
  sudo firewall-cmd --permanent --add-port=6443/tcp
  sudo firewall-cmd --permanent --add-port=10250/tcp
  sudo firewall-cmd --permanent --add-port=8472/udp
  sudo firewall-cmd --reload
  ```

  **This list will grow.** It only covers k3s itself — once ingress-nginx and MetalLB are synced
  you'll want 80/443 open too, and other core pieces may need their own ports. Treat this as a
  starting point, not the final list, and update it here as new pieces come online.

### SELinux

**Status:** `Enforcing` on this box, and — once the stale pgdg repo below stopped interfering with
`k3s-selinux`'s install — it turned out *not* to be the actual blocker. Worth a standing check
anyway, since it's a common RHEL-family gotcha even when it isn't the cause:

```bash
getenforce                      # confirm current mode
rpm -q k3s-selinux               # should be installed
sudo journalctl -u k3s -n 200 --no-pager | grep -i denied   # any AVC denials logged?
```

If something does fail with a denial later: `sudo setenforce 0` temporarily confirms SELinux is
the cause, but isn't a real fix — the actual fix is whatever policy/context is missing, not leaving
the box permissive.

### Stale/conflicting yum repos

**What happened:** an earlier manual attempt at installing PostgreSQL 13 directly on the host (via
the PGDG yum repo) left a dead `pgdg13` repo registered in `/etc/yum.repos.d/` even after that
attempt was abandoned. `dnf`/`yum` refreshes metadata for *every* enabled repo on any transaction —
so `install.sh` triggering k3s's installer to pull in `k3s-selinux` failed on a repo with nothing
to do with k3s or Postgres, because that repo had gone `410 Gone` (PG13 hit end-of-life in November
2025). Fixed by removing it:

```bash
sudo rm /etc/yum.repos.d/pgdg*.repo
sudo dnf clean all
```

No longer needed at all now that Postgres runs via CloudNativePG inside the cluster
(`src/core/argocd/apps/postgres-operator.yaml`), not as a host package.

**General lesson:** if `install.sh` fails on a seemingly unrelated repo/package error, check
`dnf repolist all` for anything stale or broken before assuming the script itself is wrong.

### Private repo credentials for Argo CD

**What happened:** the deploy key used to `git clone` this repo onto the host only authenticates
the host's own git client — it does nothing for Argo CD's `repo-server` pod, which has no
credentials of its own by default. First sync of `root` failed with `error creating SSH agent:
SSH agent requested but SSH_AUTH_SOCK not-specified`.

**Status:** fixed at the script level. `bootstrap/install.sh` now takes `--repo-ssh-key <path>` and
registers that key as a repository credential Secret in the `argocd` namespace automatically.
Because that Secret lives in the `argocd` namespace, `bootstrap/teardown.sh` deletes it along with
everything else — pass `--repo-ssh-key` again on every fresh `install.sh` run against a private
repo, same as any other flag. No need to hand-create the Secret anymore.

### `teardown.sh` hanging forever on `kubectl delete application root`

**What happened:** `root` (only `root` — see `src/core/argocd/root-app.yaml`) carries the
cascade-delete finalizer `resources-finalizer.argocd.argoproj.io`, so Argo CD tears down
everything it manages before the object actually finalizes. In testing, that cascade stalled —
most likely tangled up with the crash-looping `postgres-operator` pod from the CRD-size issue
above, which couldn't finish processing one of its own custom resources' finalizers. The
`kubectl delete application root` line had no timeout, so it blocked the whole script silently,
forever, with zero output — no error, no progress, nothing to Ctrl-C into except an unresponsive
terminal.

**Status:** fixed at the script level (see changelog below) — bounded timeouts on every delete in
this stage, and if `root`'s cascade specifically doesn't finish in 90s, the script now forces it
through by stripping the finalizer directly, on the reasoning that `k3s-uninstall.sh` right after
wipes the whole node regardless, so there's nothing to lose by not waiting out a stuck cascade.

If you're ever stuck on an older copy of this script: find the hung process (`ps aux | grep
kubectl`), `kill` it and the parent `teardown.sh`/`sudo bash` processes, then run
`kubectl -n argocd patch application root --type=merge -p '{"metadata":{"finalizers":null}}'`
by hand before continuing with `kubectl delete namespace argocd` and `k3s-uninstall.sh` yourself.

### `__REPO_URL__`/`__REVISION__` placeholders left un-substituted in `apps/*.yaml`

**What happened:** `postgres-cluster`, `cert-manager-issuers`, `metallb-config`, and
`keycloak-instance` — the four `Application` manifests that source `manifests/*.yaml` from this
same repo, rather than an external chart — still had the literal strings `__REPO_URL__` and
`__REVISION__` as their `repoURL`/`targetRevision`. `bootstrap/install.sh`'s `sed` substitution
only ever runs against `root-app.yaml`, the one file it applies directly; everything under
`apps/` is read straight from git by Argo CD itself, with no templating step of its own. All four
sat stuck at `Unknown` sync status indefinitely — no crash, no clear top-level error, just silently
never resolving, since `kubectl get applications` doesn't surface the underlying comparison error
without an explicit `-o jsonpath='{.status.conditions}'` lookup.

**Status:** fixed by hardcoding real values directly in each manifest (see
`src/core/argocd/README.md`'s "Self-referencing apps" section for the full reasoning and the
tradeoff this creates — these four won't automatically follow `root` if you ever bootstrap from a
branch other than `dev`).

### `sealed-secrets`'s chart repo 404ing

**What happened:** `https://bitnami-labs.github.io/sealed-secrets/index.yaml` started returning
`404 Not Found`. The project's GitHub org renamed from `bitnami-labs` to `bitnami` at some point
after this manifest was first written, and the old Pages URL didn't redirect.

**Status:** fixed — `apps/sealed-secrets.yaml` now points at `https://bitnami.github.io/sealed-secrets`,
version bumped to the current `2.19.3` (appVersion `0.39.1`) since the old `2.16.1` pin predated the
move. Re-verified on 2026-08-30 that this is still the free, independently-maintained project, not
Bitnami's paywalled general catalog.

### Manual `sudo kubectl`/`sudo k3s-...` commands need the full path

**Reminder, not a script bug:** the `PATH` fix in `bootstrap/lib/common.sh` only helps commands run
*inside* the bootstrap scripts. Typing `sudo kubectl ...` or `sudo k3s-uninstall.sh` directly in your
own shell is a fresh `sudo` invocation, and `sudo` resolves commands via its own `secure_path`, not
your shell's `$PATH` — which is why this same `/usr/local/bin` gap bites again for any manual
command. Either type the full path (`sudo /usr/local/bin/kubectl ...`), or fix it once at the host
level via `sudo visudo` — find the `Defaults secure_path = ...` line and add `/usr/local/bin` to it.

## Already fixed in the scripts — nothing to do, kept here as a changelog

- **`bootstrap/lib/common.sh` now prepends `/usr/local/bin` to `PATH`.** Some `sudo` configs (a
  trimmed `secure_path`, common on hardened RHEL-family systems) don't include `/usr/local/bin`
  even though k3s's `kubectl` symlink lives there — which silently broke every bare `kubectl` call
  in these scripts. This is what caused the "stuck waiting for node Ready" hang: the failure was
  real (`kubectl: command not found`), just invisible, because the wait loop redirected stderr to
  `/dev/null` to suppress unrelated noise and swallowed the real error along with it.
- **`bootstrap/install.sh`'s Argo CD manifest apply now uses `kubectl apply --server-side
  --force-conflicts`.** Plain client-side `kubectl apply` fails on Argo CD's
  `applicationsets.argoproj.io` CRD — it's large enough that the `last-applied-configuration`
  annotation client-side apply generates exceeds Kubernetes' 256KiB annotation limit.
- **`bootstrap/install.sh`'s node-Ready wait is now bounded (3 minutes) and checks `kubectl`
  actually resolves before entering the loop**, instead of being able to spin silently forever on
  a masked failure the way it did above.
