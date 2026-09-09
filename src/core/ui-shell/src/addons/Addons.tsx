import { Link } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { useWorkspace } from '../workspace/useWorkspace'
import { ModuleIcon } from '../modules/icons'
import styles from './Addons.module.css'
import { AddonsError, type AddonEntry } from './api'
import { useAddons } from './useAddons'

/** gateway's own `_NOT_INSTALLED_STATUS` value (app/modules.py) — the same
 * string `GET /modules/check-requirements` already returns, confirmed live
 * against homelab-dev during item 6's verification. Compared as a literal
 * here the same way ../modules/ModuleList.module.css's own `data-status`
 * selectors are literal strings — there's no shared constants module between
 * gateway and ui-shell today. */
const NOT_INSTALLED_STATUS = 'not installed'

/** The "/addons" route content inside shell/Shell.tsx — item 7 of
 * ui-shell-plan.md, scoped to read-only per this session's AskUserQuestion
 * decision: lists gateway's full static+live module catalog (item 6,
 * GET /modules/catalog), with a disabled Install affordance rather than a
 * real one. The actual install/uninstall mechanism (and the git-write /
 * workflow_dispatch trust-boundary question behind it) is a separate future
 * branch — nothing here fires a mutation. */
export function Addons() {
  const { logout } = useAuth()
  const { workspaces, selected } = useWorkspace()
  const { status, entries, error, refetch } = useAddons(selected)

  if (workspaces.length === 0) {
    return <p className={styles.message}>You don't belong to any workspace yet.</p>
  }

  if (status === 'idle' || status === 'loading') {
    return <p className={styles.message}>Loading add-ons...</p>
  }

  if (status === 'error') {
    return <AddonsListError error={error} onRetry={refetch} onLogout={logout} />
  }

  if (entries.length === 0) {
    // Distinct copy from ModuleList's empty state on purpose: an empty
    // catalog means no module.yaml shipped anywhere in the release, a real
    // misconfiguration — not "nothing installed in this workspace."
    return <p className={styles.message}>No add-ons found.</p>
  }

  return (
    <div className={styles.page}>
      <p className={styles.notice}>Installing and removing add-ons isn't built yet — see item 7 of ui-shell-plan.md.</p>
      <ul className={styles.list}>
        {entries.map((entry) => (
          <AddonRow key={entry.moduleId} entry={entry} />
        ))}
      </ul>
    </div>
  )
}

function AddonRow({ entry }: { entry: AddonEntry }) {
  const installed = entry.status !== NOT_INSTALLED_STATUS
  const requiresText = entry.requires.length > 0 ? `Requires: ${entry.requires.join(', ')}` : 'No dependencies'

  return (
    <li className={styles.item}>
      <ModuleIcon icon={entry.icon} />
      <div className={styles.info}>
        {installed ? (
          <Link to={`/modules/${entry.moduleId}`} className={styles.name}>
            {entry.displayName}
          </Link>
        ) : (
          <span className={styles.name}>{entry.displayName}</span>
        )}
        <span className={styles.requires}>{requiresText}</span>
      </div>
      <span className={styles.status} data-status={entry.status}>
        {entry.status}
      </span>
      <button type="button" disabled title="Not built yet — see item 7 of ui-shell-plan.md">
        Install
      </button>
    </li>
  )
}

function AddonsListError({
  error,
  onRetry,
  onLogout,
}: {
  error: AddonsError | Error | null
  onRetry: () => void
  onLogout: () => void
}) {
  if (error instanceof AddonsError) {
    if (error.status === 401) {
      return (
        <div className={styles.message}>
          <p>Your session has expired.</p>
          <button onClick={onLogout}>Log in again</button>
        </div>
      )
    }
    if (error.status === 403) {
      // Gateway's own detail message is already human-readable and names the
      // workspace + what to do about it — rendered verbatim, same precedent
      // as ../modules/ModuleList.tsx's ModuleListError.
      return <p className={styles.message}>{error.detail}</p>
    }
    if (error.status === 503) {
      return (
        <div className={styles.message}>
          <p>The module catalog is temporarily unavailable. {error.detail}</p>
          <button onClick={onRetry}>Retry</button>
        </div>
      )
    }
    return (
      <div className={styles.message}>
        <p>{error.detail}</p>
        <button onClick={onRetry}>Retry</button>
      </div>
    )
  }
  return (
    <div className={styles.message}>
      <p>{error?.message ?? 'Something went wrong loading add-ons.'}</p>
      <button onClick={onRetry}>Retry</button>
    </div>
  )
}
