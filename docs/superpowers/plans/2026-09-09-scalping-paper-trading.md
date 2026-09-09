# Suivi en paper-trading des signaux Scalping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Faire tourner le moteur de signal Scalping Or en paper-trading,
sans surveillance, pendant 7 jours, via un workflow GitHub Actions
autonome, avec un bilan chiffré consultable dans l'app.

**Architecture:** Un script Node (`scalping_tracker.js`, racine du repo)
réutilise verbatim `fetchGoldCandles`/`computeSignal` de `docs/scalping.js`
via `require`, gère le cycle de vie d'une position de paper-trading
(ouverture/clôture) et persiste l'état dans `docs/scalping_tracking.json`.
Un workflow GitHub Actions (`*/5 * * * *`) exécute ce script en continu
pendant l'essai, indépendamment de toute machine locale. Une nouvelle
section "Suivi" dans `docs/index.html` affiche les résultats.

**Tech Stack:** Node.js (runtime GitHub Actions — voir Contraintes
globales), GitHub Actions (cron + secrets), JSON pour la persistance, JS
navigateur sans framework pour l'UI (conventions déjà établies dans ce
fichier).

**Spec:** `docs/superpowers/specs/2026-09-09-scalping-paper-trading-design.md`

## Global Constraints

- Node.js est indisponible dans le sandbox local d'implémentation (même
  contrainte que le plan `2026-09-09-gold-scalping.md`) — toutes les
  étapes "lancer les tests" sont donc, dans CE sandbox, une trace
  manuelle rigoureuse plutôt qu'une exécution réelle. **Différence
  importante avec le plan précédent : ces tests vont réellement s'exécuter
  en CI** une fois le workflow GitHub Actions déclenché (Node est
  préinstallé sur les runners `ubuntu-latest`) — traiter la vérification
  manuelle comme du "best-effort en attendant la première exécution CI
  réelle", pas comme un état permanent.
- Une seule position ouverte à la fois — jamais d'empilement.
- Désambiguïsation SL/TP touchés dans la même bougie : le SL est supposé
  touché en premier (hypothèse prudente standard en backtesting).
- Durée max d'une position : 2 heures (`MAX_POSITION_DURATION_MS`).
- Durée de l'essai : 7 jours depuis le premier run (`TRIAL_DURATION_MS`),
  compté depuis `trial_start`, jamais réécrit une fois fixé.
- `docs/scalping.js` n'est pas modifié par ce chantier — uniquement
  consommé via `require`.
- Réutiliser `formatPriceUsd`/`formatPct` déjà présents dans
  `docs/index.html` (fix de la revue finale du chantier Scalping
  précédent) — ne pas les redéfinir.
