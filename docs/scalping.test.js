// docs/scalping.test.js
// Tests JS sans framework, exécutables via `node docs/scalping.test.js`.
// Chaque test compare un résultat calculé à une valeur attendue calculée
// à la main (voir commentaires) et lève une erreur explicite si ça ne
// correspond pas — pas de bibliothèque d'assertion, juste `assert` natif
// de Node pour rester sans dépendance.
const assert = require('assert');
const { fetchGoldCandles, detectPivots, classifyTrend, currentLevels, computeRSI, computeMACD, computeBollinger } = require('./scalping.js');

async function test_fetchGoldCandles_parses_and_reverses_to_chronological_order() {
  const fakeResponse = {
    ok: true,
    json: async () => ({
      status: 'ok',
      values: [
        { datetime: '2026-09-09 10:02:00', open: '2051.0', high: '2051.5', low: '2050.5', close: '2051.2' },
        { datetime: '2026-09-09 10:01:00', open: '2050.0', high: '2050.8', low: '2049.5', close: '2050.5' },
      ],
    }),
  };
  const fakeFetch = async (url) => {
    assert(url.includes('symbol=XAU/USD'), 'URL doit contenir le symbole XAU/USD');
    assert(url.includes('interval=1min'), 'URL doit demander l\'intervalle 1min');
    assert(url.includes('timezone=UTC'), 'URL doit forcer timezone=UTC (sinon le controle de fraicheur des donnees se trompe de fuseau)');
    return fakeResponse;
  };
  const candles = await fetchGoldCandles('fake-key', fakeFetch);
  assert.strictEqual(candles.length, 2);
  // Le plus ancien (10:01) doit être en premier après inversion.
  assert.strictEqual(candles[0].time, '2026-09-09 10:01:00');
  assert.strictEqual(candles[0].close, 2050.5);
  assert.strictEqual(candles[1].time, '2026-09-09 10:02:00');
  console.log('OK: test_fetchGoldCandles_parses_and_reverses_to_chronological_order');
}

async function test_fetchGoldCandles_rejects_on_error_status() {
  const fakeFetch = async () => ({
    ok: true,
    json: async () => ({ status: 'error', message: 'quota dépassé' }),
  });
  await assert.rejects(
    () => fetchGoldCandles('fake-key', fakeFetch),
    /quota dépassé/,
  );
  console.log('OK: test_fetchGoldCandles_rejects_on_error_status');
}

async function test_fetchGoldCandles_rejects_on_http_error() {
  const fakeFetch = async () => ({ ok: false, status: 429 });
  await assert.rejects(
    () => fetchGoldCandles('fake-key', fakeFetch),
    /429/,
  );
  console.log('OK: test_fetchGoldCandles_rejects_on_http_error');
}

function test_detectPivots_finds_high_and_low_with_k1() {
  // 5 bougies, K=1 : un pivot haut net à l'index 2 (14 > 10 des deux
  // côtés), un pivot bas net à l'index 3 (3 < 8 des deux côtés).
  // Calculé à la main, voir docs/superpowers/plans/2026-09-09-gold-scalping.md.
  const candles = [
    { high: 10, low: 8 },
    { high: 10, low: 8 },
    { high: 14, low: 8 },
    { high: 10, low: 3 },
    { high: 10, low: 8 },
  ];
  const pivots = detectPivots(candles, 1);
  assert.deepStrictEqual(pivots, [
    { index: 2, type: 'high', price: 14 },
    { index: 3, type: 'low', price: 3 },
  ]);
  console.log('OK: test_detectPivots_finds_high_and_low_with_k1');
}

function test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher() {
  // idx1 a High=10, égal à idx0 — pas strictement supérieur, donc pas
  // un pivot (la règle exige une inégalité stricte des deux côtés).
  const candles = [
    { high: 10, low: 5 },
    { high: 10, low: 5 },
    { high: 8, low: 5 },
  ];
  const pivots = detectPivots(candles, 1);
  assert.deepStrictEqual(pivots, []);
  console.log('OK: test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher');
}

function test_classifyTrend_haussier_on_rising_pivots() {
  // 2 pivots bas croissants (10, 12) ET 2 pivots hauts croissants (15, 18)
  // -> tendance haussière (règle du cours : creux croissants puis
  // sommets croissants, les deux exigés pour "haussier" franc en v1).
  const pivots = [
    { index: 0, type: 'low', price: 10 },
    { index: 1, type: 'high', price: 15 },
    { index: 2, type: 'low', price: 12 },
    { index: 3, type: 'high', price: 18 },
  ];
  assert.strictEqual(classifyTrend(pivots), 'haussier');
  console.log('OK: test_classifyTrend_haussier_on_rising_pivots');
}

function test_classifyTrend_neutre_when_pivots_disagree() {
  // Pivots bas croissants (10, 12) mais pivots hauts décroissants (18, 15)
  // -> ambigu, doit rester neutre (pas de tendance franche).
  const pivots = [
    { index: 0, type: 'low', price: 10 },
    { index: 1, type: 'high', price: 18 },
    { index: 2, type: 'low', price: 12 },
    { index: 3, type: 'high', price: 15 },
  ];
  assert.strictEqual(classifyTrend(pivots), 'neutre');
  console.log('OK: test_classifyTrend_neutre_when_pivots_disagree');
}

