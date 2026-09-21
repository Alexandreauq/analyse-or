// docs/portfolio.test.js
// Tests JS sans framework, exécutables via `node docs/portfolio.test.js`.
// Même convention que docs/scalping.test.js : assert natif de Node,
// fonctions test_* appelées explicitement dans main(), aucune dépendance.
const assert = require('assert');
const {
  PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
  loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
  computePositionPnL, computePortfolioTotals, findOpportunities,
  computePortfolioConcentration, computePortfolioHealth, computePortfolioAttribution,
  PORTFOLIO_CLOSED_STORAGE_KEY, loadClosedPortfolio, saveClosedPortfolio, closePosition,
  groupPriceHistoryByTicker, priceAtOrBefore, isPositionActiveOn, realValueForPosition,
  benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
  computeBenchmarkPerformanceCurve,
} = require('./portfolio.js');

// Mock localStorage minimal — Node n'a pas cet objet nativement.
function makeFakeStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = value; },
    _data: data,
  };
}

function test_validatePositionInput_accepts_positive_numbers() {
  assert.strictEqual(validatePositionInput(10, 650.5), null);
  console.log('OK: test_validatePositionInput_accepts_positive_numbers');
}

function test_validatePositionInput_rejects_non_positive_quantity() {
  assert.ok(validatePositionInput(0, 100).length > 0);
  assert.ok(validatePositionInput(-5, 100).length > 0);
  console.log('OK: test_validatePositionInput_rejects_non_positive_quantity');
}

function test_validatePositionInput_rejects_non_numeric() {
  assert.ok(validatePositionInput(NaN, 100).length > 0);
  assert.ok(validatePositionInput(10, undefined).length > 0);
  console.log('OK: test_validatePositionInput_rejects_non_numeric');
}

function test_createPosition_generates_stable_shape() {
  const pos = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  assert.strictEqual(pos.ticker, 'MC.PA');
  assert.strictEqual(pos.quantity, 10);
  assert.strictEqual(pos.buy_price, 650.0);
  assert.strictEqual(pos.buy_date, '2026-08-01');
  assert.ok(pos.id.startsWith('pos-1757500000000-'));
  console.log('OK: test_createPosition_generates_stable_shape');
}

function test_createPosition_two_calls_produce_different_ids() {
  const a = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  const b = createPosition('MC.PA', 10, 650.0, '2026-08-01', 1757500000000);
  assert.notStrictEqual(a.id, b.id);
  console.log('OK: test_createPosition_two_calls_produce_different_ids');
}

