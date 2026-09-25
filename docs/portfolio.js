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

const PORTFOLIO_CLOSED_STORAGE_KEY = 'analyse-or-portfolio-closed';

/**
 * Repli sur [] si storage est absent, la clé n'existe pas, le JSON est
 * invalide, ou la valeur stockée n'est pas un tableau — même contrat
 * que loadPortfolio.
 */
function loadClosedPortfolio(storage) {
  const s = _resolveStorage(storage);
  if (!s) return [];
  try {
    const raw = s.getItem(PORTFOLIO_CLOSED_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

function saveClosedPortfolio(positions, storage) {
  const s = _resolveStorage(storage);
  if (!s) return false;
  try {
    s.setItem(PORTFOLIO_CLOSED_STORAGE_KEY, JSON.stringify(positions));
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * Déplace une position ouverte vers la liste clôturée (garde une trace
 * de la vente : sell_price, sell_date), plutôt que de la supprimer —
 * removePosition reste la vraie suppression (erreurs de saisie), reste
 * inchangée, ne touche jamais cette liste.
 */
function closePosition(id, sellPrice, sellDate, storage) {
  if (!Number.isFinite(sellPrice) || sellPrice <= 0) {
    return { ok: false, error: 'Le prix de vente doit être un nombre positif.' };
  }
  if (!sellDate) {
    return { ok: false, error: 'La date de vente est obligatoire.' };
  }
  const openPositions = loadPortfolio(storage);
  const idx = openPositions.findIndex(p => p.id === id);
  if (idx === -1) return { ok: false, error: 'Position introuvable.' };
  const [position] = openPositions.splice(idx, 1);
  const closedPosition = { ...position, sell_price: sellPrice, sell_date: sellDate };
  const closedPositions = loadClosedPortfolio(storage);
  closedPositions.push(closedPosition);
  if (!savePortfolio(openPositions, storage) || !saveClosedPortfolio(closedPositions, storage)) {
    return { ok: false, error: "La sauvegarde a échoué (stockage local indisponible ou plein)." };
  }
  return { ok: true, positions: openPositions, closedPositions };
}

/**
 * Regroupe docs/dividend_history.json ({date, ticker, amount}) par
 * ticker, trie chaque groupe par date croissante. Meme forme que
 * groupPriceHistoryByTicker.
 */
function groupDividendHistoryByTicker(dividendHistory) {
  const byTicker = {};
  dividendHistory.forEach(entry => {
    (byTicker[entry.ticker] = byTicker[entry.ticker] || []).push(entry);
  });
  Object.values(byTicker).forEach(entries => entries.sort((a, b) => (a.date < b.date ? -1 : 1)));
  return byTicker;
}

/**
 * Cumul de dividendes reellement encaisses, positions OUVERTES et
 * CLOTUREES (un paiement recu pendant la detention reste un
 * encaissement reel meme si la position est cloturee depuis). Un
 * paiement compte pour une position si elle etait detenue AVANT la date
 * ex-dividende (buy_date strictement anterieure -- acheter LE jour
 * ex-dividende n'ouvre pas droit au paiement, contrairement a
 * isPositionActiveOn qui est inclusive sur buy_date et repond a une
 * question differente : "la position etait-elle detenue CE jour-la ?",
 * correcte pour la courbe de performance mais pas pour l'eligibilite a
 * un dividende) et non cloturee avant cette meme date (sell_date >=
 * date ex-dividende : vendre LE jour ex-dividende ouvre bien droit au
 * paiement). Agrege par devise ({ EUR: montant, USD: montant }), jamais
 * converti/melange (meme convention que computePortfolioTotals). Une
 * position dont le ticker n'est pas dans companiesByTicker (retiree de
 * l'indice suivi) est ignoree.
 */
function computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex) {
  const totals = { EUR: 0, USD: 0 };
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    const payments = dividendHistoryByTicker[position.ticker] || [];
    payments.forEach(payment => {
      if (position.buy_date >= payment.date) return;
      if (position.sell_date && position.sell_date < payment.date) return;
      totals[currency] += payment.amount * position.quantity;
    });
  });
  return totals;
}

/**
 * `dateStr` ('YYYY-MM-DD') moins `days` jours, au format 'YYYY-MM-DD'.
 * Utilise Date en UTC pour eviter tout decalage de fuseau horaire.
 */
function _dateMinusDays(dateStr, days) {
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * Revenu projete : pour chaque position OUVERTE, somme des paiements par
 * action des 365 derniers jours (TTM, trailing twelve months, fenetre
 * ]today - 365 jours, today]) x quantite detenue. Agrege par devise.
 * `today` est injectable (tests), 'YYYY-MM-DD', vaut la date du jour par
 * defaut. Retourne { EUR: { annual, monthly }, USD: { annual, monthly } }
 * -- monthly = annual / 12 (moyenne, pas un calendrier reel).
 */
function computeProjectedDividendIncome(openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, today) {
  today = today || new Date().toISOString().slice(0, 10);
  const cutoff = _dateMinusDays(today, 365);
  const annual = { EUR: 0, USD: 0 };
  openPositions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    const payments = dividendHistoryByTicker[position.ticker] || [];
    const ttmPerShare = payments
      .filter(p => p.date > cutoff && p.date <= today)
      .reduce((sum, p) => sum + p.amount, 0);
    annual[currency] += ttmPerShare * position.quantity;
  });
  return {
    EUR: { annual: annual.EUR, monthly: annual.EUR / 12 },
    USD: { annual: annual.USD, monthly: annual.USD / 12 },
  };
}

