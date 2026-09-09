# Module Scalping Or (1min) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un second volet "Scalping" (signaux d'entrée/sortie sur bougies 1min, or spot) dans l'écran Or existant, entièrement calculé côté navigateur.

**Architecture:** Un nouveau fichier `docs/scalping.js` (fetch Twelve Data → détection de pivots/tendance/S-R → indicateurs RSI/MACD/Bollinger → pattern matching de bougies → moteur de confluence → rendu DOM), chargé à la demande depuis `docs/index.html` quand l'utilisateur bascule sur l'onglet Scalping. Aucune donnée ne transite par le pipeline Python/GitHub Actions.

**Tech Stack:** JavaScript vanilla (pas de framework, cohérent avec `docs/index.html` existant), API Twelve Data (fetch client-side), tests JS sans framework exécutables via Node.

**Spec:** `docs/superpowers/specs/2026-09-09-gold-scalping-design.md`

## Global Constraints

- Aucun calcul scalping ne passe par `gold_score.py`, `indices_score.py`, ni GitHub Actions — tout est dans `docs/scalping.js` et `docs/index.html`, exécuté dans le navigateur.
- Clé API Twelve Data acceptée comme visible côté client (décision actée en spec).
- Familles de règles v1 : Tendance, Support/Résistance, Indicateurs (RSI/MACD/Bollinger), Chandeliers (8 figures : Marteau, Étoile filante, Englobante haussière, Englobante baissière, Étoile du Matin, Étoile du Soir, Pénétrante, Nuage noir). PAS de volume, PAS d'Elliott, PAS de TP multiples (un seul TP).
- Priorité de la logique de confluence (spec §6) : signal émis seulement si un déclencheur structurel (tendance + niveau S/R) ET une confirmation (indicateur + pattern de bougie) vont dans le même sens — jamais un facteur seul.
- **Contrainte d'environnement d'implémentation : Node.js n'est PAS installé dans ce sandbox.** Les tests JS de ce plan doivent être écrits pour être réellement exécutables via `node docs/scalping.test.js` sur une machine qui a Node (l'utilisateur, ou une future CI) — mais dans ce sandbox, leur "exécution" aux étapes TDD se fait par relecture manuelle rigoureuse (vérifier à la main que le code produit exactement la valeur attendue écrite dans le test), pas par une commande qui tourne réellement ici. Chaque étape de test de ce plan le précise explicitement.
- Pas d'outillage de navigateur automatisé dans ce sandbox (confirmé en session) — la vérification finale (Task 8) se fait par relecture de diff + l'utilisateur qui teste la page réelle après merge, pas par un screenshot automatisé.
- Décision de structure UI (tranchée dans ce plan, pas explicitement dans la spec) : le panneau `.scalp-panel` existant (widget TradingView 1min, déjà dans `docs/index.html`) déménage DANS l'onglet Scalping (cohérent thématiquement — c'est le graphique que les signaux lisent), et son texte d'avertissement est mis à jour (il dit actuellement "aucun point d'entrée n'est calculé automatiquement pour l'instant", qui devient faux). L'onglet Long Terme montre exactement `#content` tel qu'il existe aujourd'hui, inchangé.

---

## Task 1: `docs/scalping.js` — squelette + `fetchGoldCandles()`

**Files:**
- Create: `docs/scalping.js`
- Create: `docs/scalping.test.js`

**Interfaces:**
- Produces: `fetchGoldCandles(apiKey, fetchImpl = fetch) -> Promise<Array<{time: string, open: number, high: number, low: number, close: number}>>` (chronologique, plus ancien en premier), rejette avec une `Error` au message clair en cas de panne/quota/réponse invalide.

- [ ] **Step 1: Écrire le fichier `docs/scalping.js` avec l'en-tête et le squelette d'export**

```javascript
// docs/scalping.js
// Module Scalping Or (1min) — calcul entièrement côté navigateur, aucune
// donnée ne transite par le pipeline Python/GitHub Actions. Voir
// docs/superpowers/specs/2026-09-09-gold-scalping-design.md pour le design
// complet (règles issues du cours d'Analyse Technique L3 Dauphine).
//
// Ce fichier est chargé en <script> classique (pas de module ES) pour
// rester cohérent avec docs/index.html. Le bloc `if (typeof module...)`
// en bas de fichier permet aussi de l'importer depuis Node pour les tests
// (docs/scalping.test.js), sans framework ni bundler.

const TWELVE_DATA_URL = 'https://api.twelvedata.com/time_series';

/**
 * Récupère les dernières bougies 1min XAU/USD via Twelve Data.
 * `fetchImpl` est injectable (tests) — vaut `fetch` par défaut (navigateur).
 * Renvoie un tableau chronologique (plus ancien en premier), jamais vide
 * en cas de succès. Rejette avec une Error au message clair en cas
 * d'échec réseau, de quota dépassé, ou de réponse Twelve Data invalide.
 */
async function fetchGoldCandles(apiKey, fetchImpl = fetch) {
  // timezone=UTC explicite : par défaut Twelve Data renvoie l'heure locale
  // de l'exchange (pas UTC) — sans ce paramètre, le contrôle de fraîcheur
  // des données (docs/index.html, refreshScalpSignal) comparerait des
  // horaires dans deux fuseaux différents et se tromperait systématiquement.
  const url = `${TWELVE_DATA_URL}?symbol=XAU/USD&interval=1min&outputsize=90&timezone=UTC&apikey=${encodeURIComponent(apiKey)}`;
  let response;
  try {
    response = await fetchImpl(url);
  } catch (e) {
    throw new Error(`Impossible de contacter Twelve Data : ${e.message}`);
  }
  if (!response.ok) {
    throw new Error(`Twelve Data a répondu ${response.status}`);
  }
  const data = await response.json();
  if (data.status === 'error' || !Array.isArray(data.values)) {
    throw new Error(`Réponse Twelve Data invalide : ${data.message || 'pas de données'}`);
  }
  // Twelve Data renvoie le plus récent en premier — on inverse pour avoir
  // un ordre chronologique, attendu par toutes les fonctions de calcul
  // de ce module (pivots, indicateurs, patterns).
  return data.values
    .map(v => ({
      time: v.datetime,
      open: parseFloat(v.open),
      high: parseFloat(v.high),
      low: parseFloat(v.low),
      close: parseFloat(v.close),
    }))
    .reverse();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fetchGoldCandles };
}
```

- [ ] **Step 2: Écrire les tests dans `docs/scalping.test.js`**