function test_loadPortfolio_returns_empty_array_when_storage_missing() {
  assert.deepStrictEqual(loadPortfolio(null), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_storage_missing');
}

function test_loadPortfolio_returns_empty_array_when_key_absent() {
  const storage = makeFakeStorage();
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_key_absent');
}

function test_loadPortfolio_returns_empty_array_on_corrupt_json() {
  const storage = makeFakeStorage({ [PORTFOLIO_STORAGE_KEY]: '{not valid json' });
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_on_corrupt_json');
}

function test_loadPortfolio_returns_empty_array_when_stored_value_not_array() {
  const storage = makeFakeStorage({ [PORTFOLIO_STORAGE_KEY]: '{"not":"an array"}' });
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_loadPortfolio_returns_empty_array_when_stored_value_not_array');
}

function test_savePortfolio_then_loadPortfolio_roundtrips() {
  const storage = makeFakeStorage();
  const positions = [{ id: 'pos-1', ticker: 'MC.PA', quantity: 5, buy_price: 600, buy_date: '2026-08-01' }];
  assert.strictEqual(savePortfolio(positions, storage), true);
  assert.deepStrictEqual(loadPortfolio(storage), positions);
  console.log('OK: test_savePortfolio_then_loadPortfolio_roundtrips');
}

function test_savePortfolio_returns_false_when_storage_throws() {
  const storage = {
    getItem: () => null,
    setItem: () => { throw new Error('QuotaExceededError'); },
  };
  assert.strictEqual(savePortfolio([{ id: 'pos-1' }], storage), false);
  console.log('OK: test_savePortfolio_returns_false_when_storage_throws');
}

function test_addPosition_appends_and_saves() {
  const storage = makeFakeStorage();
  const result = addPosition('MC.PA', 10, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 1);
  assert.strictEqual(result.positions[0].ticker, 'MC.PA');
  assert.deepStrictEqual(loadPortfolio(storage), result.positions);
  console.log('OK: test_addPosition_appends_and_saves');
}

function test_addPosition_two_lots_same_ticker_stay_independent() {
  const storage = makeFakeStorage();
  addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const result = addPosition('MC.PA', 5, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.positions.length, 2);
  assert.notStrictEqual(result.positions[0].id, result.positions[1].id);
  assert.strictEqual(result.positions[0].buy_price, 600.0);
  assert.strictEqual(result.positions[1].buy_price, 650.0);
  console.log('OK: test_addPosition_two_lots_same_ticker_stay_independent');
}

function test_addPosition_rejects_invalid_input_without_saving() {
  const storage = makeFakeStorage();
  const result = addPosition('MC.PA', -1, 650.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, false);
  assert.ok(result.error.length > 0);
  assert.deepStrictEqual(loadPortfolio(storage), []);
  console.log('OK: test_addPosition_rejects_invalid_input_without_saving');
}

function test_updatePosition_changes_only_targeted_position() {
  const storage = makeFakeStorage();
  const after1 = addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const after2 = addPosition('OR.PA', 3, 60.0, '2026-07-02', storage);
  const target = after2.positions[0].ticker === 'OR.PA' ? after2.positions[0] : after2.positions[1];
  const result = updatePosition(target.id, 4, 65.0, '2026-08-01', storage);
  assert.strictEqual(result.ok, true);
  const updated = result.positions.find(p => p.id === target.id);
  assert.strictEqual(updated.quantity, 4);
  assert.strictEqual(updated.buy_price, 65.0);
  assert.strictEqual(updated.buy_date, '2026-08-01');
  const untouched = result.positions.find(p => p.ticker === 'MC.PA');
  assert.strictEqual(untouched.quantity, 10);
  console.log('OK: test_updatePosition_changes_only_targeted_position');
}

function test_updatePosition_rejects_invalid_input() {
  const storage = makeFakeStorage();
  const after = addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const result = updatePosition(after.positions[0].id, 0, 600.0, '2026-07-01', storage);
  assert.strictEqual(result.ok, false);
  console.log('OK: test_updatePosition_rejects_invalid_input');
}

function test_updatePosition_returns_error_for_unknown_id() {
  const storage = makeFakeStorage();
  const result = updatePosition('does-not-exist', 1, 1, '2026-01-01', storage);
  assert.strictEqual(result.ok, false);
  console.log('OK: test_updatePosition_returns_error_for_unknown_id');
}

function test_removePosition_deletes_only_targeted_position() {
  const storage = makeFakeStorage();
  addPosition('MC.PA', 10, 600.0, '2026-07-01', storage);
  const after2 = addPosition('OR.PA', 3, 60.0, '2026-07-02', storage);
  const toRemove = after2.positions.find(p => p.ticker === 'OR.PA');
  const result = removePosition(toRemove.id, storage);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 1);
  assert.strictEqual(result.positions[0].ticker, 'MC.PA');
  console.log('OK: test_removePosition_deletes_only_targeted_position');
}

function test_close_position_moves_it_from_open_to_closed() {
  const storage = makeFakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  const id = positions[0].id;

  const result = closePosition(id, 95.5, '2026-09-15', storage);

  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 0);
  assert.strictEqual(result.closedPositions.length, 1);
  assert.strictEqual(result.closedPositions[0].sell_price, 95.5);
  assert.strictEqual(result.closedPositions[0].sell_date, '2026-09-15');
  assert.strictEqual(result.closedPositions[0].ticker, 'MC.PA');
  assert.strictEqual(loadPortfolio(storage).length, 0);
  assert.strictEqual(loadClosedPortfolio(storage).length, 1);
  console.log('OK: test_close_position_moves_it_from_open_to_closed');
}

function test_close_position_rejects_non_positive_sell_price() {
  const storage = makeFakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  const result = closePosition(positions[0].id, -1, '2026-09-15', storage);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(loadPortfolio(storage).length, 1);
  console.log('OK: test_close_position_rejects_non_positive_sell_price');
}