/**
 * Rendement sur cout par position OUVERTE : dividende TTM par action /
 * buy_price * 100. Ne retourne que les positions dont le TTM > 0 (les
 * non-payeuses sont exclues, pas affichees a 0% -- evite le bruit dans
 * la liste). `today` injectable (tests), meme fenetre TTM que
 * computeProjectedDividendIncome. Une position dont le ticker n'est pas
 * dans companiesByTicker (retiree de l'indice suivi) est ignoree, meme
 * convention que les autres fonctions de ce fichier. Tableau
 * [{ ticker, ttmPerShare, yieldOnCost, projectedAnnual }, ...],
 * projectedAnnual = ttmPerShare * quantity (revenu annuel projete de
 * cette ligne).
 */
function computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, today) {
  today = today || new Date().toISOString().slice(0, 10);
  const cutoff = _dateMinusDays(today, 365);
  const results = [];
  openPositions.forEach(position => {
    if (!companiesByTicker[position.ticker]) return;
    const payments = dividendHistoryByTicker[position.ticker] || [];
    const ttmPerShare = payments
      .filter(p => p.date > cutoff && p.date <= today)
      .reduce((sum, p) => sum + p.amount, 0);
    if (ttmPerShare <= 0) return;
    results.push({
      ticker: position.ticker,
      ttmPerShare,
      yieldOnCost: (ttmPerShare / position.buy_price) * 100,
      projectedAnnual: ttmPerShare * position.quantity,
    });
  });
  return results;
}

/**
 * Regroupe docs/price_history.json (liste plate {date, ticker, price})
 * par ticker, trie chaque groupe par date croissante.
 */
function groupPriceHistoryByTicker(priceHistory) {
  const byTicker = {};
  priceHistory.forEach(entry => {
    (byTicker[entry.ticker] = byTicker[entry.ticker] || []).push(entry);
  });
  Object.values(byTicker).forEach(entries => entries.sort((a, b) => (a.date < b.date ? -1 : 1)));
  return byTicker;
}

/**
 * Prix connu d'un ticker à la date D : la dernière entrée dont la date
 * est <= D (jamais d'extrapolation future). null si aucune n'existe.
 */
function priceAtOrBefore(priceHistoryByTicker, ticker, date) {
  const entries = priceHistoryByTicker[ticker];
  if (!entries || !entries.length) return null;
  let result = null;
  for (const entry of entries) {
    if (entry.date > date) break;
    result = entry.price;
  }
  return result;
}

/**
 * Une position (ouverte ou clôturée) est active à la date D si
 * buy_date <= D et (sell_date absent OU sell_date >= D).
 */
