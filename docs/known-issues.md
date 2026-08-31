# Known issues & host prerequisites

Running notes from testing `bootstrap/install.sh` on a real box, kept separate from
[`ARCHITECTURE.md`](architecture/ARCHITECTURE.md) because this is host-specific troubleshooting,
not design. First target: RHEL/Rocky/AlmaLinux 9. Add to this as new hosts turn up new gotchas.

## Still on you — host-level decisions a script shouldn't make for you

### `postgres-backup.yaml`'s ObjectStore endpoint — confirm it after SeaweedFS syncs

Added 2026-08-31 alongside SeaweedFS/Postgres backup wiring. `manifests/postgres-backup.yaml`'s
`ObjectStore.spec.configuration.endpointURL` ships with a best-guess Service DNS name for
SeaweedFS's S3 gateway (`storage-seaweedfs-s3.storage-seaweedfs.svc.cluster.local:8333`) — the
Helm chart's exact generated Service name depends on its `_helpers.tpl` fullname template, which
wasn't worth vendoring the whole chart locally just to confirm ahead of a real sync. Same class of
"can't be known until it actually runs on your cluster" as MetalLB's pool below, not a bug.

**Fix:** once `apps/optional/storage-seaweedfs` has synced, confirm the real name —

```bash
kubectl -n storage-seaweedfs get svc
```

— and update `endpointURL` in both `manifests/postgres-backup.yaml` (the `ObjectStore`) and the
`S3_ENDPOINT` env var on the bucket-creation `Job` in that same file if it differs from the
placeholder. Until this is confirmed, `postgres-backup` and `postgres-cluster`'s WAL archiving will
sit Degraded/erroring — same "commit a TODO'd default, Argo CD shows it degraded until addressed"
pattern as `metallb-pool.yaml`/`cluster-issuer.yaml` below, and expected on a fresh install.

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

### node-exporter's default port (9100) can collide with a pre-existing host exporter

**What happened:** `monitoring.yaml`'s `kube-prometheus-stack` bundles `prometheus-node-exporter`,
which runs with `hostNetwork: true` — it binds directly to the host's port 9100, not just a
container port. On a box already running its own native `node_exporter` (feeding a separate,
pre-existing Prometheus setup), that's an immediate, permanent `CrashLoopBackOff`:
`listen tcp 0.0.0.0:9100: bind: address already in use`.

**Status:** worked around, not a script fix — this is a legitimate per-host conflict, not a bug.
`monitoring.yaml` now overrides the chart's `prometheus-node-exporter.service.port`/`targetPort` to
`9101` instead of touching whatever's already on `9100`. Confirmed against the chart's own
`ServiceMonitor` template that Prometheus discovers this port by name (`metrics`), not the number,
so scraping keeps working automatically with no other change needed. If you hit the same collision
on a different host, check what's already bound first (`ss -ltnp | grep 9100`) before assuming which
side should move.

**Reminder if you change a synced Application's Helm values:** Argo CD only sees what's pushed to
git — running `kubectl annotate ... refresh=hard` (or any resync/restart) *before* the commit
actually reaches the branch it's tracking just re-applies the same old config. If a fix doesn't seem
to take effect, check the live resource directly (e.g. `kubectl get svc ... -o jsonpath='{.spec.ports}'`)
to confirm what's actually deployed before assuming the fix itself is wrong.

### Keycloak's pod stuck unhealthy after a fresh install — two separate bugs, not one

**What happened:** `platform-0` sat unhealthy for the first ~40 minutes of a fresh install
(2026-08-30), showing two *different* failures in sequence — worth recording as two bugs, not one,
since fixing the first one just uncovered the second:

1. `Warning FailedMount ... secret "keycloak-tls" not found`. `spec.http.tlsSecret: keycloak-tls`
   is Keycloak's own internal HTTPS listener cert, required for the pod to start at all — this is
   completely independent of `spec.ingress.enabled`. An earlier comment in this repo assumed the
   hostname TODO "wasn't blocking anything right now" because ingress was disabled; that was true
   for external routing, not for this field. Nothing in the scaffold created that Secret.