function test_close_position_returns_error_for_unknown_id() {
  const storage = makeFakeStorage();
  const result = closePosition('id-inconnu', 95.5, '2026-09-15', storage);
  assert.strictEqual(result.ok, false);
  console.log('OK: test_close_position_returns_error_for_unknown_id');
}

function test_remove_position_never_touches_the_closed_list() {
  const storage = makeFakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  removePosition(positions[0].id, storage);
  assert.strictEqual(loadPortfolio(storage).length, 0);
  assert.strictEqual(loadClosedPortfolio(storage).length, 0);
  console.log('OK: test_remove_position_never_touches_the_closed_list');
}

function test_load_closed_portfolio_returns_empty_array_when_storage_absent() {
  assert.deepStrictEqual(loadClosedPortfolio(null), []);
  console.log('OK: test_load_closed_portfolio_returns_empty_array_when_storage_absent');
}

function test_load_closed_portfolio_degrades_to_empty_array_on_corrupt_json() {
  const storage = makeFakeStorage();
  storage.setItem(PORTFOLIO_CLOSED_STORAGE_KEY, 'pas du json');
  assert.deepStrictEqual(loadClosedPortfolio(storage), []);
  console.log('OK: test_load_closed_portfolio_degrades_to_empty_array_on_corrupt_json');
}

function test_computePositionPnL_gain() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  const result = computePositionPnL(position, 660);
  assert.strictEqual(result.value, 6600);
  assert.strictEqual(result.pnlAbs, 600);
  assert.ok(Math.abs(result.pnlPct - 10) < 1e-9);
  console.log('OK: test_computePositionPnL_gain');
}

function test_computePositionPnL_loss() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  const result = computePositionPnL(position, 540);
  assert.strictEqual(result.value, 5400);
  assert.strictEqual(result.pnlAbs, -600);
  assert.ok(Math.abs(result.pnlPct - -10) < 1e-9);
  console.log('OK: test_computePositionPnL_loss');
}

function test_computePositionPnL_null_when_price_unavailable() {
  const position = { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' };
  assert.strictEqual(computePositionPnL(position, null), null);
  assert.strictEqual(computePositionPnL(position, undefined), null);
  assert.strictEqual(computePositionPnL(position, NaN), null);
  console.log('OK: test_computePositionPnL_null_when_price_unavailable');
}

function test_computePortfolioTotals_splits_eur_and_usd() {
  const positions = [
    { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' },
    { id: 'p2', ticker: 'AAPL', quantity: 5, buy_price: 180, buy_date: '2026-07-01' },
  ];
  const companiesByTicker = {
    'MC.PA': { ticker: 'MC.PA', index: 'CAC40', current_price: 660 },
    'AAPL': { ticker: 'AAPL', index: 'NASDAQ', current_price: 200 },
  };
  const currencyByIndex = { CAC40: 'EUR', NASDAQ: 'USD' };
  const totals = computePortfolioTotals(positions, companiesByTicker, currencyByIndex);
  assert.strictEqual(totals.EUR.value, 6600);
  assert.strictEqual(totals.EUR.pnlAbs, 600);
  assert.strictEqual(totals.USD.value, 1000);
  assert.strictEqual(totals.USD.pnlAbs, 100);
  console.log('OK: test_computePortfolioTotals_splits_eur_and_usd');
}

function test_computePortfolioTotals_skips_position_with_unknown_ticker() {
  const positions = [{ id: 'p1', ticker: 'DELISTED', quantity: 10, buy_price: 600, buy_date: '2026-07-01' }];
  const totals = computePortfolioTotals(positions, {}, { CAC40: 'EUR' });
  assert.strictEqual(totals.EUR.value, 0);
  assert.strictEqual(totals.USD.value, 0);
  console.log('OK: test_computePortfolioTotals_skips_position_with_unknown_ticker');
}

function test_computePortfolioTotals_empty_positions_returns_zeroed_totals() {
  const totals = computePortfolioTotals([], {}, {});
  assert.deepStrictEqual(totals, { EUR: { value: 0, pnlAbs: 0 }, USD: { value: 0, pnlAbs: 0 } });
  console.log('OK: test_computePortfolioTotals_empty_positions_returns_zeroed_totals');
}

function test_findOpportunities_returns_entree_alerts_not_held() {
  const companies = [
    { ticker: 'MC.PA', alerts: [{ kind: 'entree' }] },
    { ticker: 'OR.PA', alerts: [{ kind: 'risque' }] },
    { ticker: 'AAPL', alerts: [{ kind: 'entree' }] },
  ];
  const result = findOpportunities(companies, new Set(['AAPL']));
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].ticker, 'MC.PA');
  console.log('OK: test_findOpportunities_returns_entree_alerts_not_held');
}

