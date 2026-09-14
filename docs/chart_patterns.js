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