```javascript
// docs/scalping.test.js
// Tests JS sans framework, exécutables via `node docs/scalping.test.js`.
// Chaque test compare un résultat calculé à une valeur attendue calculée
// à la main (voir commentaires) et lève une erreur explicite si ça ne
// correspond pas — pas de bibliothèque d'assertion, juste `assert` natif
// de Node pour rester sans dépendance.
const assert = require('assert');
const { fetchGoldCandles } = require('./scalping.js');

async function test_fetchGoldCandles_parses_and_reverses_to_chronological_order() {
  const fakeResponse = {
    ok: true,
    json: async () => ({
      status: 'ok',
      values: [
        { datetime: '2026-09-09 10:02:00', open: '2051.0', high: '2051.5', low: '2050.5', close: '2051.2' },
        { datetime: '2026-09-09 10:01:00', open: '2050.0', high: '2050.8', low: '2049.5', close: '2050.5' },
      ],
    }),
  };
  const fakeFetch = async (url) => {
    assert(url.includes('symbol=XAU/USD'), 'URL doit contenir le symbole XAU/USD');
    assert(url.includes('interval=1min'), 'URL doit demander l\'intervalle 1min');
    assert(url.includes('timezone=UTC'), 'URL doit forcer timezone=UTC (sinon le controle de fraicheur des donnees se trompe de fuseau)');
    return fakeResponse;
  };
  const candles = await fetchGoldCandles('fake-key', fakeFetch);
  assert.strictEqual(candles.length, 2);
  // Le plus ancien (10:01) doit être en premier après inversion.
  assert.strictEqual(candles[0].time, '2026-09-09 10:01:00');
  assert.strictEqual(candles[0].close, 2050.5);
  assert.strictEqual(candles[1].time, '2026-09-09 10:02:00');
  console.log('OK: test_fetchGoldCandles_parses_and_reverses_to_chronological_order');
}

async function test_fetchGoldCandles_rejects_on_error_status() {
  const fakeFetch = async () => ({
    ok: true,
    json: async () => ({ status: 'error', message: 'quota dépassé' }),
  });
  await assert.rejects(
    () => fetchGoldCandles('fake-key', fakeFetch),
    /quota dépassé/,
  );
  console.log('OK: test_fetchGoldCandles_rejects_on_error_status');
}

async function test_fetchGoldCandles_rejects_on_http_error() {
  const fakeFetch = async () => ({ ok: false, status: 429 });
  await assert.rejects(
    () => fetchGoldCandles('fake-key', fakeFetch),
    /429/,
  );
  console.log('OK: test_fetchGoldCandles_rejects_on_http_error');
}

async function main() {
  await test_fetchGoldCandles_parses_and_reverses_to_chronological_order();
  await test_fetchGoldCandles_rejects_on_error_status();
  await test_fetchGoldCandles_rejects_on_http_error();
  console.log('Tous les tests scalping.test.js sont passés.');
}

main().catch(e => {
  console.error('ÉCHEC :', e);
  process.exit(1);
});
```

- [ ] **Step 3: "Exécuter" les tests (vérification manuelle — Node indisponible dans ce sandbox)**

Commande normale (sur une machine avec Node) : `node docs/scalping.test.js`
Résultat attendu normalement : les 3 lignes `OK: ...` puis `Tous les tests scalping.test.js sont passés.`

**Dans ce sandbox, Node n'est pas installé** — à la place, relis `fetchGoldCandles` à la main contre chacun des 3 tests :
1. Le fake fetch du test 1 renvoie 2 valeurs (10:02 puis 10:01, plus récent en premier comme le fait vraiment Twelve Data) — vérifie que `.reverse()` les remet bien dans l'ordre 10:01 puis 10:02, que `parseFloat('2050.5')` donne bien le nombre `2050.5`, et que l'URL contient bien `timezone=UTC` (sans quoi les dates renvoyées seraient en heure locale de l'exchange, pas UTC, et fausseraient le contrôle de fraîcheur de Task 7).
2. Le test 2 vérifie que `data.status === 'error'` déclenche bien le `throw new Error(...)` avec le message Twelve Data inclus.
3. Le test 3 vérifie que `!response.ok` déclenche bien le `throw` avec le code HTTP inclus.

Note ce que tu as vérifié (pas juste "ça a l'air bon") dans ton rapport.

- [ ] **Step 4: Commit**

```bash
git add docs/scalping.js docs/scalping.test.js
git commit -m "feat(scalping): fetchGoldCandles (Twelve Data, XAU/USD 1min)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Détection de pivots

**Files:**
- Modify: `docs/scalping.js` (ajouter après `fetchGoldCandles`)
- Modify: `docs/scalping.test.js`

**Interfaces:**
- Consumes: rien de nouveau (candles au format `{time, open, high, low, close}` de Task 1)
- Produces: `detectPivots(candles, k) -> Array<{index: number, type: 'high'|'low', price: number}>`, trié par `index` croissant.

- [ ] **Step 1: Ajouter les tests dans `docs/scalping.test.js`**

Ajoute avant `async function main()` :

```javascript
const { fetchGoldCandles, detectPivots } = require('./scalping.js');
```

(remplace la ligne d'import existante en haut du fichier par celle-ci — un seul `require` qui liste toutes les fonctions exportées au fur et à mesure des tasks)

```javascript
function test_detectPivots_finds_high_and_low_with_k1() {
  // 5 bougies, K=1 : un pivot haut net à l'index 2 (14 > 10 des deux
  // côtés), un pivot bas net à l'index 3 (3 < 8 des deux côtés).
  // Calculé à la main, voir docs/superpowers/plans/2026-09-09-gold-scalping.md.
  const candles = [
    { high: 10, low: 8 },
    { high: 10, low: 8 },
    { high: 14, low: 8 },
    { high: 10, low: 3 },
    { high: 10, low: 8 },
  ];
  const pivots = detectPivots(candles, 1);
  assert.deepStrictEqual(pivots, [
    { index: 2, type: 'high', price: 14 },
    { index: 3, type: 'low', price: 3 },
  ]);
  console.log('OK: test_detectPivots_finds_high_and_low_with_k1');
}

function test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher() {
  // idx1 a High=10, égal à idx0 — pas strictement supérieur, donc pas
  // un pivot (la règle exige une inégalité stricte des deux côtés).
  const candles = [
    { high: 10, low: 5 },
    { high: 10, low: 5 },
    { high: 8, low: 5 },
  ];
  const pivots = detectPivots(candles, 1);
  assert.deepStrictEqual(pivots, []);
  console.log('OK: test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher');
}
```

Et ajoute ces deux appels dans `main()` avant `console.log('Tous les tests...')` :
```javascript
  test_detectPivots_finds_high_and_low_with_k1();
  test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher();
```

- [ ] **Step 2: Implémenter `detectPivots` dans `docs/scalping.js`**

Ajoute après `fetchGoldCandles`, avant le bloc `if (typeof module...)` :

```javascript
/**
 * Un pivot haut à l'index i : High[i] est strictement supérieur aux
 * High des k bougies avant ET des k bougies après (symétrique pour un
 * pivot bas sur les Low). Un pivot n'est donc confirmé qu'une fois les k
 * bougies suivantes closes — délai inhérent à la méthode, pas un bug.
 * Renvoie les pivots confirmés, triés par index croissant.
 */
function detectPivots(candles, k) {
  const pivots = [];
  for (let i = k; i < candles.length - k; i++) {
    const isHigh = candles.slice(i - k, i).every(c => c.high < candles[i].high)
      && candles.slice(i + 1, i + 1 + k).every(c => c.high < candles[i].high);
    if (isHigh) pivots.push({ index: i, type: 'high', price: candles[i].high });

    const isLow = candles.slice(i - k, i).every(c => c.low > candles[i].low)
      && candles.slice(i + 1, i + 1 + k).every(c => c.low > candles[i].low);
    if (isLow) pivots.push({ index: i, type: 'low', price: candles[i].low });
  }
  return pivots.sort((a, b) => a.index - b.index);
}
```

Mets aussi à jour l'export en bas du fichier :
```javascript
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fetchGoldCandles, detectPivots };
}
```

- [ ] **Step 3: Vérification manuelle (Node indisponible)**

Trace `detectPivots` à la main sur les 2 tests :
1. Pour l'index 2 (K=1) : `candles.slice(1,2)` = `[{high:10}]`, tous `<14` ✓ ; `candles.slice(3,4)` = `[{high:10}]`, tous `<14` ✓ → pivot haut confirmé. Même raisonnement pour l'index 3 côté Low (3 < 8 des deux côtés). Les index 0, 1, 4 n'ont pas assez de voisins des deux côtés (boucle `i` va de `k` à `length-k-1`, donc de 1 à 3 inclus ici) ou ne sont pas des extrêmes stricts.
2. Pour le 2e test : idx1 a High=10, égal (pas strictement inférieur) à `candles[0].high=10` → `every(c => c.high < candles[i].high)` est `false` → pas de pivot. Confirme la règle d'inégalité stricte.

- [ ] **Step 4: Commit**

```bash
git add docs/scalping.js docs/scalping.test.js
git commit -m "feat(scalping): detection de pivots haut/bas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Tendance et niveaux de Support/Résistance

