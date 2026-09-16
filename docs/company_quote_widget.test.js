// docs/company_quote_widget.test.js
// Tests JS sans framework, exécutables via `node docs/company_quote_widget.test.js`
// (voir docs/scalping.test.js pour la convention : `node docs/*.test.js` est
// le seul moyen d'exécuter réellement ces tests, aucune machine de dev n'a
// Node.js installé dans ce projet).
const assert = require('assert');
const { resolveCompanyTvSymbol, NO_RELIABLE_COMPANY_TV_DATA } = require('./company_quote_widget.js');

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

function test_resolveCompanyTvSymbol_applies_lse_epic_override() {
  // Aviva : code EPIC officiel "AV.", que Yahoo absorbe dans son propre
  // suffixe ".L" — sans l'override, on obtiendrait à tort "LSE:AV".
  assert.strictEqual(resolveCompanyTvSymbol('AV.L', 'FTSE'), 'LSE:AV.');
  console.log('OK: test_resolveCompanyTvSymbol_applies_lse_epic_override');
}

function test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_and_nikkei() {
  assert.deepStrictEqual([...NO_RELIABLE_COMPANY_TV_DATA].sort(), ['HANGSENG', 'NIKKEI225']);
  console.log('OK: test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_and_nikkei');
}

function main() {
  test_resolveCompanyTvSymbol_null_for_hangseng();
  test_resolveCompanyTvSymbol_null_for_nikkei225();
  test_resolveCompanyTvSymbol_builds_symbol_for_working_indices();
  test_resolveCompanyTvSymbol_applies_lse_epic_override();
  test_NO_RELIABLE_COMPANY_TV_DATA_contains_exactly_hangseng_and_nikkei();
  console.log('Tous les tests company_quote_widget.test.js sont passés.');
}

main();
