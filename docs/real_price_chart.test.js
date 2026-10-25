// docs/real_price_chart.test.js
// Tests JS sans framework, exécutables via `node docs/real_price_chart.test.js`
// (voir docs/scalping.test.js pour la convention : `node docs/*.test.js` est
// le seul moyen d'exécuter réellement ces tests, aucune machine de dev n'a
// Node.js installé dans ce projet).
const assert = require('assert');
const {
  REAL_PRICE_CHART_TIMEFRAMES,
  formatRealPriceChartMonthLabel,
  sliceRealPriceChartByMonths,
  sliceRealPriceChartByDays,
  buildRealPriceChartSvgPaths,
  buildRealPriceChartGridlines,
  buildRealPriceChartScale,
} = require('./real_price_chart.js');

function test_formatRealPriceChartMonthLabel_formats_french_short_months() {
  assert.strictEqual(formatRealPriceChartMonthLabel('2026-10-23'), 'oct. 2026');
  assert.strictEqual(formatRealPriceChartMonthLabel('2020-01-05'), 'janv. 2020');
  console.log('OK: test_formatRealPriceChartMonthLabel_formats_french_short_months');
}

function test_sliceRealPriceChartByMonths_keeps_only_the_recent_window() {
  const pairs = [
    ['2026-01-01', 10], ['2026-04-01', 11], ['2026-07-01', 12],
    ['2026-09-01', 13], ['2026-10-01', 14], ['2026-10-23', 15],
  ];
  const sliced = sliceRealPriceChartByMonths(pairs, 3);
  // 3 mois avant 2026-10-23 -> coupure 2026-07-23 : seuls 2026-09-01,
  // 2026-10-01, 2026-10-23 sont >= a cette coupure.
  assert.deepStrictEqual(sliced, [['2026-09-01', 13], ['2026-10-01', 14], ['2026-10-23', 15]]);
  console.log('OK: test_sliceRealPriceChartByMonths_keeps_only_the_recent_window');
}

function test_sliceRealPriceChartByMonths_null_returns_everything() {
  const pairs = [['2020-01-01', 1], ['2026-10-23', 2]];
  assert.deepStrictEqual(sliceRealPriceChartByMonths(pairs, null), pairs);
  console.log('OK: test_sliceRealPriceChartByMonths_null_returns_everything');
}

function test_sliceRealPriceChartByMonths_falls_back_to_last_two_points_when_window_too_narrow() {
  // Historique qui vient de commencer il y a 1 semaine : une fenetre "3
  // mois" ne doit jamais rendre un graphique vide ou a un seul point.
  const pairs = [['2026-10-16', 10], ['2026-10-23', 11]];
  const sliced = sliceRealPriceChartByMonths(pairs, 3);
  assert.deepStrictEqual(sliced, pairs);
  console.log('OK: test_sliceRealPriceChartByMonths_falls_back_to_last_two_points_when_window_too_narrow');
}

function test_buildRealPriceChartSvgPaths_scales_to_the_viewbox() {
  const { line, area } = buildRealPriceChartSvgPaths([10, 20, 10], 100, 50, 0);
  // Min=10 -> bas du viewBox (y=50), max=20 -> haut (y=0).
  assert.strictEqual(line, 'M 0.0 50.0 L 50.0 0.0 L 100.0 50.0');
  assert.ok(area.startsWith(line));
  assert.ok(area.endsWith('L 100.0 50 L 0.0 50 Z'));
  console.log('OK: test_buildRealPriceChartSvgPaths_scales_to_the_viewbox');
}

function test_buildRealPriceChartSvgPaths_handles_a_flat_series_without_dividing_by_zero() {
  const { line } = buildRealPriceChartSvgPaths([5, 5, 5], 100, 50, 0);
  assert.ok(!line.includes('NaN'));
  console.log('OK: test_buildRealPriceChartSvgPaths_handles_a_flat_series_without_dividing_by_zero');
}