2. Once that was fixed, the pod moved to `CreateContainerConfigError` /
   `Error: secret "platform-postgres-app" not found` — see the next entry; this is really the same
   root cause as the namespace problem below, just surfacing as a container start failure here.

**Status:** both fixed.

- `keycloak-tls`: `manifests/keycloak-instance.yaml` now includes a `cert-manager.io/v1 Certificate`
  for `keycloak-tls`, issued off the `platform-ca` `ClusterIssuer` (see `manifests/cluster-issuer.yaml`).
- The Postgres credential: see "Postgres is a shared core service, but its auto-generated credential
  can't leave its namespace" below.

### Postgres is a shared core service, but its auto-generated credential can't leave its namespace

**What happened:** `postgres-cluster.yaml`'s `bootstrap.initdb` (deliberately, see that file's
comments) has no `secret:` field, so CNPG auto-generates the `keycloak` role's password itself, into
a Secret named `platform-postgres-app` — **in the `postgres` namespace**, because that's where the
`Cluster` resource lives. Kubernetes Secrets can't cross namespaces, and Keycloak's CR lives in the
`keycloak` namespace, so its `db.usernameSecret`/`passwordSecret` references could never resolve.
This wasn't a fluke of timing — it was never going to work as written, and it'll happen again for
any *future* service that wants to use this same shared Postgres cluster from its own namespace.

