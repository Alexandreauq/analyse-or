# Portefeuille personnel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user track the stock positions they actually hold (manual entry, scoped to the ~180 companies already covered by the Indices module) with live P&L, score/alert context, and a list of untracked "entrée"-signal opportunities — no broker connection, 100% client-side.

**Architecture:** A new `docs/portfolio.js` holds all storage/calculation logic as pure, Node-testable functions (same shape as `docs/scalping.js`: classic `<script>`, top-level `function` declarations, `module.exports` guard at the bottom, dependencies injectable as parameters). `docs/index.html` gets a new `#portefeuille` screen wired into the existing hash-router (`parseRoute`/`SLIDING_PANES`/`renderRoute`), lazy-loads `portfolio.js` on first visit (same pattern as `loadScalpingModule`), and does all DOM rendering by calling the pure functions from `portfolio.js` — exactly the split already used between `scalping.js` (logic) and `docs/index.html`'s inline script (rendering).

**Tech Stack:** Vanilla JS (no framework, no build step), `localStorage` for persistence, existing `indices.json` (already fetched by the Indices module) as the source of truth for company names/scores/prices/alerts. Node's built-in `assert` for tests (no test framework/dependency).

**Spec:** `docs/superpowers/specs/2026-09-10-portefeuille-design.md`

## Global Constraints