**Files:**
- Modify: `docs/scalping.js`
- Modify: `docs/scalping.test.js`

**Interfaces:**
- Consumes: `Array<{index, type, price}>` de `detectPivots` (Task 2)
- Produces:
  - `classifyTrend(pivots) -> 'haussier' | 'baissier' | 'neutre'`
  - `currentLevels(pivots, currentPrice) -> {support: number|null, resistance: number|null}`

- [ ] **Step 1: Ajouter les tests**

Mets à jour l'import : `const { fetchGoldCandles, detectPivots, classifyTrend, currentLevels } = require('./scalping.js');`

```javascript
function test_classifyTrend_haussier_on_rising_pivots() {
  // 2 pivots bas croissants (10, 12) ET 2 pivots hauts croissants (15, 18)
  // -> tendance haussière (règle du cours : creux croissants puis
  // sommets croissants, les deux exigés pour "haussier" franc en v1).
  const pivots = [
    { index: 0, type: 'low', price: 10 },
    { index: 1, type: 'high', price: 15 },
    { index: 2, type: 'low', price: 12 },
    { index: 3, type: 'high', price: 18 },
  ];
  assert.strictEqual(classifyTrend(pivots), 'haussier');
  console.log('OK: test_classifyTrend_haussier_on_rising_pivots');
}

function test_classifyTrend_neutre_when_pivots_disagree() {
  // Pivots bas croissants (10, 12) mais pivots hauts décroissants (18, 15)
  // -> ambigu, doit rester neutre (pas de tendance franche).
  const pivots = [
    { index: 0, type: 'low', price: 10 },
    { index: 1, type: 'high', price: 18 },
    { index: 2, type: 'low', price: 12 },
    { index: 3, type: 'high', price: 15 },
  ];
  assert.strictEqual(classifyTrend(pivots), 'neutre');
  console.log('OK: test_classifyTrend_neutre_when_pivots_disagree');
}

function test_classifyTrend_neutre_when_not_enough_pivots() {
  const pivots = [{ index: 0, type: 'low', price: 10 }];
  assert.strictEqual(classifyTrend(pivots), 'neutre');
  console.log('OK: test_classifyTrend_neutre_when_not_enough_pivots');
}

function test_currentLevels_picks_nearest_unbroken_pivots() {
  const pivots = [
    { index: 0, type: 'low', price: 95 },
    { index: 1, type: 'high', price: 105 },
    { index: 2, type: 'low', price: 98 },
    { index: 3, type: 'high', price: 110 },
  ];
  // Prix courant = 100 : support = dernier pivot bas sous 100 -> 98 ;
  // résistance = dernier pivot haut au-dessus de 100 -> 110.
  assert.deepStrictEqual(currentLevels(pivots, 100), { support: 98, resistance: 110 });
  console.log('OK: test_currentLevels_picks_nearest_unbroken_pivots');
}

function test_currentLevels_null_when_no_pivot_on_one_side() {
  const pivots = [{ index: 0, type: 'low', price: 98 }];
  assert.deepStrictEqual(currentLevels(pivots, 100), { support: 98, resistance: null });
  console.log('OK: test_currentLevels_null_when_no_pivot_on_one_side');
}
```

Ajoute les 5 appels dans `main()`.

- [ ] **Step 2: Implémenter dans `docs/scalping.js`**

```javascript
/**
 * Tendance courte : haussière si les 2 derniers pivots bas confirmés sont
 * strictement croissants ET les 2 derniers pivots hauts confirmés sont
 * strictement croissants (règle du cours — les deux exigés pour "haussier"
 * franc plutôt que "en formation"). Symétrique pour baissière. Neutre sinon,
 * y compris si pas assez de pivots d'un type ou de l'autre.
 */
function classifyTrend(pivots) {
  const lows = pivots.filter(p => p.type === 'low');
  const highs = pivots.filter(p => p.type === 'high');
  if (lows.length < 2 || highs.length < 2) return 'neutre';
  const lastLows = lows.slice(-2);
  const lastHighs = highs.slice(-2);
  const lowsRising = lastLows[1].price > lastLows[0].price;
  const highsRising = lastHighs[1].price > lastHighs[0].price;
  const lowsFalling = lastLows[1].price < lastLows[0].price;
  const highsFalling = lastHighs[1].price < lastHighs[0].price;
  if (lowsRising && highsRising) return 'haussier';
  if (lowsFalling && highsFalling) return 'baissier';
  return 'neutre';
}

/**
 * Support = dernier pivot bas confirmé sous currentPrice ; résistance =
 * dernier pivot haut confirmé au-dessus. null si aucun pivot de ce côté.
 * (La notion de "non cassé depuis" est simplifiée en v1 à "le plus
 * récent en dessous/au-dessus du prix" — un niveau déjà cassé aurait de
 * toute façon le prix de l'autre côté, donc ne serait plus le plus
 * proche pivot pertinent.)
 */
function currentLevels(pivots, currentPrice) {
  const belowLows = pivots.filter(p => p.type === 'low' && p.price < currentPrice);
  const aboveHighs = pivots.filter(p => p.type === 'high' && p.price > currentPrice);
  return {
    support: belowLows.length ? belowLows[belowLows.length - 1].price : null,
    resistance: aboveHighs.length ? aboveHighs[aboveHighs.length - 1].price : null,
  };
}
```