- L'essai ne commence à compter qu'une fois cette branche mergée ET le
  premier run réel du workflow effectué — la dernière tâche du plan
  inclut la vérification de ce premier run avant de considérer le
  chantier terminé (un workflow cassé découvert seulement après "7 jours"
  gâcherait toute la fenêtre d'essai).

---

## Task 1: Moteur de paper-trading — `scalping_tracker.js` + tests

**Files:**
- Create: `scalping_tracker.js` (racine du repo, à côté de
  `gold_score.py`/`indices_score.py`)
- Create: `scalping_tracker.test.js` (racine du repo)

**Interfaces:**
- Consomme : `fetchGoldCandles(apiKey, fetchImpl)` et `computeSignal(candles)`
  de `docs/scalping.js` (déjà en prod, signatures figées).
- Produit (consommé par Task 2 — le workflow appelle `main()` via
  `node scalping_tracker.js`, rien d'autre n'en dépend directement) :
  - `loadTracking(filePath) -> {trial_start, trial_ended, positions}`
  - `saveTracking(data, filePath) -> void`
  - `computeReturn(direction, entryPrice, closePrice) -> {return_usd, return_pct}`
  - `decidePositionOutcome(position, candlesSinceEntry, now, trialElapsed) -> {closed: false} | {closed: true, reason, price}`
  - `buildPositionFromSignal(signal, now) -> position object`
  - `runOnce(apiKey, fetchImpl, now, filePath) -> Promise<data | undefined>`
    (`undefined` si le fetch a échoué — aucune écriture dans ce cas)

- [ ] **Step 1: Écrire les tests dans `scalping_tracker.test.js`**

```javascript
// scalping_tracker.test.js
// Tests JS sans framework, exécutables via `node scalping_tracker.test.js`
// (comme docs/scalping.test.js). Chaque test compare un résultat calculé
// à une valeur attendue calculée à la main.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  loadTracking,
  saveTracking,
  computeReturn,
  decidePositionOutcome,
  buildPositionFromSignal,
  runOnce,
  TRIAL_DURATION_MS,
  MAX_POSITION_DURATION_MS,
} = require('./scalping_tracker.js');

function tempFile() {
  return path.join(os.tmpdir(), `scalping_tracker_test_${Date.now()}_${Math.random().toString(36).slice(2)}.json`);
}

function test_loadTracking_returns_default_shape_when_file_missing() {
  const f = tempFile();
  if (fs.existsSync(f)) fs.unlinkSync(f);
  const data = loadTracking(f);
  assert.deepStrictEqual(data, { trial_start: null, trial_ended: false, positions: [] });
  console.log('OK: test_loadTracking_returns_default_shape_when_file_missing');
}

function test_saveTracking_then_loadTracking_roundtrips() {
  const f = tempFile();
  const data = { trial_start: '2026-09-09T14:00:00.000Z', trial_ended: false, positions: [{ id: 'x' }] };
  saveTracking(data, f);
  const reloaded = loadTracking(f);
  assert.deepStrictEqual(reloaded, data);
  fs.unlinkSync(f);
  console.log('OK: test_saveTracking_then_loadTracking_roundtrips');
}

function test_computeReturn_achat_gain() {
  // Achat : entrée 100, clôture 105 -> gain de 5$, +5%.
  const r = computeReturn('achat', 100, 105);
  assert.deepStrictEqual(r, { return_usd: 5, return_pct: 5 });
  console.log('OK: test_computeReturn_achat_gain');
}

function test_computeReturn_vente_gain() {
  // Vente : entrée 100, clôture 95 -> gain de 5$ (le prix a baissé,
  // ce qui est gagnant pour une position vendeuse), +5%.
  const r = computeReturn('vente', 100, 95);
  assert.deepStrictEqual(r, { return_usd: 5, return_pct: 5 });
  console.log('OK: test_computeReturn_vente_gain');
}

function test_decidePositionOutcome_tp_hit() {
  // Achat, SL=95, TP=110. Bougie 1 : high=108 (pas assez pour TP), low=99
  // (pas assez pour SL) -> rien. Bougie 2 : high=112 >= 110 -> TP touché,
  // prix de clôture = le niveau du TP (110), pas le high de la bougie.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [
    { high: 108, low: 99, close: 107 },
    { high: 112, low: 105, close: 111 },
  ];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'tp_hit', price: 110 });
  console.log('OK: test_decidePositionOutcome_tp_hit');
}

function test_decidePositionOutcome_sl_hit() {
  // Achat, SL=95, TP=110. Une seule bougie : low=93 <= 95 -> SL touché.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [{ high: 97, low: 93, close: 94 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 95 });
  console.log('OK: test_decidePositionOutcome_sl_hit');
}

function test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first() {
  // Achat, SL=95, TP=110. Une bougie qui franchit les deux (low=90<=95 ET
  // high=112>=110) -> règle de désambiguïsation : SL supposé touché en
  // premier, jamais TP dans ce cas.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [{ high: 112, low: 90, close: 100 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 95 });
  console.log('OK: test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first');
}

function test_decidePositionOutcome_vente_sl_hit() {
  // Vente : SL=110 (au-dessus de l'entrée), TP=90 (en dessous). Une bougie
  // dont le high atteint 112 >= 110 -> SL touché (le prix est monté contre
  // la position vendeuse).
  const position = { direction: 'vente', stop_loss: 110, take_profit: 90 };
  const candles = [{ high: 112, low: 100, close: 108 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 110 });
  console.log('OK: test_decidePositionOutcome_vente_sl_hit');
}

function test_decidePositionOutcome_max_duration() {
  // Ni SL ni TP touché, mais 2h se sont écoulées depuis l'entrée -> clôture
  // forcée au dernier prix de clôture connu.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + MAX_POSITION_DURATION_MS; // exactement 2h plus tard
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'max_duration', price: 99.5 });
  console.log('OK: test_decidePositionOutcome_max_duration');
}

function test_decidePositionOutcome_trial_end_safety_net() {
  // Ni SL ni TP touché, moins de 2h écoulées, mais trialElapsed=true (fin
  // de l'essai) -> clôture forcée, filet de sécurité.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + 10 * 60 * 1000; // 10 minutes plus tard seulement
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, true);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'trial_end', price: 99.5 });
  console.log('OK: test_decidePositionOutcome_trial_end_safety_net');
}

function test_decidePositionOutcome_stays_open() {
  // Ni SL ni TP touché, moins de 2h écoulées, essai pas terminé -> reste
  // ouverte.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + 10 * 60 * 1000;
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, false);
  assert.deepStrictEqual(outcome, { closed: false });
  console.log('OK: test_decidePositionOutcome_stays_open');
}

function test_buildPositionFromSignal_with_pattern() {
  const signal = {
    status: 'achat', price: 3450, stopLoss: 3448, takeProfit: 3455,
    trend: 'baissier', pattern: { name: 'Marteau', direction: 'haussier' },
  };
  const now = new Date('2026-09-09T14:35:00.000Z').getTime();
  const position = buildPositionFromSignal(signal, now);
  assert.deepStrictEqual(position, {
    id: 'scalp-2026-09-09T14:35:00.000Z',
    direction: 'achat',
    status: 'open',
    entry_time: '2026-09-09T14:35:00.000Z',
    entry_price: 3450,
    stop_loss: 3448,
    take_profit: 3455,
    trend_at_entry: 'baissier',
    pattern_at_entry: 'Marteau',
    close_time: null,
    close_price: null,
    close_reason: null,
    return_usd: null,
    return_pct: null,
  });
  console.log('OK: test_buildPositionFromSignal_with_pattern');
}

function test_buildPositionFromSignal_without_pattern() {
  const signal = { status: 'vente', price: 3450, stopLoss: 3452, takeProfit: 3445, trend: 'haussier', pattern: null };
  const now = new Date('2026-09-09T14:35:00.000Z').getTime();
  const position = buildPositionFromSignal(signal, now);
  assert.strictEqual(position.pattern_at_entry, null);
  console.log('OK: test_buildPositionFromSignal_without_pattern');
}

async function test_runOnce_opens_position_on_new_signal() {
  const f = tempFile();
  // Réponse Twelve Data fabriquée avec assez de bougies (>= 36, le
  // SCALP_MIN_CANDLES de docs/scalping.js) pour que computeSignal ne
  // reste pas bloqué sur le garde-fou "pas assez de données". On ne
  // contrôle pas ici la valeur exacte du signal produit (achat/vente/
  // neutre dépend de la logique de confluence complète) ; ce test
  // vérifie seulement que runOnce se comporte correctement dans les deux
  // cas : soit une position est ouverte et trial_start est fixé, soit
  // aucune position n'est ouverte mais trial_start est fixé quand même.
  const values = [];
  for (let i = 0; i < 40; i++) {
    const t = new Date(Date.UTC(2026, 8, 9, 14, 0, 0) + i * 60000);
    const p = 3450 + Math.sin(i) * 2; // légère variation, sans importance pour ce test
    values.push({
      datetime: t.toISOString().slice(0, 19).replace('T', ' '),
      open: String(p), high: String(p + 0.5), low: String(p - 0.5), close: String(p),
    });
  }
  values.reverse(); // Twelve Data renvoie le plus récent en premier
  const fakeFetch = async () => ({ ok: true, json: async () => ({ status: 'ok', values }) });
  const now = Date.now();
  const data = await runOnce('fake-key', fakeFetch, now, f);
  assert.ok(data, 'runOnce doit renvoyer les données en cas de succès du fetch');
  assert.strictEqual(data.trial_start, new Date(now).toISOString());
  assert.ok(Array.isArray(data.positions));
  if (fs.existsSync(f)) fs.unlinkSync(f);
  console.log('OK: test_runOnce_opens_position_on_new_signal');
}

async function test_runOnce_does_not_write_on_fetch_failure() {
  const f = tempFile();
  if (fs.existsSync(f)) fs.unlinkSync(f);
  const fakeFetch = async () => { throw new Error('reseau indisponible'); };
  const result = await runOnce('fake-key', fakeFetch, Date.now(), f);
  assert.strictEqual(result, undefined);
  assert.strictEqual(fs.existsSync(f), false, 'aucun fichier ne doit etre cree si le fetch echoue');
  console.log('OK: test_runOnce_does_not_write_on_fetch_failure');
}

async function main() {
  test_loadTracking_returns_default_shape_when_file_missing();
  test_saveTracking_then_loadTracking_roundtrips();
  test_computeReturn_achat_gain();
  test_computeReturn_vente_gain();
  test_decidePositionOutcome_tp_hit();
  test_decidePositionOutcome_sl_hit();
  test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first();
  test_decidePositionOutcome_vente_sl_hit();
  test_decidePositionOutcome_max_duration();
  test_decidePositionOutcome_trial_end_safety_net();
  test_decidePositionOutcome_stays_open();
  test_buildPositionFromSignal_with_pattern();
  test_buildPositionFromSignal_without_pattern();
  await test_runOnce_opens_position_on_new_signal();
  await test_runOnce_does_not_write_on_fetch_failure();
  console.log('Tous les tests scalping_tracker sont passes.');
}

main().catch(e => { console.error(e); process.exit(1); });
```

- [ ] **Step 2: Vérifier que les tests échouent (best-effort — Node
  indisponible localement)**

`scalping_tracker.js` n'existe pas encore : le `require('./scalping_tracker.js')`
en haut du fichier de test échouerait avec `Cannot find module`. C'est
l'échec attendu. Comme Node n'est pas installé dans ce sandbox, cette
étape ne peut pas être exécutée réellement ici — elle SERA vérifiée pour
de vrai lors du premier run du workflow (Task 2/4). Note-le dans ton
rapport plutôt que de l'ignorer.

- [ ] **Step 3: Implémenter `scalping_tracker.js`**

```javascript
// scalping_tracker.js
// Moteur de paper-trading pour les signaux du module Scalping Or.
// Réutilise verbatim fetchGoldCandles/computeSignal de docs/scalping.js
// (via require) — zéro logique dupliquée, zéro risque de divergence
// entre ce que le navigateur affiche et ce que ce script évalue.
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { fetchGoldCandles, computeSignal } = require('./docs/scalping.js');

const TRACKING_FILE = path.join(__dirname, 'docs', 'scalping_tracking.json');
const TRIAL_DURATION_MS = 7 * 24 * 60 * 60 * 1000; // 7 jours
const MAX_POSITION_DURATION_MS = 2 * 60 * 60 * 1000; // 2 heures

function loadTracking(filePath = TRACKING_FILE) {
  if (!fs.existsSync(filePath)) {
    return { trial_start: null, trial_ended: false, positions: [] };
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function saveTracking(data, filePath = TRACKING_FILE) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2) + '\n', 'utf8');
}

/**
 * Achat : return_usd = clôture - entrée (gagnant si le prix monte).
 * Vente : return_usd = entrée - clôture (gagnant si le prix baisse).
 */
function computeReturn(direction, entryPrice, closePrice) {
  const return_usd = direction === 'achat' ? closePrice - entryPrice : entryPrice - closePrice;
  const return_pct = (return_usd / entryPrice) * 100;
  return { return_usd, return_pct };
}

/**
 * Examine, dans l'ordre chronologique, les bougies postérieures à
 * l'entrée pour décider si la position doit être clôturée. Règle de
 * désambiguïsation : si une même bougie touche à la fois le SL et le TP,
 * le SL est vérifié en premier et gagne toujours — hypothèse prudente
 * standard en backtesting, qui évite de surestimer la performance.
 * `candlesSinceEntry` doit déjà être filtré (candle.time > entry_time) par
 * l'appelant.
 */
function decidePositionOutcome(position, candlesSinceEntry, now, trialElapsed) {
  const isAchat = position.direction === 'achat';
  for (const c of candlesSinceEntry) {
    const slTouched = isAchat ? c.low <= position.stop_loss : c.high >= position.stop_loss;
    if (slTouched) {
      return { closed: true, reason: 'sl_hit', price: position.stop_loss };
    }
    const tpTouched = isAchat ? c.high >= position.take_profit : c.low <= position.take_profit;
    if (tpTouched) {
      return { closed: true, reason: 'tp_hit', price: position.take_profit };
    }
  }
  const lastClose = candlesSinceEntry.length ? candlesSinceEntry[candlesSinceEntry.length - 1].close : position.entry_price;
  const entryTime = new Date(position.entry_time).getTime();
  if (now - entryTime >= MAX_POSITION_DURATION_MS) {
    return { closed: true, reason: 'max_duration', price: lastClose };
  }
  if (trialElapsed) {
    return { closed: true, reason: 'trial_end', price: lastClose };
  }
  return { closed: false };
}

/**
 * Construit une nouvelle position à partir d'un signal computeSignal
 * ('achat'/'vente' uniquement — ne pas appeler avec 'neutre').
 */
function buildPositionFromSignal(signal, now) {
  const iso = new Date(now).toISOString();
  return {
    id: `scalp-${iso}`,
    direction: signal.status,
    status: 'open',
    entry_time: iso,
    entry_price: signal.price,
    stop_loss: signal.stopLoss,
    take_profit: signal.takeProfit,
    trend_at_entry: signal.trend,
    pattern_at_entry: signal.pattern ? signal.pattern.name : null,
    close_time: null,
    close_price: null,
    close_reason: null,
    return_usd: null,
    return_pct: null,
  };
}

/**
 * Un cycle complet du tracker. Renvoie les données sauvegardées, ou
 * `undefined` si le fetch a échoué (aucune écriture dans ce cas — le
 * prochain run réessaiera). `now` et `filePath` sont injectables pour les
 * tests ; en production, les valeurs par défaut (Date.now(), le vrai
 * fichier) s'appliquent.
 */
async function runOnce(apiKey, fetchImpl, now = Date.now(), filePath = TRACKING_FILE) {
  const data = loadTracking(filePath);
  if (!data.trial_start) {
    data.trial_start = new Date(now).toISOString();
  }
  const trialElapsed = (now - new Date(data.trial_start).getTime()) >= TRIAL_DURATION_MS;

  let candles;
  try {
    candles = await fetchGoldCandles(apiKey, fetchImpl);
  } catch (e) {
    console.error(`Run ignore (fetch echoue) : ${e.message}`);
    return undefined;
  }

  const openPosition = data.positions.find(p => p.status === 'open');

  if (openPosition) {
    const entryTime = new Date(openPosition.entry_time).getTime();
    const candlesSinceEntry = candles.filter(c => new Date(c.time.replace(' ', 'T') + 'Z').getTime() > entryTime);
    const outcome = decidePositionOutcome(openPosition, candlesSinceEntry, now, trialElapsed);
    if (outcome.closed) {
      openPosition.status = 'closed';
      openPosition.close_time = new Date(now).toISOString();
      openPosition.close_price = outcome.price;
      openPosition.close_reason = outcome.reason;
      const { return_usd, return_pct } = computeReturn(openPosition.direction, openPosition.entry_price, outcome.price);
      openPosition.return_usd = return_usd;
      openPosition.return_pct = return_pct;
    }
  } else if (!trialElapsed) {
    const signal = computeSignal(candles);
    if (signal.status === 'achat' || signal.status === 'vente') {
      data.positions.push(buildPositionFromSignal(signal, now));
    }
  }

  const stillOpen = data.positions.some(p => p.status === 'open');
  if (trialElapsed && !stillOpen) {
    data.trial_ended = true;
  }

  saveTracking(data, filePath);
  return data;
}

/**
 * Point d'entree CLI (appele par le workflow). Non couvert par des tests
 * unitaires (effet de bord process.env/execSync) - la logique testable
 * est entierement dans runOnce/decidePositionOutcome/etc. ci-dessus.
 */
async function main() {
  const apiKey = process.env.TWELVE_DATA_API_KEY;
  if (!apiKey) {
    console.error('TWELVE_DATA_API_KEY manquant dans l\'environnement.');
    process.exit(1);
  }
  const data = await runOnce(apiKey, fetch);
  if (data && data.trial_ended) {
    console.log('Essai termine (7 jours ecoules, plus de position ouverte).');
    try {
      execSync('gh workflow disable scalping_tracker.yml', { stdio: 'inherit' });
      console.log('Workflow desactive automatiquement.');
    } catch (e) {
      console.error(`Desactivation automatique du workflow impossible (non bloquant) : ${e.message}`);
    }
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  loadTracking,
  saveTracking,
  computeReturn,
  decidePositionOutcome,
  buildPositionFromSignal,
  runOnce,
  TRIAL_DURATION_MS,
  MAX_POSITION_DURATION_MS,
};
```

- [ ] **Step 4: Vérification manuelle (Node indisponible localement, best-effort)**

Retrace chaque test à la main contre le code ci-dessus :
- `computeReturn('achat', 100, 105)` : `return_usd = 105-100 = 5`,
  `return_pct = 5/100*100 = 5`. ✓ `computeReturn('vente', 100, 95)` :
  `return_usd = 100-95 = 5`, `return_pct = 5`. ✓
- `decidePositionOutcome` TP : bougie 1 (`high=108<110`, `low=99>95`) ne
  déclenche rien ; bougie 2 (`high=112>=110`) → `tp_hit`, `price=110` (le
  niveau, pas le high réel de la bougie — c'est voulu, cf. commentaire).
  ✓ SL : `low=93<=95` → `sl_hit`, `price=95`. ✓ Ambiguïté même bougie :
  le code vérifie `slTouched` AVANT `tpTouched` dans la même itération et
  retourne immédiatement si vrai — donc `low=90<=95` (vrai) fait sortir la
  fonction avec `sl_hit` avant même d'évaluer `tpTouched`. ✓ Vente SL :
  `high=112>=110` (le SL vente est au-dessus du prix) → `sl_hit`,
  `price=110`. ✓
- `max_duration` : `now - entryTime = 2*60*60*1000` exactement =
  `MAX_POSITION_DURATION_MS` → `now - entryTime >= MAX_POSITION_DURATION_MS`
  est vrai (`>=`, pas `>`) → `max_duration`, `price=99.5` (le close de la
  seule bougie fournie). ✓
- `trial_end` : `now-entryTime` = 10 min (`< 2h`, donc pas
  `max_duration`), mais `trialElapsed=true` passé en argument → tombe
  dans la branche suivante → `trial_end`, `price=99.5`. ✓
- `stays_open` : ni l'un ni l'autre → `{closed:false}`. ✓
- `buildPositionFromSignal` : `iso = '2026-09-09T14:35:00.000Z'`
  (`new Date(...).toISOString()` sur un timestamp construit depuis
  cette même chaîne redonne exactement cette chaîne, millisecondes
  incluses) → `id = 'scalp-2026-09-09T14:35:00.000Z'` par simple
  concaténation. Tous les autres champs sont une copie directe des champs
  du signal, `pattern_at_entry` vaut `'Marteau'` (accès à `.name`) dans le
  premier test et `null` (court-circuit du `? :`) dans le second. ✓
- `runOnce_opens_position_on_new_signal` : ce test ne verifie PAS quel
  signal exact sort de `computeSignal` (dépend de toute la logique de
  confluence, hors scope de ce test) — seulement que `trial_start` est
  posé sur `new Date(now).toISOString()` (le fichier n'existait pas, donc
  `data.trial_start` était `null` avant l'appel) et que `positions` reste
  un tableau valide dans tous les cas. ✓
- `runOnce_does_not_write_on_fetch_failure` : `fetchGoldCandles` propage
  l'erreur du `fetchImpl` fourni (déjà vérifié par les tests existants de
  `docs/scalping.test.js`), le `catch` de `runOnce` renvoie `undefined`
  **avant** tout appel à `saveTracking` → le fichier temporaire, jamais
  créé au départ, reste absent. ✓

- [ ] **Step 5: Commit**

```bash
git add scalping_tracker.js scalping_tracker.test.js
git commit -m "feat(scalping-tracker): moteur de paper-trading (cycle de vie position)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Workflow GitHub Actions — `scalping_tracker.yml`

**Files:**
- Create: `.github/workflows/scalping_tracker.yml`

**Interfaces:**
- Consomme : `scalping_tracker.js` (Task 1) via `node scalping_tracker.js`,
  variable d'environnement `TWELVE_DATA_API_KEY` (secret GitHub, configuré
  en Task 4), `GH_TOKEN` (secret `GITHUB_TOKEN` automatique, pour la
  désactivation best-effort du workflow via `gh` en fin d'essai).
- Produit : `docs/scalping_tracking.json`, committé et poussé sur `main` à
  chaque run qui modifie l'état.

- [ ] **Step 1: Créer le workflow**

```yaml
name: Suivi paper-trading Scalping Or

on:
  schedule:
    - cron: "*/5 * * * *"   # toutes les 5 minutes, plancher technique GitHub Actions
  workflow_dispatch: {}

concurrency:
  group: scalping-tracker
  cancel-in-progress: false

permissions:
  contents: write
  actions: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - name: Récupérer le dépôt
        uses: actions/checkout@v4

      - name: Installer Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Exécuter le suivi de paper-trading
        run: node scalping_tracker.js
        env:
          TWELVE_DATA_API_KEY: ${{ secrets.TWELVE_DATA_API_KEY }}
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Publier le suivi mis à jour
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/scalping_tracking.json
          git diff --staged --quiet || git commit -m "Mise a jour du suivi Scalping"
          git pull --rebase --autostash
          git push
```

(Le `GH_TOKEN` est nécessaire pour que la commande `gh workflow disable`
dans `scalping_tracker.js`'s `main()` s'authentifie — c'est le token
automatique du workflow, pas un nouveau secret à créer. La permission
`actions: write` est nécessaire pour que ce token puisse effectivement
désactiver un workflow.)

- [ ] **Step 2: Vérification manuelle (pas de runner GitHub Actions
  disponible dans ce sandbox)**

- Vérifie que `docs/scalping_tracking.json` correspond bien au chemin
  utilisé par défaut dans `scalping_tracker.js` (`TRACKING_FILE`) —
  `path.join(__dirname, 'docs', 'scalping_tracking.json')` avec
  `__dirname` = racine du repo (où vit `scalping_tracker.js`) donne bien
  `<repo>/docs/scalping_tracking.json`. ✓
- Vérifie que `node scalping_tracker.js` fonctionne bien exécuté depuis la
  racine du repo (le `working-directory` par défaut d'un `run:` GitHub
  Actions est la racine du checkout) — cohérent avec le `require('./docs/scalping.js')`
  du script (chemin relatif au fichier, pas au cwd, donc robuste de toute
  façon).
- Ce step sera revérifié pour de vrai après le merge (Task 4, Step 4) via
  un premier run réel.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/scalping_tracker.yml
git commit -m "feat(scalping-tracker): workflow GitHub Actions (cron 5min)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Section "Suivi" dans l'écran Or

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consomme : `docs/scalping_tracking.json` (Task 1/2, format défini dans
  la spec) via `fetch` côté client ; `formatPriceUsd`/`formatPct` déjà
  présents dans ce fichier (ne pas redéfinir).

- [ ] **Step 1: Ajouter le CSS**

Dans le `<style>`, juste après le bloc `.scalp-signal-status.neutre`
existant (cherche `.scalp-signal-status.neutre { background: var(--panel);`
— juste après sa fermeture), ajoute :

```css
  .tracking-summary { display: flex; gap: 16px; margin: 0 0 16px; flex-wrap: wrap; }
  .tracking-stat { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; flex: 1; min-width: 120px; }
  .tracking-stat-value { font-family: 'Fraunces', serif; font-size: 22px; color: var(--gold); }
  .tracking-stat-label { color: var(--muted); font-size: 11px; margin-top: 2px; }
  .tracking-row { display: flex; justify-content: space-between; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .tracking-row.open { color: var(--gold); }
  .tracking-banner { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 12px; color: var(--muted); }
```

- [ ] **Step 2: Ajouter le 3e bouton d'onglet et la vue "Suivi"**

Remplace (cherche `<div class="or-tabbar" role="tablist">` jusqu'à la
fermeture du `</div>` de `.or-tabbar`) :

```html
  <div class="or-tabbar" role="tablist">
    <button class="or-tab-btn" id="orTabLongTerme" role="tab" aria-selected="true" type="button">Long terme</button>
    <button class="or-tab-btn" id="orTabScalping" role="tab" aria-selected="false" type="button">Scalping</button>
  </div>
```

par :

```html
  <div class="or-tabbar" role="tablist">
    <button class="or-tab-btn" id="orTabLongTerme" role="tab" aria-selected="true" type="button">Long terme</button>
    <button class="or-tab-btn" id="orTabScalping" role="tab" aria-selected="false" type="button">Scalping</button>
    <button class="or-tab-btn" id="orTabSuivi" role="tab" aria-selected="false" type="button">Suivi</button>
  </div>
```

Puis, juste après la fermeture du `<div id="orScalpingView" hidden>...</div>`
existant (avant `<main id="content">`), ajoute :

```html
  <div id="orSuiviView" hidden>
    <div class="scalp-signal-panel" id="scalpTrackingPanel">
      <div class="empty">Chargement…</div>
    </div>
  </div>
```

- [ ] **Step 3: Ajouter la logique JS de chargement et de rendu**

Juste après la fonction `refreshScalpSignal` existante (cherche la
fermeture de cette fonction, juste avant `function startScalpingPolling`),
ajoute :

```javascript
let scalpTrackingLoaded = false;

async function loadScalpTracking() {
  const panel = document.getElementById('scalpTrackingPanel');
  try {
    const res = await fetch(`scalping_tracking.json?t=${Date.now()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    scalpTrackingLoaded = true;
    renderScalpTracking(data);
  } catch (e) {
    panel.innerHTML = `<div class="empty">Suivi indisponible pour l'instant.<br>${e.message}</div>`;
  }
}

const SCALP_CLOSE_REASON_LABELS = {
  tp_hit: 'TP touché',
  sl_hit: 'SL touché',
  max_duration: 'Durée max (2h)',
  trial_end: "Fin d'essai",
};

function renderScalpTracking(data) {
  const panel = document.getElementById('scalpTrackingPanel');
  const closed = data.positions.filter(p => p.status === 'closed');
  const wins = closed.filter(p => p.return_usd > 0).length;
  const winRate = closed.length ? (wins / closed.length * 100) : null;
  const avgPct = closed.length ? (closed.reduce((s, p) => s + p.return_pct, 0) / closed.length) : null;
  const totalUsd = closed.reduce((s, p) => s + p.return_usd, 0);

  const banner = data.trial_ended
    ? `<div class="tracking-banner">Essai terminé.</div>`
    : (data.trial_start
        ? `<div class="tracking-banner">Essai en cours depuis le ${new Date(data.trial_start).toLocaleString('fr-FR')}.</div>`
        : `<div class="tracking-banner">Essai pas encore démarré.</div>`);

  const summary = `
    <div class="tracking-summary">
      <div class="tracking-stat"><div class="tracking-stat-value">${closed.length}</div><div class="tracking-stat-label">Positions clôturées</div></div>
      <div class="tracking-stat"><div class="tracking-stat-value">${winRate != null ? winRate.toFixed(0) + '%' : '—'}</div><div class="tracking-stat-label">Taux de réussite</div></div>
      <div class="tracking-stat"><div class="tracking-stat-value">${avgPct != null ? formatPct(avgPct) : '—'}</div><div class="tracking-stat-label">Rendement moyen</div></div>
      <div class="tracking-stat"><div class="tracking-stat-value">${formatPriceUsd(totalUsd)}</div><div class="tracking-stat-label">Cumulé</div></div>
    </div>
  `;

  const rows = data.positions.slice().reverse().map(p => {
    const label = p.status === 'open' ? 'En cours' : (SCALP_CLOSE_REASON_LABELS[p.close_reason] || p.close_reason);
    const resultText = p.status === 'open' ? '—' : `${formatPriceUsd(p.return_usd)} (${formatPct(p.return_pct)})`;
    return `<div class="tracking-row ${p.status === 'open' ? 'open' : ''}">
      <span>${p.direction === 'achat' ? 'Achat' : 'Vente'} — ${new Date(p.entry_time).toLocaleString('fr-FR')}</span>
      <span>${label}</span>
      <span>${resultText}</span>
    </div>`;
  }).join('');

  panel.innerHTML = banner + summary + (rows || '<div class="empty">Aucune position pour l\'instant.</div>');
}
```

- [ ] **Step 4: Câbler le bouton d'onglet et mettre à jour les 2 autres**

Remplace le bloc des deux handlers existants (cherche
`document.getElementById('orTabLongTerme').addEventListener('click', () => {`
jusqu'à la fermeture du handler `orTabScalping`) par :

```javascript
document.getElementById('orTabLongTerme').addEventListener('click', () => {
  document.getElementById('orTabLongTerme').setAttribute('aria-selected', 'true');
  document.getElementById('orTabScalping').setAttribute('aria-selected', 'false');
  document.getElementById('orTabSuivi').setAttribute('aria-selected', 'false');
  document.getElementById('orScalpingView').hidden = true;
  document.getElementById('orSuiviView').hidden = true;
  document.getElementById('content').hidden = false;
  stopScalpingPolling();
});
document.getElementById('orTabScalping').addEventListener('click', () => {
  document.getElementById('orTabScalping').setAttribute('aria-selected', 'true');
  document.getElementById('orTabLongTerme').setAttribute('aria-selected', 'false');
  document.getElementById('orTabSuivi').setAttribute('aria-selected', 'false');
  document.getElementById('content').hidden = true;
  document.getElementById('orSuiviView').hidden = true;
  document.getElementById('orScalpingView').hidden = false;
  loadScalpChart();
  loadScalpingModule().then(startScalpingPolling).catch(e => {
    document.getElementById('scalpSignalPanel').innerHTML = `<div class="empty">${e.message}</div>`;
  });
});
document.getElementById('orTabSuivi').addEventListener('click', () => {
  document.getElementById('orTabSuivi').setAttribute('aria-selected', 'true');
  document.getElementById('orTabLongTerme').setAttribute('aria-selected', 'false');
  document.getElementById('orTabScalping').setAttribute('aria-selected', 'false');
  document.getElementById('content').hidden = true;
  document.getElementById('orScalpingView').hidden = true;
  document.getElementById('orSuiviView').hidden = false;
  stopScalpingPolling();
  loadScalpTracking();
});
```

- [ ] **Step 5: Vérification manuelle du diff (pas d'outillage de
  navigateur dans ce sandbox)**

1. Chaque `id` référencé (`orTabSuivi`, `orSuiviView`, `scalpTrackingPanel`)
   existe bien dans le HTML modifié à l'étape 2.
2. `formatPriceUsd`/`formatPct` sont bien réutilisées, pas redéfinies
   (grep `function formatPriceUsd` / `function formatPct` — une seule
   définition de chacune, pré-existante).
3. Les 3 boutons d'onglet sont bien mutuellement exclusifs : chaque
   handler met `aria-selected` à `false` sur les 2 autres boutons et
   cache les 2 autres vues.
4. `renderRoute()`'s bloc de reprise du polling (`!document.getElementById('orScalpingView').hidden`)
   n'est pas affecté par cet ajout — `orSuiviView` est un `<div>` séparé,
   `orScalpingView.hidden` garde exactement la même sémantique
   qu'avant.
5. Pas d'accolades de template literal non appariées dans
   `renderScalpTracking` (compte les backticks et `${...}`).
6. `python -m pytest tests/test_indices_score.py -q` passe toujours
   (changement HTML/JS pur, aucun impact Python attendu).

- [ ] **Step 6: Commit**

```bash
git add docs/index.html
git commit -m "feat(scalping-tracker): section Suivi dans l'ecran Or

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Secret GitHub, merge, premier run réel

**Files:** `.github` (secret, pas un fichier versionné), git/gh
uniquement.

- [ ] **Step 1: Configurer le secret `TWELVE_DATA_API_KEY`**

Ce workflow tourne côté serveur GitHub — il a besoin de sa propre copie du
secret (distincte de la clé déjà en dur dans `docs/index.html` côté
navigateur, qui reste inchangée). Avant tout merge :

1. Demander à l'utilisateur confirmation d'utiliser la même clé Twelve
   Data déjà fournie plus tôt dans la session, ou une clé différente s'il
   préfère en isoler l'usage.
2. Configurer le secret :
```bash
gh secret set TWELVE_DATA_API_KEY --repo Alexandreauq/analyse-or
```
(la commande demande la valeur sur stdin ou via `--body` — ne jamais
faire apparaître la clé en clair dans un message de commit ou un log)

Si l'agent qui exécute ce plan n'a pas accès à `gh` avec les droits
nécessaires sur ce repo, **s'arrêter ici et demander à l'utilisateur de
configurer le secret lui-même** plutôt que de merger sans lui — le
workflow échouerait silencieusement à chaque run (erreur 401 Twelve Data,
aucune position jamais ouverte) sans que personne ne s'en aperçoive avant
la fin des 7 jours annoncés.

- [ ] **Step 2: Créer la branche et pousser**

```bash
git checkout -b scalping-paper-trading
git push -u origin scalping-paper-trading
```

(Si les commits des tasks précédentes ont déjà été faits sur cette
branche, ignorer ce step.)

- [ ] **Step 3: Tests finaux avant merge**

```bash
python -m pytest tests/test_indices_score.py -q
```

(Aucun test Python ne doit être affecté par ce chantier — confirmer que
la suite reste verte avant de merger.)

- [ ] **Step 4: Synchroniser et merger**

```bash
git checkout scalping-paper-trading
git pull --ff-only origin scalping-paper-trading
git checkout main
git pull --ff-only origin main
git merge scalping-paper-trading --no-edit
```

En cas de conflit sur `docs/indices.json`/`indices_history.json`/`docs/score.json`/`score.json`
(fichiers auto-générés par les crons existants, peuvent avoir avancé
entre-temps) :
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
git branch -d scalping-paper-trading
git push origin --delete scalping-paper-trading
```

- [ ] **Step 7: Vérifier le premier run réel du workflow**

L'essai ne compte officiellement qu'à partir du premier run réussi
(c'est ce run qui fixe `trial_start`). Un workflow cassé découvert
seulement après "7 jours" annoncés à l'utilisateur gâcherait toute la
fenêtre d'essai — donc ne pas déclarer ce chantier terminé sans
vérifier ce premier run.

1. Déclencher un run manuel immédiatement plutôt que d'attendre jusqu'à
   5 minutes le prochain cron :
```bash
gh workflow run scalping_tracker.yml --repo Alexandreauq/analyse-or
```
2. Attendre ~30-60 secondes, puis vérifier son statut :
```bash
gh run list --workflow=scalping_tracker.yml --repo Alexandreauq/analyse-or --limit=1
```
3. Si le run a échoué (`failure`), consulter ses logs
   (`gh run view --repo Alexandreauq/analyse-or --log`) et diagnostiquer
   avant de considérer ce chantier terminé — les causes probables sont :
   secret mal configuré (Step 1), permission `actions: write` insuffisante
   pour la désactivation automatique (non bloquant en soi, mais à noter),
   ou une erreur dans `scalping_tracker.js` que ce plan n'aurait pas
   couverte.
4. Si le run a réussi, vérifier que `docs/scalping_tracking.json` a bien
   été créé/commité avec un `trial_start` non nul (`git log --oneline -3`
   doit montrer un commit "Mise a jour du suivi Scalping" juste après le
   run, sauf si aucune position n'a été ouverte ET que le fichier
   n'existait pas encore — dans ce cas `git diff --staged --quiet` du
   workflow aurait quand même détecté une différence car `trial_start`
   passe de rien à une valeur ; si vraiment aucun commit n'apparaît,
   c'est un signal à investiguer, pas à ignorer).
5. Rapporter clairement à l'utilisateur : le run a réussi, l'essai a
   officiellement démarré à `<trial_start>`, se termine dans 7 jours à
   `<trial_start + 7j>`, consultable dès maintenant dans Or → Suivi.
