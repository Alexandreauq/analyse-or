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

  const structurelAchat = trend === 'baissier' && (nearSupport || brokeResistance);
  const structurelVente = trend === 'haussier' && (nearResistance || brokeSupport);

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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    fetchGoldCandles, detectPivots, classifyTrend, currentLevels,
    computeRSI, computeMACD, computeBollinger, matchCandlestickPattern,
    computeSignal,
  };
}
