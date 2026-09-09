import { Link, useParams } from 'react-router'
import { useWorkspace } from '../workspace/useWorkspace'
import styles from './ModuleDetail.module.css'
import { ModuleIcon } from './icons'
import { useModules } from './useModules'

/** The "modules/:moduleId" route inside shell/Shell.tsx. Built entirely from
 * the already-fetched GET /modules list (no second gateway call — gateway
 * has no per-module detail route). Routed by module_id, not raw nav_path —
 * module_id is guaranteed present/unique/URL-safe (^[a-z0-9-]+$ in
 * ModuleManifest), nav_path is completely unvalidated server-side and can be
 * null for any module installed before item 4's annotations existed. nav_path
 * is still shown below as a labeled field, just not used to build this route. */
export function ModuleDetail() {
  const { moduleId } = useParams<{ moduleId: string }>()
  const { selected } = useWorkspace()
  const { status, modules } = useModules(selected)

  if (status === 'idle' || status === 'loading') {
    return <p className={styles.message}>Loading...</p>
  }

  const module = modules.find((m) => m.moduleId === moduleId)

  if (!module) {
    return (
      <div className={styles.message}>
        <p>
          "{moduleId}" isn't installed in the "{selected}" workspace (or the link is stale).
        </p>
        <Link to="/">Back to modules</Link>
      </div>
    )
  }

  return (
    <div className={styles.detail}>
      <Link to="/" className={styles.back}>
        &larr; Back to modules
      </Link>
      <div className={styles.header}>
        <ModuleIcon icon={module.icon} />
        <h2>{module.displayName}</h2>
      </div>
      <dl className={styles.fields}>
        <dt>Status</dt>
        <dd>{module.status}</dd>
        <dt>Module ID</dt>
        <dd>{module.moduleId}</dd>
        <dt>Icon</dt>
        <dd>{module.icon}</dd>
        <dt>nav_path</dt>
        <dd>{module.navPath ?? <em>not set</em>}</dd>
      </dl>
      <p className={styles.notice}>
        Opening {module.displayName}'s own UI isn't built yet — see item 8 of ui-shell-plan.md
        (reverse-proxying into a module's own UI).
      </p>
    </div>
  )
}