- Client-side only — **no changes to any `.py` file, `indices_score.py`, or GitHub Actions workflow.** The spec's phase-1 scope is explicit about this.
- `localStorage` key is exactly `analyse-or-portfolio`.
- Position object shape is exactly `{id, ticker, quantity, buy_price, buy_date}` — snake_case, matching the naming already used in `indices.json` (`current_price`, `entry_price`).
- A ticker may appear in **multiple** positions (separate lots) — never merge/average on save.
- Only tickers present in `indicesData.companies` are selectable when adding a position (the ~180 already-tracked companies) — the design's "hors périmètre" explicitly excludes arbitrary tickers.
- Two portfolio totals, **EUR and USD, never blended into one number**.
- `docs/portfolio.js` must work when `require()`'d from Node with zero DOM/browser globals available except what's explicitly passed in (mirrors `docs/scalping.js`'s `fetchImpl` parameter pattern) — this is what makes `docs/portfolio.test.js` possible without a browser.
- Every task that touches `docs/index.html` ends by bumping `CACHE_NAME` in `docs/service-worker.js` (currently `"analyse-or-shell-v16"` — next task uses `v17`, the one after `v18`, and so on in task order).
- Already on branch `portefeuille` — no need to create it. Commit at the end of every task. Every commit message ends with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- No browser automation is available in this environment. Verification for `docs/index.html` changes is: careful self-review of the diff (does every `getElementById` target an id that exists in the HTML you just wrote? does every function you call exist?), running `node docs/portfolio.test.js` for the logic layer, and — noted in the final task — asking the user to click through the real page after the branch is merged.

---

## File Structure

- **Create `docs/portfolio.js`** — all storage CRUD, validation, P&L/totals calculation, opportunity detection. Zero DOM access. Loaded as a classic `<script src="portfolio.js">` (lazy, on first visit to the Portefeuille tab), so every top-level `function` it declares becomes directly callable from `docs/index.html`'s inline script with no namespace prefix — exactly how `computeSignal`/`fetchGoldCandles` from `scalping.js` are called today.
- **Create `docs/portfolio.test.js`** — Node-runnable tests for everything in `portfolio.js`, same manual-runner style as `docs/scalping.test.js` (no framework).
- **Modify `docs/index.html`** — nav entries (top-bar, drawer, home card), a new `#portefeuille` screen, routing wiring, lazy-load of `portfolio.js`, and the render functions that turn `portfolio.js`'s data into HTML (these render functions live in `docs/index.html`, not `portfolio.js` — same split as `renderScalpSignal` today).
- **Modify `docs/service-worker.js`** — `CACHE_NAME` bump, once per task that touches `docs/index.html`.

---

### Task 1: `docs/portfolio.js` — storage and CRUD

**Files:**
- Create: `docs/portfolio.js`
- Create: `docs/portfolio.test.js`

**Interfaces:**
- Consumes: nothing (first task).
- Produces (used by Task 2 and by `docs/index.html` in Tasks 4-5):
  - `PORTFOLIO_STORAGE_KEY` (string constant, value `"analyse-or-portfolio"`)
  - `validatePositionInput(quantity, buyPrice) -> string | null` (error message, or `null` if valid)
  - `createPosition(ticker, quantity, buyPrice, buyDate, now = Date.now()) -> {id, ticker, quantity, buy_price, buy_date}`
  - `loadPortfolio(storage) -> Array<Position>` (`storage` optional, defaults to `localStorage` when available, else `null`)
  - `savePortfolio(positions, storage) -> boolean` (true on success)
  - `addPosition(ticker, quantity, buyPrice, buyDate, storage) -> {ok: true, positions} | {ok: false, error}`
  - `updatePosition(id, quantity, buyPrice, buyDate, storage) -> {ok: true, positions} | {ok: false, error}`
  - `removePosition(id, storage) -> {ok: true, positions} | {ok: false, error}`

- [ ] **Step 1: Write the failing tests**

Create `docs/portfolio.test.js`:

```javascript
// docs/portfolio.test.js
// Tests JS sans framework, exécutables via `node docs/portfolio.test.js`.
// Même convention que docs/scalping.test.js : assert natif de Node,
// fonctions test_* appelées explicitement dans main(), aucune dépendance.
const assert = require('assert');
const {
  PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
  loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
} = require('./portfolio.js');

// Mock localStorage minimal — Node n'a pas cet objet nativement.
function makeFakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = value; },
    _data: data,
  };
}

function test_validatePositionInput_accepts_positive_numbers() {
  assert.strictEqual(validatePositionInput(10, 650.5), null);
  console.log('OK: test_validatePositionInput_accepts_positive_numbers');
}

function test_validatePositionInput_rejects_non_positive_quantity() {
  assert.ok(validatePositionInput(0, 100).length > 0);
  assert.ok(validatePositionInput(-5, 100).length > 0);
  console.log('OK: test_validatePositionInput_rejects_non_positive_quantity');
}

function test_validatePositionInput_rejects_non_numeric() {
  assert.ok(validatePositionInput(NaN, 100).length > 0);
  assert.ok(validatePositionInput(10, undefined).length > 0);
  console.log('OK: test_validatePositionInput_rejects_non_numeric');
}

function test_createPosition_generates_stable_shape() {
  const pos = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  assert.strictEqual(pos.ticker, 'MC.PA');
  assert.strictEqual(pos.quantity, 10);
  assert.strictEqual(pos.buy_price, 650.0);
  assert.strictEqual(pos.buy_date, '2026-08-01');
  assert.ok(pos.id.startsWith('pos-1757500000000-'));
  console.log('OK: test_createPosition_generates_stable_shape');
}

function test_createPosition_two_calls_produce_different_ids() {
  const a = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  const b = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  assert.notStrictEqual(a.id, b.id);
  console.log('OK: test_createPosition_two_calls_produce_different_ids');
}

function test_loadPortfolio_returns_empty_array_when_storage_missing() {
  assert.deepStrictEqual(loadPortfolio(null), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_storage_missing');
}

function test_loadPortfolio_returns_empty_array_when_key_absent() {
  const storage = makeFakeStorage();
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_key_absent');
}

function test_loadPortfolio_returns_empty_array_on_corrupt_json() {
  const storage = makeFakeStorage({ [PORTFOLIO_STORAGE_KEY]: '{not valid json' });
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_on_corrupt_json');
}

function test_loadPortfolio_returns_empty_array_when_stored_value_not_array() {
  const storage = makeFakeStorage({ [PORTFOLIO_STORAGE_KEY]: '{"not":"an array"}' });
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_stored_value_not_array');
}

function test_savePortfolio_then_loadPortfolio_roundtrips() {
  const storage = makeFakeStorage();
  const positions = [{ id: 'pos-1', ticker: 'MC.PA', quantity: 5, buy_price: 600, buy_date: '2026-08-01' }];
  assert.strictEqual(savePortfolio(positions, storage), true);
  assert.deepStrictEqual(loadPortfolio(storage), positions);
  console.log('OK: test_savePortfolio_then_loadPortfolio_roundtrips');
}

function test_savePortfolio_returns_false_when_storage_throws() {
  const storage = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError'); },
  };
  assert.strictEqual(savePortfolio([{ id: 'pos-1' }], storage), false);
  console.log('OK: test_savePortfolio_returns_false_when_storage_throws');
}

function test_addPosition_appends_and_saves() {
  const storage = makeFakeStorage();
  const result = addPosition('MC.PA', 10, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 1);
  assert.strictEqual(result.positions[0].ticker, 'MC.PA');
  assert.deepStrictEqual(loadPortfolio(storage), result.positions);
  console.log('OK: test_addPosition_appends_and_saves');
}

function test_addPosition_two_lots_same_ticker_stay_independent() {
  const storage = makeFakeStorage();
  addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const result = addPosition('MC.PA', 5, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.positions.length, 2);
  assert.notStrictEqual(result.positions[0].id, result.positions[1].id);
  assert.strictEqual(result.positions[0].buy_price, 600.0);
  assert.strictEqual(result.positions[1].buy_price, 650.0);
  console.log('OK: test_addPosition_two_lots_same_ticker_stay_independent');
}

function test_addPosition_rejects_invalid_input_without_saving() {
  const storage = makeFakeStorage();
  const result = addPosition('MC.PA', -1, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, false);
  assert.ok(result.error.length > 0);
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_addPosition_rejects_invalid_input_without_saving');
}

function test_updatePosition_changes_only_targeted_position() {
  const storage = makeFakeStorage();
  const after1 = addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const after2 = addPosition('OR.PA', 3, 60.0, '2026-07-02', storage);
  const target = after2.positions[0].ticker === 'OR.PA' ? after2.positions[0] : after2.positions[1];
  const result = updatePosition(target.id, 4, 65.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, true);
  const updated = result.positions.find(p => p.id === target.id);
  assert.strictEqual(updated.quantity, 4);
  assert.strictEqual(updated.buy_price, 65.0);
  assert.strictEqual(updated.buy_date, '2026-08-01');
  const untouched = result.positions.find(p => p.ticker === 'MC.PA');
  assert.strictEqual(untouched.quantity, 10);
  console.log('OK: test_updatePosition_changes_only_targeted_position');
}

function test_updatePosition_rejects_invalid_input() {
  const storage = makeFakeStorage();
  const after = addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const result = updatePosition(after.positions[0].id, 0, 600.0, '2026-07-01', storage);
  assert.strictEqual(result.ok, false);
  console.log('OK: test_updatePosition_rejects_invalid_input');
}

function test_updatePosition_returns_error_for_unknown_id() {
  const storage = makeFakeStorage();
  const result = updatePosition('does-not-exist', 1, 1, '2026-01-01', storage);
  assert.strictEqual(result.ok, false);
  console.log('OK: test_updatePosition_returns_error_for_unknown_id');
}

function test_removePosition_deletes_only_targeted_position() {
  const storage = makeFakeStorage();
  addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const after2 = addPosition('OR.PA', 3, 60.0, '2026-07-02', storage);
  const toRemove = after2.positions.find(p => p.ticker === 'OR.PA');
  const result = removePosition(toRemove.id, storage);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 1);
  assert.strictEqual(result.positions[0].ticker, 'MC.PA');
  console.log('OK: test_removePosition_deletes_only_targeted_position');
}

function main() {
  test_validatePositionInput_accepts_positive_numbers();
  test_validatePositionInput_rejects_non_positive_quantity();
  test_validatePositionInput_rejects_non_numeric();
  test_createPosition_generates_stable_shape();
  test_createPosition_two_calls_produce_different_ids();
  test_loadPortfolio_returns_empty_array_when_storage_missing();
  test_loadPortfolio_returns_empty_array_when_key_absent();
  test_loadPortfolio_returns_empty_array_on_corrupt_json();
  test_loadPortfolio_returns_empty_array_when_stored_value_not_array();
  test_savePortfolio_then_loadPortfolio_roundtrips();
  test_savePortfolio_returns_false_when_storage_throws();
  test_addPosition_appends_and_saves();
  test_addPosition_two_lots_same_ticker_stay_independent();
  test_addPosition_rejects_invalid_input_without_saving();
  test_updatePosition_changes_only_targeted_position();
  test_updatePosition_rejects_invalid_input();
  test_updatePosition_returns_error_for_unknown_id();
  test_removePosition_deletes_only_targeted_position();
  console.log('Tous les tests portfolio.test.js (Task 1) sont passés.');
}

main();
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node docs/portfolio.test.js`
Expected: `Error: Cannot find module './portfolio.js'` (file doesn't exist yet)

- [ ] **Step 3: Write `docs/portfolio.js`**

```javascript
// docs/portfolio.js
// Portefeuille personnel — suivi de positions détenues, calcul entièrement
// côté navigateur, aucune donnée ne transite par le pipeline Python/GitHub
// Actions. Voir docs/superpowers/specs/2026-09-10-portefeuille-design.md.
//
// Ce fichier ne touche jamais au DOM : uniquement stockage (localStorage)
// et calcul purs, testables depuis Node sans navigateur (voir
// docs/portfolio.test.js) — même séparation que docs/scalping.js (logique)
// vs docs/index.html (rendu). Chargé en <script> classique (pas de module
// ES) pour rester cohérent avec docs/index.html ; le bloc
// `if (typeof module...)` en bas permet aussi de l'importer depuis Node.

const PORTFOLIO_STORAGE_KEY = 'analyse-or-portfolio';

/**
 * `null` si (quantity, buyPrice) sont des nombres finis strictement
 * positifs, sinon un message d'erreur prêt à afficher.
 */
function validatePositionInput(quantity, buyPrice) {
  if (!Number.isFinite(quantity) || quantity <= 0) {
    return 'La quantité doit être un nombre positif.';
  }
  if (!Number.isFinite(buyPrice) || buyPrice <= 0) {
    return "Le prix d'achat doit être un nombre positif.";
  }
  return null;
}

/**
 * `now` est injectable (tests) — vaut Date.now() par défaut. L'id combine
 * l'horodatage et un suffixe aléatoire court : stable, unique même pour
 * deux lots du même ticker créés à la même milliseconde.
 */
function createPosition(ticker, quantity, buyPrice, buyDate, now = Date.now()) {
  const suffix = Math.random().toString(36).slice(2, 8);
  return {
    id: `pos-${now}-${suffix}`,
    ticker,
    quantity,
    buy_price: buyPrice,
    buy_date: buyDate,
  };
}

function _resolveStorage(storage) {
  if (storage !== undefined) return storage;
  return typeof localStorage !== 'undefined' ? localStorage : null;
}

/**
 * Repli sur [] si storage est absent, la clé n'existe pas, le JSON est
 * invalide, ou la valeur stockée n'est pas un tableau — jamais
 * d'exception qui casserait l'onglet Portefeuille au chargement.
 */
function loadPortfolio(storage) {
  const s = _resolveStorage(storage);
  if (!s) return [];
  try {
    const raw = s.getItem(PORTFOLIO_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

/**
 * `false` si storage est absent ou si l'écriture échoue (quota dépassé,
 * navigation privée stricte) — jamais d'exception.
 */
function savePortfolio(positions, storage) {
  const s = _resolveStorage(storage);
  if (!s) return false;
  try {
    s.setItem(PORTFOLIO_STORAGE_KEY, JSON.stringify(positions));
    return true;
  } catch (e) {
    return false;
  }
}

function addPosition(ticker, quantity, buyPrice, buyDate, storage) {
  const error = validatePositionInput(quantity, buyPrice);
  if (error) return { ok: false, error };
  const positions = loadPortfolio(storage);
  positions.push(createPosition(ticker, quantity, buyPrice, buyDate));
  if (!savePortfolio(positions, storage)) {
    return { ok: false, error: "La sauvegarde a échoué (stockage local indisponible ou plein)." };
  }
  return { ok: true, positions };
}

function updatePosition(id, quantity, buyPrice, buyDate, storage) {
  const error = validatePositionInput(quantity, buyPrice);
  if (error) return { ok: false, error };
  const positions = loadPortfolio(storage);
  const idx = positions.findIndex(p => p.id === id);
  if (idx === -1) return { ok: false, error: 'Position introuvable.' };
  positions[idx] = { ...positions[idx], quantity, buy_price: buyPrice, buy_date: buyDate };
  if (!savePortfolio(positions, storage)) {
    return { ok: false, error: "La sauvegarde a échoué (stockage local indisponible ou plein)." };
  }
  return { ok: true, positions };
}

function removePosition(id, storage) {
  const positions = loadPortfolio(storage).filter(p => p.id !== id);
  if (!savePortfolio(positions, storage)) {
    return { ok: false, error: "La sauvegarde a échoué (stockage local indisponible ou plein)." };
  }
  return { ok: true, positions };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
    loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node docs/portfolio.test.js`
Expected: 18 `OK: ...` lines then `Tous les tests portfolio.test.js (Task 1) sont passés.`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "$(cat <<'EOF'
feat(portfolio): stockage et CRUD des positions (docs/portfolio.js)

Logique pure (localStorage + validation), testable depuis Node sans
navigateur — même séparation logique/rendu que docs/scalping.js.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `docs/portfolio.js` — P&L, totaux, opportunités

**Files:**
- Modify: `docs/portfolio.js` (append)
- Modify: `docs/portfolio.test.js` (append tests + update `main()`)

**Interfaces:**
- Consumes: `Position` shape from Task 1 (`{id, ticker, quantity, buy_price, buy_date}`).
- Produces (used by `docs/index.html` in Tasks 4-5):
  - `computePositionPnL(position, currentPrice) -> {value, pnlAbs, pnlPct} | null` (`null` if `currentPrice` is not a finite number — ticker's price unavailable)
  - `computePortfolioTotals(positions, companiesByTicker, currencyByIndex) -> {EUR: {value, pnlAbs}, USD: {value, pnlAbs}}`
    - `companiesByTicker`: `{[ticker]: company}` where `company` has at least `.current_price` and `.index`
    - `currencyByIndex`: `{[indexKey]: 'EUR' | 'USD'}`
    - Positions whose ticker is missing from `companiesByTicker` are skipped (contribute to neither total) — this is how a delisted ticker degrades gracefully rather than crashing the total.
  - `findOpportunities(companies, heldTickers) -> Array<company>` — `companies` is `indicesData.companies`, `heldTickers` is a `Set<string>` of tickers already held; returns companies with an active `entree` alert (`company.alerts.some(a => a.kind === 'entree')`) that are not already held.

- [ ] **Step 1: Write the failing tests**

Append to `docs/portfolio.test.js`, replacing the `require` line and `main()` call list:

```javascript
const {
  PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
  loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
  computePositionPnL, computePortfolioTotals, findOpportunities,
} = require('./portfolio.js');
```

Add before `function main() {`:

```javascript
function test_computePositionPnL_gain() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  const result = computePositionPnL(position, 660);
  assert.strictEqual(result.value, 6600);
  assert.strictEqual(result.pnlAbs, 600);
  assert.ok(Math.abs(result.pnlPct - 10) < 1e-9);
  console.log('OK: test_computePositionPnL_gain');
}

function test_computePositionPnL_loss() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  const result = computePositionPnL(position, 540);
  assert.strictEqual(result.value, 5400);
  assert.strictEqual(result.pnlAbs, -600);
  assert.ok(Math.abs(result.pnlPct - -10) < 1e-9);
  console.log('OK: test_computePositionPnL_loss');
}

function test_computePositionPnL_null_when_price_unavailable() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  assert.strictEqual(computePositionPnL(position, null), null);
  assert.strictEqual(computePositionPnL(position, undefined), null);
  assert.strictEqual(computePositionPnL(position, NaN), null);
  console.log('OK: test_computePositionPnL_null_when_price_unavailable');
}

function test_computePortfolioTotals_splits_eur_and_usd() {
  const positions = [
    { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' },
    { id: 'p2', ticker: 'AAPL', quantity: 5, buy_price: 180, buy_date: '2026-07-01' },
  ];
  const companiesByTicker = {
    'MC.PA': { ticker: 'MC.PA', index: 'CAC40', current_price: 660 },
    'AAPL': { ticker: 'AAPL', index: 'NASDAQ', current_price: 200 },
  };
  const currencyByIndex = { CAC40: 'EUR', NASDAQ: 'USD' };
  const totals = computePortfolioTotals(positions, companiesByTicker, currencyByIndex);
  assert.strictEqual(totals.EUR.value, 6600);
  assert.strictEqual(totals.EUR.pnlAbs, 600);
  assert.strictEqual(totals.USD.value, 1000);
  assert.strictEqual(totals.USD.pnlAbs, 100);
  console.log('OK: test_computePortfolioTotals_splits_eur_and_usd');
}

function test_computePortfolioTotals_skips_position_with_unknown_ticker() {
  const positions = [{ id: 'p1', ticker: 'DELISTED', quantity: 10, buy_price: 600, buy_date: '2026-07-01' }];
  const totals = computePortfolioTotals(positions, {}, { CAC40: 'EUR' });
  assert.strictEqual(totals.EUR.value, 0);
  assert.strictEqual(totals.USD.value, 0);
  console.log('OK: test_computePortfolioTotals_skips_position_with_unknown_ticker');
}

function test_computePortfolioTotals_empty_positions_returns_zeroed_totals() {
  const totals = computePortfolioTotals([], {}, {});
  assert.deepStrictEqual(totals, { EUR: { value: 0, pnlAbs: 0 }, USD: { value: 0, pnlAbs: 0 } });
  console.log('OK: test_computePortfolioTotals_empty_positions_returns_zeroed_totals');
}

function test_findOpportunities_returns_entree_alerts_not_held() {
  const companies = [
    { ticker: 'MC.PA', alerts: [{ kind: 'entree' }] },
    { ticker: 'OR.PA', alerts: [{ kind: 'risque' }] },
    { ticker: 'AAPL', alerts: [{ kind: 'entree' }] },
  ];
  const result = findOpportunities(companies, new Set(['AAPL']));
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].ticker, 'MC.PA');
  console.log('OK: test_findOpportunities_returns_entree_alerts_not_held');
}

function test_findOpportunities_excludes_companies_without_entree_alert() {
  const companies = [{ ticker: 'MC.PA', alerts: [] }];
  const result = findOpportunities(companies, new Set());
  assert.strictEqual(result.length, 0);
  console.log('OK: test_findOpportunities_excludes_companies_without_entree_alert');
}

function test_findOpportunities_handles_missing_alerts_field() {
  const companies = [{ ticker: 'MC.PA' }];
  const result = findOpportunities(companies, new Set());
  assert.strictEqual(result.length, 0);
  console.log('OK: test_findOpportunities_handles_missing_alerts_field');
}
```

Update the `main()` body to also call the 8 new test functions (after the Task 1 ones, before the final `console.log`):

```javascript
  test_computePositionPnL_gain();
  test_computePositionPnL_loss();
  test_computePositionPnL_null_when_price_unavailable();
  test_computePortfolioTotals_splits_eur_and_usd();
  test_computePortfolioTotals_skips_position_with_unknown_ticker();
  test_computePortfolioTotals_empty_positions_returns_zeroed_totals();
  test_findOpportunities_returns_entree_alerts_not_held();
  test_findOpportunities_excludes_companies_without_entree_alert();
  test_findOpportunities_handles_missing_alerts_field();
  console.log('Tous les tests portfolio.test.js sont passés.');
```

(Replace the old final `console.log('Tous les tests portfolio.test.js (Task 1) sont passés.');` with the block above.)

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `node docs/portfolio.test.js`
Expected: `TypeError: computePositionPnL is not a function` (not exported yet)

- [ ] **Step 3: Append to `docs/portfolio.js`**

Insert before the `if (typeof module !== 'undefined' ...)` block at the bottom:

```javascript
/**
 * `null` si currentPrice n'est pas un nombre fini (prix indisponible pour
 * ce ticker) — jamais NaN qui se propagerait silencieusement dans les
 * totaux du portefeuille.
 */
function computePositionPnL(position, currentPrice) {
  if (!Number.isFinite(currentPrice)) return null;
  const value = currentPrice * position.quantity;
  const cost = position.buy_price * position.quantity;
  const pnlAbs = value - cost;
  const pnlPct = cost !== 0 ? (pnlAbs / cost) * 100 : 0;
  return { value, pnlAbs, pnlPct };
}

/**
 * Deux totaux séparés, jamais convertis/mélangés (voir spec). Une
 * position dont le ticker n'est pas dans companiesByTicker (retiré de
 * l'indice suivi) ou dont computePositionPnL renvoie null est ignorée
 * pour ce calcul, plutôt que de faire échouer tout le total.
 */
function computePortfolioTotals(positions, companiesByTicker, currencyByIndex) {
  const totals = { EUR: { value: 0, pnlAbs: 0 }, USD: { value: 0, pnlAbs: 0 } };
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const pnl = computePositionPnL(position, company.current_price);
    if (!pnl) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    totals[currency].value += pnl.value;
    totals[currency].pnlAbs += pnl.pnlAbs;
  });
  return totals;
}

/**
 * Entreprises suivies avec un signal "entrée" actif que l'utilisateur ne
 * détient pas déjà (heldTickers). Même filtre d'alerte que
 * hasMajorNewsAlert côté docs/index.html, mais sur "entree" au lieu de
 * "actu_majeure".
 */
function findOpportunities(companies, heldTickers) {
  return companies.filter(c =>
    !heldTickers.has(c.ticker) && (c.alerts || []).some(a => a.kind === 'entree')
  );
}
```

Update the `module.exports` block to include the three new functions:

```javascript
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
    loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
    computePositionPnL, computePortfolioTotals, findOpportunities,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node docs/portfolio.test.js`
Expected: 27 `OK: ...` lines then `Tous les tests portfolio.test.js sont passés.`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "$(cat <<'EOF'
feat(portfolio): P&L, totaux EUR/USD séparés, détection d'opportunités

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Nouvel onglet "Portefeuille" — navigation et routage (écran vide)

Ce task rend l'onglet **navigable** (nav desktop/mobile/accueil, routage, écran squelette) sans encore afficher de vraies données — objectif : vérifier que la plomberie de navigation est correcte avant d'y brancher le rendu réel (Task 4-5).

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes: rien de nouveau (routage existant : `parseRoute`, `SLIDING_PANES`, `renderRoute`, pattern `loadScalpingModule`).
- Produces (utilisé par Task 4) :
  - Écran `<div id="portefeuille" class="screen app" hidden>` avec `<main id="portefeuilleContent">`.
  - `let portfolioJsLoaded = false;` + `function loadPortfolioModule() -> Promise<void>` (charge `portfolio.js`, résout immédiatement si déjà chargé).
  - `let portfolioScreenLoaded = false;` (pour ne charger `portfolio.js` + rendre qu'une fois par session de page, même pattern que `scoreLoaded`/`indicesLoaded`).
  - Route `#portefeuille` reconnue par `parseRoute()` → `{screen: 'portefeuille'}`.

- [ ] **Step 1: Ajouter l'icône SVG**

Dans le bloc `<svg style="display:none" aria-hidden="true">` (repérer `<symbol id="icon-indices"...`, vers la ligne 724), ajouter juste après :

```html
  <symbol id="icon-portefeuille" viewBox="0 0 24 24"><path d="M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z M9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2 M4 13h16"/></symbol>
```

- [ ] **Step 2: Ajouter l'entrée de nav desktop (top-bar)**

Repérer (vers la ligne 742) :

```html
  <button class="nav-item" data-route="indices">
    <svg class="nav-icon"><use href="#icon-indices"/></svg>
    <span>Indices</span>
  </button>
  <button class="theme-toggle" id="themeToggleDesktop" type="button" aria-label="Basculer mode jour/nuit">
```

Insérer entre les deux :

```html
  <button class="nav-item" data-route="portefeuille">
    <svg class="nav-icon"><use href="#icon-portefeuille"/></svg>
    <span>Portefeuille</span>
  </button>
```

- [ ] **Step 3: Ajouter l'entrée de nav mobile (drawer)**

Repérer (vers la ligne 766, dans `<nav class="drawer" id="drawer"...`) :

```html
  <button class="nav-item" data-route="indices">
    <svg class="nav-icon"><use href="#icon-indices"/></svg>
    <span>Indices</span>
  </button>
  <button class="nav-item" id="themeToggleDrawer" type="button">
```

Insérer entre les deux :

```html
  <button class="nav-item" data-route="portefeuille">
    <svg class="nav-icon"><use href="#icon-portefeuille"/></svg>
    <span>Portefeuille</span>
  </button>
```

- [ ] **Step 4: Ajouter la carte de module sur l'accueil**

Repérer (vers la ligne 792, dans `<div class="home-modules">`) :

```html
    <a href="#indices" class="module-card">
      <svg class="module-card-icon"><use href="#icon-indices"/></svg>
      <span class="module-card-name">Indices</span>
      <span class="module-card-desc">Scoring fondamental CAC 40 / DAX, analyses IA.</span>
    </a>
  </div>
</div>
```

Remplacer par :

```html
    <a href="#indices" class="module-card">
      <svg class="module-card-icon"><use href="#icon-indices"/></svg>
      <span class="module-card-name">Indices</span>
      <span class="module-card-desc">Scoring fondamental CAC 40 / DAX, analyses IA.</span>
    </a>
    <a href="#portefeuille" class="module-card">
      <svg class="module-card-icon"><use href="#icon-portefeuille"/></svg>
      <span class="module-card-name">Portefeuille</span>
      <span class="module-card-desc">Suivi de tes positions, P&amp;L, opportunités.</span>
    </a>
  </div>
</div>
```

- [ ] **Step 5: Ajouter l'écran `#portefeuille`**

Repérer la fin de l'écran `#indices` (vers la ligne 862) :

```html
  <main id="indicesContent">
    <div class="skeleton">
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
    </div>
  </main>
</div>

<script>
```

Insérer un nouvel écran entre `</div>` (fin de `#indices`) et `<script>` :

```html
<div id="portefeuille" class="screen app" hidden>
  <header>
    <div class="eyebrow">Suivi personnel</div>
    <h1>Portefeuille</h1>
  </header>

  <main id="portefeuilleContent">
    <div class="skeleton">
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
    </div>
  </main>
</div>

<script>
```

- [ ] **Step 6: Ajouter l'écran à `SLIDING_PANES`**

Repérer (vers la ligne 1360) :

```javascript
const SLIDING_PANES = ['or', 'indices'];
```

Remplacer par :

```javascript
const SLIDING_PANES = ['or', 'indices', 'portefeuille'];
```

- [ ] **Step 7: Reconnaître la route dans `parseRoute()`**

Repérer (vers la ligne 1904) :

```javascript
function parseRoute() {
  const hash = location.hash.slice(1);
  if (hash === 'or') return { screen: 'or' };
  if (hash === 'indices') return { screen: 'indices', ticker: null };
```

Insérer une ligne juste après la ligne `if (hash === 'or') ...` :

```javascript
  if (hash === 'portefeuille') return { screen: 'portefeuille' };
```

- [ ] **Step 8: Charger `portfolio.js` à la demande + brancher `renderRoute()`**

Repérer le bloc `loadScalpingModule` (vers la ligne 928-943) et insérer juste après sa fermeture (avant `function renderScalpSignal(signal) {`) :

```javascript
let portfolioJsLoaded = false;

function loadPortfolioModule() {
  // Charge docs/portfolio.js à la demande (premier passage sur l'onglet
  // Portefeuille), même logique paresseuse que loadScalpingModule.
  return new Promise((resolve, reject) => {
    if (portfolioJsLoaded) { resolve(); return; }
    const script = document.createElement('script');
    script.src = 'portfolio.js';
    script.onload = () => { portfolioJsLoaded = true; resolve(); };
    script.onerror = () => reject(new Error("portfolio.js n'a pas pu être chargé"));
    document.body.appendChild(script);
  });
}

let portfolioScreenLoaded = false;

async function loadPortfolioScreen() {
  // Squelette pour Task 3 : confirme juste que la route/le chargement
  // fonctionnent. Le vrai rendu (formulaire, positions, totaux,
  // opportunités) arrive en Task 4-5 et remplacera ce corps de fonction.
  if (portfolioScreenLoaded) return;
  portfolioScreenLoaded = true;
  try {
    await loadPortfolioModule();
    document.getElementById('portefeuilleContent').innerHTML = '<div class="empty">Portefeuille (à venir).</div>';
  } catch (e) {
    document.getElementById('portefeuilleContent').innerHTML = `<div class="empty">Portefeuille indisponible pour l'instant.<br>${e.message}</div>`;
  }
}
```

Repérer `renderRoute()` (vers la ligne 1950) :

```javascript
  if (route.screen === 'indices') {
    loadIndices(route.ticker, route.view);
  }
}
```

Remplacer par :

```javascript
  if (route.screen === 'indices') {
    loadIndices(route.ticker, route.view);
  }
  if (route.screen === 'portefeuille') {
    loadPortfolioScreen();
  }
}
```

- [ ] **Step 9: Vérification manuelle du diff**

Relire le diff complet et confirmer :
- Chaque `id=` référencé par un nouveau `getElementById` (`portefeuilleContent`) existe bien dans le HTML ajouté à l'étape 5.
- `data-route="portefeuille"` est identique dans les trois endroits (top-bar, drawer) et correspond exactement à ce que `parseRoute()` renvoie comme `screen`.
- Aucune accolade `{`/`}` non fermée (vérifier avec un `grep -c` ou une relecture des blocs modifiés).

- [ ] **Step 10: Bump du cache du service worker**

Dans `docs/service-worker.js`, remplacer `"analyse-or-shell-v16"` par `"analyse-or-shell-v17"`.

- [ ] **Step 11: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "$(cat <<'EOF'
feat(portfolio): onglet Portefeuille navigable (nav + routage, écran vide)

Nav desktop/mobile/accueil, route #portefeuille, chargement paresseux de
portfolio.js — écran encore vide, le rendu réel arrive dans les tâches
suivantes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Affichage des positions et des totaux (lecture)

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes: `loadPortfolio`, `computePositionPnL`, `computePortfolioTotals` (Task 1-2, `portfolio.js`) ; `indicesData`, `INDEX_CURRENCY_BY_KEY`, `formatPriceForIndex`, `formatPct`, `alertColor`, `hasMajorNewsAlert`, `allIndicesBadgesHtml` (déjà définis dans `docs/index.html`, réutilisés tels quels).
- Produces (utilisé par Task 5) :
  - `function renderPortfolioScreen()` — remplace le corps de `loadPortfolioScreen` (Task 3) : s'assure que `indicesData` est chargé (même pattern que `loadIndices`), puis construit `companiesByTicker`/`currencyByIndex` et appelle les fonctions de rendu ci-dessous.
  - `function portfolioTotalsHtml(totals) -> string`
  - `function portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex) -> string`

- [ ] **Step 1: Remplacer `loadPortfolioScreen` par la version qui charge `indicesData`**

Repérer le corps de `loadPortfolioScreen` écrit en Task 3 :

```javascript
async function loadPortfolioScreen() {
  // Squelette pour Task 3 : confirme juste que la route/le chargement
  // fonctionnent. Le vrai rendu (formulaire, positions, totaux,
  // opportunités) arrive en Task 4-5 et remplacera ce corps de fonction.
  if (portfolioScreenLoaded) return;
  portfolioScreenLoaded = true;
  try {
    await loadPortfolioModule();
    document.getElementById('portefeuilleContent').innerHTML = '<div class="empty">Portefeuille (à venir).</div>';
  } catch (e) {
    document.getElementById('portefeuilleContent').innerHTML = `<div class="empty">Portefeuille indisponible pour l'instant.<br>${e.message}</div>`;
  }
}
```

Remplacer par :

```javascript
async function loadPortfolioScreen() {
  const content = document.getElementById('portefeuilleContent');
  try {
    await loadPortfolioModule();
  } catch (e) {
    content.innerHTML = `<div class="empty">Portefeuille indisponible pour l'instant.<br>${e.message}</div>`;
    return;
  }
  if (!indicesData) {
    // Même pattern que loadIndices : le portefeuille dépend des mêmes
    // données (companies/scores/alertes/prix), partagées via la variable
    // globale indicesData — si l'utilisateur ouvre Portefeuille avant
    // Indices, ce fetch alimente les deux ; s'il l'a déjà fait, on
    // réutilise ce qui est déjà chargé sans re-fetcher.
    try {
      const res = await fetch('indices.json?t=' + Date.now());
      if (!res.ok) throw new Error('indices.json introuvable');
      indicesData = await res.json();
    } catch (e) {
      content.innerHTML = `<div class="empty">Données indisponibles pour l'instant.<br>${e.message}</div>`;
      return;
    }
  }
  renderPortfolioScreen();
}