Checked whether CNPG has a supported way to annotate that auto-generated secret for a mirroring tool
to pick up (e.g. via `inheritedMetadata`) before reaching for a bigger fix — confirmed via a
CloudNativePG GitHub discussion that it doesn't: "no direct mechanism for annotating CNPG's
auto-generated app/superuser secrets."
([cloudnative-pg/cloudnative-pg#3653](https://github.com/cloudnative-pg/cloudnative-pg/discussions/3653))

**Status:** fixed generally, not just for Keycloak — this will recur for every future service that
wants this Postgres cluster, so it's solved once at the platform level rather than patched per-app:

- `apps/reflector.yaml` deploys [kubernetes-reflector](https://github.com/emberstack/kubernetes-reflector)
  (wave 0, core tier) — a small controller that mirrors an annotated Secret into other namespaces and
  keeps it in sync.
- `bootstrap/install.sh` (step 2d) generates one real credential — `platform-postgres-keycloak-credentials`,
  a plain `kubernetes.io/basic-auth` Secret in the `postgres` namespace, **never committed to git** —
  idempotently (only the first time; re-running the script doesn't rotate an existing credential out
  from under a running cluster), and annotates it for Reflector's auto-mirror mode
  (`reflection-auto-namespaces: keycloak`).
- `manifests/postgres-cluster.yaml`'s `Cluster` now has a `managed.roles` entry for `keycloak` pointing
  at that Secret — CNPG reconciles the role's actual database password to match it on an ongoing
  basis, which works whether the cluster is brand new or (like the one this was fixed against)
  already past its initial bootstrap.
- `manifests/keycloak-instance.yaml`'s `db.usernameSecret`/`passwordSecret` now point at
  `platform-postgres-keycloak-credentials` instead of the CNPG-auto-generated one.

**Loose end, harmless:** CNPG's own auto-generated `platform-postgres-app` Secret in the `postgres`
namespace is now unused — nothing references it anymore, and its password value is stale (CNPG
doesn't delete it after bootstrap). Left in place rather than cleaned up; not worth the extra
complexity for a Secret nothing reads.

**To apply this on an already-running cluster:** push, then either re-run `bootstrap/install.sh`
(every earlier step is idempotent and no-ops on what's already there) or run step 2d's commands by
hand, then force-refresh `reflector`, `postgres-cluster`, and `keycloak-instance`:
`kubectl -n argocd annotate application reflector postgres-cluster keycloak-instance argocd.argoproj.io/refresh=hard --overwrite`.

**One more step this actually needed, worth remembering for next time:** after all of the above
synced clean (`keycloak-instance` Synced/Healthy, correct secret name confirmed in both the live
`Keycloak` CR's `spec.db` and the `StatefulSet`'s pod template env vars), `platform-0` itself *still*
didn't restart — same pod object, same 88-minute-old failure, still erroring on the old secret name.
The Keycloak operator's `StatefulSet` uses `updateStrategy: OnDelete` — the operator deliberately
controls pod rollout timing itself rather than letting Kubernetes replace pods automatically the
moment the template changes, so an already-running pod keeps its stale spec until something actually
deletes it. Fix was `kubectl -n keycloak delete pod platform-0` — safe here since it had never
started successfully, so there was no session/data state on it to lose. **Any future change to
`keycloak-instance.yaml`'s `Keycloak` CR will hit this same gap** — check `spec.updateStrategy.type`
on the `platform` `StatefulSet` if a CR change syncs clean but the pod doesn't visibly react, and
delete the pod by hand if it's `OnDelete`.

### Reaching Keycloak (or anything with a pinned `hostname`) by raw IP breaks after the first page

**What happened (2026-08-31):** port-forwarded to `platform-service` and connected fine via
`https://<homelab-IP>:8443` — the login page loaded — but everything broke immediately after
(submitting the login form, loading the admin console). `keycloak-instance.yaml`'s `Keycloak` CR
pins `spec.hostname.hostname: keycloak.platform.local`, and Keycloak's hostname provider enforces
that value strictly for every URL it generates once you're past the first static page — form
actions, redirects, the admin console's own asset URLs all point at `keycloak.platform.local`
regardless of what address the browser actually used to connect. A browser that can't resolve that
name has no way to follow them.

**Status:** worked around, not a bug — this is Keycloak (and `hostname`-pinned services generally)
behaving as configured, not a defect. Fix: make the configured hostname actually resolve, rather
than trying to bypass it. On whichever machine's browser you're using, add a line to its hosts file
— `C:\Windows\System32\drivers\etc\hosts` on Windows (Notepad "Run as administrator" to save it),
`/etc/hosts` on Linux/macOS — pointing the configured hostname at wherever you're actually reaching
the service:

```text
192.168.4.129 keycloak.platform.local
```

Then browse to `https://keycloak.platform.local:8443`, not the raw IP. This is a temporary,
per-machine, testing-only mapping — it doesn't affect anything else at that IP (hosts entries only
add name→IP resolution, they don't restrict or remove existing IP-based access), and it only
applies on the one machine whose hosts file you edited. Once `platform-gateway` and a real
`Ingress` exist, `keycloak.platform.local` is meant to resolve to ingress-nginx's IP
(`192.168.4.240`, from `metallb-pool.yaml`) instead of the homelab box's own address — update or
remove this entry at that point rather than leaving it pointed at the wrong place.

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
- **`bootstrap/install.sh` now installs and enables `iscsid`** (Longhorn's host prerequisite —
  `iscsi-initiator-utils` on dnf, `open-iscsi` on apt), best-effort and non-fatal, skippable with
  `--skip-iscsi`. Deliberately leaves `iscsi.service` alone (a separate unit that does unrelated
  boot-time node auto-discovery — enabling it can add 2-3 minutes to every boot for no benefit
  here); verified the `iscsid`-vs-`iscsi.service` distinction against
  [Longhorn's own docs](https://longhorn.io/kb/troubleshooting-open-iscsi-on-rhel/) rather than
  assuming.
- **`bootstrap/install.sh` now generates `platform-postgres-keycloak-credentials`** (step 2d) and
  `apps/reflector.yaml` mirrors it across namespaces — see "Postgres is a shared core service, but
  its auto-generated credential can't leave its namespace" above.
