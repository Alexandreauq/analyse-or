// docs/chart_patterns.test.js
// Tests JS sans framework, exécutables via `node docs/chart_patterns.test.js`
// (comme docs/scalping.test.js). Chaque test compare un résultat calculé
// à une valeur attendue calculée à la main.
const assert = require('assert');
const { detectChartPatterns, CHARTPATTERN_HEIGHT_TOLERANCE } = require('./chart_patterns.js');

function test_doubleTop_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 100 },
    { index: 10, type: 'high', price: 110.5 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 99);
  assert.deepStrictEqual(result, {
    name: 'Double Top', direction: 'baissier', kind: 'retournement',
    breakoutPrice: 100, extremityPrice: 110.5, patternHeight: 10.5,
  });
  console.log('OK: test_doubleTop_detected');
}

function test_doubleTop_rejected_when_heights_too_far_apart() {
  // Identique au test précédent mais h2=112 au lieu de 110.5 : écart de
  // 2$ > CHARTPATTERN_HEIGHT_TOLERANCE (1$) -> aucune figure détectée.
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 100 },
    { index: 10, type: 'high', price: 112 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 99);
  assert.strictEqual(result, null);
  console.log('OK: test_doubleTop_rejected_when_heights_too_far_apart');
}

function test_doubleBottom_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 90 },
    { index: 5, type: 'high', price: 100 },
    { index: 10, type: 'low', price: 90.5 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 101);
  assert.deepStrictEqual(result, {
    name: 'Double Bottom', direction: 'haussier', kind: 'retournement',
    breakoutPrice: 100, extremityPrice: 90, patternHeight: 10,
  });
  console.log('OK: test_doubleBottom_detected');
}

function test_teteEpaule_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 100 },   // épaule 1
    { index: 5, type: 'low', price: 95 },
    { index: 10, type: 'high', price: 110 },  // tête
    { index: 15, type: 'low', price: 96 },
    { index: 20, type: 'high', price: 100.5 }, // épaule 2
  ];
  const result = detectChartPatterns(pivots, 'haussier', 95);
  assert.deepStrictEqual(result, {
    name: 'Tête-Épaule', direction: 'baissier', kind: 'retournement',
    breakoutPrice: 96, extremityPrice: 110, patternHeight: 14,
  });
  console.log('OK: test_teteEpaule_detected');
}

function test_teteEpauleInversee_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 100 },
    { index: 5, type: 'high', price: 105 },
    { index: 10, type: 'low', price: 90 },
    { index: 15, type: 'high', price: 104 },
    { index: 20, type: 'low', price: 100.5 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 105);
  assert.deepStrictEqual(result, {
    name: 'Tête-Épaule inversée', direction: 'haussier', kind: 'retournement',
    breakoutPrice: 104, extremityPrice: 90, patternHeight: 14,
  });
  console.log('OK: test_teteEpauleInversee_detected');
}

function test_triangleAscendant_detected() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 95 },
    { index: 10, type: 'high', price: 110.5 },
    { index: 15, type: 'low', price: 100 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 111);
  assert.deepStrictEqual(result, {
    name: 'Triangle ascendant', direction: 'haussier', kind: 'continuation',
    breakoutPrice: 110.5, extremityPrice: 95, patternHeight: 15.5,
  });
  console.log('OK: test_triangleAscendant_detected');
}

function test_triangleDescendant_detected() {
  const pivots = [
    { index: 0, type: 'low', price: 90 },
    { index: 5, type: 'high', price: 105 },
    { index: 10, type: 'low', price: 90.5 },
    { index: 15, type: 'high', price: 100 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 90);
  assert.deepStrictEqual(result, {
    name: 'Triangle descendant', direction: 'baissier', kind: 'continuation',
    breakoutPrice: 90.5, extremityPrice: 105, patternHeight: 14.5,
  });
  console.log('OK: test_triangleDescendant_detected');
}

function test_triangleSymetrique_breakoutHaussier() {
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 90 },
    { index: 10, type: 'high', price: 105 },
    { index: 15, type: 'low', price: 95 },
  ];
  const result = detectChartPatterns(pivots, 'haussier', 106);
  assert.deepStrictEqual(result, {
    name: 'Triangle symétrique', direction: 'haussier', kind: 'continuation',
    breakoutPrice: 105, extremityPrice: 90, patternHeight: 20,
  });
  console.log('OK: test_triangleSymetrique_breakoutHaussier');
}

function test_triangleSymetrique_breakoutBaissier() {
  // Mêmes pivots que le test précédent, mais tendance et prix inversés
  // -> cassure dans l'autre sens (le triangle symétrique ne présume pas
  // du sens avant la cassure effective, cf. spec).
  const pivots = [
    { index: 0, type: 'high', price: 110 },
    { index: 5, type: 'low', price: 90 },
    { index: 10, type: 'high', price: 105 },
    { index: 15, type: 'low', price: 95 },
  ];
  const result = detectChartPatterns(pivots, 'baissier', 89);
  assert.deepStrictEqual(result, {
    name: 'Triangle symétrique', direction: 'baissier', kind: 'continuation',
    breakoutPrice: 95, extremityPrice: 110, patternHeight: 20,
  });
  console.log('OK: test_triangleSymetrique_breakoutBaissier');
}

function main() {
  test_doubleTop_detected();
  test_doubleTop_rejected_when_heights_too_far_apart();
  test_doubleBottom_detected();
  test_teteEpaule_detected();
  test_teteEpauleInversee_detected();
  test_triangleAscendant_detected();
  test_triangleDescendant_detected();
  test_triangleSymetrique_breakoutHaussier();
  test_triangleSymetrique_breakoutBaissier();
  console.log('Tous les tests chart_patterns sont passes.');
}

main();
