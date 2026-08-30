# Known issues & host prerequisites

Running notes from testing `bootstrap/install.sh` on a real box, kept separate from
[`ARCHITECTURE.md`](architecture/ARCHITECTURE.md) because this is host-specific troubleshooting,
not design. First target: RHEL/Rocky/AlmaLinux 9. Add to this as new hosts turn up new gotchas.

## Still on you — host-level decisions a script shouldn't make for you

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