function renderPortfolioScreen() {
  const companiesByTicker = {};
  indicesData.companies.forEach(c => { companiesByTicker[c.ticker] = c; });
  const currencyByIndex = { ...INDEX_CURRENCY_BY_KEY, ...(indicesData.index_currency || {}) };
  const positions = loadPortfolio();
  const totals = computePortfolioTotals(positions, companiesByTicker, currencyByIndex);

  document.getElementById('portefeuilleContent').innerHTML = `
    ${portfolioTotalsHtml(totals)}
    <h2>Tes positions</h2>
    ${portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex)}
  `;
}
```

(`portfolioScreenLoaded` de Task 3 n'est plus utile comme garde — `renderPortfolioScreen` doit pouvoir être ré-appelée après un ajout/modification/suppression de position (Task 5). Retirer la ligne `let portfolioScreenLoaded = false;` ajoutée en Task 3 ainsi que le `if (portfolioScreenLoaded) return; portfolioScreenLoaded = true;` — déjà fait ci-dessus en réécrivant tout le corps de la fonction.)

- [ ] **Step 2: Ajouter `portfolioTotalsHtml` et `portfolioPositionsHtml`**

Insérer juste après `renderPortfolioScreen` :

```javascript
function portfolioTotalsHtml(totals) {
  const rows = [
    ['EUR', totals.EUR, formatPrice],
    ['USD', totals.USD, formatPriceUsd],
  ].filter(([, t]) => t.value !== 0 || t.pnlAbs !== 0);
  if (!rows.length) return '';
  return `
    <div class="portfolio-totals">
      ${rows.map(([label, t, fmt]) => `
        <div class="portfolio-total-card">
          <span class="portfolio-total-label">Total ${label}</span>
          <span class="portfolio-total-value">${fmt(t.value)}</span>
          <span class="portfolio-total-pnl" style="color:${t.pnlAbs >= 0 ? 'var(--gold)' : 'var(--rust)'}">${t.pnlAbs >= 0 ? '+' : ''}${fmt(t.pnlAbs)}</span>
        </div>`).join('')}
    </div>`;
}

function portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex) {
  if (!positions.length) {
    return '<div class="empty">Aucune position pour l\'instant — ajoute ta première ligne ci-dessous.</div>';
  }
  return `<div class="portfolio-position-list">${positions.map(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) {
      return `
        <div class="portfolio-position-row portfolio-position-degraded" data-id="${position.id}">
          <div class="portfolio-position-main">
            <span class="company-name">${position.ticker}</span>
            <span class="hero-sub">Cette entreprise n'est plus suivie — cours et score indisponibles.</span>
          </div>
          <div class="portfolio-position-actions">
            <button class="toggle-btn portfolio-edit-btn" type="button" data-id="${position.id}">Modifier</button>
            <button class="toggle-btn portfolio-delete-btn" type="button" data-id="${position.id}">Supprimer</button>
          </div>
        </div>`;
    }
    const pnl = computePositionPnL(position, company.current_price);
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    const fmt = v => formatPriceForIndex(v, company.index);
    return `
      <div class="portfolio-position-row" data-id="${position.id}">
        <div class="portfolio-position-main">
          <a class="company-name" href="#indices/${company.ticker}">${company.name}</a>
          <span class="company-ticker">${company.ticker}${allIndicesBadgesHtml(company)}</span>
          <span class="hero-sub">${position.quantity} × ${fmt(position.buy_price)} (${position.buy_date})</span>
        </div>
        <div class="portfolio-position-pnl">
          <span class="price-value">${fmt(pnl ? pnl.value : company.current_price * position.quantity)}</span>
          ${pnl ? `<span style="color:${pnl.pnlAbs >= 0 ? 'var(--gold)' : 'var(--rust)'}">${pnl.pnlAbs >= 0 ? '+' : ''}${fmt(pnl.pnlAbs)} (${formatPct(pnl.pnlPct)})</span>` : ''}
          <span class="company-score" style="color:${company.score >= 0 ? 'var(--gold)' : 'var(--rust)'};">${company.score > 0 ? '+' : ''}${company.score}</span>
        </div>
        <div class="portfolio-position-actions">
          <button class="toggle-btn portfolio-edit-btn" type="button" data-id="${position.id}">Modifier</button>
          <button class="toggle-btn portfolio-delete-btn" type="button" data-id="${position.id}">Supprimer</button>
        </div>
      </div>`;
  }).join('')}</div>`;
}
```

(`portfolio-edit-btn`/`portfolio-delete-btn` n'ont pas encore d'écouteur — branché en Task 5. Sans écouteur, cliquer dessus ne fait rien pour l'instant, ce qui est attendu à ce stade.)

- [ ] **Step 3: CSS**

Repérer la fin du bloc de styles `.company-row`/`.company-score` existant (chercher `.company-score {` dans `docs/index.html`) et ajouter juste après :

```css
  .portfolio-totals { display: flex; gap: 12px; margin: 4px 0 20px; flex-wrap: wrap; }
  .portfolio-total-card {
    display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 140px;
    background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 14px 16px;
  }
  .portfolio-total-label { color: var(--muted); font-size: var(--text-xs); }
  .portfolio-total-value { font-family: 'Newsreader', serif; font-size: var(--text-xl); }
  .portfolio-total-pnl { font-size: var(--text-sm); font-weight: 500; }

  .portfolio-position-list { display: flex; flex-direction: column; gap: 10px; margin: 4px 0 24px; }
  .portfolio-position-row {
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;
    background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 14px 16px;
  }
  .portfolio-position-degraded { opacity: .7; }
  .portfolio-position-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .portfolio-position-pnl { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; font-size: var(--text-sm); }
  .portfolio-position-actions { display: flex; gap: 8px; }
  .portfolio-position-actions .toggle-btn { flex: none; padding: 6px 12px; font-size: var(--text-xs); }
```

- [ ] **Step 4: Vérification manuelle du diff**

Relire le diff et confirmer :
- `formatPrice`, `formatPriceUsd`, `formatPriceForIndex`, `formatPct`, `allIndicesBadgesHtml` existent bien déjà ailleurs dans `docs/index.html` avec ces signatures exactes (`grep -n "^function formatPct\|^function formatPriceForIndex\|^function allIndicesBadgesHtml" docs/index.html`).
- Toutes les fonctions appelées depuis `docs/index.html` sans préfixe (`loadPortfolio`, `computePositionPnL`, `computePortfolioTotals`) sont bien exportées par `portfolio.js` (Task 1-2) et deviennent globales une fois le `<script src="portfolio.js">` chargé.
- Aucune accolade non fermée dans les blocs modifiés.

- [ ] **Step 5: Bump du cache du service worker**

Dans `docs/service-worker.js`, remplacer `"analyse-or-shell-v17"` par `"analyse-or-shell-v18"`.

- [ ] **Step 6: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "$(cat <<'EOF'
feat(portfolio): affichage des positions et des totaux EUR/USD

Lecture seule pour l'instant — ajout/modification/suppression arrivent
en tâche suivante.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Formulaire d'ajout/modification/suppression + opportunités

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes: `addPosition`, `updatePosition`, `removePosition`, `findOpportunities` (Task 1-2) ; `renderPortfolioScreen`, `portfolioPositionsHtml` (Task 4) ; `indicesData`, `alsoIndicesBadgesHtml`, `hasMajorNewsAlert` (existants).
- Produces: onglet Portefeuille complet et fonctionnel (dernière tâche du plan).

- [ ] **Step 1: Formulaire d'ajout et section opportunités**

Repérer `renderPortfolioScreen` (écrite en Task 4) :

```javascript
  document.getElementById('portefeuilleContent').innerHTML = `
    ${portfolioTotalsHtml(totals)}
    <h2>Tes positions</h2>
    ${portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex)}
  `;
}
```

Remplacer par :

```javascript
  const heldTickers = new Set(positions.map(p => p.ticker));
  const opportunities = findOpportunities(indicesData.companies, heldTickers);

  document.getElementById('portefeuilleContent').innerHTML = `
    ${portfolioTotalsHtml(totals)}
    ${portfolioFormHtml(indicesData.companies)}
    <h2>Tes positions</h2>
    ${portfolioPositionsHtml(positions, companiesByTicker, currencyByIndex)}
    ${portfolioOpportunitiesHtml(opportunities)}
  `;
  wirePortfolioForm();
  wirePortfolioPositionActions(companiesByTicker);
}
```

- [ ] **Step 2: `portfolioFormHtml` et `portfolioOpportunitiesHtml`**

Insérer après `portfolioPositionsHtml` (Task 4) :

```javascript
function portfolioFormHtml(companies) {
  const grouped = {};
  companies.forEach(c => { (grouped[c.index] = grouped[c.index] || []).push(c); });
  const indexNames = { ...INDEX_DISPLAY_NAMES, ...(indicesData.index_names || {}) };
  const optionsHtml = Object.keys(grouped).sort().map(key => `
    <optgroup label="${indexNames[key] || key}">
      ${grouped[key]
        .slice().sort((a, b) => a.name.localeCompare(b.name, 'fr'))
        .map(c => `<option value="${c.ticker}">${c.name} (${c.ticker})</option>`).join('')}
    </optgroup>`).join('');
  return `
    <form class="portfolio-form" id="portfolioAddForm">
      <h2>Ajouter une position</h2>
      <select class="list-sort" id="portfolioFormTicker" required>
        <option value="" disabled selected>Choisir une entreprise…</option>
        ${optionsHtml}
      </select>
      <div class="portfolio-form-row">
        <input class="list-search" type="number" id="portfolioFormQuantity" placeholder="Quantité" min="0" step="any" required>
        <input class="list-search" type="number" id="portfolioFormPrice" placeholder="Prix d'achat" min="0" step="any" required>
        <input class="list-search" type="date" id="portfolioFormDate" required>
      </div>
      <p class="portfolio-form-error" id="portfolioFormError" hidden></p>
      <button class="toggle-btn" type="submit">Ajouter</button>
    </form>`;
}