function test_findOpportunities_excludes_companies_without_entree_alert() {
  const companies = [{ ticker: 'MC.PA', alerts: [] }];
  const result = findOpportunities(companies, new Set());
  assert.strictEqual(result.length, 0);
  console.log('OK: test_findOpportunities_excludes_companies_without_entree_alert');
}

function test_findOpportunities_handles_missing_alerts_field() {
  const companies = [{ ticker: 'MC.PA' }];
  const result = findOpportunities(companies, new Set());
  assert.strictEqual(result.length, 0);
  console.log('OK: test_findOpportunities_handles_missing_alerts_field');
}

function test_computePortfolioConcentration_counts_by_index_and_sector() {
  const positions = [
    { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' },
    { id: 'p2', ticker: 'OR.PA', quantity: 4, buy_price: 50, buy_date: '2026-07-01' },
    { id: 'p3', ticker: 'AAPL', quantity: 5, buy_price: 180, buy_date: '2026-07-01' },
  ];
  const companiesByTicker = {
    'MC.PA': { ticker: 'MC.PA', index: 'CAC40', sector: 'Consumer Cyclical', current_price: 660 },
    'OR.PA': { ticker: 'OR.PA', index: 'CAC40', sector: 'Consumer Cyclical', current_price: 55 },
    'AAPL': { ticker: 'AAPL', index: 'NASDAQ', sector: 'Technology', current_price: 200 },
  };
  const currencyByIndex = { CAC40: 'EUR', NASDAQ: 'USD' };
  const result = computePortfolioConcentration(positions, companiesByTicker, currencyByIndex);
  assert.strictEqual(result.totalPositions, 3);
  assert.deepStrictEqual(result.byIndex, { CAC40: 2, NASDAQ: 1 });
  assert.deepStrictEqual(result.bySector, { 'Consumer Cyclical': 2, Technology: 1 });
  assert.strictEqual(result.byCurrency.EUR, 660 * 10 + 55 * 4);
  assert.strictEqual(result.byCurrency.USD, 200 * 5);
  console.log('OK: test_computePortfolioConcentration_counts_by_index_and_sector');
}

function test_computePortfolioConcentration_skips_unknown_ticker() {
  const positions = [{ id: 'p1', ticker: 'DELISTED', quantity: 10, buy_price: 600, buy_date: '2026-07-01' }];
  const result = computePortfolioConcentration(positions, {}, {});
  assert.strictEqual(result.totalPositions, 0);
  assert.deepStrictEqual(result.byIndex, {});
  assert.deepStrictEqual(result.byCurrency, {});
  console.log('OK: test_computePortfolioConcentration_skips_unknown_ticker');
}

function test_computePortfolioConcentration_defaults_missing_sector_to_inconnu() {
  const positions = [{ id: 'p1', ticker: 'MC.PA', quantity: 1, buy_price: 600, buy_date: '2026-07-01' }];
  const companiesByTicker = { 'MC.PA': { ticker: 'MC.PA', index: 'CAC40', current_price: 660 } };
  const result = computePortfolioConcentration(positions, companiesByTicker, { CAC40: 'EUR' });
  assert.deepStrictEqual(result.bySector, { Inconnu: 1 });
  console.log('OK: test_computePortfolioConcentration_defaults_missing_sector_to_inconnu');
}

function test_computePortfolioHealth_averages_score_and_counts_alerts() {
  const positions = [
    { id: 'p1', ticker: 'MC.PA', quantity: 1, buy_price: 600, buy_date: '2026-07-01' },
    { id: 'p2', ticker: 'OR.PA', quantity: 1, buy_price: 50, buy_date: '2026-07-01' },
  ];
  const companiesByTicker = {
    'MC.PA': { ticker: 'MC.PA', score: 20, alerts: [{ kind: 'risque' }] },
    'OR.PA': { ticker: 'OR.PA', score: 10, alerts: [{ kind: 'actu_majeure' }, { kind: 'risque' }] },
  };
  const result = computePortfolioHealth(positions, companiesByTicker);
  assert.strictEqual(result.avgScore, 15);
  assert.strictEqual(result.scoredPositions, 2);
  assert.strictEqual(result.riskAlertCount, 2);
  assert.strictEqual(result.majorNewsAlertCount, 1);
  console.log('OK: test_computePortfolioHealth_averages_score_and_counts_alerts');
}

function test_computePortfolioHealth_null_avg_score_when_no_scored_positions() {
  const result = computePortfolioHealth([{ id: 'p1', ticker: 'DELISTED', quantity: 1, buy_price: 1, buy_date: '2026-07-01' }], {});
  assert.strictEqual(result.avgScore, null);
  assert.strictEqual(result.scoredPositions, 0);
  console.log('OK: test_computePortfolioHealth_null_avg_score_when_no_scored_positions');
}

function test_computePortfolioAttribution_sorts_by_pnl_desc() {
  const positions = [
    { id: 'p1', ticker: 'MC.PA', quantity: 10, buy_price: 600, buy_date: '2026-07-01' },
    { id: 'p2', ticker: 'AAPL', quantity: 5, buy_price: 200, buy_date: '2026-07-01' },
  ];
  const companiesByTicker = {
    'MC.PA': { ticker: 'MC.PA', name: 'LVMH', index: 'CAC40', current_price: 660 },
    'AAPL': { ticker: 'AAPL', name: 'Apple', index: 'NASDAQ', current_price: 180 },
  };
  const result = computePortfolioAttribution(positions, companiesByTicker);
  assert.strictEqual(result.length, 2);
  assert.strictEqual(result[0].ticker, 'MC.PA');
  assert.strictEqual(result[0].pnlAbs, 600);
  assert.strictEqual(result[1].ticker, 'AAPL');
  assert.strictEqual(result[1].pnlAbs, -100);
  console.log('OK: test_computePortfolioAttribution_sorts_by_pnl_desc');
}

function test_computePortfolioAttribution_skips_position_without_price() {
  const positions = [{ id: 'p1', ticker: 'MC.PA', quantity: 1, buy_price: 600, buy_date: '2026-07-01' }];
  const companiesByTicker = { 'MC.PA': { ticker: 'MC.PA', name: 'LVMH', current_price: null } };
  const result = computePortfolioAttribution(positions, companiesByTicker);
  assert.strictEqual(result.length, 0);
  console.log('OK: test_computePortfolioAttribution_skips_position_without_price');
}

function test_group_price_history_by_ticker_sorts_each_group_by_date() {
  const history = [
    { date: '2026-09-15', ticker: 'MC.PA', price: 110 },
    { date: '2026-09-10', ticker: 'MC.PA', price: 105 },
    { date: '2026-09-12', ticker: '^FCHI', price: 7800 },
  ];
  const grouped = groupPriceHistoryByTicker(history);
  assert.deepStrictEqual(grouped['MC.PA'].map(e => e.date), ['2026-09-10', '2026-09-15']);
  assert.strictEqual(grouped['^FCHI'].length, 1);
}

function test_price_at_or_before_returns_the_latest_entry_not_after_the_date() {
  const grouped = { 'MC.PA': [{ date: '2026-09-10', price: 100 }, { date: '2026-09-15', price: 110 }] };
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-12'), 100);
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-20'), 110);
}

function test_price_at_or_before_never_looks_into_the_future() {
  const grouped = { 'MC.PA': [{ date: '2026-09-10', price: 100 }] };
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-05'), null);
}

function test_price_at_or_before_returns_null_for_an_unknown_ticker() {
  assert.strictEqual(priceAtOrBefore({}, 'INCONNU.PA', '2026-09-12'), null);
}

function test_is_position_active_on_respects_buy_and_sell_dates() {
  const open = { buy_date: '2026-01-15' };
  assert.strictEqual(isPositionActiveOn(open, '2026-01-14'), false);
  assert.strictEqual(isPositionActiveOn(open, '2026-01-15'), true);
  assert.strictEqual(isPositionActiveOn(open, '2026-09-21'), true);

  const closed = { buy_date: '2026-01-15', sell_date: '2026-06-01' };
  assert.strictEqual(isPositionActiveOn(closed, '2026-05-31'), true);
  assert.strictEqual(isPositionActiveOn(closed, '2026-06-01'), true);
  assert.strictEqual(isPositionActiveOn(closed, '2026-06-02'), false);
}

function test_compute_performance_curve_weights_by_invested_capital_not_naive_average() {
  const positions = [
    { ticker: 'A', buy_date: '2026-01-01', quantity: 100, buy_price: 1.0 },   // cout 100
    { ticker: 'B', buy_date: '2026-01-01', quantity: 1, buy_price: 1000.0 },  // cout 1000
  ];
  const priceHistoryByTicker = {
    A: [{ date: '2026-02-01', price: 2.0 }],   // +100%
    B: [{ date: '2026-02-01', price: 1010.0 }], // +1%
  };
  const curve = computePerformanceCurve(positions, priceHistoryByTicker, realValueForPosition);
  assert.strictEqual(curve.length, 1);
  // pondere : (100 + 10) / (100 + 1000) * 100 = 10.0, PAS (100+1)/2 = 50.5 (moyenne naive)
  assert.ok(Math.abs(curve[0].pnlPct - 10.0) < 0.001);
}

function test_compute_performance_curve_excludes_a_position_with_no_known_price_on_a_date_without_dropping_the_date() {
  const positions = [
    { ticker: 'A', buy_date: '2026-01-01', quantity: 10, buy_price: 10.0 },
    { ticker: 'B', buy_date: '2026-03-01', quantity: 10, buy_price: 20.0 }, // pas encore actif au 02-01
  ];
  const priceHistoryByTicker = {
    A: [{ date: '2026-02-01', price: 11.0 }],
    B: [{ date: '2026-02-01', price: 21.0 }],
  };
  const curve = computePerformanceCurve(positions, priceHistoryByTicker, realValueForPosition);
  assert.strictEqual(curve.length, 1);
  // seule A compte au 02-01 : (11-10)*10 / (10*10) * 100 = 10%
  assert.ok(Math.abs(curve[0].pnlPct - 10.0) < 0.001);
}

function test_compute_portfolio_performance_curves_splits_by_currency_and_skips_empty_currency() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 5, buy_price: 90.0 }];
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const priceHistoryByTicker = { 'MC.PA': [{ date: '2026-02-01', price: 100.0 }] };
  const curves = computePortfolioPerformanceCurves([], [], companiesByTicker, currencyByIndex, priceHistoryByTicker);
  assert.deepStrictEqual(curves, { EUR: [], USD: [] });

  const curvesWithPosition = computePortfolioPerformanceCurves(
    openPositions, [], companiesByTicker, currencyByIndex, priceHistoryByTicker);
  assert.strictEqual(curvesWithPosition.EUR.length, 1);
  assert.deepStrictEqual(curvesWithPosition.USD, []);
}