function test_classifyTrend_neutre_when_not_enough_pivots() {
  const pivots = [{ index: 0, type: 'low', price: 10 }];
  assert.strictEqual(classifyTrend(pivots), 'neutre');
  console.log('OK: test_classifyTrend_neutre_when_not_enough_pivots');
}

function test_currentLevels_picks_nearest_unbroken_pivots() {
  const pivots = [
    { index: 0, type: 'low', price: 95 },
    { index: 1, type: 'high', price: 105 },
    { index: 2, type: 'low', price: 98 },
    { index: 3, type: 'high', price: 110 },
  ];
  // Prix courant = 100 : support = dernier pivot bas sous 100 -> 98 ;
  // résistance = dernier pivot haut au-dessus de 100 -> 110.
  assert.deepStrictEqual(currentLevels(pivots, 100), { support: 98, resistance: 110 });
  console.log('OK: test_currentLevels_picks_nearest_unbroken_pivots');
}

function test_currentLevels_null_when_no_pivot_on_one_side() {
  const pivots = [{ index: 0, type: 'low', price: 98 }];
  assert.deepStrictEqual(currentLevels(pivots, 100), { support: 98, resistance: null });
  console.log('OK: test_currentLevels_null_when_no_pivot_on_one_side');
}

function test_computeRSI_all_gains_is_100() {
  // 15 clôtures, 14 hausses de +1 chacune -> aucune baisse -> RSI=100
  // (convention standard quand la moyenne des baisses est nulle).
  const closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24];
  assert.strictEqual(computeRSI(closes, 14), 100);
  console.log('OK: test_computeRSI_all_gains_is_100');
}

function test_computeRSI_all_losses_is_0() {
  const closes = [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10];
  assert.strictEqual(computeRSI(closes, 14), 0);
  console.log('OK: test_computeRSI_all_losses_is_0');
}

function test_computeRSI_balanced_alternating_is_50() {
  // 14 variations alternées +1/-1 (7 hausses, 7 baisses de même ampleur)
  // -> RS=1 -> RSI=50.
  const closes = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10];
  assert.strictEqual(computeRSI(closes, 14), 50);
  console.log('OK: test_computeRSI_balanced_alternating_is_50');
}

function test_computeMACD_constant_offset_on_linear_series() {
  // closes en progression arithmétique parfaite (+2 à chaque bougie).
  // Avec fast=2/slow=4/signal=2, la MACD line et la ligne signal
  // convergent toutes deux vers la constante 2 -> histogramme 0.
  // Calcul détaillé à la main dans docs/superpowers/plans/2026-09-09-gold-scalping.md.
  const closes = [10, 12, 14, 16, 18, 20, 22];
  const result = computeMACD(closes, 2, 4, 2);
  assert.deepStrictEqual(result, { macd: 2, signal: 2, histogram: 0 });
  console.log('OK: test_computeMACD_constant_offset_on_linear_series');
}

function test_computeBollinger_zero_variance() {
  const closes = [100, 100, 100, 100];
  assert.deepStrictEqual(computeBollinger(closes, 4, 2), { middle: 100, upper: 100, lower: 100 });
  console.log('OK: test_computeBollinger_zero_variance');
}

function test_computeBollinger_with_variance() {
  // closes=[0,0,4,4], période 4 : moyenne=2, écart-type population=2
  // -> upper=2+2*2=6, lower=2-2*2=-2.
  const closes = [0, 0, 4, 4];
  assert.deepStrictEqual(computeBollinger(closes, 4, 2), { middle: 2, upper: 6, lower: -2 });
  console.log('OK: test_computeBollinger_with_variance');
}

async function main() {
  await test_fetchGoldCandles_parses_and_reverses_to_chronological_order();
  await test_fetchGoldCandles_rejects_on_error_status();
  await test_fetchGoldCandles_rejects_on_http_error();
  test_detectPivots_finds_high_and_low_with_k1();
  test_detectPivots_rejects_equal_neighbor_as_not_strictly_higher();
  test_classifyTrend_haussier_on_rising_pivots();
  test_classifyTrend_neutre_when_pivots_disagree();
  test_classifyTrend_neutre_when_not_enough_pivots();
  test_currentLevels_picks_nearest_unbroken_pivots();
  test_currentLevels_null_when_no_pivot_on_one_side();
  test_computeRSI_all_gains_is_100();
  test_computeRSI_all_losses_is_0();
  test_computeRSI_balanced_alternating_is_50();
  test_computeMACD_constant_offset_on_linear_series();
  test_computeBollinger_zero_variance();
  test_computeBollinger_with_variance();
  console.log('Tous les tests scalping.test.js sont passés.');
}

main().catch(e => {
  console.error('ÉCHEC :', e);
  process.exit(1);
});
