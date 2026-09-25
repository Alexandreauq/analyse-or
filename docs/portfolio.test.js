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
  groupDividendHistoryByTicker, computeDividendsReceived, computeProjectedDividendIncome,
  computeYieldOnCost,
  resampleCurveWeekly, computePeriodicChanges, computePortfolioVolatility,
  computeMaxDrawdown, computeSharpeRatio, computePortfolioRiskMetrics,
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

function test_close_position_rejects_empty_sell_date() {
  const storage = makeFakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  const result = closePosition(positions[0].id, 95.5, '', storage);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(loadPortfolio(storage).length, 1);
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

function test_group_dividend_history_by_ticker_sorts_each_group_by_date() {
  const history = [
    { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 },
    { date: '2025-06-15', ticker: 'MC.PA', amount: 3.40 },
    { date: '2026-03-01', ticker: 'AAPL', amount: 0.25 },
  ];
  const grouped = groupDividendHistoryByTicker(history);
  assert.deepStrictEqual(grouped['MC.PA'].map(e => e.date), ['2025-06-15', '2026-06-15']);
  assert.strictEqual(grouped['AAPL'].length, 1);
  console.log('OK: test_group_dividend_history_by_ticker_sorts_each_group_by_date');
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

function test_compute_dividends_received_only_counts_payments_within_holding_period() {
  const positions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', quantity: 10 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2025-12-01', ticker: 'MC.PA', amount: 3.0 },  // avant l'achat, exclu
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // apres l'achat, inclus
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 35.5) < 0.001); // 3.55 * 10
  assert.strictEqual(received.USD, 0);
  console.log('OK: test_compute_dividends_received_only_counts_payments_within_holding_period');
}

function test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold() {
  const closedPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', sell_date: '2026-03-01', quantity: 10 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2026-02-01', ticker: 'MC.PA', amount: 3.0 },  // pendant la detention, inclus
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // apres la vente, exclu
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(closedPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001); // 3.0 * 10
  console.log('OK: test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold');
}

function test_compute_dividends_received_splits_by_currency() {
  const positions = [
    { id: '1', ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 10 },
    { id: '2', ticker: 'AAPL', buy_date: '2026-01-01', quantity: 5 },
  ];
  const dividendHistoryByTicker = {
    'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 3.0 }],
    'AAPL': [{ date: '2026-06-15', ticker: 'AAPL', amount: 0.25 }],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' }, 'AAPL': { index: 'NASDAQ' } };
  const currencyByIndex = { CAC40: 'EUR', NASDAQ: 'USD' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001);
  assert.ok(Math.abs(received.USD - 1.25) < 0.001);
  console.log('OK: test_compute_dividends_received_splits_by_currency');
}

function test_compute_dividends_received_skips_position_with_unknown_ticker() {
  const positions = [{ id: '1', ticker: 'RETIRE.PA', buy_date: '2026-01-01', quantity: 10 }];
  const dividendHistoryByTicker = { 'RETIRE.PA': [{ date: '2026-06-15', ticker: 'RETIRE.PA', amount: 3.0 }] };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, {}, {});
  assert.deepStrictEqual(received, { EUR: 0, USD: 0 });
  console.log('OK: test_compute_dividends_received_skips_position_with_unknown_ticker');
}

function test_compute_dividends_received_excludes_payment_exactly_on_the_buy_date() {
  // La Series yfinance est indexee par date EX-DIVIDENDE : il faut avoir
  // detenu la position AVANT cette date (achat strictement anterieur),
  // pas seulement CE jour-la, pour avoir droit au paiement -- acheter le
  // jour ex-dividende n'ouvre pas droit au dividende.
  const positions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', quantity: 10 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-01-15', ticker: 'MC.PA', amount: 3.0 }] }; // exactement buy_date, exclu
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.strictEqual(received.EUR, 0);
  console.log('OK: test_compute_dividends_received_excludes_payment_exactly_on_the_buy_date');
}

function test_compute_dividends_received_includes_payment_the_day_after_the_buy_date() {
  const positions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', quantity: 10 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-01-16', ticker: 'MC.PA', amount: 3.0 }] }; // lendemain de buy_date, inclus
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001);
  console.log('OK: test_compute_dividends_received_includes_payment_the_day_after_the_buy_date');
}

function test_compute_dividends_received_excludes_payment_the_day_before_the_buy_date() {
  const positions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', quantity: 10 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-01-14', ticker: 'MC.PA', amount: 3.0 }] }; // veille de buy_date, exclu
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.strictEqual(received.EUR, 0);
  console.log('OK: test_compute_dividends_received_excludes_payment_the_day_before_the_buy_date');
}

