// docs/scalping.test.js
// Tests JS sans framework, exécutables via `node docs/scalping.test.js`.
// Chaque test compare un résultat calculé à une valeur attendue calculée
// à la main (voir commentaires) et lève une erreur explicite si ça ne
// correspond pas — pas de bibliothèque d'assertion, juste `assert` natif
// de Node pour rester sans dépendance.
const assert = require('assert');
const { fetchGoldCandles } = require('./scalping.js');

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

async function main() {
  await test_fetchGoldCandles_parses_and_reverses_to_chronological_order();
  await test_fetchGoldCandles_rejects_on_error_status();
  await test_fetchGoldCandles_rejects_on_http_error();
  console.log('Tous les tests scalping.test.js sont passés.');
}

main().catch(e => {
  console.error('ÉCHEC :', e);
  process.exit(1);
});
