import { useState } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { useWorkspace } from '../workspace/useWorkspace'
import { ModuleIcon } from '../modules/icons'
import styles from './Addons.module.css'
import { AddonMutationError } from './mutations'
import { AddonsError, type AddonEntry } from './api'
import { useAddons } from './useAddons'
import { useAddonMutations, type UseAddonMutationsResult } from './useAddonMutations'
import type { MutationEntry } from './mutationState'

/** gateway's own `_NOT_INSTALLED_STATUS` value (app/modules.py) — the same
 * string `GET /modules/check-requirements` already returns, confirmed live
 * against homelab-dev during item 6's verification. Compared as a literal
 * here the same way ../modules/ModuleList.module.css's own `data-status`
 * selectors are literal strings — there's no shared constants module between
 * gateway and ui-shell today. */
const NOT_INSTALLED_STATUS = 'not installed'

/** gateway's `require_role(derived, "editor")` — the minimum role
 * app/modules.py's install/uninstall endpoints actually enforce
 * (app/auth.py's `_ROLE_PRIORITY`, owner > editor > viewer). Copied here as
 * a literal set for the same reason NOT_INSTALLED_STATUS above is: gating
 * the buttons client-side is a UX nicety (skip the round trip to a 403 a
 * viewer could never act on), not the real enforcement — gateway checks this
 * again on every request regardless of what this renders. */
const MUTATION_ROLES = new Set(['owner', 'editor'])

/** The "/addons" route content inside shell/Shell.tsx — item 7 of
 * ui-shell-plan.md. Lists gateway's full static+live module catalog (item 6,
 * GET /modules/catalog) and, for anyone with at least editor access in the
 * selected workspace, real Install/Remove buttons wired to gateway's
 * POST /modules/{id}/install and .../uninstall (the mutation mechanism,
 * feature/gateway-module-lifecycle-dispatch — already live-verified end to
 * end against homelab-dev). Both endpoints are fire-and-forget (a 202 just
 * means "queued"); useAddonMutations is what turns that into something this
 * page can actually show someone. */
export function Addons() {
  const { logout } = useAuth()
  const { workspaces, selected } = useWorkspace()
  const { status, entries, error, refetch } = useAddons(selected)
  const currentRole = workspaces.find((w) => w.name === selected)?.role
  const canMutate = currentRole !== undefined && MUTATION_ROLES.has(currentRole)
  const mutations = useAddonMutations(selected, entries, status, refetch, NOT_INSTALLED_STATUS)

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
      <ul className={styles.list}>
        {entries.map((entry) => (
          <AddonRow
            key={entry.moduleId}
            entry={entry}
            canMutate={canMutate}
            mutation={mutations.stateFor(entry.moduleId)}
            onInstall={mutations.install}
            onUninstall={mutations.uninstall}
          />
        ))}
      </ul>
    </div>
  )
}

function AddonRow({
  entry,
  canMutate,
  mutation,
  onInstall,
  onUninstall,
}: {
  entry: AddonEntry
  canMutate: boolean
  mutation: MutationEntry | undefined
  onInstall: UseAddonMutationsResult['install']
  onUninstall: UseAddonMutationsResult['uninstall']
}) {
  const installed = entry.status !== NOT_INSTALLED_STATUS
  const requiresText = entry.requires.length > 0 ? `Requires: ${entry.requires.join(', ')}` : 'No dependencies'
  const [confirmingRemove, setConfirmingRemove] = useState(false)

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
        <AddonRowNote mutation={mutation} />
      </div>
      <span className={styles.status} data-status={entry.status}>
        {entry.status}
      </span>
      <AddonRowActions
        installed={installed}
        canMutate={canMutate}
        mutation={mutation}
        confirmingRemove={confirmingRemove}
        onConfirmRemoveClick={() => setConfirmingRemove(true)}
        onCancelRemove={() => setConfirmingRemove(false)}
        onInstall={() => onInstall(entry.moduleId)}
        onUninstall={() => {
          setConfirmingRemove(false)
          onUninstall(entry.moduleId)
        }}
      />
    </li>
  )
}

function AddonRowNote({ mutation }: { mutation: MutationEntry | undefined }) {
  if (!mutation) return null

  if (mutation.phase === 'queued') {
    return (
      <span className={styles.mutationNote}>
        {mutation.action === 'install' ? 'Install queued — checking for it to finish…' : 'Removal queued — checking for it to finish…'}
      </span>
    )
  }

  if (mutation.phase === 'timed-out') {
    return <span className={styles.mutationNote}>Still processing — refresh in a bit to check, or try again below.</span>
  }

  if (mutation.phase === 'error') {
    return <span className={styles.mutationError}>{describeMutationError(mutation)}</span>
  }

  // 'submitting' has no note of its own — the button's own label already says so.
  return null
}

function describeMutationError(mutation: MutationEntry): string {
  const err = mutation.error
  if (err instanceof AddonMutationError) {
    if (err.status === 409 && err.unsatisfied && err.unsatisfied.length > 0) {
      return `Can't install — missing: ${err.unsatisfied.join(', ')}`
    }
    return err.detail
  }
  return err?.message ?? 'Something went wrong.'
}

function AddonRowActions({
  installed,
  canMutate,
  mutation,
  confirmingRemove,
  onConfirmRemoveClick,
  onCancelRemove,
  onInstall,
  onUninstall,
}: {
  installed: boolean
  canMutate: boolean
  mutation: MutationEntry | undefined
  confirmingRemove: boolean
  onConfirmRemoveClick: () => void
  onCancelRemove: () => void
  onInstall: () => void
  onUninstall: () => void
}) {
  if (!canMutate) {
    return (
      <button type="button" disabled title="Requires at least editor access in this workspace">
        {installed ? 'Remove' : 'Install'}
      </button>
    )
  }

  if (mutation?.phase === 'submitting' || mutation?.phase === 'queued') {
    return (
      <button type="button" disabled>
        {mutation.action === 'install' ? 'Installing…' : 'Removing…'}
      </button>
    )
  }

  // 'timed-out', 'error', and no mutation at all (idle) all fall through to
  // a normal, clickable button below — neither of the first two should leave
  // a row stuck forever.

  if (!installed) {
    return (
      <button type="button" onClick={onInstall}>
        Install
      </button>
    )
  }

  if (confirmingRemove) {
    return (
      <div className={styles.confirmRemove}>
        <p className={styles.confirmRemoveNote}>Removing can take a few minutes to fully complete.</p>
        <span className={styles.confirmRow}>
          <button type="button" className={styles.dangerButton} onClick={onUninstall}>
            Confirm remove
          </button>
          <button type="button" onClick={onCancelRemove}>
            Cancel
          </button>
        </span>
      </div>
    )
  }

  return (
    <button type="button" onClick={onConfirmRemoveClick}>
      Remove
    </button>
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
