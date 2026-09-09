# Figures chartistes (Double Top/Bottom, Tête-Épaule, Triangles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter 7 figures chartistes (Double Top/Bottom, Tête-Épaule +
inversée, Triangle ascendant/descendant/symétrique) au moteur de
confluence Scalping Or.

**Architecture:** Un nouveau module autonome (`docs/chart_patterns.js`)
détecte les figures à partir de pivots pré-calculés (aucune dépendance
d'exécution vers `docs/scalping.js`) ; `computeSignal` (dans
`docs/scalping.js`, modifié) l'appelle et combine son résultat avec la
logique existante via deux déclencheurs structurels distincts
(retournement / continuation).

**Tech Stack:** JS navigateur sans framework, même patron dual-environnement
(`<script>` + `module.exports`) que le reste du module Scalping.

**Spec:** `docs/superpowers/specs/2026-09-09-figures-chartistes-design.md`

## Global Constraints

- **Ce chantier ne merge PAS sur `main`** tant que l'essai de
  paper-trading en cours n'est pas terminé (`trial_start` +
  7 jours — voir `docs/scalping_tracking.json`, fin prévue le
  2026-09-16T17:18Z ou plus tard si l'essai a été prolongé). La dernière
  tâche de ce plan pousse la branche et s'arrête là — aucune tâche de ce
  plan ne merge, ne touche aux secrets GitHub, ni ne déclenche de workflow.
- Node.js est indisponible dans le sandbox local d'implémentation — toutes
  les étapes "lancer les tests" sont une trace manuelle rigoureuse, pas une
  exécution réelle. Contrairement au chantier de suivi paper-trading,
  **aucun workflow CI n'exécutera ce fichier de tests dans un futur
  proche** (la branche ne merge pas) — ne pas promettre une vérification
  réelle à venir, le dire clairement dans chaque rapport de tâche.
- Aucune donnée de volume (déjà exclue de tout le module Or — Twelve Data
  n'en fournit pas pour XAU/USD).
- Le moteur de chandeliers existant (`matchCandlestickPattern`, les 8
  figures 1-3 bougies) n'est pas modifié.

---

## Task 1 : Détection des figures chartistes — `docs/chart_patterns.js`

**Files:**
- Create: `docs/chart_patterns.js`

**Interfaces:**
- Consomme : rien à l'exécution (module pur — pas de `require`, pas de
  dépendance vers `docs/scalping.js`). Attend en entrée des `pivots` déjà
  calculés par l'appelant via `detectPivots(candles, CHARTPATTERN_PIVOT_K)`
  (fonction définie dans `docs/scalping.js`, Task 3 s'occupera de
  l'appeler correctement).
- Produit : `detectChartPatterns(pivots, trend, price) -> {name, direction: 'haussier'|'baissier', kind: 'retournement'|'continuation', breakoutPrice, extremityPrice, patternHeight} | null`,
  et la constante `CHARTPATTERN_PIVOT_K` (consommées par Task 3).

Pas d'étapes TDD classiques (7 figures géométriques, plus simple de
tout écrire d'un bloc puis de vérifier soigneusement — même décision
que pour `matchCandlestickPattern` dans le chantier précédent).

- [ ] **Step 1 : Implémenter `docs/chart_patterns.js`**