Mets à jour l'export : `module.exports = { fetchGoldCandles, detectPivots, classifyTrend, currentLevels };`

- [ ] **Step 3: Vérification manuelle (Node indisponible)**

Trace chaque test :
1. `lastLows=[10,12]` croissant, `lastHighs=[15,18]` croissant → `haussier`. ✓
2. `lastLows=[10,12]` croissant mais `lastHighs=[18,15]` décroissant → ni (rising&&rising) ni (falling&&falling) → `neutre`. ✓
3. Un seul pivot bas, aucun pivot haut → `lows.length<2` → `neutre` direct. ✓
4. `belowLows` = pivots bas <100 → seulement `{price:98}` (95 aussi <100 mais 98 est le dernier/plus récent dans le tableau) → `support:98`. `aboveHighs` = pivots hauts >100 → `105` et `110`, le dernier est `110` → `resistance:110`. ✓
5. Aucun pivot haut dans le tableau → `aboveHighs=[]` → `resistance:null`. ✓

- [ ] **Step 4: Commit**

```bash
git add docs/scalping.js docs/scalping.test.js
git commit -m "feat(scalping): classification tendance courte + niveaux S/R dynamiques

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Indicateurs — RSI, MACD, Bollinger

**Files:**
- Modify: `docs/scalping.js`
- Modify: `docs/scalping.test.js`

**Interfaces:**
- Produces (chacune renvoie la valeur du **dernier** point seulement — c'est tout ce dont le moteur de confluence a besoin) :
  - `computeRSI(closes, period) -> number`
  - `computeMACD(closes, fastPeriod, slowPeriod, signalPeriod) -> {macd: number, signal: number, histogram: number}`
  - `computeBollinger(closes, period, mult) -> {middle: number, upper: number, lower: number}`

- [ ] **Step 1: Ajouter les tests**

Mets à jour l'import : `const { fetchGoldCandles, detectPivots, classifyTrend, currentLevels, computeRSI, computeMACD, computeBollinger } = require('./scalping.js');`

```javascript
function test_computeRSI_all_gains_is_100() {
  // 15 clôtures, 14 hausses de +1 chacune -> aucune baisse -> RSI=100
  // (convention standard quand la moyenne des baisses est nulle).
  const closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24];
  assert.strictEqual(computeRSI(closes, 14), 100);
  console.log('OK: test_computeRSI_all_gains_is_100');
}

function test_computeRSI_all_losses_is_0() {
  const closes = [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10];
  assert.strictEqual(computeRSI(closes, 14), 0);
  console.log('OK: test_computeRSI_all_losses_is_0');
}

function test_computeRSI_balanced_alternating_is_50() {
  // 14 variations alternées +1/-1 (7 hausses, 7 baisses de même ampleur)
  // -> RS=1 -> RSI=50.
  const closes = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10];
  assert.strictEqual(computeRSI(closes, 14), 50);
  console.log('OK: test_computeRSI_balanced_alternating_is_50');
}

function test_computeMACD_constant_offset_on_linear_series() {
  // closes en progression arithmétique parfaite (+2 à chaque bougie).
  // Avec fast=2/slow=4/signal=2, la MACD line et la ligne signal
  // convergent toutes deux vers la constante 2 -> histogramme 0.
  // Calcul détaillé à la main dans docs/superpowers/plans/2026-09-09-gold-scalping.md.
  const closes = [10, 12, 14, 16, 18, 20, 22];
  const result = computeMACD(closes, 2, 4, 2);
  assert.deepStrictEqual(result, { macd: 2, signal: 2, histogram: 0 });
  console.log('OK: test_computeMACD_constant_offset_on_linear_series');
}

function test_computeBollinger_zero_variance() {
  const closes = [100, 100, 100, 100];
  assert.deepStrictEqual(computeBollinger(closes, 4, 2), { middle: 100, upper: 100, lower: 100 });
  console.log('OK: test_computeBollinger_zero_variance');
}

function test_computeBollinger_with_variance() {
  // closes=[0,0,4,4], période 4 : moyenne=2, écart-type population=2
  // -> upper=2+2*2=6, lower=2-2*2=-2.
  const closes = [0, 0, 4, 4];
  assert.deepStrictEqual(computeBollinger(closes, 4, 2), { middle: 2, upper: 6, lower: -2 });
  console.log('OK: test_computeBollinger_with_variance');
}
```

Ajoute les 6 appels dans `main()`.

- [ ] **Step 2: Implémenter dans `docs/scalping.js`**

```javascript
/**
 * RSI classique (formule du cours) : compare la moyenne des hausses à la
 * moyenne des baisses sur les `period` dernières variations. Renvoie 100
 * si aucune baisse (évite une division par zéro), pas juste une valeur
 * indéfinie.
 */
function computeRSI(closes, period) {
  const changes = [];
  for (let i = closes.length - period; i < closes.length; i++) {
    changes.push(closes[i] - closes[i - 1]);
  }
  const gains = changes.filter(c => c > 0);
  const losses = changes.filter(c => c < 0).map(c => -c);
  const avgGain = gains.reduce((a, b) => a + b, 0) / period;
  const avgLoss = losses.reduce((a, b) => a + b, 0) / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

/**
 * Moyenne mobile exponentielle, seedée par une SMA classique des `period`
 * premières valeurs (convention standard). Renvoie la dernière valeur.
 */
function _emaLast(values, period) {
  const k = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
  }
  return ema;
}

/**
 * Renvoie la série complète des EMA (une valeur par index à partir de
 * `period-1`), nécessaire pour calculer la ligne signal du MACD (qui est
 * elle-même une EMA de la série MACD, pas juste de son dernier point).
 */
function _emaSeries(values, period) {
  const k = 2 / (period + 1);
  const out = [];
  let ema = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out.push(ema);
  for (let i = period; i < values.length; i++) {
    ema = values[i] * k + ema * (1 - k);
    out.push(ema);
  }
  return out;
}

/**
 * MACD(fastPeriod, slowPeriod, signalPeriod) — formule du cours.
 * Renvoie uniquement le dernier point {macd, signal, histogram}, seul
 * nécessaire au moteur de confluence.
 */
