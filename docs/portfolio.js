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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
    loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
    computePositionPnL, computePortfolioTotals, findOpportunities,
  };
}
