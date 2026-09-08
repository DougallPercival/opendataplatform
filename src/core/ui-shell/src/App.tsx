// ui-shell-plan.md item 3: real browser login, still deliberately not real
// nav (that's item 5, a separate future branch) — just enough UI to prove
// the auth flow actually works end to end: a login button when logged out,
// the logged-in username + a logout button when logged in. See
// src/auth/AuthContext.tsx for the actual OAuth2/PKCE implementation this
// consumes.
import { useAuth } from './auth/useAuth'

function App() {
  const { status, tokens, message, login, logout } = useAuth()

  return (
    <main>
      <h1>ui-shell</h1>
      {status === 'loading' && <p>loading...</p>}
      {status === 'unauthenticated' && (
        <>
          <p>not logged in.</p>
          <button onClick={login}>Log in</button>
        </>
      )}
      {status === 'authenticated' && tokens && (
        <>
          <p>logged in as {tokens.preferredUsername}.</p>
          <button onClick={logout}>Log out</button>
        </>
      )}
      {status === 'error' && <p>login error: {message}</p>}
    </main>
  )
}

export default App