```javascript
// docs/chart_patterns.js
// Détection de figures chartistes (Double Top/Bottom, Tête-Épaule,
// Triangles) — module autonome, sans dépendance d'exécution vers
// docs/scalping.js. Chargé en <script> classique navigateur (comme
// scalping.js), avec module.exports pour Node (docs/chart_patterns.test.js).
// Voir docs/superpowers/specs/2026-09-09-figures-chartistes-design.md.

const CHARTPATTERN_PIVOT_K = 5; // plus grand que SCALP_PIVOT_K (3, tendance/S-R) : une figure chartiste s'étale sur bien plus de bougies qu'un niveau ponctuel
const CHARTPATTERN_HEIGHT_TOLERANCE = 1.0; // $ de tolérance pour juger deux sommets/creux "de hauteur comparable" (même esprit que SCALP_LEVEL_PROXIMITY)

function _heightClose(a, b) {
  return Math.abs(a - b) <= CHARTPATTERN_HEIGHT_TOLERANCE;
}

/**
 * Détecte une des 7 figures chartistes reconnues à partir de `pivots`
 * (déjà calculés par l'appelant avec K=CHARTPATTERN_PIVOT_K), `trend`
 * (classifyTrend, docs/scalping.js) et `price` (dernière clôture, pour
 * juger si une cassure est confirmée). Renvoie la première figure
 * trouvée (ordre : retournement d'abord, puis continuation) ou null.
 * Ne lève jamais d'exception — pivots insuffisants renvoie simplement null.
 */
function detectChartPatterns(pivots, trend, price) {
  const highs = pivots.filter(p => p.type === 'high');
  const lows = pivots.filter(p => p.type === 'low');

  // --- Double Top (retournement baissier) ---
  if (trend === 'haussier' && highs.length >= 2) {
    const [h1, h2] = highs.slice(-2);
    const between = lows.filter(l => l.index > h1.index && l.index < h2.index);
    if (between.length && _heightClose(h1.price, h2.price)) {
      const neckline = between[between.length - 1].price;
      if (price < neckline) {
        return {
          name: 'Double Top', direction: 'baissier', kind: 'retournement',
          breakoutPrice: neckline, extremityPrice: Math.max(h1.price, h2.price),
          patternHeight: Math.max(h1.price, h2.price) - neckline,
        };
      }
    }
  }

  // --- Double Bottom (retournement haussier) ---
  if (trend === 'baissier' && lows.length >= 2) {
    const [l1, l2] = lows.slice(-2);
    const between = highs.filter(h => h.index > l1.index && h.index < l2.index);
    if (between.length && _heightClose(l1.price, l2.price)) {
      const neckline = between[between.length - 1].price;
      if (price > neckline) {
        return {
          name: 'Double Bottom', direction: 'haussier', kind: 'retournement',
          breakoutPrice: neckline, extremityPrice: Math.min(l1.price, l2.price),
          patternHeight: neckline - Math.min(l1.price, l2.price),
        };
      }
    }
  }

  // --- Tête-Épaule (retournement baissier) ---
  if (trend === 'haussier' && highs.length >= 3) {
    const [s1, head, s2] = highs.slice(-3);
    const troughs = lows.filter(l => l.index > s1.index && l.index < s2.index);
    if (troughs.length >= 2 && _heightClose(s1.price, s2.price) && head.price > s1.price && head.price > s2.price) {
      const neckline = troughs[troughs.length - 1].price; // le plus récent des deux creux — approximation de la ligne de cou (voir spec)
      if (price < neckline) {
        return {
          name: 'Tête-Épaule', direction: 'baissier', kind: 'retournement',
          breakoutPrice: neckline, extremityPrice: head.price,
          patternHeight: head.price - neckline,
        };
      }
    }
  }

  // --- Tête-Épaule inversée (retournement haussier) ---
  if (trend === 'baissier' && lows.length >= 3) {
    const [s1, head, s2] = lows.slice(-3);
    const peaks = highs.filter(h => h.index > s1.index && h.index < s2.index);
    if (peaks.length >= 2 && _heightClose(s1.price, s2.price) && head.price < s1.price && head.price < s2.price) {
      const neckline = peaks[peaks.length - 1].price;
      if (price > neckline) {
        return {
          name: 'Tête-Épaule inversée', direction: 'haussier', kind: 'retournement',
          breakoutPrice: neckline, extremityPrice: head.price,
          patternHeight: neckline - head.price,
        };
      }
    }
  }

  // --- Triangle ascendant (continuation haussière) ---
  if (trend === 'haussier' && highs.length >= 2 && lows.length >= 2) {
    const [h1, h2] = highs.slice(-2);
    const [l1, l2] = lows.slice(-2);
    if (_heightClose(h1.price, h2.price) && l2.price > l1.price) {
      const resistance = h2.price;
      if (price > resistance) {
        return {
          name: 'Triangle ascendant', direction: 'haussier', kind: 'continuation',
          breakoutPrice: resistance, extremityPrice: l1.price,
          patternHeight: resistance - l1.price,
        };
      }
    }
  }

  // --- Triangle descendant (continuation baissière) ---
  if (trend === 'baissier' && highs.length >= 2 && lows.length >= 2) {
    const [h1, h2] = highs.slice(-2);
    const [l1, l2] = lows.slice(-2);
    if (_heightClose(l1.price, l2.price) && h2.price < h1.price) {
      const support = l2.price;
      if (price < support) {
        return {
          name: 'Triangle descendant', direction: 'baissier', kind: 'continuation',
          breakoutPrice: support, extremityPrice: h1.price,
          patternHeight: h1.price - support,
        };
      }
    }
  }

  // --- Triangle symétrique (continuation, sens déterminé par la cassure) ---
  if (highs.length >= 2 && lows.length >= 2) {
    const [h1, h2] = highs.slice(-2);
    const [l1, l2] = lows.slice(-2);
    const converging = h2.price < h1.price && l2.price > l1.price;
    if (converging) {
      if (trend === 'haussier' && price > h2.price) {
        return {
          name: 'Triangle symétrique', direction: 'haussier', kind: 'continuation',
          breakoutPrice: h2.price, extremityPrice: l1.price,
          patternHeight: h1.price - l1.price,
        };
      }
      if (trend === 'baissier' && price < l2.price) {
        return {
          name: 'Triangle symétrique', direction: 'baissier', kind: 'continuation',
          breakoutPrice: l2.price, extremityPrice: h1.price,
          patternHeight: h1.price - l1.price,
        };
      }
    }
  }

  return null;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CHARTPATTERN_PIVOT_K, CHARTPATTERN_HEIGHT_TOLERANCE, detectChartPatterns };
}
```