function test_REAL_PRICE_CHART_TIMEFRAMES_has_the_six_expected_options() {
  assert.deepStrictEqual(
    REAL_PRICE_CHART_TIMEFRAMES.map((t) => t.key),
    ['1j', '3m', '6m', '1a', '3a', 'all'],
  );
  console.log('OK: test_REAL_PRICE_CHART_TIMEFRAMES_has_the_six_expected_options');
}

function test_sliceRealPriceChartByDays_keeps_only_the_last_day() {
  const pairs = [
    ['2026-10-20', 10], ['2026-10-23', 11], ['2026-10-24', 12], ['2026-10-25', 13],
  ];
  const sliced = sliceRealPriceChartByDays(pairs, 1);
  // 1 jour avant 2026-10-25 -> coupure 2026-10-24 : seuls 2026-10-24 et
  // 2026-10-25 sont >= a cette coupure.
  assert.deepStrictEqual(sliced, [['2026-10-24', 12], ['2026-10-25', 13]]);
  console.log('OK: test_sliceRealPriceChartByDays_keeps_only_the_last_day');
}

function test_sliceRealPriceChartByDays_falls_back_to_last_two_points_across_a_weekend() {
  // Lundi : "1 jour avant" tombe un dimanche, sans donnee de marche --
  // doit quand meme renvoyer les 2 derniers points reels (vendredi + lundi).
  const pairs = [['2026-10-16', 10], ['2026-10-19', 11]]; // vendredi, lundi
  const sliced = sliceRealPriceChartByDays(pairs, 1);
  assert.deepStrictEqual(sliced, pairs);
  console.log('OK: test_sliceRealPriceChartByDays_falls_back_to_last_two_points_across_a_weekend');
}

function test_buildRealPriceChartGridlines_returns_evenly_spaced_levels_from_max_to_min() {
  assert.deepStrictEqual(buildRealPriceChartGridlines(0, 100, 5), [100, 75, 50, 25, 0]);
  assert.deepStrictEqual(buildRealPriceChartGridlines(10, 10, 3), [10, 10, 10]);
  console.log('OK: test_buildRealPriceChartGridlines_returns_evenly_spaced_levels_from_max_to_min');
}

function test_buildRealPriceChartScale_maps_index_and_value_to_pixels() {
  const scale = buildRealPriceChartScale([10, 20, 10], 100, 50, 0);
  assert.strictEqual(scale.min, 10);
  assert.strictEqual(scale.max, 20);
  assert.strictEqual(scale.x(0), 0);
  assert.strictEqual(scale.x(2), 100);
  assert.strictEqual(scale.y(20), 0);
  assert.strictEqual(scale.y(10), 50);
  console.log('OK: test_buildRealPriceChartScale_maps_index_and_value_to_pixels');
}

function test_buildRealPriceChartScale_handles_a_single_point_without_dividing_by_zero() {
  const scale = buildRealPriceChartScale([42], 100, 50, 0);
  assert.strictEqual(scale.x(0), 0);
  assert.ok(!Number.isNaN(scale.y(42)));
  console.log('OK: test_buildRealPriceChartScale_handles_a_single_point_without_dividing_by_zero');
}

function main() {
  test_formatRealPriceChartMonthLabel_formats_french_short_months();
  test_sliceRealPriceChartByMonths_keeps_only_the_recent_window();
  test_sliceRealPriceChartByMonths_null_returns_everything();
  test_sliceRealPriceChartByMonths_falls_back_to_last_two_points_when_window_too_narrow();
  test_buildRealPriceChartSvgPaths_scales_to_the_viewbox();
  test_buildRealPriceChartSvgPaths_handles_a_flat_series_without_dividing_by_zero();
  test_REAL_PRICE_CHART_TIMEFRAMES_has_the_six_expected_options();
  test_sliceRealPriceChartByDays_keeps_only_the_last_day();
  test_sliceRealPriceChartByDays_falls_back_to_last_two_points_across_a_weekend();
  test_buildRealPriceChartGridlines_returns_evenly_spaced_levels_from_max_to_min();
  test_buildRealPriceChartScale_maps_index_and_value_to_pixels();
  test_buildRealPriceChartScale_handles_a_single_point_without_dividing_by_zero();
  console.log('Tous les tests real_price_chart.test.js sont passés.');
}

main();