function portfolioOpportunitiesHtml(opportunities) {
  if (!opportunities.length) return '';
  return `
    <h2>Opportunités</h2>
    <p class="hero-sub">Entreprises suivies avec un signal d'entrée actif que tu ne détiens pas encore.</p>
    <div class="index-card-list">${opportunities.map(c => `
      <a class="company-row" href="#indices/${c.ticker}">
        <div class="company-row-left">
          <span class="company-name">${c.name}</span>
          <span class="company-ticker">${c.ticker}${allIndicesBadgesHtml(c)}</span>
        </div>
        <span class="company-score" style="color:${c.score >= 0 ? 'var(--gold)' : 'var(--rust)'};">${c.score > 0 ? '+' : ''}${c.score}</span>
      </a>`).join('')}</div>`;
}
```

- [ ] **Step 3: Câblage du formulaire d'ajout**

Insérer après `portfolioOpportunitiesHtml` :

```javascript
function wirePortfolioForm() {
  const form = document.getElementById('portfolioAddForm');
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const ticker = document.getElementById('portfolioFormTicker').value;
    const quantity = parseFloat(document.getElementById('portfolioFormQuantity').value);
    const buyPrice = parseFloat(document.getElementById('portfolioFormPrice').value);
    const buyDate = document.getElementById('portfolioFormDate').value;
    const errorEl = document.getElementById('portfolioFormError');
    if (!ticker) {
      errorEl.textContent = 'Choisis une entreprise.';
      errorEl.hidden = false;
      return;
    }
    const result = addPosition(ticker, quantity, buyPrice, buyDate);
    if (!result.ok) {
      errorEl.textContent = result.error;
      errorEl.hidden = false;
      return;
    }
    renderPortfolioScreen();
  });
}
```

- [ ] **Step 4: Câblage modifier/supprimer**

Insérer après `wirePortfolioForm` :

```javascript
function wirePortfolioPositionActions(companiesByTicker) {
  document.querySelectorAll('.portfolio-delete-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      removePosition(btn.dataset.id);
      renderPortfolioScreen();
    });
  });
  document.querySelectorAll('.portfolio-edit-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = document.querySelector(`.portfolio-position-row[data-id="${btn.dataset.id}"]`);
      const position = loadPortfolio().find(p => p.id === btn.dataset.id);
      if (!position || !row) return;
      row.innerHTML = `
        <div class="portfolio-form-row">
          <input class="list-search" type="number" value="${position.quantity}" id="portfolioEditQuantity" min="0" step="any">
          <input class="list-search" type="number" value="${position.buy_price}" id="portfolioEditPrice" min="0" step="any">
          <input class="list-search" type="date" value="${position.buy_date}" id="portfolioEditDate">
        </div>
        <p class="portfolio-form-error" id="portfolioEditError" hidden></p>
        <div class="portfolio-position-actions">
          <button class="toggle-btn" type="button" id="portfolioEditSave">Enregistrer</button>
          <button class="toggle-btn" type="button" id="portfolioEditCancel">Annuler</button>
        </div>`;
      document.getElementById('portfolioEditCancel').addEventListener('click', () => renderPortfolioScreen());
      document.getElementById('portfolioEditSave').addEventListener('click', () => {
        const quantity = parseFloat(document.getElementById('portfolioEditQuantity').value);
        const buyPrice = parseFloat(document.getElementById('portfolioEditPrice').value);
        const buyDate = document.getElementById('portfolioEditDate').value;
        const result = updatePosition(position.id, quantity, buyPrice, buyDate);
        const errorEl = document.getElementById('portfolioEditError');
        if (!result.ok) {
          errorEl.textContent = result.error;
          errorEl.hidden = false;
          return;
        }
        renderPortfolioScreen();
      });
    });
  });
}
```

- [ ] **Step 5: CSS du formulaire**

Ajouter à la suite du bloc CSS de Task 4 :

```css
  .portfolio-form {
    display: flex; flex-direction: column; gap: 10px; margin: 4px 0 24px;
    background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-lg);
    padding: 16px;
  }
  .portfolio-form h2 { margin: 0; }
  .portfolio-form-row { display: flex; gap: 8px; flex-wrap: wrap; }
  .portfolio-form-row input { flex: 1; min-width: 110px; }
  .portfolio-form-error { color: var(--rust); font-size: var(--text-sm); margin: 0; }