- [ ] **Step 2 : Relecture manuelle**

Vérifie en particulier :
- Chaque figure exige bien le `trend` requis par la spec (Double Top/Tête-Épaule
  → `haussier`, Double Bottom/Tête-Épaule inversée → `baissier`, Triangle
  ascendant → `haussier`, Triangle descendant → `baissier`, Triangle
  symétrique → sens déterminé par la cassure effective).
- L'ordre de test (retournement d'abord, continuation ensuite, chaque
  figure retournant immédiatement dès qu'elle matche) signifie qu'une
  seule figure est jamais renvoyée par appel — documente ce choix dans
  ton rapport, ce n'est pas un oubli.
- `_heightClose` utilise une tolérance absolue en dollars
  (`CHARTPATTERN_HEIGHT_TOLERANCE = 1.0`), pas un pourcentage — cohérent
  avec `SCALP_LEVEL_PROXIMITY`/`SCALP_STOP_BUFFER` déjà dans
  `docs/scalping.js`.

- [ ] **Step 3 : Commit**

```bash
git add docs/chart_patterns.js
git commit -m "feat(chart-patterns): detection de 7 figures chartistes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2 : Tests — `docs/chart_patterns.test.js`

**Files:**
- Create: `docs/chart_patterns.test.js`

**Interfaces:**
- Consomme : `detectChartPatterns(pivots, trend, price)`,
  `CHARTPATTERN_HEIGHT_TOLERANCE` (Task 1).

- [ ] **Step 1 : Écrire les tests**

```javascript
// docs/chart_patterns.test.js
// Tests JS sans framework, exécutables via `node docs/chart_patterns.test.js`
// (comme docs/scalping.test.js). Chaque test compare un résultat calculé
// à une valeur attendue calculée à la main.
const assert = require('assert');
const { detectChartPatterns, CHARTPATTERN_HEIGHT_TOLERANCE } = require('./chart_patterns.js');

function test_doubleTop_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 100 },
    { index: 10, type: 'high', price: 110.5 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 99);
  assert.deepStrictEqual(result, {
    name: 'Double Top', direction: 'baissier', kind: 'retournement',
    breakoutPrice: 100, extremityPrice: 110.5, patternHeight: 10.5,
  });
  console.log('OK: test_doubleTop_detected');
}

function test_doubleTop_rejected_when_heights_too_far_apart() {
  // Identique au test précédent mais h2=112 au lieu de 110.5 : écart de
  // 2$ > CHARTPATTERN_HEIGHT_TOLERANCE (1$) -> aucune figure détectée.
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 100 },
    { index: 10, type: 'high', price: 112 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 99);
  assert.strictEqual(result, null);
  console.log('OK: test_doubleTop_rejected_when_heights_too_far_apart');
}