function test_compute_dividends_received_includes_payment_exactly_on_the_sell_date() {
  // Cote vente, la convention reste isPositionActiveOn (>=) : vendre LE
  // jour ex-dividende ouvre bien droit au paiement, contrairement au cote
  // achat.
  const closedPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', sell_date: '2026-03-01', quantity: 10 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-03-01', ticker: 'MC.PA', amount: 3.0 }] }; // exactement sell_date, inclus
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(closedPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001);
  console.log('OK: test_compute_dividends_received_includes_payment_exactly_on_the_sell_date');
}

function test_compute_projected_dividend_income_uses_a_365_day_ttm_window() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2025-01-01', ticker: 'MC.PA', amount: 3.0 },  // > 365 jours avant today, exclu
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // dans la fenetre, inclus
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.ok(Math.abs(income.EUR.annual - 35.5) < 0.001); // 3.55 * 10
  console.log('OK: test_compute_projected_dividend_income_uses_a_365_day_ttm_window');
}

function test_compute_projected_dividend_income_excludes_payment_exactly_on_the_ttm_cutoff_day() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2025-09-21', ticker: 'MC.PA', amount: 5.0 }] }; // exactement 365 jours avant today, exclu
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.strictEqual(income.EUR.annual, 0);
  console.log('OK: test_compute_projected_dividend_income_excludes_payment_exactly_on_the_ttm_cutoff_day');
}

function test_compute_projected_dividend_income_includes_payment_one_day_inside_the_ttm_window() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2025-09-22', ticker: 'MC.PA', amount: 5.0 }] }; // 364 jours avant today, inclus
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.ok(Math.abs(income.EUR.annual - 50.0) < 0.001);
  console.log('OK: test_compute_projected_dividend_income_includes_payment_one_day_inside_the_ttm_window');
}

function test_compute_projected_dividend_income_monthly_is_annual_over_twelve() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 12.0 }] };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.ok(Math.abs(income.EUR.monthly - income.EUR.annual / 12) < 0.0001);
  assert.ok(Math.abs(income.EUR.annual - 120.0) < 0.001); // 12.0 * 10
  console.log('OK: test_compute_projected_dividend_income_monthly_is_annual_over_twelve');
}

function test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history() {
  const openPositions = [{ id: '1', ticker: 'GROWTH.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const companiesByTicker = { 'GROWTH.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(openPositions, {}, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.deepStrictEqual(income, { EUR: { annual: 0, monthly: 0 }, USD: { annual: 0, monthly: 0 } });
  console.log('OK: test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history');
}

function test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 100.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 5.0 }] };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const result = computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, '2026-09-21');
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].ticker, 'MC.PA');
  assert.ok(Math.abs(result[0].ttmPerShare - 5.0) < 0.001);
  assert.ok(Math.abs(result[0].yieldOnCost - 5.0) < 0.001); // 5.0 / 100.0 * 100
  assert.ok(Math.abs(result[0].projectedAnnual - 50.0) < 0.001); // 5.0 * 10
  console.log('OK: test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price');
}

function test_compute_yield_on_cost_excludes_payment_exactly_on_the_ttm_cutoff_day() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 100.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2025-09-21', ticker: 'MC.PA', amount: 5.0 }] }; // exactement 365 jours avant, exclu
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const result = computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, '2026-09-21');
  assert.deepStrictEqual(result, []); // ttmPerShare = 0, position exclue
  console.log('OK: test_compute_yield_on_cost_excludes_payment_exactly_on_the_ttm_cutoff_day');
}

