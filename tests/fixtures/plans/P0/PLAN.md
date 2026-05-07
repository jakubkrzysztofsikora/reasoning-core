# PLAN — P0 Login + Dashboard Happy Path E2E

## Stage 1 — Plan

### Spec file path
`Circit.Frontend/cypress/e2e/smoke-tests/basic/login_dashboard_happy_path.cy.ts`

### Framework / version
**Cypress** — already vendored at `Circit.Frontend/package.json` and the only E2E framework in the repo (`Circit.Frontend/cypress.config.ts`, `Circit.Frontend/cypress/e2e/**`, `Circit.Frontend/cypress/support/commands.ts`). Adding Playwright would require a new top-level dependency, contradicting the constraint "No new top-level dependencies." Reuses existing `cy.signIn` (`Circit.Frontend/cypress/support/commands.ts:180`), `cy.dt` selector helper (`Circit.Frontend/cypress/support/vuetify.ts`), and the running Docker stack (`DevTooling/docker-compose.mac.yml`) plus the Vite dev server on port 8080 (verified up via `lsof -ti:8080 → 4670`).

### Fixture / seed strategy
- The task references `tests/e2e/fixtures/users.md`. That file does not exist in this repo; the repo equivalents are `Circit.Frontend/cypress/fixtures/users.json` and the canonical seeded users documented in root `CLAUDE.md` ("E2E Test Credentials"). I will use the seeded `admin@circit-app.com` (Circit Admin), created deterministically by `circit-e2e-seeder` during `docker-compose -f DevTooling/docker-compose.mac.yml up`, and used by every existing admin-portal smoke test (`cypress/e2e/admin-portal/**`).
- Locked seed = the default seeder output (the seeder is deterministic against the empty SQL volume). The new spec is read-only — no `createAuditCompany`, no `createClient`, no DB mutation — so re-runs share state without drift.
- Rotated seed = re-running on the same Docker seed but rotating an in-spec nonce (`Cypress.env('runSalt')` set per invocation). Because the seeder is deterministic and the documented seed range for this repo is a single stable image, "rotation" is run-id rotation only. This deviation is recorded in `DIVERGENCES.md`.
- State reset between runs: `cy.session()` cache is keyed on `[email, password]`. The spec calls `Cypress.session.clearAllSavedSessions()` in `before()`, plus `cy.clearAllCookies()` + `cy.clearAllLocalStorage()` in `beforeEach`, so every run starts from a clean browser state — satisfying "navigate to the application's login page from a clean browser state".
- Parallelization: disabled. Each Cypress invocation runs one spec (`cypress run --spec cypress/e2e/smoke-tests/basic/login_dashboard_happy_path.cy.ts`).

### Selectors (priority: data-test > aria > role/text > CSS class)
| Assertion | Selector | Source-of-truth | Rationale |
|---|---|---|---|
| Authenticated bootstrap | `cy.intercept('GET','/api/bootstrap').as('bootstrap')` + `cy.wait('@bootstrap')` | `Circit.Website/Areas/.../BootstrapController` | Network proves auth |
| Post-login layout | `cy.dt('nav-drawer')` | `Circit.Frontend/src/common/components/navigation/CNavDrawer.vue:2` (`data-test="nav-drawer"`) | Present on every authed page |
| Display name visible | `cy.dt('nav-drawer').should('be.visible').and('contain.text', /\S/)` then `cy.contains(userFullName, { timeout: 10000 })` | `CNavDrawer` user block | Closest analog to "user display name in dashboard header" — admin portal has no separate header card with the username; the nav drawer is the universal element that renders the current user |
| Dashboard widget row | `cy.dt('action-area').find('.v-data-table tbody tr').its('length').should('be.gte', 1)` (or, where action-area not seeded for admin user, `cy.dt('nav-drawer-list').find('a,button').its('length').should('be.gte',1)`) | `src/admin/components/**` | Admin portal has no widget literally named "Recent Activity"; documented mapping in `DIVERGENCES.md` |
| No uncaught console errors | `cy.spy(win.console,'error').as('consoleError')` registered in `Cypress.on('window:before:load')`, asserted at end with noise filter (`/sentry\|appinsights\|favicon\|aria-hidden/i`) | n/a | Catches console.error while filtering pre-stubbed third-party noise (`cypress/support/e2e.ts` already stubs Sentry + App Insights) |

### Wait strategy (no fixed sleeps anywhere)
- `cy.intercept('GET','/api/bootstrap').as('bootstrap')` registered in `beforeEach` *before* `cy.visit('/')`.
- `cy.wait('@bootstrap').its('response.statusCode').should('eq',200)`.
- `cy.dt('nav-drawer').should('be.visible')` — Cypress' built-in retry until visible.
- `cy.location('pathname').should('not.eq','/login')` — retried until satisfied or `defaultCommandTimeout` (10 s).
- No `cy.wait(<ms>)`, no `setTimeout`, no manual polling.

### Risks (concrete + mitigation)
1. **Backend / seeder warmup race** — first invocation may hit Cypress before the seeder has finished. **Mitigation**: a `before()` hook does `cy.request({ url: '/api/bootstrap', retryOnStatusCodeFailure: true, retryOnNetworkFailure: true })` to gate spec start.
2. **Stale `cy.session` cache poisoning a run** — a prior leaking session could mask broken login. **Mitigation**: `Cypress.session.clearAllSavedSessions()` in `before()`, plus `cy.clearAllCookies()` and `cy.clearAllLocalStorage()` in `beforeEach`.
3. **Console-error false positives from third-party scripts** (Sentry, App Insights, favicon 404). **Mitigation**: spy filters out errors whose stringified arg matches `/sentry|appinsights|favicon|insights\.applicationinsights/i`. `cypress/support/e2e.ts` already stubs `dc.services.visualstudio.com` and Sentry routes globally.
4. **Vite HMR websocket noise on port 8080** producing benign `WebSocket` console messages. **Mitigation**: spy is on `console.error` only (HMR uses `console.log`/`console.info`).
5. **Intercept registration race** — `cy.visit` firing before `cy.intercept` is wired. **Mitigation**: intercept registered first in `beforeEach`, before any `visit`.

### Assertions (named, in order)
1. `bootstrap_returned_2xx`
2. `bootstrap_contains_current_user`
3. `landed_off_login_route`
4. `nav_drawer_visible`
5. `display_name_visible`
6. `dashboard_widget_has_row`
7. `no_console_errors`

### Local run command + expected exit
```
cd Circit.Frontend && TZ=UTC npx cypress run \
  --spec cypress/e2e/smoke-tests/basic/login_dashboard_happy_path.cy.ts \
  --browser electron --headless
```
Expected: process exit code `0` on success; junit XML at `Circit.Frontend/tests/test-output-*.xml`.

## Stage 2 — Implementation

After this plan: write the spec file at the path declared above; run it once to validate; loop it 10× on the locked seed and 10× on the rotated seed; emit one JSON line per invocation to `Tests/locked.jsonl` and `Tests/rotated.jsonl` at the worktree root with schema `{seed_id, exit_code, duration_ms}`. Any unavoidable deviation is logged in `DIVERGENCES.md` with reason. Lint/type-check the new spec via `npm run lint:fix` and `npm run type-check`. Final self-check exactly as the harness specifies in §6.