function test_doubleBottom_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 90 },
    { index: 5, type: 'high', price: 100 },
    { index: 10, type: 'low', price: 90.5 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 101);
  assert.deepStrictEqual(result, {
    name: 'Double Bottom', direction: 'haussier', kind: 'retournement',
    breakoutPrice: 100, extremityPrice: 90, patternHeight: 10,
  });
  console.log('OK: test_doubleBottom_detected');
}

function test_teteEpaule_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 100 },   // épaule 1
    { index: 5, type: 'low', price: 95 },
    { index: 10, type: 'high', price: 110 },  // tête
    { index: 15, type: 'low', price: 96 },
    { index: 20, type: 'high', price: 100.5 }, // épaule 2
  ];
  const result = detectChartPatterns(pivots, 'haussier', 95);
  assert.deepStrictEqual(result, {
    name: 'Tête-Épaule', direction: 'baissier', kind: 'retournement',
    breakoutPrice: 96, extremityPrice: 110, patternHeight: 14,
  });
  console.log('OK: test_teteEpaule_detected');
}

function test_teteEpauleInversee_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 100 },
    { index: 5, type: 'high', price: 105 },
    { index: 10, type: 'low', price: 90 },
    { index: 15, type: 'high', price: 104 },
    { index: 20, type: 'low', price: 100.5 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 105);
  assert.deepStrictEqual(result, {
    name: 'Tête-Épaule inversée', direction: 'haussier', kind: 'retournement',
    breakoutPrice: 104, extremityPrice: 90, patternHeight: 14,
  });
  console.log('OK: test_teteEpauleInversee_detected');
}

function test_triangleAscendant_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 95 },
    { index: 10, type: 'high', price: 110.5 },
    { index: 15, type: 'low', price: 100 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 111);
  assert.deepStrictEqual(result, {
    name: 'Triangle ascendant', direction: 'haussier', kind: 'continuation',
    breakoutPrice: 110.5, extremityPrice: 95, patternHeight: 15.5,
  });
  console.log('OK: test_triangleAscendant_detected');
}

function test_triangleDescendant_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 90 },
    { index: 5, type: 'high', price: 105 },
    { index: 10, type: 'low', price: 90.5 },
    { index: 15, type: 'high', price: 100 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 90);
  assert.deepStrictEqual(result, {
    name: 'Triangle descendant', direction: 'baissier', kind: 'continuation',
    breakoutPrice: 90.5, extremityPrice: 105, patternHeight: 14.5,
  });
  console.log('OK: test_triangleDescendant_detected');
}

function test_triangleSymetrique_breakoutHaussier() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 90 },
    { index: 10, type: 'high', price: 105 },
    { index: 15, type: 'low', price: 95 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 106);
  assert.deepStrictEqual(result, {
    name: 'Triangle symétrique', direction: 'haussier', kind: 'continuation',
    breakoutPrice: 105, extremityPrice: 90, patternHeight: 20,
  });
  console.log('OK: test_triangleSymetrique_breakoutHaussier');
}

function test_triangleSymetrique_breakoutBaissier() {
  // Mêmes pivots que le test précédent, mais tendance et prix inversés
  // -> cassure dans l'autre sens (le triangle symétrique ne présume pas
  // du sens avant la cassure effective, cf. spec).
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 90 },
    { index: 10, type: 'high', price: 105 },
    { index: 15, type: 'low', price: 95 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 89);
  assert.deepStrictEqual(result, {
    name: 'Triangle symétrique', direction: 'baissier', kind: 'continuation',
    breakoutPrice: 95, extremityPrice: 110, patternHeight: 20,
  });
  console.log('OK: test_triangleSymetrique_breakoutBaissier');
}

function main() {
  test_doubleTop_detected();
  test_doubleTop_rejected_when_heights_too_far_apart();
  test_doubleBottom_detected();
  test_teteEpaule_detected();
  test_teteEpauleInversee_detected();
  test_triangleAscendant_detected();
  test_triangleDescendant_detected();
  test_triangleSymetrique_breakoutHaussier();
  test_triangleSymetrique_breakoutBaissier();
  console.log('Tous les tests chart_patterns sont passes.');
}

