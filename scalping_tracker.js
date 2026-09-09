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
const STALE_THRESHOLD_MS = 5 * 60 * 1000; // aligne sur SCALP_STALE_THRESHOLD_MS du navigateur (docs/index.html)

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
function decidePositionOutcome(position, candlesSinceEntry, now, trialElapsed, isStale = false) {
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
  if (isStale) {
    // Donnees perimees : on ne force aucune cloture basee sur l'horloge
    // murale (max_duration/trial_end) tant qu'on n'a pas de confirmation
    // fraiche du marche — seul un vrai SL/TP touche dans les bougies
    // recues (deja verifie ci-dessus) peut clore une position ici.
    return { closed: false };
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

  const lastCandleTime = new Date(candles[candles.length - 1].time.replace(' ', 'T') + 'Z').getTime();
  const isStale = (now - lastCandleTime) > STALE_THRESHOLD_MS;
  if (isStale) {
    console.log(`Donnees perimees (${Math.round((now - lastCandleTime) / 60000)} min) - pas d'ouverture ni de cloture forcee ce run.`);
  }

  const openPosition = data.positions.find(p => p.status === 'open');

  if (openPosition) {
    const entryTime = new Date(openPosition.entry_time).getTime();
    const candlesSinceEntry = candles.filter(c => new Date(c.time.replace(' ', 'T') + 'Z').getTime() > entryTime);
    const outcome = decidePositionOutcome(openPosition, candlesSinceEntry, now, trialElapsed, isStale);
    if (outcome.closed) {
      openPosition.status = 'closed';
      openPosition.close_time = new Date(now).toISOString();
      openPosition.close_price = outcome.price;
      openPosition.close_reason = outcome.reason;
      const { return_usd, return_pct } = computeReturn(openPosition.direction, openPosition.entry_price, outcome.price);
      openPosition.return_usd = return_usd;
      openPosition.return_pct = return_pct;
    }
  } else if (!trialElapsed && !isStale) {
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
  STALE_THRESHOLD_MS,
};