function computeMACD(closes, fastPeriod, slowPeriod, signalPeriod) {
  const fastSeries = _emaSeries(closes, fastPeriod);
  const slowSeries = _emaSeries(closes, slowPeriod);
  // fastSeries commence à l'index (fastPeriod-1) de `closes`, slowSeries à
  // (slowPeriod-1) — slowSeries est donc plus courte (elle démarre plus
  // tard). On aligne les deux séries sur le même index de `closes` en
  // décalant fastSeries de (fastSeries.length - slowSeries.length), pour
  // calculer la ligne MACD uniquement sur la période où les deux existent.
  const macdSeries = [];
  for (let i = 0; i < slowSeries.length; i++) {
    const fastIdx = fastSeries.length - slowSeries.length + i;
    macdSeries.push(fastSeries[fastIdx] - slowSeries[i]);
  }
  const signal = _emaLast(macdSeries, signalPeriod);
  const macd = macdSeries[macdSeries.length - 1];
  return { macd, signal, histogram: macd - signal };
}

/**
 * Bandes de Bollinger (formule du cours : MM `period` ± mult × écart-type
 * population sur `period`). Renvoie le dernier point uniquement.
 */
function computeBollinger(closes, period, mult) {
  const window = closes.slice(-period);
  const mean = window.reduce((a, b) => a + b, 0) / period;
  const variance = window.reduce((sum, c) => sum + (c - mean) ** 2, 0) / period;
  const stdev = Math.sqrt(variance);
  return { middle: mean, upper: mean + mult * stdev, lower: mean - mult * stdev };
}
```

Mets à jour l'export : `module.exports = { fetchGoldCandles, detectPivots, classifyTrend, currentLevels, computeRSI, computeMACD, computeBollinger };`

- [ ] **Step 3: Vérification manuelle (Node indisponible)**

- RSI : pour le test "all gains", les 14 changements sont tous `+1` → `gains=[1×14]`, `losses=[]` → `avgLoss=0` → renvoie `100` directement (branche explicite, pas de NaN). Pour "all losses", symétrique → `avgGain=0` → `rs=0` → `100-100/(1+0)=0`. Pour "alternating", 7 gains de 1 et 7 pertes de 1 → `avgGain=avgLoss=0.5` → `rs=1` → `100-100/2=50`.
- MACD : reprends le calcul détaillé de l'EMA(2) et l'EMA(4) donné dans le design ci-dessus (Task 4 du plan) — `fastSeries` (EMA2) = `[11,13,15,17,19,21]` à partir de l'index1, `slowSeries` (EMA4) = `[13,15,17,19]` à partir de l'index3. `slowSeries.length=4`, donc pour `i=3` (dernier point), `fastIdx = 6-4+3=5` → `fastSeries[5]=21`, `slowSeries[3]=19` → `macd=21-19=2`. La série MACD complète alignée = `[15-13, 17-15, 19-17, 21-19] = [2,2,2,2]`. Signal = EMA(2) de `[2,2,2,2]` = constante `2` (EMA d'une série constante = la constante elle-même). `histogram = 2-2 = 0`. ✓
- Bollinger : `[0,0,4,4]`, moyenne=2, écarts=[-2,-2,2,2], carrés=[4,4,4,4], variance=16/4=4, stdev=2 → `upper=2+2×2=6`, `lower=2-2×2=-2`. ✓ `[100,100,100,100]` : variance=0 → bornes = moyenne. ✓

- [ ] **Step 4: Commit**

```bash
git add docs/scalping.js docs/scalping.test.js
git commit -m "feat(scalping): indicateurs RSI/MACD/Bollinger

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Reconnaissance de patterns de bougies

**Files:**
- Modify: `docs/scalping.js`

