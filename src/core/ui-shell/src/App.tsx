// Deliberately static — no state, no data fetching, no auth. This is
// item 1 of docs/architecture/ui-shell-plan.md: prove the scaffold/deploy
// pipeline (git push -> CI build -> Argo deploy -> reachable over Ingress)
// works end-to-end before any of items 2-8's real design decisions (nav,
// auth, module registry, ...) get built on top of it. See that doc and
// this package's own README for what's deliberately not here yet.
function App() {
  return (
    <main>
      <h1>ui-shell</h1>
      <p>placeholder — nothing live here yet</p>
    </main>
  )
}

export default App
