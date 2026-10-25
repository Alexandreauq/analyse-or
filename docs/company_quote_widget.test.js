// docs/company_quote_widget.test.js
// Tests JS sans framework, exécutables via `node docs/company_quote_widget.test.js`
// (voir docs/scalping.test.js pour la convention : `node docs/*.test.js` est
// le seul moyen d'exécuter réellement ces tests, aucune machine de dev n'a
// Node.js installé dans ce projet).
const assert = require('assert');
const {
  resolveCompanyTvSymbol, NO_RELIABLE_COMPANY_TV_DATA, TV_TICKER_OVERRIDE_BY_YAHOO_TICKER,
} = require('./company_quote_widget.js');

function test_resolveCompanyTvSymbol_null_for_hangseng() {
  // Constaté en production le 2026-09-17 : NetEase (9999.HK) affiche "Ce
  // symbole est uniquement disponible sur TradingView" — tout Hang Seng
  // est concerné, pas seulement ce ticker.
  assert.strictEqual(resolveCompanyTvSymbol('9999.HK', 'HANGSENG'), null);
  console.log('OK: test_resolveCompanyTvSymbol_null_for_hangseng');
}

function test_resolveCompanyTvSymbol_null_for_nikkei225() {
  // Confirmé par l'utilisateur le 2026-09-17 : même symptôme que Hang Seng.
  assert.strictEqual(resolveCompanyTvSymbol('7203.T', 'NIKKEI225'), null);
  console.log('OK: test_resolveCompanyTvSymbol_null_for_nikkei225');
}

function test_resolveCompanyTvSymbol_builds_symbol_for_working_indices() {
  // CAC40 : suffixe Yahoo ".PA" retiré, préfixe TradingView "EURONEXT:" ajouté.
  assert.strictEqual(resolveCompanyTvSymbol('MC.PA', 'CAC40'), 'EURONEXT:MC');
  // NASDAQ : pas de suffixe Yahoo à retirer.
  assert.strictEqual(resolveCompanyTvSymbol('AAPL', 'NASDAQ'), 'NASDAQ:AAPL');
  console.log('OK: test_resolveCompanyTvSymbol_builds_symbol_for_working_indices');
}

function test_resolveCompanyTvSymbol_null_for_ftse() {
  // Constaté en production le 2026-10-25 : Admiral Group (ADM.L) affiche
  // "Ce symbole est uniquement disponible sur TradingView" sur le widget
  // gratuit ; confirmé par l'utilisateur que TOUT le FTSE est concerné
  // (pas une collision de ticker propre à ADM, qui existe aussi au NYSE
  // sous Archer-Daniels-Midland — vérifié que "LSE:ADM" résout bien vers
  // Admiral Group sur tradingview.com, donc ce n'est pas une ambiguïté de
  // symbole : la donnée LSE par action n'est simplement pas couverte par
  // l'offre gratuite de widget, même limitation que Hang Seng/Nikkei 225
  // (voir NO_RELIABLE_COMPANY_TV_DATA dans company_quote_widget.js).
  assert.strictEqual(resolveCompanyTvSymbol('ADM.L', 'FTSE'), null);
  assert.strictEqual(resolveCompanyTvSymbol('AV.L', 'FTSE'), null);
  console.log('OK: test_resolveCompanyTvSymbol_null_for_ftse');
}