function test_compute_benchmark_performance_curve_scales_cost_by_index_ratio_since_buy_date() {
  const positions = [{ ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 5, buy_price: 90.0 }]; // cost = 450
  const priceHistoryByTicker = {
    'MC.PA': [{ date: '2026-02-01', price: 90.0 }],
    '^FCHI': [
      { date: '2026-01-01', price: 8000.0 },
      { date: '2026-02-01', price: 8800.0 }, // +10% depuis l'achat
    ],
  };
  const benchmarkCurve = computeBenchmarkPerformanceCurve(positions, '^FCHI', priceHistoryByTicker);
  assert.strictEqual(benchmarkCurve.length, 1);
  assert.strictEqual(benchmarkCurve[0].date, '2026-02-01');
  // 450 * (8800/8000) = 495, pnlPct = (495-450)/450*100 = 10.0
  assert.ok(Math.abs(benchmarkCurve[0].pnlPct - 10.0) < 0.001, `expected ~10.0, got ${benchmarkCurve[0].pnlPct}`);
}

function test_compute_benchmark_performance_curve_returns_null_value_when_index_price_at_buy_date_is_unknown() {
  const positions = [{ ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 5, buy_price: 90.0 }];
  const priceHistoryByTicker = {
    'MC.PA': [{ date: '2026-02-01', price: 90.0 }],
    '^FCHI': [{ date: '2026-02-01', price: 8800.0 }], // aucune entree a la date d'achat ou avant
  };
  const benchmarkCurve = computeBenchmarkPerformanceCurve(positions, '^FCHI', priceHistoryByTicker);
  assert.strictEqual(benchmarkCurve.length, 0); // position exclue de cette date faute de prix d'indice au buy_date
}