**Interfaces:**
- Consumes: `candles` (format Task 1), `trend` (résultat de `classifyTrend`, Task 3 — plusieurs patterns n'ont de sens que dans un contexte de tendance donné)
- Produces: `matchCandlestickPattern(candles, trend) -> {name: string, direction: 'haussier'|'baissier'} | null` — examine les 1 à 3 dernières bougies de `candles`.

Cette task n'a **pas** d'étapes TDD (décision actée en spec : "le pattern matching de bougies... restera vérifié manuellement"). Écris le code directement, puis fais un travail de relecture soigné (Step 2) au lieu d'un cycle RED/GREEN.

- [ ] **Step 1: Implémenter dans `docs/scalping.js`**

Ajoute après `computeBollinger`, avant l'export final :

```javascript
function _bodySize(c) { return Math.abs(c.close - c.open); }
function _isBullish(c) { return c.close > c.open; }
function _upperWick(c) { return c.high - Math.max(c.open, c.close); }
function _lowerWick(c) { return Math.min(c.open, c.close) - c.low; }

/**
 * Reconnaît un sous-ensemble de 8 figures de chandeliers (les jugées
 * "efficace/très efficace" par le cours — voir spec §5) sur les 1 à 3
 * dernières bougies de `candles`. `trend` est la tendance courte au
 * moment de l'examen (`classifyTrend`) — plusieurs figures n'ont de sens
 * qu'en contexte (ex: Marteau seulement en tendance baissière). Renvoie
 * la première figure trouvée (ordre de test = ordre de spécificité
 * décroissante : 3 bougies avant 2 avant 1) ou null.
 */
function matchCandlestickPattern(candles, trend) {
  const n = candles.length;
  if (n < 1) return null;
  const last = candles[n - 1];

  // --- Figures à 3 bougies ---
  if (n >= 3) {
    const [c1, c2, c3] = candles.slice(-3);
    // Étoile du Matin : bougie baissière, petit corps isolé (étoile), bougie haussière qui valide.
    if (trend === 'baissier' && !_isBullish(c1) && _bodySize(c2) < _bodySize(c1) * 0.5 && _isBullish(c3) && c3.close > (c1.open + c1.close) / 2) {
      return { name: 'Étoile du Matin', direction: 'haussier' };
    }
    // Étoile du Soir : symétrique.
    if (trend === 'haussier' && _isBullish(c1) && _bodySize(c2) < _bodySize(c1) * 0.5 && !_isBullish(c3) && c3.close < (c1.open + c1.close) / 2) {
      return { name: 'Étoile du Soir', direction: 'baissier' };
    }
  }

  // --- Figures à 2 bougies ---
  if (n >= 2) {
    const [prev, cur] = candles.slice(-2);
    // Englobante haussière : corps de `cur` (vert) englobe le corps de `prev` (rouge).
    if (trend === 'baissier' && !_isBullish(prev) && _isBullish(cur) && cur.open <= prev.close && cur.close >= prev.open) {
      return { name: 'Englobante haussière', direction: 'haussier' };
    }
    // Englobante baissière : symétrique.
    if (trend === 'haussier' && _isBullish(prev) && !_isBullish(cur) && cur.open >= prev.close && cur.close <= prev.open) {
      return { name: 'Englobante baissière', direction: 'baissier' };
    }
    // Pénétrante : bougie verte ouvre sous le corps rouge précédent, clôture au-dessus de son milieu.
    if (trend === 'baissier' && !_isBullish(prev) && _isBullish(cur) && cur.open < prev.close && cur.close > (prev.open + prev.close) / 2 && cur.close < prev.open) {
      return { name: 'Pénétrante', direction: 'haussier' };
    }
    // Nuage noir : symétrique.
    if (trend === 'haussier' && _isBullish(prev) && !_isBullish(cur) && cur.open > prev.close && cur.close < (prev.open + prev.close) / 2 && cur.close > prev.open) {
      return { name: 'Nuage noir', direction: 'baissier' };
    }
  }

  // --- Figures à 1 bougie ---
  const body = _bodySize(last);
  const upperWick = _upperWick(last);
  const lowerWick = _lowerWick(last);
  if (trend === 'baissier' && lowerWick >= body * 2 && upperWick < body * 0.3) {
    return { name: 'Marteau', direction: 'haussier' };
  }
  if (trend === 'haussier' && upperWick >= body * 2 && lowerWick < body * 0.3) {
    return { name: 'Étoile filante', direction: 'baissier' };
  }

  return null;
}
```

Mets à jour l'export : ajoute `matchCandlestickPattern` à la liste.

- [ ] **Step 2: Relecture manuelle (remplace le cycle TDD pour cette task)**

Relis chaque bloc contre sa définition dans la synthèse du cours
(`Synthese_pour_scoring_technique.md`, section 7) :
- Vérifie que chaque figure est bien conditionnée par le bon `trend`
  (Marteau/Étoile du Matin/Englobante haussière/Pénétrante exigent
  `'baissier'` ; leurs symétriques exigent `'haussier'`).
- Vérifie qu'aucune condition ne peut être vraie pour les deux sens à la
  fois avec les mêmes bougies (ex: une bougie ne peut pas être à la fois
  Marteau et Étoile filante puisque l'une exige `lowerWick>=2×body` et
  l'autre `upperWick>=2×body`, et le `trend` diffère).
- Note que la fonction s'arrête à la première figure trouvée (ordre
  3-bougies → 2-bougies → 1-bougie) — documente ce choix explicitement
  dans ton rapport de task, ce n'est pas un TODO oublié.

- [ ] **Step 3: Commit**

```bash
git add docs/scalping.js
git commit -m "feat(scalping): reconnaissance de 8 patterns de bougies

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Moteur de confluence — `computeSignal`

**Files:**
- Modify: `docs/scalping.js`

**Interfaces:**
- Consumes: toutes les fonctions des Tasks 1-5.
- Produces: `computeSignal(candles) -> {status: 'achat'|'vente'|'neutre', price: number, entry: number|null, stopLoss: number|null, takeProfit: number|null, trend: string, pattern: object|null}`

Pas d'étapes TDD ici non plus (même décision qu'en Task 5 — logique de
confluence vérifiée manuellement).

- [ ] **Step 1: Implémenter dans `docs/scalping.js`**

```javascript
const SCALP_PIVOT_K = 3;
const SCALP_RSI_PERIOD = 14;
const SCALP_MACD_FAST = 12;
const SCALP_MACD_SLOW = 26;
const SCALP_MACD_SIGNAL = 9;
const SCALP_BOLLINGER_PERIOD = 20;
const SCALP_BOLLINGER_MULT = 2;
const SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5; // repli si aucun niveau S/R clair pour le TP
const SCALP_LEVEL_PROXIMITY = 0.5; // $ de tolérance pour juger un "rebond" sur un niveau

/**
 * Moteur de confluence (spec §6) : combine tendance + S/R + indicateurs +
 * chandeliers en un signal Achat/Vente/Neutre, avec Entrée/Stop-loss/TP
 * si un signal est émis. Ne lève jamais d'exception — `candles` trop
 * court renvoie `neutre` avec tous les champs de prix à null.
 */
function computeSignal(candles) {
  const price = candles.length ? candles[candles.length - 1].close : null;
  if (!price || candles.length < SCALP_BOLLINGER_PERIOD + 1) {
    return { status: 'neutre', price, entry: null, stopLoss: null, takeProfit: null, trend: 'neutre', pattern: null };
  }

  const pivots = detectPivots(candles, SCALP_PIVOT_K);
  const trend = classifyTrend(pivots);
  const levels = currentLevels(pivots, price);
  const closes = candles.map(c => c.close);
  const rsi = computeRSI(closes, SCALP_RSI_PERIOD);
  const macd = computeMACD(closes, SCALP_MACD_FAST, SCALP_MACD_SLOW, SCALP_MACD_SIGNAL);
  const pattern = matchCandlestickPattern(candles, trend);

  const nearSupport = levels.support !== null && Math.abs(price - levels.support) <= SCALP_LEVEL_PROXIMITY;
  const nearResistance = levels.resistance !== null && Math.abs(price - levels.resistance) <= SCALP_LEVEL_PROXIMITY;
  const brokeResistance = levels.resistance !== null && price > levels.resistance;
  const brokeSupport = levels.support !== null && price < levels.support;

  const structurelAchat = trend === 'haussier' && (nearSupport || brokeResistance);
  const structurelVente = trend === 'baissier' && (nearResistance || brokeSupport);

  const confirmationAchat = rsi < 70 && macd.macd > macd.signal && pattern && pattern.direction === 'haussier';
  const confirmationVente = rsi > 30 && macd.macd < macd.signal && pattern && pattern.direction === 'baissier';

  if (structurelAchat && confirmationAchat) {
    const stopLoss = levels.support !== null ? levels.support - SCALP_LEVEL_PROXIMITY : price - price * 0.001;
    const risk = price - stopLoss;
    const takeProfit = levels.resistance !== null && levels.resistance > price
      ? levels.resistance
      : price + risk * SCALP_TAKEPROFIT_RISK_MULTIPLE;
    return { status: 'achat', price, entry: price, stopLoss, takeProfit, trend, pattern };
  }
  if (structurelVente && confirmationVente) {
    const stopLoss = levels.resistance !== null ? levels.resistance + SCALP_LEVEL_PROXIMITY : price + price * 0.001;
    const risk = stopLoss - price;
    const takeProfit = levels.support !== null && levels.support < price
      ? levels.support
      : price - risk * SCALP_TAKEPROFIT_RISK_MULTIPLE;
    return { status: 'vente', price, entry: price, stopLoss, takeProfit, trend, pattern };
  }
  return { status: 'neutre', price, entry: null, stopLoss: null, takeProfit: null, trend, pattern: null };
}
```

Mets à jour l'export final avec toutes les fonctions, y compris `computeSignal` :
```javascript
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    fetchGoldCandles, detectPivots, classifyTrend, currentLevels,
    computeRSI, computeMACD, computeBollinger, matchCandlestickPattern,
    computeSignal,
  };
}
```

- [ ] **Step 2: Relecture manuelle**

Vérifie en particulier :
- Le garde-fou `candles.length < SCALP_BOLLINGER_PERIOD + 1` renvoie bien un objet complet (pas `undefined` sur un champ) même quand il n'y a pas assez de données — sinon le rendu DOM (Task 7) plantera sur un champ manquant.
- Aucune branche ne peut renvoyer à la fois `structurelAchat` et `structurelVente` vrais simultanément (ils dépendent tous deux de `trend`, qui ne peut valoir qu'une seule chose à la fois) — donc jamais de conflit achat/vente.
- Le calcul de `stopLoss`/`takeProfit` a un repli (`price ± price*0.001` pour le stop, `price ± risk×1.5` pour le TP) quand `levels.support`/`levels.resistance` est `null` — jamais de `null` arithmétique qui donnerait `NaN`.

- [ ] **Step 3: Commit**

```bash
git add docs/scalping.js
git commit -m "feat(scalping): moteur de confluence computeSignal

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Intégration UI dans `docs/index.html`

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: `computeSignal`, `fetchGoldCandles` (`docs/scalping.js`, chargé en `<script>`)