function isPositionActiveOn(position, date) {
  if (position.buy_date > date) return false;
  if (position.sell_date && position.sell_date < date) return false;
  return true;
}

function realValueForPosition(position, date, priceHistoryByTicker) {
  const price = priceAtOrBefore(priceHistoryByTicker, position.ticker, date);
  return Number.isFinite(price) ? price * position.quantity : null;
}

/**
 * Valeur "fantôme" à la date D si le même COÛT (pas la même quantité)
 * avait été investi dans l'indice benchmark à la date d'achat de la
 * position : cost * indexPrice(D) / indexPrice(buy_date). Nécessaire
 * car la quantité réelle (actions de l'entreprise) n'a pas de sens
 * multipliée par un niveau d'indice — les unités ne correspondent pas
 * (corrige un bug trouvé lors de la vérification de l'UI : la formule
 * initiale, qui réutilisait quantity * prix d'indice brut, donnait des
 * % de benchmark aberrants, ex. +2983%).
 */
function benchmarkValueForPosition(indexTicker) {
  return (position, date, priceHistoryByTicker) => {
    const priceAtBuy = priceAtOrBefore(priceHistoryByTicker, indexTicker, position.buy_date);
    const priceAtDate = priceAtOrBefore(priceHistoryByTicker, indexTicker, date);
    if (!Number.isFinite(priceAtBuy) || priceAtBuy === 0 || !Number.isFinite(priceAtDate)) return null;
    const cost = position.buy_price * position.quantity;
    return cost * (priceAtDate / priceAtBuy);
  };
}

/**
 * Courbe de rendement en % pour un ensemble de positions sur toutes les
 * dates disponibles dans priceHistoryByTicker pour les tickers de ces
 * positions. `valueForPosition(position, date, priceHistoryByTicker)`
 * fournit la VALEUR à la date D pour une position — le portefeuille réel
 * l'appelle avec realValueForPosition (prix réel × quantité réelle), la
 * courbe benchmark avec benchmarkValueForPosition(indexTicker) (même
 * coût scalé par l'évolution de l'indice depuis l'achat) : même fonction
 * d'agrégation dans les deux cas.
 *
 * pnlPct(D) = somme(value(D) - cost) / somme(cost) * 100 — pondéré par
 * le capital investi, jamais une moyenne des % de chaque ligne.
 */
function computePerformanceCurve(positions, priceHistoryByTicker, valueForPosition) {
  const dates = new Set();
  positions.forEach(p => {
    const entries = priceHistoryByTicker[p.ticker];
    if (entries) entries.forEach(e => dates.add(e.date));
  });
  return Array.from(dates).sort().map(date => {
    let totalCost = 0;
    let totalPnlAbs = 0;
    positions.forEach(position => {
      if (!isPositionActiveOn(position, date)) return;
      const value = valueForPosition(position, date, priceHistoryByTicker);
      if (!Number.isFinite(value)) return;
      const cost = position.buy_price * position.quantity;
      totalCost += cost;
      totalPnlAbs += value - cost;
    });
    return { date, pnlPct: totalCost !== 0 ? (totalPnlAbs / totalCost) * 100 : null };
  }).filter(point => point.pnlPct !== null);
}

/**
 * { EUR: [...], USD: [...] } — tableau vide pour une devise sans
 * aucune position (ouverte ou clôturée), jamais de conversion entre
 * devises (cohérent avec computePortfolioTotals).
 */
function computePortfolioPerformanceCurves(openPositions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker) {
  const allPositions = [...openPositions, ...closedPositions];
  const byCurrency = { EUR: [], USD: [] };
  allPositions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    byCurrency[currency].push(position);
  });
  const curves = {};
  Object.keys(byCurrency).forEach(currency => {
    curves[currency] = byCurrency[currency].length
      ? computePerformanceCurve(byCurrency[currency], priceHistoryByTicker, realValueForPosition)
      : [];
  });
  return curves;
}

/**
 * Courbe benchmark pour un ensemble de positions donné : réutilise
 * EXACTEMENT les mêmes positions (mêmes buy_date/sell_date/cost) que
 * computePortfolioPerformanceCurves pour cette devise, seule la source
 * de prix change (indexTicker au lieu du ticker de chaque position) —
 * comparaison apples-to-apples.
 */
