// Talks to gateway's POST /modules/{id}/install and .../uninstall
// (src/core/gateway/app/modules.py, ui-shell-plan.md item 7's mutation
// mechanism — feature/gateway-module-lifecycle-dispatch, already
// live-verified end to end against homelab-dev). Structurally a sibling of
// ./api.ts's GET /modules/catalog wrapper, not a shared helper — same "hand-
// rolled per endpoint" choice that file's own comment already makes, now
// applied to a POST instead of a GET.
//
// Both endpoints are fire-and-forget: a 202 means gateway told GitHub to
// start the module-lifecycle workflow, nothing more. Neither call's success
// means the module is actually installed/removed yet — see useAddonMutations
// for how the rest of the page turns that into something a user can watch.
import { gatewayBaseUrl } from '../modules/config'

export type AddonMutationAction = 'install' | 'uninstall'

/** Mirrors ./api.ts's AddonsError, plus one field: gateway's 409 (unsatisfied
 * `requires`) carries a structured `unsatisfied` list alongside `detail` —
 * see app/modules.py's install_module docstring for the exact response
 * shape. Every other error status (401/403/404/422/503) only ever has
 * `detail`, so this stays optional rather than every AddonMutationError
 * needing one. */
export class AddonMutationError extends Error {
  status: number
  detail: string
  unsatisfied?: string[]

  constructor(action: AddonMutationAction, status: number, detail: string, unsatisfied?: string[]) {
    super(`${action} failed: ${status} ${detail}`)
    this.name = 'AddonMutationError'
    this.status = status
    this.detail = detail
    this.unsatisfied = unsatisfied
  }
}

async function postMutation(action: AddonMutationAction, moduleId: string, workspace: string, accessToken: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`${gatewayBaseUrl()}/modules/${encodeURIComponent(moduleId)}/${action}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'X-Workspace': workspace,
    },
    signal,
  })

  if (!response.ok) {
    let detail = response.statusText
    let unsatisfied: string[] | undefined
    try {
      const body = (await response.json()) as { detail?: string; unsatisfied?: string[] }
      if (body.detail) detail = body.detail
      if (Array.isArray(body.unsatisfied)) unsatisfied = body.unsatisfied
    } catch {
      // Non-JSON error body (shouldn't happen against a real gateway
      // response, but don't let a parse failure mask the original HTTP
      // status) — fall back to statusText, already assigned above.
    }
    throw new AddonMutationError(action, response.status, detail, unsatisfied)
  }

  // 202's own JSON body ({module_id, action, status: "queued", detail}) has
  // nothing the caller needs — the point of calling this is "did the
  // dispatch succeed," not the echoed request. useAddonMutations tracks the
  // "queued" state itself.
}

export function installModule(moduleId: string, workspace: string, accessToken: string, signal?: AbortSignal): Promise<void> {
  return postMutation('install', moduleId, workspace, accessToken, signal)
}

export function uninstallModule(moduleId: string, workspace: string, accessToken: string, signal?: AbortSignal): Promise<void> {
  return postMutation('uninstall', moduleId, workspace, accessToken, signal)
}