```

- [ ] **Step 6: Vérification manuelle du diff**

Relire le diff et confirmer :
- `addPosition`, `updatePosition`, `removePosition`, `findOpportunities` correspondent exactement aux signatures produites en Task 1-2 (mêmes noms, même ordre de paramètres — `updatePosition(id, quantity, buyPrice, buyDate, storage)`, `storage` omis ici donc `undefined`, ce qui résout vers `localStorage` via `_resolveStorage`).
- Chaque `id=` ciblé par `getElementById`/`querySelector` dans les nouveaux écouteurs (`portfolioFormTicker`, `portfolioFormQuantity`, `portfolioFormPrice`, `portfolioFormDate`, `portfolioFormError`, `portfolioEditQuantity`, `portfolioEditPrice`, `portfolioEditDate`, `portfolioEditError`, `portfolioEditSave`, `portfolioEditCancel`) existe bien dans le HTML généré à l'étape correspondante.
- `INDEX_DISPLAY_NAMES`, `alsoIndicesBadgesHtml`/`allIndicesBadgesHtml` sont bien déjà définis ailleurs dans le fichier avec ces noms exacts.
- Aucune accolade non fermée.

- [ ] **Step 7: Bump du cache du service worker**

Dans `docs/service-worker.js`, remplacer `"analyse-or-shell-v18"` par `"analyse-or-shell-v19"`.

- [ ] **Step 8: Lancer la suite de tests JS complète**

Run: `node docs/portfolio.test.js`
Expected: tous les tests (Task 1 + Task 2) toujours au vert — ce task n'a touché que `docs/index.html`/`docs/service-worker.js`, `portfolio.js` ne doit pas avoir changé de comportement.

- [ ] **Step 9: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "$(cat <<'EOF'
feat(portfolio): formulaire ajout/modification/suppression + opportunités

Onglet Portefeuille complet : ajout d'une position (ticker parmi les
entreprises suivies + quantité/prix/date), édition et suppression en
ligne, section opportunités (signaux "entrée" non détenus).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: Fin de plan — utiliser `superpowers:finishing-a-development-branch`**

Ce plan ne couvre volontairement pas de vérification visuelle automatisée
(pas d'outil de navigateur disponible dans ce bac à sable). Avant de
proposer la fusion, rappeler explicitement à l'utilisateur de tester la
page réelle après merge : ajouter une position, vérifier les totaux
EUR/USD, modifier et supprimer une ligne, ouvrir la section
Opportunités — puis utiliser la skill `finishing-a-development-branch`
pour décider merge local / PR / garder la branche.

---

## Self-Review Notes

- **Spec coverage** — chaque section de la spec a une tâche : formulaire manuel (Task 5), lots multiples par ticker (Task 1, `addPosition`), scope ~180 entreprises (Task 5, `<select>` généré depuis `indicesData.companies`), affichage cours/P&L/score/alertes (Task 4), totaux EUR/USD séparés (Task 2 + Task 4), section Opportunités (Task 2 + Task 5), localStorage (Task 1), dégradation ticker disparu/storage corrompu/formulaire invalide (Task 1, Task 4), nouvel onglet nav (Task 3).
- **Type consistency** — `Position` shape (`id, ticker, quantity, buy_price, buy_date`) et les signatures `computePositionPnL(position, currentPrice)`, `computePortfolioTotals(positions, companiesByTicker, currencyByIndex)`, `findOpportunities(companies, heldTickers)`, `addPosition/updatePosition(id?, quantity, buyPrice, buyDate, storage?)` sont utilisées identiquement dans toutes les tâches qui les consomment.
- **Hors périmètre confirmé non traité** : aucune tâche ne touche `indices_score.py`, aucun import CSV, aucune connexion broker — conforme à la spec.