main();
```

- [ ] **Step 2 : Vérification manuelle (Node indisponible — pas de run CI à venir pour ce fichier, voir Contraintes globales)**

Retrace chaque test à la main contre le code de Task 1 :
- Double Top : `h1=110,h2=110.5` → écart 0,5$ ≤ 1$ → figure valide.
  `between` = le pivot bas d'index 5 (entre index 0 et 10). `neckline=100`.
  `price=99 < 100` → cassure confirmée. `extremityPrice=max(110,110.5)=110.5`,
  `patternHeight=110.5-100=10.5`. ✓
- Rejet tolérance : `h1=110,h2=112` → écart 2$ > 1$ → `_heightClose` faux
  → aucune branche ne matche (les autres figures exigent soit ≥3 pivots
  hauts, soit des pivots bas suffisants, absents ici) → `null`. ✓
- Double Bottom : symétrique, `neckline=100`, `price=101>100` → cassure. ✓
- Tête-Épaule : `s1=100,head=110,s2=100.5` → écart épaules 0,5$≤1$,
  `head>s1` et `head>s2`. `troughs`=[95@idx5,96@idx15] (les deux entre
  idx0 et idx20). `neckline=troughs[last]=96`. `price=95<96` → cassure.
  `patternHeight=110-96=14`. ✓
- Symétrique triangle (les deux sens) : mêmes pivots convergents
  (`h2<h1` et `l2>l1`), seul `trend`+`price` changent — confirme que le
  sens de cassure n'est pas pré-déterminé par la forme mais par la
  cassure réelle. ✓ (`patternHeight=h1-l1=110-90=20` dans les deux cas,
  seul `breakoutPrice`/`extremityPrice`/`direction` changent de sens.)

- [ ] **Step 3 : Commit**

```bash
git add docs/chart_patterns.test.js
git commit -m "test(chart-patterns): couverture des 7 figures + cas de tolerance

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3 : Intégration dans `computeSignal` — `docs/scalping.js`

**Files:**
- Modify: `docs/scalping.js`

**Interfaces:**
- Consomme : `detectChartPatterns(pivots, trend, price)`,
  `CHARTPATTERN_PIVOT_K` (Task 1, via le résolveur cross-environnement
  décrit ci-dessous).
- Produit : `computeSignal` modifié, même signature/forme de retour
  qu'avant (`{status, price, entry, stopLoss, takeProfit, trend, pattern}`)
  — `pattern` peut maintenant être un chandelier OU une figure chartiste.

Pas de tests automatisés pour cette task (même décision que pour le
moteur de confluence original — logique vérifiée manuellement).

- [ ] **Step 1 : Ajouter le résolveur cross-environnement**

Juste après `const TWELVE_DATA_URL = ...;` en haut de `docs/scalping.js`,
ajoute :

```javascript
// docs/chart_patterns.js expose detectChartPatterns/CHARTPATTERN_PIVOT_K.
// Dans le navigateur, chart_patterns.js est chargé en <script> avant tout
// appel à computeSignal (docs/index.html, loadScalpingModule) — ses
// déclarations globales (function/const) sont donc directement lisibles
// ici par identifiant nu, sans import. Pour Node (tests,
// scalping_tracker.js), chaque fichier require()é est un module isolé
// sans scope global partagé : require() explicite. Attention à ne PAS
// redéclarer `CHARTPATTERN_PIVOT_K` ici (collision de nom avec le const
// global de chart_patterns.js dans le navigateur, SyntaxError) — d'où
// le passage par une fonction wrapper plutôt qu'une constante.
const _chartPatternsModule = (typeof require === 'function') ? require('./chart_patterns.js') : null;
function _detectChartPatterns(pivots, trend, price) {
  return _chartPatternsModule
    ? _chartPatternsModule.detectChartPatterns(pivots, trend, price)
    : detectChartPatterns(pivots, trend, price);
}
function _chartPatternPivotK() {
  return _chartPatternsModule ? _chartPatternsModule.CHARTPATTERN_PIVOT_K : CHARTPATTERN_PIVOT_K;
}
```

- [ ] **Step 2 : Remplacer `computeSignal`**