function test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend() {
  const openPositions = [{ id: '1', ticker: 'GROWTH.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const companiesByTicker = { 'GROWTH.PA': { index: 'CAC40' } };
  const result = computeYieldOnCost(openPositions, {}, companiesByTicker, '2026-09-21');
  assert.deepStrictEqual(result, []);
  console.log('OK: test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend');
}

function test_compute_yield_on_cost_skips_position_with_unknown_ticker() {
  const openPositions = [{ id: '1', ticker: 'RETIRE.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const dividendHistoryByTicker = { 'RETIRE.PA': [{ date: '2026-06-15', ticker: 'RETIRE.PA', amount: 5.0 }] };
  const result = computeYieldOnCost(openPositions, dividendHistoryByTicker, {}, '2026-09-21');
  assert.deepStrictEqual(result, []);
  console.log('OK: test_compute_yield_on_cost_skips_position_with_unknown_ticker');
}

function test_resample_curve_weekly_keeps_the_latest_point_per_week_bucket() {
  const curve = [
    { date: '2026-01-05', pnlPct: 1 },
    { date: '2026-01-06', pnlPct: 2 }, // meme semaine que le 05, le plus recent doit etre garde
    { date: '2026-01-12', pnlPct: 3 },
  ];
  const resampled = resampleCurveWeekly(curve);
  assert.strictEqual(resampled.length, 2);
  assert.strictEqual(resampled[0].pnlPct, 2);
  assert.strictEqual(resampled[1].pnlPct, 3);
  console.log('OK: test_resample_curve_weekly_keeps_the_latest_point_per_week_bucket');
}

function test_compute_periodic_changes_returns_consecutive_differences() {
  const points = [{ pnlPct: 1 }, { pnlPct: 4 }, { pnlPct: 2 }];
  const changes = computePeriodicChanges(points);
  assert.deepStrictEqual(changes, [3, -2]);
  console.log('OK: test_compute_periodic_changes_returns_consecutive_differences');
}

function test_compute_portfolio_volatility_returns_null_with_fewer_than_two_weekly_changes() {
  const curve = [{ date: '2026-01-05', pnlPct: 0 }, { date: '2026-01-06', pnlPct: 1 }]; // meme semaine -> 1 seul point resample
  assert.strictEqual(computePortfolioVolatility(curve), null);
  console.log('OK: test_compute_portfolio_volatility_returns_null_with_fewer_than_two_weekly_changes');
}

function test_compute_portfolio_volatility_annualizes_weekly_stddev() {
  const curve = [
    { date: '2026-01-05', pnlPct: 0 },
    { date: '2026-01-12', pnlPct: -2 },
    { date: '2026-01-19', pnlPct: 0 },
  ];
  // changes = [-2, 2], ecart-type echantillon = sqrt(((-2-0)^2 + (2-0)^2) / (2-1)) = sqrt(8)
  const expected = Math.sqrt(8) * Math.sqrt(52);
  assert.ok(Math.abs(computePortfolioVolatility(curve) - expected) < 0.001);
  console.log('OK: test_compute_portfolio_volatility_annualizes_weekly_stddev');
}

function test_compute_max_drawdown_finds_largest_peak_to_trough_decline() {
  const curve = [
    { date: '2026-01-01', pnlPct: 0 },
    { date: '2026-01-05', pnlPct: 10 },  // pic
    { date: '2026-01-10', pnlPct: 4 },   // creux (-6 depuis le pic)
    { date: '2026-01-15', pnlPct: 8 },
    { date: '2026-01-20', pnlPct: -2 },  // creux plus profond (-12 depuis le pic de 10)
  ];
  assert.ok(Math.abs(computeMaxDrawdown(curve) - (-12)) < 0.001);
  console.log('OK: test_compute_max_drawdown_finds_largest_peak_to_trough_decline');
}

function test_compute_max_drawdown_returns_zero_for_a_monotonically_increasing_curve() {
  const curve = [{ date: '2026-01-01', pnlPct: 0 }, { date: '2026-01-05', pnlPct: 5 }, { date: '2026-01-10', pnlPct: 10 }];
  assert.strictEqual(computeMaxDrawdown(curve), 0);
  console.log('OK: test_compute_max_drawdown_returns_zero_for_a_monotonically_increasing_curve');
}

function test_compute_max_drawdown_returns_null_for_empty_curve() {
  assert.strictEqual(computeMaxDrawdown([]), null);
  console.log('OK: test_compute_max_drawdown_returns_null_for_empty_curve');
}

function test_compute_sharpe_ratio_subtracts_risk_free_rate_from_annualized_return() {
  const curve = [
    { date: '2026-01-05', pnlPct: 0 },
    { date: '2026-01-12', pnlPct: -2 },
    { date: '2026-01-19', pnlPct: 0 },
  ];
  // changes = [-2, 2], moyenne = 0, rendement annualise = 0 * 52 = 0
  const volatility = computePortfolioVolatility(curve);
  const expected = (0 - 3.68) / volatility;
  assert.ok(Math.abs(computeSharpeRatio(curve, 3.68) - expected) < 0.001);
  console.log('OK: test_compute_sharpe_ratio_subtracts_risk_free_rate_from_annualized_return');
}

function test_compute_sharpe_ratio_returns_null_with_insufficient_data() {
  const curve = [{ date: '2026-01-05', pnlPct: 0 }, { date: '2026-01-06', pnlPct: 1 }];
  assert.strictEqual(computeSharpeRatio(curve, 3.68), null);
  console.log('OK: test_compute_sharpe_ratio_returns_null_with_insufficient_data');
}

function test_compute_portfolio_risk_metrics_returns_null_sharpe_without_risk_free_rate() {
  const curve = [
    { date: '2026-01-05', pnlPct: 0 },
    { date: '2026-01-12', pnlPct: -2 },
    { date: '2026-01-19', pnlPct: 0 },
  ];
  const metrics = computePortfolioRiskMetrics(curve, null);
  assert.strictEqual(metrics.sharpe, null);
  assert.ok(metrics.volatility != null);
  assert.ok(metrics.maxDrawdown != null);
  console.log('OK: test_compute_portfolio_risk_metrics_returns_null_sharpe_without_risk_free_rate');
}

function test_compute_portfolio_risk_metrics_computes_sharpe_when_risk_free_rate_given() {
  const curve = [
    { date: '2026-01-05', pnlPct: 0 },
    { date: '2026-01-12', pnlPct: -2 },
    { date: '2026-01-19', pnlPct: 0 },
  ];
  const metrics = computePortfolioRiskMetrics(curve, 3.68);
  assert.ok(metrics.sharpe != null);
  console.log('OK: test_compute_portfolio_risk_metrics_computes_sharpe_when_risk_free_rate_given');
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
  test_close_position_rejects_empty_sell_date();
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
  test_group_dividend_history_by_ticker_sorts_each_group_by_date();
  test_price_at_or_before_returns_the_latest_entry_not_after_the_date();
  test_price_at_or_before_never_looks_into_the_future();
  test_price_at_or_before_returns_null_for_an_unknown_ticker();
  test_is_position_active_on_respects_buy_and_sell_dates();
  test_compute_performance_curve_weights_by_invested_capital_not_naive_average();
  test_compute_performance_curve_excludes_a_position_with_no_known_price_on_a_date_without_dropping_the_date();
  test_compute_portfolio_performance_curves_splits_by_currency_and_skips_empty_currency();
  test_compute_benchmark_performance_curve_scales_cost_by_index_ratio_since_buy_date();
  test_compute_benchmark_performance_curve_returns_null_value_when_index_price_at_buy_date_is_unknown();
  test_compute_dividends_received_only_counts_payments_within_holding_period();
  test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold();
  test_compute_dividends_received_splits_by_currency();
  test_compute_dividends_received_skips_position_with_unknown_ticker();
  test_compute_dividends_received_excludes_payment_exactly_on_the_buy_date();
  test_compute_dividends_received_includes_payment_the_day_after_the_buy_date();
  test_compute_dividends_received_excludes_payment_the_day_before_the_buy_date();
  test_compute_dividends_received_includes_payment_exactly_on_the_sell_date();
  test_compute_projected_dividend_income_uses_a_365_day_ttm_window();
  test_compute_projected_dividend_income_excludes_payment_exactly_on_the_ttm_cutoff_day();
  test_compute_projected_dividend_income_includes_payment_one_day_inside_the_ttm_window();
  test_compute_projected_dividend_income_monthly_is_annual_over_twelve();
  test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history();
  test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price();
  test_compute_yield_on_cost_excludes_payment_exactly_on_the_ttm_cutoff_day();
  test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend();
  test_compute_yield_on_cost_skips_position_with_unknown_ticker();
  test_resample_curve_weekly_keeps_the_latest_point_per_week_bucket();
  test_compute_periodic_changes_returns_consecutive_differences();
  test_compute_portfolio_volatility_returns_null_with_fewer_than_two_weekly_changes();
  test_compute_portfolio_volatility_annualizes_weekly_stddev();
  test_compute_max_drawdown_finds_largest_peak_to_trough_decline();
  test_compute_max_drawdown_returns_zero_for_a_monotonically_increasing_curve();
  test_compute_max_drawdown_returns_null_for_empty_curve();
  test_compute_sharpe_ratio_subtracts_risk_free_rate_from_annualized_return();
  test_compute_sharpe_ratio_returns_null_with_insufficient_data();
  test_compute_portfolio_risk_metrics_returns_null_sharpe_without_risk_free_rate();
  test_compute_portfolio_risk_metrics_computes_sharpe_when_risk_free_rate_given();
  console.log('Tous les tests portfolio.test.js sont passés.');
}

main();