function computeBenchmarkPerformanceCurve(positions, indexTicker, priceHistoryByTicker) {
  return computePerformanceCurve(positions, priceHistoryByTicker, benchmarkValueForPosition(indexTicker));
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

/**
 * Répartition du portefeuille par indice/secteur (en nombre de lignes)
 * et par devise (en valeur détenue) — jamais en valeur pondérée pour
 * indice/secteur, ni convertie entre devises pour la répartition par
 * devise : le site ne convertit jamais entre devises ailleurs (voir
 * computePortfolioTotals), donc sommer des € et des $ n'aurait pas de
 * sens. Une position dont le ticker n'est plus suivi est ignorée (comme
 * ailleurs dans ce fichier) plutôt que de faire échouer tout le calcul.
 */
function computePortfolioConcentration(positions, companiesByTicker, currencyByIndex) {
  const byIndex = {};
  const bySector = {};
  const byCurrency = {};
  let totalPositions = 0;
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    totalPositions++;
    byIndex[company.index] = (byIndex[company.index] || 0) + 1;
    const sector = company.sector || 'Inconnu';
    bySector[sector] = (bySector[sector] || 0) + 1;
    const currency = (currencyByIndex && currencyByIndex[company.index]) || 'EUR';
    const pnl = computePositionPnL(position, company.current_price);
    const value = pnl ? pnl.value : position.quantity * position.buy_price;
    byCurrency[currency] = (byCurrency[currency] || 0) + value;
  });
  return { totalPositions, byIndex, bySector, byCurrency };
}

/**
 * Santé du portefeuille selon le modèle : score moyen à parts égales
 * entre positions (pas pondéré par la valeur détenue — additionner des
 * valeurs dans des devises différentes n'aurait pas de sens sans
 * conversion, que ce fichier ne fait jamais) + nombre de positions avec
 * une alerte "risque" ou "actu_majeure" active.
 */
function computePortfolioHealth(positions, companiesByTicker) {
  let scoreSum = 0;
  let scoreCount = 0;
  let riskAlertCount = 0;
  let majorNewsAlertCount = 0;
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    if (Number.isFinite(company.score)) {
      scoreSum += company.score;
      scoreCount++;
    }
    const alerts = company.alerts || [];
    if (alerts.some(a => a.kind === 'risque')) riskAlertCount++;
    if (alerts.some(a => a.kind === 'actu_majeure')) majorNewsAlertCount++;
  });
  return {
    avgScore: scoreCount ? scoreSum / scoreCount : null,
    scoredPositions: scoreCount,
    riskAlertCount,
    majorNewsAlertCount,
  };
}

/**
 * Contribution de chaque position au P&L, triée par contribution
 * absolue décroissante (plus gros contributeur positif en premier).
 * Version "simple" de l'attribution de performance : pas de comparaison
 * à l'indice sur la période de détention (demanderait le cours de
 * l'indice à la date d'achat, non disponible pour des positions
 * ajoutées manuellement à une date arbitraire).
 */
function computePortfolioAttribution(positions, companiesByTicker) {
  const rows = [];
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const pnl = computePositionPnL(position, company.current_price);
    if (!pnl) return;
    rows.push({
      ticker: position.ticker, name: company.name, index: company.index,
      pnlAbs: pnl.pnlAbs, pnlPct: pnl.pnlPct,
    });
  });
  rows.sort((a, b) => b.pnlAbs - a.pnlAbs);
  return rows;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
    loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
    computePositionPnL, computePortfolioTotals, findOpportunities,
    computePortfolioConcentration, computePortfolioHealth, computePortfolioAttribution,
    PORTFOLIO_CLOSED_STORAGE_KEY, loadClosedPortfolio, saveClosedPortfolio, closePosition,
    groupPriceHistoryByTicker, priceAtOrBefore, isPositionActiveOn, realValueForPosition,
    benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
    computeBenchmarkPerformanceCurve,
    groupDividendHistoryByTicker, computeDividendsReceived, computeProjectedDividendIncome,
    computeYieldOnCost,
  };
}
