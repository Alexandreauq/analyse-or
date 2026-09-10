// docs/portfolio.test.js
// Tests JS sans framework, exécutables via `node docs/portfolio.test.js`.
// Même convention que docs/scalping.test.js : assert natif de Node,
// fonctions test_* appelées explicitement dans main(), aucune dépendance.
const assert = require('assert');
const {
  PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
  loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
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
  console.log('Tous les tests portfolio.test.js (Task 1) sont passés.');
}

main();
