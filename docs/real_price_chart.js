// docs/real_price_chart.js
// Graphique de repli auto-hebergé (SVG, donnees reelles deja accumulees
// par le site dans price_history.json) pour les entreprises sans widget
// TradingView gratuit disponible (FTSE au 2026-10-25 -- voir
// NO_RELIABLE_COMPANY_TV_DATA, docs/company_quote_widget.js). Maquette
// validee par l'utilisateur le 2026-10-25 avant implementation (memes
// fonctions, portees depuis la maquette). Fonctions pures, testables
// sans navigateur -- voir docs/real_price_chart.test.js.

const REAL_PRICE_CHART_TIMEFRAMES = [
  { key: '1j', label: '1 J', days: 1 },
  { key: '3m', label: '3 M', months: 3 },
  { key: '6m', label: '6 M', months: 6 },
  { key: '1a', label: '1 A', months: 12 },
  { key: '3a', label: '3 A', months: 36 },
  { key: 'all', label: 'Tout', months: null },
];

const REAL_PRICE_CHART_MOIS_FR = [
  'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
  'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
];

function formatRealPriceChartMonthLabel(iso) {
  const [y, m] = iso.split('-');
  return REAL_PRICE_CHART_MOIS_FR[parseInt(m, 10) - 1] + ' ' + y;
}

/**
 * Filtre `pairs` ([date ISO, prix], triees par date croissante) aux
 * `months` derniers mois avant la derniere date -- `months=null` renvoie
 * tout. Repli sur les 2 derniers points si la fenetre choisie n'en
 * contient pas assez pour tracer une ligne (entreprise dont
 * l'accumulation d'historique vient de commencer).
 */
function sliceRealPriceChartByMonths(pairs, months) {
  if (months === null) return pairs;
  const lastDate = new Date(pairs[pairs.length - 1][0]);
  const cutoff = new Date(lastDate);
  cutoff.setMonth(cutoff.getMonth() - months);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  const sliced = pairs.filter((p) => p[0] >= cutoffIso);
  return sliced.length >= 2 ? sliced : pairs.slice(-2);
}

/**
 * Comme sliceRealPriceChartByMonths, mais pour une fenetre en JOURS
 * calendaires (utilisé pour "1 J" -- "setMonth" n'a pas de sens pour une
 * fenetre d'un seul jour, d'où une fonction dédiée plutôt qu'un cas
 * particulier dans sliceRealPriceChartByMonths). Même repli sur les 2
 * derniers points si la fenêtre est trop étroite (ex. lundi vs vendredi
 * précédent, un jour calendaire "1" ne retrouve ni samedi ni dimanche).
 */
function sliceRealPriceChartByDays(pairs, days) {
  const lastDate = new Date(pairs[pairs.length - 1][0]);
  const cutoff = new Date(lastDate);
  cutoff.setDate(cutoff.getDate() - days);
  const cutoffIso = cutoff.toISOString().slice(0, 10);
  const sliced = pairs.filter((p) => p[0] >= cutoffIso);
  return sliced.length >= 2 ? sliced : pairs.slice(-2);
}

/**
 * Chemins SVG (ligne + aire sous la courbe) pour `values`, mis a
 * l'echelle dans un viewBox `width`x`height` avec une marge verticale
 * `pad` (evite que les extremes touchent les bords).
 */
function buildRealPriceChartSvgPaths(values, width, height, pad) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = (max - min) || 1;
  const n = values.length;
  const x = (i) => (n > 1 ? (i / (n - 1)) * width : 0);
  const y = (v) => pad + (1 - (v - min) / span) * (height - pad * 2);
  let line = `M ${x(0).toFixed(1)} ${y(values[0]).toFixed(1)}`;
  for (let i = 1; i < n; i++) line += ` L ${x(i).toFixed(1)} ${y(values[i]).toFixed(1)}`;
  const area = `${line} L ${x(n - 1).toFixed(1)} ${height} L ${x(0).toFixed(1)} ${height} Z`;
  return { line, area };
}

/**
 * `count` niveaux de prix également espacés entre `min` et `max`
 * inclus, du plus haut au plus bas (ordre d'affichage naturel pour un
 * axe des prix : le plus haut en haut). `count` doit être >= 2.
 */
function buildRealPriceChartGridlines(min, max, count) {
  const span = max - min;
  const levels = [];
  for (let i = 0; i < count; i++) {
    levels.push(max - (span * i) / (count - 1));
  }
  return levels;
}

/**
 * Mise à l'échelle réutilisable (séparée de buildRealPriceChartSvgPaths,
 * qui construit directement des chemins SVG) : `x(i)` donne la position
 * horizontale du i-ème point, `y(valeur)` la position verticale d'un
 * prix donné — utilisé par le curseur interactif (index.html) pour
 * repositionner ses propres éléments SVG sans reconstruire tout le
 * graphique à chaque mouvement de souris.
 */
function buildRealPriceChartScale(values, width, height, pad) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = (max - min) || 1;
  const n = values.length;
  return {
    min,
    max,
    x: (i) => (n > 1 ? (i / (n - 1)) * width : 0),
    y: (v) => pad + (1 - (v - min) / span) * (height - pad * 2),
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    REAL_PRICE_CHART_TIMEFRAMES,
    formatRealPriceChartMonthLabel,
    sliceRealPriceChartByMonths,
    sliceRealPriceChartByDays,
    buildRealPriceChartSvgPaths,
    buildRealPriceChartGridlines,
    buildRealPriceChartScale,
  };
}
