// docs/assistant_suggestions.test.js
// Tests JS sans framework, exécutables via `node docs/assistant_suggestions.test.js`.
const assert = require('assert');
const { PLACEHOLDER_EXAMPLES, nextPlaceholderIndex, HOME_GUIDE, normalizeText, filterGuide } = require('./assistant_suggestions.js');

function test_exemples_du_placeholder() {
  assert.strictEqual(PLACEHOLDER_EXAMPLES.length, 6);
  assert.strictEqual(nextPlaceholderIndex(5, 6), 0);
  assert.strictEqual(nextPlaceholderIndex(2, 6), 3);
}

function test_normalizeText_minuscules_sans_accents() {
  assert.strictEqual(normalizeText('Énergie Été'), 'energie ete');
}

function test_filterGuide_ne_garde_que_les_questions_correspondantes() {
  const g = filterGuide(HOME_GUIDE, 'top');
  assert.deepStrictEqual(g.map(c => c.key), ['indices']);
  assert.deepStrictEqual(g[0].questions, ['Top 5 du CAC 40']);
  assert.strictEqual(filterGuide(HOME_GUIDE, '').length, 3);
  assert.deepStrictEqual(filterGuide(HOME_GUIDE, 'zzzz'), []);
  assert.deepStrictEqual(filterGuide(HOME_GUIDE, 'evenement macro')[0].questions, ['Quel est le prochain événement macro ?']);
}

function test_suggestionsForRoute_fiche_entreprise() {
  const { suggestionsForRoute } = require('./assistant_suggestions.js');
  assert.deepStrictEqual(suggestionsForRoute('#indices/MC.PA', 'LVMH'), ["Explique le repère d'entrée de LVMH", 'Critères Graham de LVMH ?']);
  assert.deepStrictEqual(suggestionsForRoute('#indices/MC.PA'), ["Explique le repère d'entrée de MC.PA", 'Critères Graham de MC.PA ?']);
  assert.strictEqual(suggestionsForRoute('#xyz').length, 2);
  assert.deepStrictEqual(suggestionsForRoute('#portefeuille'), ['Analyse mon portefeuille', 'Quel est le statut des bots ?']);
}

function test_contextLabel_par_route() {
  const { contextLabel } = require('./assistant_suggestions.js');
  assert.strictEqual(contextLabel('#indices/MC.PA', 'LVMH'), 'Fiche LVMH');
  assert.strictEqual(contextLabel('#indices/MC.PA/analyse', 'LVMH'), 'Analyse LVMH');
  assert.strictEqual(contextLabel('#indices/suivi'), 'Suivi des signaux');
  assert.strictEqual(contextLabel('#indices/compare/MC.PA,RMS.PA'), 'Comparateur');
  assert.strictEqual(contextLabel(''), 'Accueil');
  assert.strictEqual(contextLabel('#or'), 'Or');
}

test_exemples_du_placeholder();
test_suggestionsForRoute_fiche_entreprise();
test_contextLabel_par_route();
test_normalizeText_minuscules_sans_accents();
test_filterGuide_ne_garde_que_les_questions_correspondantes();
console.log('OK');