- [ ] **Step 1: Ajouter le CSS**

Dans le `<style>`, juste après le bloc `.scalp-note` existant (cherche
`.scalp-note { color: var(--muted);` — juste après sa fermeture), ajoute :

```css
  .or-tabbar { display: flex; gap: 10px; margin: 4px 0 16px; }
  .or-tab-btn {
    flex: 1; background: var(--panel); border: 1px solid var(--border); color: var(--text);
    font-size: 13px; font-weight: 500; padding: 12px 0; border-radius: 10px;
  }
  .or-tab-btn[aria-selected="true"] { color: var(--gold); border-color: var(--gold); }

  .scalp-signal-panel { margin: 0 0 20px; }
  .scalp-signal-status {
    display: inline-block; padding: 4px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 600; letter-spacing: 0.02em; margin-bottom: 12px;
  }
  .scalp-signal-status.achat { background: var(--gold); color: var(--bg); }
  .scalp-signal-status.vente { background: var(--rust); color: var(--bg); }
  .scalp-signal-status.neutre { background: var(--panel); color: var(--muted); border: 1px solid var(--border); }
```

- [ ] **Step 2: Restructurer le HTML de l'écran `#or`**

Remplace le bloc existant (de `<div class="scalp-panel">` jusqu'à `<main id="content">`
inclus, en gardant `<main id="content">` tel quel) :

```html
  <div class="or-tabbar" role="tablist">
    <button class="or-tab-btn" id="orTabLongTerme" role="tab" aria-selected="true" type="button">Long terme</button>
    <button class="or-tab-btn" id="orTabScalping" role="tab" aria-selected="false" type="button">Scalping</button>
  </div>

  <div id="orScalpingView" hidden>
    <div class="scalp-panel">
      <h2>Graphique temps réel</h2>
      <div class="scalp-chart-box">
        <div id="tv-scalping-chart" style="height:100%;"></div>
      </div>
      <p class="scalp-note">Or spot (OANDA:XAUUSD), bougies 1 minute — widget
        TradingView, outils de dessin et indicateurs disponibles. Ce
        graphique n'affecte pas le score macro de l'onglet Long terme.</p>
    </div>
    <div class="scalp-signal-panel" id="scalpSignalPanel">
      <div class="empty">Chargement…</div>
    </div>
  </div>

  <main id="content">
    <div class="empty">Chargement…</div>
  </main>
```

(le `<main id="content">` n'est PAS touché — c'est l'onglet Long terme,
inchangé)

- [ ] **Step 3: Ajouter la logique JS de bascule d'onglet et de rendu du signal**

Juste après la fonction `loadScalpChart()` existante (cherche
`document.body.appendChild(script);\n}` qui la termine), ajoute :

```javascript
const TWELVE_DATA_API_KEY = 'REMPLACE_PAR_TA_CLE_TWELVE_DATA';

let scalpingJsLoaded = false;
let scalpingPollTimer = null;

function loadScalpingModule() {
  // Charge docs/scalping.js à la demande (premier clic sur l'onglet
  // Scalping), même logique paresseuse que loadScalpChart/loadIndices.
  return new Promise((resolve, reject) => {
    if (scalpingJsLoaded) { resolve(); return; }
    const script = document.createElement('script');
    script.src = 'scalping.js';
    script.onload = () => { scalpingJsLoaded = true; resolve(); };
    script.onerror = () => reject(new Error('scalping.js n\'a pas pu être chargé'));
    document.body.appendChild(script);
  });
}

function renderScalpSignal(signal) {
  const panel = document.getElementById('scalpSignalPanel');
  const statusLabel = { achat: 'Achat', vente: 'Vente', neutre: 'Neutre — en attente de confluence' }[signal.status];
  const priceRows = signal.status === 'neutre' ? '' : `
    <div class="price-row"><span class="price-label">Entrée</span><span class="price-value">${formatPrice(signal.entry)}</span></div>
    <div class="price-row"><span class="price-label">Stop-loss</span><span class="price-value" style="color:var(--rust)">${formatPrice(signal.stopLoss)}</span></div>
    <div class="price-row"><span class="price-label">TP</span><span class="price-value" style="color:var(--gold)">${formatPrice(signal.takeProfit)}</span></div>
  `;
  panel.innerHTML = `
    <span class="scalp-signal-status ${signal.status}">${statusLabel}</span>
    <div class="price-panel">
      <div class="price-row"><span class="price-label">Cours actuel</span><span class="price-value">${formatPrice(signal.price)}</span></div>
      ${priceRows}
      <p class="price-disclaimer">Signal probabiliste basé sur tendance/S-R/indicateurs/chandeliers sur bougies 1min — pas un conseil d'investissement.</p>
    </div>
  `;
}

const SCALP_STALE_THRESHOLD_MS = 5 * 60 * 1000; // 5 minutes

async function refreshScalpSignal() {
  const panel = document.getElementById('scalpSignalPanel');
  try {
    const candles = await fetchGoldCandles(TWELVE_DATA_API_KEY);
    const lastCandleTime = new Date(candles[candles.length - 1].time.replace(' ', 'T') + 'Z');
    const ageMs = Date.now() - lastCandleTime.getTime();
    if (ageMs > SCALP_STALE_THRESHOLD_MS) {
      // Marché fermé (week-end) ou API en retard — le cours de l'or spot
      // (forex) est fermé du vendredi soir au dimanche soir UTC. Plutôt
      // que d'afficher un signal calculé sur une donnée périmée comme
      // s'il était en direct, on le dit explicitement (cf. spec, section
      // "Risques connus").
      panel.innerHTML = `<div class="empty">Dernière donnée disponible : il y a ${Math.round(ageMs / 60000)} min — marché probablement fermé (week-end) ou API en retard. Pas de signal affiché sur une donnée aussi ancienne.</div>`;
      return;
    }
    const signal = computeSignal(candles);
    renderScalpSignal(signal);
  } catch (e) {
    panel.innerHTML = `<div class="empty">Scalping indisponible pour l'instant.<br>${e.message}</div>`;
  }
}