function test_TV_TICKER_OVERRIDE_BY_YAHOO_TICKER_still_has_the_lse_epic_overrides() {
  // La table d'override EPIC (Aviva "AV." etc.) n'est plus atteignable via
  // resolveCompanyTvSymbol('...', 'FTSE') depuis que FTSE est dans
  // NO_RELIABLE_COMPANY_TV_DATA (court-circuité avant toute résolution de
  // symbole) -- elle resterait utile si un autre indice mélangeant les
  // places (AEXALL, SBF120...) incluait un jour une valeur cotée à
  // Londres via le repli par suffixe Yahoo (TV_EXCHANGE_PREFIX_BY_SUFFIX).
  // Ce test verifie directement les données de la table plutôt que de
  // feindre un chemin de résolution qui n'existe plus pour le FTSE.
  assert.strictEqual(TV_TICKER_OVERRIDE_BY_YAHOO_TICKER['AV.L'], 'AV.');
  assert.strictEqual(TV_TICKER_OVERRIDE_BY_YAHOO_TICKER['BT-A.L'], 'BT.A');
  console.log('OK: test_TV_TICKER_OVERRIDE_BY_YAHOO_TICKER_still_has_the_lse_epic_overrides');
}

function test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_nikkei_and_ftse() {
  assert.deepStrictEqual([...NO_RELIABLE_COMPANY_TV_DATA].sort(), ['FTSE', 'HANGSENG', 'NIKKEI225']);
  console.log('OK: test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_nikkei_and_ftse');
}

function test_resolveCompanyTvSymbol_uses_yahoo_suffix_for_indices_without_mapping() {
  // AEX All-Share mélange Amsterdam, Bruxelles et Milan : le suffixe Yahoo
  // donne la place, pas l'indice (qui n'a pas de ligne dans la table).
  assert.strictEqual(resolveCompanyTvSymbol('ASML.AS', 'AEXALL'), 'EURONEXT:ASML');
  assert.strictEqual(resolveCompanyTvSymbol('AED.BR', 'AEXALL'), 'EURONEXT:AED');
  assert.strictEqual(resolveCompanyTvSymbol('RACE.MI', 'AEXALL'), 'MIL:RACE');
  assert.strictEqual(resolveCompanyTvSymbol('SAF.PA', 'SBF120'), 'EURONEXT:SAF');
  console.log('OK: test_resolveCompanyTvSymbol_uses_yahoo_suffix_for_indices_without_mapping');
}

function test_resolveCompanyTvSymbol_returns_null_without_known_suffix() {
  // CRH et Flutter (Euro Stoxx 50) n'ont pas de suffixe : pas de symbole
  // deviné, l'appelant affiche le repli.
  assert.strictEqual(resolveCompanyTvSymbol('CRH', 'EUROSTOXX50'), null);
  assert.strictEqual(resolveCompanyTvSymbol('FLUT', 'EUROSTOXX50'), null);
  console.log('OK: test_resolveCompanyTvSymbol_returns_null_without_known_suffix');
}

function test_resolveCompanyTvSymbol_routes_sp500_companies_by_their_exchange() {
  // Apple est au Nasdaq, Berkshire (classe B, tiret Yahoo) est au NYSE avec un point.
  assert.strictEqual(resolveCompanyTvSymbol('AAPL', 'SP500'), 'NASDAQ:AAPL');
  assert.strictEqual(resolveCompanyTvSymbol('BRK-B', 'SP500'), 'NYSE:BRK.B');
  assert.strictEqual(resolveCompanyTvSymbol('ZZZ-UNKNOWN', 'SP500'), null);
  console.log('OK: test_resolveCompanyTvSymbol_routes_sp500_companies_by_their_exchange');
}

function main() {
  test_resolveCompanyTvSymbol_null_for_hangseng();
  test_resolveCompanyTvSymbol_null_for_nikkei225();
  test_resolveCompanyTvSymbol_builds_symbol_for_working_indices();
  test_resolveCompanyTvSymbol_null_for_ftse();
  test_TV_TICKER_OVERRIDE_BY_YAHOO_TICKER_still_has_the_lse_epic_overrides();
  test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_nikkei_and_ftse();
  test_resolveCompanyTvSymbol_uses_yahoo_suffix_for_indices_without_mapping();
  test_resolveCompanyTvSymbol_returns_null_without_known_suffix();
  test_resolveCompanyTvSymbol_routes_sp500_companies_by_their_exchange();
  console.log('Tous les tests company_quote_widget.test.js sont passés.');
}

main();
