// docs/company_quote_widget.js
// Résolution du symbole TradingView pour le mini-widget "Cours en direct"
// d'une fiche entreprise (voir loadSideQuoteWidget dans index.html).
//
// NO_RELIABLE_COMPANY_TV_DATA (Hang Seng, Nikkei 225) : constaté en
// production le 2026-09-17 — le widget gratuit TradingView affiche "Ce
// symbole est uniquement disponible sur TradingView" pour TOUTES les
// entreprises de ces deux places, pas seulement certaines (confirmé sur
// NetEase/9999.HK, puis généralisé après confirmation utilisateur sur une
// entreprise japonaise). Même limitation de licence de données déjà
// rencontrée et corrigée au niveau de l'INDICE lui-même (voir
// INDEX_TV_SYMBOL_BY_KEY dans index.html, qui bascule Nikkei/Hang Seng sur
// un CFD Vantage) — mais un CFD d'indice n'a pas d'équivalent par action
// individuelle chez Vantage, donc pas de symbole de repli possible ici :
// resolveCompanyTvSymbol renvoie null pour ces deux indices, et l'appelant
// doit afficher un repli (cours réel déjà disponible localement via
// company.current_price, indépendant de TradingView) plutôt que de tenter
// un widget qui échouera systématiquement.
const NO_RELIABLE_COMPANY_TV_DATA = new Set(['HANGSENG', 'NIKKEI225']);

// Préfixe d'échange TradingView par indice — dérivé du suffixe Yahoo
// Finance pour CAC40 (.PA → Euronext Paris) et DAX (.DE → Xetra), vérifié
// contre TradingView/MarketScreener y compris pour les cas a priori
// ambigus (STMicroelectronics → STMPA, Stellantis → STLAP, SAP, VOW3).
// NASDAQ n'a PAS de suffixe Yahoo (tickers US bruts, ex. "AAPL") — d'où
// une table par indice plutôt qu'une déduction par suffixe (l'ancienne
// approche, qui a laissé chaque entreprise Nasdaq sans widget de cours :
// aucun suffixe ne matchait jamais). Un futur indice avec un autre
// préfixe/suffixe devra ajouter sa propre entrée ici.
// DOW : les 21 entreprises suivies (voir DOW_COMPANIES, indices_score.py)
// sont toutes cotées au NYSE — les 9 composants du Dow cotés Nasdaq
// (Apple, Microsoft...) sont déjà suivis côté NASDAQ, pas dupliqués ici,
// donc un seul préfixe suffit comme pour les 3 autres indices.
const TV_EXCHANGE_PREFIX_BY_INDEX = { CAC40: 'EURONEXT:', DAX: 'XETR:', NASDAQ: 'NASDAQ:', DOW: 'NYSE:', FTSE: 'LSE:', SMI: 'SIX:', IBEX35: 'BME:', FTSEMIB: 'MIL:', NIKKEI225: 'TSE:', HANGSENG: 'HKEX:' };
const TV_TICKER_SUFFIX_BY_INDEX = { CAC40: '.PA', DAX: '.DE', NASDAQ: '', DOW: '', FTSE: '.L', SMI: '.SW', IBEX35: '.MC', FTSEMIB: '.MI', NIKKEI225: '.T', HANGSENG: '.HK' };
// LSE : certains codes EPIC "courts" portent un point terminal officiel
// pour éviter toute collision (ex. Aviva = "AV.", pas "AV"), que Yahoo
// absorbe dans son propre suffixe ".L" (ticker Yahoo "AV.L") — une fois ce
// suffixe retiré normalement, TradingView reçoit "AV" au lieu du vrai code
// et renvoie une 404 (vérifié en direct contre tradingview.com pour les 9
// entrées ci-dessous). BT Group est un cas à part : le point EPIC est au
// milieu ("BT.A"), pas en fin. Clé = ticker Yahoo complet.
const TV_TICKER_OVERRIDE_BY_YAHOO_TICKER = {
  'AV.L': 'AV.', 'BA.L': 'BA.', 'BP.L': 'BP.', 'JD.L': 'JD.', 'NG.L': 'NG.',
  'RR.L': 'RR.', 'SN.L': 'SN.', 'UU.L': 'UU.', 'BT-A.L': 'BT.A',
};

/**
 * Symbole TradingView pour le widget de cours d'une entreprise, ou `null`
 * si cet indice n'a pas de données fiables pour les actions individuelles
 * sur l'offre gratuite de TradingView (voir NO_RELIABLE_COMPANY_TV_DATA) —
 * l'appelant doit alors afficher un repli plutôt que d'embarquer un widget
 * voué à échouer.
 */
function resolveCompanyTvSymbol(ticker, indexKey) {
  if (NO_RELIABLE_COMPANY_TV_DATA.has(indexKey)) return null;
  const prefix = TV_EXCHANGE_PREFIX_BY_INDEX[indexKey];
  if (!prefix) return null;
  const suffix = TV_TICKER_SUFFIX_BY_INDEX[indexKey] || '';
  let bareTicker = suffix && ticker.endsWith(suffix) ? ticker.slice(0, -suffix.length) : ticker;
  if (Object.prototype.hasOwnProperty.call(TV_TICKER_OVERRIDE_BY_YAHOO_TICKER, ticker)) {
    bareTicker = TV_TICKER_OVERRIDE_BY_YAHOO_TICKER[ticker];
  }
  return prefix + bareTicker;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    NO_RELIABLE_COMPANY_TV_DATA, TV_EXCHANGE_PREFIX_BY_INDEX,
    TV_TICKER_SUFFIX_BY_INDEX, TV_TICKER_OVERRIDE_BY_YAHOO_TICKER,
    resolveCompanyTvSymbol,
  };
}