function startScalpingPolling() {
  refreshScalpSignal();
  if (scalpingPollTimer) return;
  scalpingPollTimer = setInterval(() => {
    if (document.visibilityState === 'visible') refreshScalpSignal();
  }, 60000);
}

function stopScalpingPolling() {
  if (scalpingPollTimer) { clearInterval(scalpingPollTimer); scalpingPollTimer = null; }
}
```

- [ ] **Step 4: Câbler les boutons d'onglet**

Trouve la fin du `<script>` (juste avant `renderRoute(false);` en toute
fin de fichier) et ajoute juste avant :

```javascript
document.getElementById('orTabLongTerme').addEventListener('click', () => {
  document.getElementById('orTabLongTerme').setAttribute('aria-selected', 'true');
  document.getElementById('orTabScalping').setAttribute('aria-selected', 'false');
  document.getElementById('orScalpingView').hidden = true;
  document.getElementById('content').hidden = false;
  stopScalpingPolling();
});
document.getElementById('orTabScalping').addEventListener('click', () => {
  document.getElementById('orTabScalping').setAttribute('aria-selected', 'true');
  document.getElementById('orTabLongTerme').setAttribute('aria-selected', 'false');
  document.getElementById('content').hidden = true;
  document.getElementById('orScalpingView').hidden = false;
  loadScalpChart();
  loadScalpingModule().then(startScalpingPolling).catch(e => {
    document.getElementById('scalpSignalPanel').innerHTML = `<div class="empty">${e.message}</div>`;
  });
});
```

- [ ] **Step 5: Vérification manuelle du diff (pas d'outillage de navigateur dans ce sandbox)**

Ce dépôt n'a pas de tests JS pour le HTML/UI (constaté sur les modules
frontend précédents de ce projet). Relis le diff complet de
`docs/index.html` à la recherche de :
1. Balises non fermées ou accolades de template literal non appariées
   dans `renderScalpSignal`.
2. Chaque `id` référencé en JS (`orTabLongTerme`, `orTabScalping`,
   `orScalpingView`, `scalpSignalPanel`, `content`) existe bien dans le
   HTML modifié à l'étape 2.
3. `formatPrice` (utilisée dans `renderScalpSignal`) est bien une
   fonction déjà existante dans ce fichier (cherche `function
   formatPrice`) — ne pas la redéfinir.
4. Le clic initial sur "Long terme" ne casse rien : `content` doit
   rester visible par défaut au chargement de la page (c'est déjà son
   état par défaut, ce bloc ne doit pas y toucher).
5. `refreshScalpSignal` parse bien `candles[...].time` (format
   `"YYYY-MM-DD HH:MM:SS"`, UTC grâce à `timezone=UTC` de Task 1) en
   `Date` valide (`.replace(' ', 'T') + 'Z'` donne un ISO 8601 valide,
   ex. `"2026-09-09T10:02:00Z"`) — vérifie à la main sur un exemple que
   `new Date(...)` ne renvoie pas `Invalid Date`.

Confirme aussi que `python -m pytest tests/test_indices_score.py -q`
passe toujours (aucun test Python ne doit être affecté par ce
changement HTML/JS pur).

- [ ] **Step 6: Commit**

```bash
git add docs/index.html
git commit -m "feat(scalping): bascule Long terme / Scalping dans l'ecran Or

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Clé API, vérification en conditions réelles, merge

**Files:** `docs/index.html` (juste la clé), aucun autre — git/gh uniquement.

- [ ] **Step 1: Remplacer le placeholder de clé API**

Avant tout merge, `TWELVE_DATA_API_KEY` (Task 7, Step 3) contient
`'REMPLACE_PAR_TA_CLE_TWELVE_DATA'` — un placeholder volontaire, pas
oublié. Il faut :
1. Que l'utilisateur crée un compte gratuit sur twelvedata.com et
   génère une clé API.
2. Remplacer la valeur dans `docs/index.html` par cette clé réelle avant
   de merger sur `main` (sinon le module Scalping ne fonctionnera jamais
   en production).

Si ce plan est exécuté par un agent qui n'a pas la clé de l'utilisateur,
**s'arrêter ici et demander la clé** plutôt que de merger avec le
placeholder — un merge avec un placeholder casserait silencieusement la
fonctionnalité en production (l'appel Twelve Data échouerait avec un
401, affiché comme "Scalping indisponible" à chaque visite).

- [ ] **Step 2: Créer la branche et pousser**

```bash
git checkout -b gold-scalping-module
git push -u origin gold-scalping-module
```

(Si les commits des tasks précédentes ont déjà été faits sur cette
branche par les tasks 1-7, ignorer ce step.)

- [ ] **Step 3: Vérification en conditions réelles**

Ce chantier est purement frontend (aucun impact sur `indices.yml`,
`gold_score.py`, ou tout autre composant serveur) — **pas de run
GitHub Actions à déclencher pour ce chantier**, contrairement aux
chantiers backend précédents de ce projet.

La vraie vérification (que Twelve Data renvoie des données XAU/USD 1min
exploitables, que le moteur de confluence produit des signaux sensés en
conditions réelles) ne peut se faire qu'en ouvrant la page réellement
déployée avec une vraie clé API — chose que ni ce sandbox (pas
d'outillage de navigateur) ni un simple `pytest` ne peuvent vérifier.

Après avoir mergé sur `main` (Step 5), **demander explicitement à
l'utilisateur d'ouvrir le site, d'aller sur Or → Scalping, et de
confirmer que** :
1. Le graphique TradingView 1min s'affiche toujours normalement.
2. Un état "Neutre — en attente de confluence" ou un vrai signal
   s'affiche (pas un message d'erreur "Scalping indisponible").
3. Les prix affichés (cours actuel notamment) sont cohérents avec le
   cours de l'or réel du moment.

Si l'utilisateur rapporte une erreur, la diagnostiquer avant de
considérer ce chantier terminé (ne pas le déclarer fini sur la seule
base des tests unitaires).

- [ ] **Step 4: Synchroniser et merger**

```bash
git checkout gold-scalping-module
git pull --ff-only origin gold-scalping-module
git checkout main
git pull --ff-only origin main
git merge gold-scalping-module --no-edit
```

En cas de conflit sur `docs/indices.json`/`indices_history.json`/`docs/score.json`
(fichiers auto-générés par les crons, peuvent avoir avancé entre-temps) :
```bash
git checkout --theirs docs/indices.json indices_history.json docs/score.json score.json
git add docs/indices.json indices_history.json docs/score.json score.json
git commit --no-edit
```

- [ ] **Step 5: Tests finaux et push**

```bash
python -m pytest tests/test_indices_score.py -q
git push origin main
```

- [ ] **Step 6: Nettoyer la branche**

```bash
git branch -d gold-scalping-module
git push origin --delete gold-scalping-module
```
