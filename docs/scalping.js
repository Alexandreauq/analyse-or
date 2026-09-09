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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fetchGoldCandles, detectPivots, classifyTrend, currentLevels };
}
