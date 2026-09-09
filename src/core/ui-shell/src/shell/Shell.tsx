import { Navigate, Route, Routes } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { ModuleDetail } from '../modules/ModuleDetail'
import { ModuleList } from '../modules/ModuleList'
import { WorkspaceProvider } from '../workspace/WorkspaceContext'
import { WorkspaceSwitcher } from '../workspace/WorkspaceSwitcher'
import styles from './Shell.module.css'

/** The authenticated app frame — only ever rendered when useAuth().status ===
 * 'authenticated' (see ../App.tsx's Gate). Owns its own nested <Routes>: a
 * supported "descendant Routes" pattern, matched against whatever's left of
 * the URL after App.tsx's outer <Route path="/*"> already matched — no
 * <Outlet> needed since Shell is rendered directly as that route's element.
 * WorkspaceProvider mounts here and nowhere else, so an unauthenticated
 * visitor's tree never fires a /modules call. */
export function Shell() {
  const { tokens, logout } = useAuth()

  return (
    <WorkspaceProvider>
      <div className={styles.shell}>
        <header className={styles.header}>
          <h1 className={styles.title}>ui-shell</h1>
          <div className={styles.headerControls}>
            <WorkspaceSwitcher />
            <span className={styles.username}>{tokens?.preferredUsername}</span>
            <button onClick={logout}>Log out</button>
          </div>
        </header>
        <main className={styles.content}>
          <Routes>
            <Route path="/" element={<ModuleList />} />
            <Route path="modules/:moduleId" element={<ModuleDetail />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </WorkspaceProvider>
  )
}