Remplace la fonction `computeSignal` existante (de `function computeSignal(candles) {`
jusqu'à son `}` fermant, juste avant le bloc `module.exports`) par :

```javascript
function computeSignal(candles) {
  const price = candles.length ? candles[candles.length - 1].close : null;
  if (!price || candles.length < SCALP_MIN_CANDLES) {
    return { status: 'neutre', price, entry: null, stopLoss: null, takeProfit: null, trend: 'neutre', pattern: null };
  }

  const pivots = detectPivots(candles, SCALP_PIVOT_K);
  const trend = classifyTrend(pivots);
  const levels = currentLevels(pivots, price);
  const closes = candles.map(c => c.close);
  const rsi = computeRSI(closes, SCALP_RSI_PERIOD);
  const macd = computeMACD(closes, SCALP_MACD_FAST, SCALP_MACD_SLOW, SCALP_MACD_SIGNAL);
  const candlestickPattern = matchCandlestickPattern(candles, trend);
  const chartPivots = detectPivots(candles, _chartPatternPivotK());
  const chartPattern = _detectChartPatterns(chartPivots, trend, price);

  const nearSupport = levels.support !== null && Math.abs(price - levels.support) <= SCALP_LEVEL_PROXIMITY;
  const nearResistance = levels.resistance !== null && Math.abs(price - levels.resistance) <= SCALP_LEVEL_PROXIMITY;
  const brokeResistance = levels.resistance !== null && price > levels.resistance;
  const brokeSupport = levels.support !== null && price < levels.support;

  const chartRetournementHaussier = chartPattern && chartPattern.kind === 'retournement' && chartPattern.direction === 'haussier';
  const chartRetournementBaissier = chartPattern && chartPattern.kind === 'retournement' && chartPattern.direction === 'baissier';
  const chartContinuationHaussier = chartPattern && chartPattern.kind === 'continuation' && chartPattern.direction === 'haussier';
  const chartContinuationBaissier = chartPattern && chartPattern.kind === 'continuation' && chartPattern.direction === 'baissier';

  // Deux déclencheurs structurels : retournement (logique existante,
  // élargie aux figures chartistes de retournement) et continuation
  // (nouveau, uniquement pour les triangles). Note : structurelAchat et
  // structurelVente ne sont plus strictement mutuellement exclusifs pris
  // isolément (un même `trend` peut désormais satisfaire l'un via le
  // chemin retournement et l'autre via le chemin continuation) — ce n'est
  // pas un bug : confirmationAchat/confirmationVente restent, elles,
  // mutuellement exclusives par construction (`macd.macd` ne peut pas
  // être à la fois > et < `macd.signal`), donc au plus UN des deux blocs
  // `if` plus bas peut jamais renvoyer un signal.
  const structurelAchat = (trend === 'baissier' && (nearSupport || brokeResistance || chartRetournementHaussier))
    || (trend === 'haussier' && chartContinuationHaussier);
  const structurelVente = (trend === 'haussier' && (nearResistance || brokeSupport || chartRetournementBaissier))
    || (trend === 'baissier' && chartContinuationBaissier);

  // Le pattern de confirmation peut venir du chandelier OU de la figure
  // chartiste. Sélection par SENS (jamais l'un ne doit masquer l'autre
  // s'ils vont dans des sens différents), priorité à la figure chartiste
  // quand les deux vont dans le même sens (poids ×2 au barème du cours,
  // contre ×1 pour les chandeliers).
  const patternHaussier = (chartPattern && chartPattern.direction === 'haussier') ? chartPattern
    : (candlestickPattern && candlestickPattern.direction === 'haussier') ? candlestickPattern
    : null;
  const patternBaissier = (chartPattern && chartPattern.direction === 'baissier') ? chartPattern
    : (candlestickPattern && candlestickPattern.direction === 'baissier') ? candlestickPattern
    : null;

  const confirmationAchat = rsi < 70 && macd.macd > macd.signal && patternHaussier !== null;
  const confirmationVente = rsi > 30 && macd.macd < macd.signal && patternBaissier !== null;

  if (structurelAchat && confirmationAchat) {
    const pattern = patternHaussier;
    let stopLoss, takeProfit;
    if (pattern === chartPattern) {
      // Règle du cours spécifique aux figures chartistes : objectif =
      // hauteur de la figure projetée depuis la cassure ; stop juste
      // au-delà du point le plus extrême de la figure.
      stopLoss = chartPattern.extremityPrice - SCALP_STOP_BUFFER;
      takeProfit = chartPattern.breakoutPrice + chartPattern.patternHeight;
    } else {
      stopLoss = levels.support !== null ? levels.support - SCALP_STOP_BUFFER : price - price * 0.001;
      const risk = price - stopLoss;
      takeProfit = levels.resistance !== null && levels.resistance > price
        ? levels.resistance
        : price + risk * SCALP_TAKEPROFIT_RISK_MULTIPLE;
    }
    return { status: 'achat', price, entry: price, stopLoss, takeProfit, trend, pattern };
  }
  if (structurelVente && confirmationVente) {
    const pattern = patternBaissier;
    let stopLoss, takeProfit;
    if (pattern === chartPattern) {
      stopLoss = chartPattern.extremityPrice + SCALP_STOP_BUFFER;
      takeProfit = chartPattern.breakoutPrice - chartPattern.patternHeight;
    } else {
      stopLoss = levels.resistance !== null ? levels.resistance + SCALP_STOP_BUFFER : price + price * 0.001;
      const risk = stopLoss - price;
      takeProfit = levels.support !== null && levels.support < price
        ? levels.support
        : price - risk * SCALP_TAKEPROFIT_RISK_MULTIPLE;
    }
    return { status: 'vente', price, entry: price, stopLoss, takeProfit, trend, pattern };
  }
  return { status: 'neutre', price, entry: null, stopLoss: null, takeProfit: null, trend, pattern: null };
}
```

- [ ] **Step 3 : Vérification manuelle (Node indisponible — pas de run CI à venir, voir Contraintes globales)**

1. Confirme que **tous les tests existants de `docs/scalping.test.js` et
   `docs/scalping_tracker.test.js` restent valables sans modification** —
   ils testent `detectPivots`/`classifyTrend`/`currentLevels`/`computeRSI`/
   `computeMACD`/`computeBollinger`/`matchCandlestickPattern` directement,
   aucune de ces fonctions n'est touchée par cette task ; ils ne testent
   pas `computeSignal` lui-même (déjà le cas avant cette task).
2. Trace un scénario Triangle ascendant de bout en bout : `trend='haussier'`,
   `chartPattern={name:'Triangle ascendant', direction:'haussier', kind:'continuation', breakoutPrice:110.5, extremityPrice:95, patternHeight:15.5}`
   (repris du test Task 2), `rsi=50` (<70 ✓), `macd.macd>macd.signal` (✓).
   `chartContinuationHaussier=true` → `structurelAchat = (trend==='baissier' && ...) || (trend==='haussier' && true) = true`.
   `patternHaussier = chartPattern` (chartPattern.direction='haussier').
   `confirmationAchat = true`. → branche achat, `pattern===chartPattern` vrai
   → `stopLoss = 95 - SCALP_STOP_BUFFER(1.5) = 93.5`,
   `takeProfit = 110.5 + 15.5 = 126`. Vérifie que ces valeurs ne sont
   jamais `NaN` (elles ne dépendent que de `chartPattern`, jamais de
   `levels.support`/`levels.resistance` dans cette branche). ✓
3. Trace un scénario où AUCUNE figure chartiste n'est détectée
   (`chartPattern=null`) mais un chandelier haussier existe
   (`candlestickPattern={direction:'haussier',...}`) : `patternHaussier =
   candlestickPattern` (chartPattern étant null, on retombe sur le
   chandelier) — confirme que le comportement d'avant cette task
   (chandelier seul comme confirmation) est intact quand aucune figure
   chartiste n'est présente. ✓
4. Trace un scénario de "masquage évité", avec des données concrètes :
   `trend='haussier'`. Dans ce contexte, `matchCandlestickPattern` peut
   renvoyer `Étoile filante` (`direction:'baissier'` — un des 4 chandeliers
   qui exigent `trend==='haussier'`) **pendant que**, sur les mêmes
   bougies, `detectChartPatterns` détecte un `Triangle ascendant`
   (`direction:'haussier'`, continuation, exige aussi `trend==='haussier'`)
   — rien n'empêche les deux d'être vrais simultanément, ce sont des
   critères géométriques indépendants. Avec
   `candlestickPattern={direction:'baissier', name:'Étoile filante'}` et
   `chartPattern={direction:'haussier', name:'Triangle ascendant', kind:'continuation', ...}` :
   `patternHaussier = chartPattern` (seul candidat haussier) et
   `patternBaissier = candlestickPattern` (seul candidat baissier) — les
   deux se calculent correctement et indépendamment. Si le code utilisait
   au lieu de ça un simple `pattern = chartPattern || candlestickPattern`,
   `pattern` vaudrait toujours `chartPattern` (haussier) ici, et
   `confirmationVente` — qui a pourtant un vrai candidat valide
   (`Étoile filante`) — échouerait à tort (`pattern.direction !== 'baissier'`).
   C'est exactement le bug qu'évite la conception en deux variables
   séparées.

- [ ] **Step 4 : Commit**

```bash
git add docs/scalping.js
git commit -m "feat(chart-patterns): integration dans le moteur de confluence

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4 : Chargement dans `docs/index.html`

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consomme : rien de nouveau côté interface — charge simplement le
  script `chart_patterns.js` avant `scalping.js`.

- [ ] **Step 1 : Modifier `loadScalpingModule`**

Remplace (cherche `function loadScalpingModule`) :

```javascript
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
```

par :

```javascript
function loadScalpingModule() {
  // Charge docs/chart_patterns.js puis docs/scalping.js à la demande
  // (premier clic sur l'onglet Scalping), même logique paresseuse que
  // loadScalpChart/loadIndices. chart_patterns.js en premier (ordre de
  // dépendance conceptuelle — computeSignal, dans scalping.js, appelle
  // detectChartPatterns) ; l'ordre d'exécution n'a pas d'importance
  // stricte ici (aucune des deux fonctions n'est appelée avant que les
  // deux scripts aient fini de charger), mais garder cet ordre de
  // lecture évite toute confusion.
  return new Promise((resolve, reject) => {
    if (scalpingJsLoaded) { resolve(); return; }
    const chartPatternsScript = document.createElement('script');
    chartPatternsScript.src = 'chart_patterns.js';
    chartPatternsScript.onload = () => {
      const scalpingScript = document.createElement('script');
      scalpingScript.src = 'scalping.js';
      scalpingScript.onload = () => { scalpingJsLoaded = true; resolve(); };
      scalpingScript.onerror = () => reject(new Error('scalping.js n\'a pas pu être chargé'));
      document.body.appendChild(scalpingScript);
    };
    chartPatternsScript.onerror = () => reject(new Error('chart_patterns.js n\'a pas pu être chargé'));
    document.body.appendChild(chartPatternsScript);
  });
}
```

- [ ] **Step 2 : Vérification manuelle**

1. Confirme que `scalpingJsLoaded` garde exactement la même sémantique
   qu'avant (`true` seulement une fois que LES DEUX scripts ont fini de
   charger, pas juste `chart_patterns.js`).
2. Confirme que les deux gestionnaires `onerror` distincts donnent un
   message d'erreur clair selon LEQUEL des deux scripts a échoué à
   charger (pas un message générique ambigu).
3. `python -m pytest tests/test_indices_score.py -q` doit toujours
   passer (changement HTML/JS pur, aucun impact Python).

- [ ] **Step 3 : Commit**

```bash
git add docs/index.html
git commit -m "feat(chart-patterns): chargement de chart_patterns.js avant scalping.js

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5 : Push de la branche — PAS de merge

**Files:** aucun — git uniquement.

- [ ] **Step 1 : Pousser la branche**

```bash
git push -u origin chart-patterns
```

- [ ] **Step 2 : S'arrêter là, explicitement**

**Ne pas merger.** Ne pas créer de Pull Request destinée à un merge
immédiat. Ne toucher à aucun secret GitHub, ne déclencher aucun workflow.

Rapporter clairement à l'utilisateur :
1. La branche `chart-patterns` est poussée et prête, tous les tests
   manuellement vérifiés (voir contrainte globale : aucune exécution
   Node réelle n'a eu lieu dans ce sandbox).
2. Le merge est **volontairement différé** jusqu'à la fin de l'essai de
   paper-trading en cours (`docs/scalping_tracking.json`, `trial_start` +
   7 jours — vérifier la valeur réelle du fichier au moment de ce
   rapport plutôt que de supposer la date annoncée initialement, au cas
   où l'essai aurait été prolongé ou aurait démarré à une heure légèrement
   différente).
3. Une fois cette date passée, revenir sur cette branche pour : relire le
   diff une dernière fois à la lumière des vrais résultats de l'essai
   (surtout si le taux de réussite v1 s'avère faible — ça pourrait
   changer la priorité de ce chantier), lancer la revue finale sur tout
   le diff (comme pour les deux chantiers précédents), puis seulement
   alors merger sur `main`.
