# TW-249 TDD Evidence — GitHub Pages subpath fix

Ticket: TW-249 | Branch: `fix/tw-249-pages-subpath` | Base: `main` @ 769ad4e
Intent: make the Vite/React SPA serve correctly at the GitHub Pages project subpath
`https://twistorhq.github.io/lbt-os/` (build `--base=/lbt-os/`).

## RED (problem demonstrated, TW-236 investigation)

1. Router: `App.jsx` used bare `<BrowserRouter>`. Verified with the repo's own
   `react-router-dom`: `matchPath('/', '/lbt-os/')` → `null` (only `'*'` matched),
   so the live URL rendered `NotFound` instead of `MarketingHome`.
2. Images: `MarketingHome.jsx` (2 sites) and `StoryArc.jsx` (2 sites) used
   root-absolute `src="/img/..."`. Vite does not rewrite absolute public-dir
   paths, so they 404 under `/lbt-os/`.

## GREEN (fix)

- `frontend/src/App.jsx`: `<BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '')} ...>`
  Trailing-slash strip is load-bearing: the repo's `@remix-run/router@6.30.3`
  `stripBasename` returns `null` for path `/lbt-os` against basename `/lbt-os/`,
  while basename `/lbt-os` matches all three real cases.
- `frontend/src/pages/MarketingHome.jsx`, `frontend/src/components/StoryArc.jsx`:
  `/img/hvac-*.jpg` → `` `${import.meta.env.BASE_URL}img/hvac-*.jpg` ``.
- `frontend/index.html`: `<title>` "LBT OS — Lean Business Tracker" → "Twistor Trades".
- Fix round (Rosa): `Strategy.jsx:689` plain `<a href="/app/billing">` → `<Link to=...>`
  (bypassed basename → 404 + full reload); `MarketingHome.jsx` desktop nav and
  mobile menu now branch `l.href.startsWith('/')` → `<Link to>` else `<a>`,
  matching the existing footer convention.

## Verification (all run against `vite build --base=/lbt-os/`, dummy Clerk key)

| Check | Result |
|---|---|
| Build healthy, app not tree-shaken out | 1,228.50 kB JS bundle containing "money walking out your door" |
| `basename:"/lbt-os/".replace(/\/$/,"")` in built bundle | present at BrowserRouter call site |
| `stripBasename` (repo's own @remix-run/router) | `/lbt-os/`→`/`, `/lbt-os`→`/`, `/lbt-os/sign-in`→`/sign-in`, `/lbt-os/app/billing`→`/app/billing` all PASS; `/lbt-osx`→null (no prefix collision) |
| Image paths in bundle | literal `"/lbt-os/img/hvac-tech.jpg"`, `"/lbt-os/img/hvac-team.jpg"` |
| No remaining `src="/img` in `frontend/src/` | `grep -rn 'src="/img' src/` → none |
| No other root-absolute refs | grepped `src="/`, `href="/` (both quote styles), dynamic hrefs across `frontend/src` and `public/` → clean; `index.html` `/src/main.jsx` is dev-only (Vite rewrites at build); favicon is data-URI; no og tags/manifest/sitemap |
| `<title>Twistor Trades</title>` in `dist/index.html` | present |
| Dev-mode behavior | `BASE_URL='/'` → `basename=''` → react-router normalizes to `/` → identity; dev behavior unchanged |
| `npx vitest run` | 8/8 pass |

## Scope note

Remaining in-app "LBT OS" body copy (Dashboard/Admin/etc.) is out of this ticket's scope.
Production deploy (TW-236) still requires the real `VITE_CLERK_PUBLISHABLE_KEY` at
build time — without it the build silently emits a degenerate ~375KB vendor-only
bundle (entry throws at load, blank page).