function main() {
  test_validatePositionInput_accepts_positive_numbers();
  test_validatePositionInput_rejects_non_positive_quantity();
  test_validatePositionInput_rejects_non_numeric();
  test_createPosition_generates_stable_shape();
  test_createPosition_two_calls_produce_different_ids();
  test_loadPortfolio_returns_empty_array_when_storage_missing();
  test_loadPortfolio_returns_empty_array_when_key_absent();
  test_loadPortfolio_returns_empty_array_on_corrupt_json();
  test_loadPortfolio_returns_empty_array_when_stored_value_not_array();
  test_savePortfolio_then_loadPortfolio_roundtrips();
  test_savePortfolio_returns_false_when_storage_throws();
  test_addPosition_appends_and_saves();
  test_addPosition_two_lots_same_ticker_stay_independent();
  test_addPosition_rejects_invalid_input_without_saving();
  test_updatePosition_changes_only_targeted_position();
  test_updatePosition_rejects_invalid_input();
  test_updatePosition_returns_error_for_unknown_id();
  test_removePosition_deletes_only_targeted_position();
  test_close_position_moves_it_from_open_to_closed();
  test_close_position_rejects_non_positive_sell_price();
  test_close_position_returns_error_for_unknown_id();
  test_remove_position_never_touches_the_closed_list();
  test_load_closed_portfolio_returns_empty_array_when_storage_absent();
  test_load_closed_portfolio_degrades_to_empty_array_on_corrupt_json();
  test_computePositionPnL_gain();
  test_computePositionPnL_loss();
  test_computePositionPnL_null_when_price_unavailable();
  test_computePortfolioTotals_splits_eur_and_usd();
  test_computePortfolioTotals_skips_position_with_unknown_ticker();
  test_computePortfolioTotals_empty_positions_returns_zeroed_totals();
  test_findOpportunities_returns_entree_alerts_not_held();
  test_findOpportunities_excludes_companies_without_entree_alert();
  test_findOpportunities_handles_missing_alerts_field();
  test_computePortfolioConcentration_counts_by_index_and_sector();
  test_computePortfolioConcentration_skips_unknown_ticker();
  test_computePortfolioConcentration_defaults_missing_sector_to_inconnu();
  test_computePortfolioHealth_averages_score_and_counts_alerts();
  test_computePortfolioHealth_null_avg_score_when_no_scored_positions();
  test_computePortfolioAttribution_sorts_by_pnl_desc();
  test_computePortfolioAttribution_skips_position_without_price();
  test_group_price_history_by_ticker_sorts_each_group_by_date();
  test_price_at_or_before_returns_the_latest_entry_not_after_the_date();
  test_price_at_or_before_never_looks_into_the_future();
  test_price_at_or_before_returns_null_for_an_unknown_ticker();
  test_is_position_active_on_respects_buy_and_sell_dates();
  test_compute_performance_curve_weights_by_invested_capital_not_naive_average();
  test_compute_performance_curve_excludes_a_position_with_no_known_price_on_a_date_without_dropping_the_date();
  test_compute_portfolio_performance_curves_splits_by_currency_and_skips_empty_currency();
  test_compute_benchmark_performance_curve_scales_cost_by_index_ratio_since_buy_date();
  test_compute_benchmark_performance_curve_returns_null_value_when_index_price_at_buy_date_is_unknown();
  console.log('Tous les tests portfolio.test.js sont passés.');
}

main();
