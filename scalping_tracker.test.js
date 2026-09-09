// scalping_tracker.test.js
// Tests JS sans framework, exécutables via `node scalping_tracker.test.js`
// (comme docs/scalping.test.js). Chaque test compare un résultat calculé
// à une valeur attendue calculée à la main.
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {
  loadTracking,
  saveTracking,
  computeReturn,
  decidePositionOutcome,
  buildPositionFromSignal,
  runOnce,
  TRIAL_DURATION_MS,
  MAX_POSITION_DURATION_MS,
  STALE_THRESHOLD_MS,
} = require('./scalping_tracker.js');

function tempFile() {
  return path.join(os.tmpdir(), `scalping_tracker_test_${Date.now()}_${Math.random().toString(36).slice(2)}.json`);
}

function test_loadTracking_returns_default_shape_when_file_missing() {
  const f = tempFile();
  if (fs.existsSync(f)) fs.unlinkSync(f);
  const data = loadTracking(f);
  assert.deepStrictEqual(data, { trial_start: null, trial_ended: false, positions: [] });
  console.log('OK: test_loadTracking_returns_default_shape_when_file_missing');
}

function test_saveTracking_then_loadTracking_roundtrips() {
  const f = tempFile();
  const data = { trial_start: '2026-09-09T14:00:00.000Z', trial_ended: false, positions: [{ id: 'x' }] };
  saveTracking(data, f);
  const reloaded = loadTracking(f);
  assert.deepStrictEqual(reloaded, data);
  fs.unlinkSync(f);
  console.log('OK: test_saveTracking_then_loadTracking_roundtrips');
}

function test_computeReturn_achat_gain() {
  // Achat : entrée 100, clôture 105 -> gain de 5$, +5%.
  const r = computeReturn('achat', 100, 105);
  assert.deepStrictEqual(r, { return_usd: 5, return_pct: 5 });
  console.log('OK: test_computeReturn_achat_gain');
}

function test_computeReturn_vente_gain() {
  // Vente : entrée 100, clôture 95 -> gain de 5$ (le prix a baissé,
  // ce qui est gagnant pour une position vendeuse), +5%.
  const r = computeReturn('vente', 100, 95);
  assert.deepStrictEqual(r, { return_usd: 5, return_pct: 5 });
  console.log('OK: test_computeReturn_vente_gain');
}

function test_decidePositionOutcome_tp_hit() {
  // Achat, SL=95, TP=110. Bougie 1 : high=108 (pas assez pour TP), low=99
  // (pas assez pour SL) -> rien. Bougie 2 : high=112 >= 110 -> TP touché,
  // prix de clôture = le niveau du TP (110), pas le high de la bougie.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [
    { high: 108, low: 99, close: 107 },
    { high: 112, low: 105, close: 111 },
  ];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'tp_hit', price: 110 });
  console.log('OK: test_decidePositionOutcome_tp_hit');
}

function test_decidePositionOutcome_sl_hit() {
  // Achat, SL=95, TP=110. Une seule bougie : low=93 <= 95 -> SL touché.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [{ high: 97, low: 93, close: 94 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 95 });
  console.log('OK: test_decidePositionOutcome_sl_hit');
}

function test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first() {
  // Achat, SL=95, TP=110. Une bougie qui franchit les deux (low=90<=95 ET
  // high=112>=110) -> règle de désambiguïsation : SL supposé touché en
  // premier, jamais TP dans ce cas.
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110 };
  const candles = [{ high: 112, low: 90, close: 100 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 95 });
  console.log('OK: test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first');
}

function test_decidePositionOutcome_vente_sl_hit() {
  // Vente : SL=110 (au-dessus de l'entrée), TP=90 (en dessous). Une bougie
  // dont le high atteint 112 >= 110 -> SL touché (le prix est monté contre
  // la position vendeuse).
  const position = { direction: 'vente', stop_loss: 110, take_profit: 90 };
  const candles = [{ high: 112, low: 100, close: 108 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 110 });
  console.log('OK: test_decidePositionOutcome_vente_sl_hit');
}

function test_decidePositionOutcome_max_duration() {
  // Ni SL ni TP touché, mais 2h se sont écoulées depuis l'entrée -> clôture
  // forcée au dernier prix de clôture connu.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + MAX_POSITION_DURATION_MS; // exactement 2h plus tard
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, false);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'max_duration', price: 99.5 });
  console.log('OK: test_decidePositionOutcome_max_duration');
}

function test_decidePositionOutcome_trial_end_safety_net() {
  // Ni SL ni TP touché, moins de 2h écoulées, mais trialElapsed=true (fin
  // de l'essai) -> clôture forcée, filet de sécurité.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + 10 * 60 * 1000; // 10 minutes plus tard seulement
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, true);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'trial_end', price: 99.5 });
  console.log('OK: test_decidePositionOutcome_trial_end_safety_net');
}

function test_decidePositionOutcome_stays_open() {
  // Ni SL ni TP touché, moins de 2h écoulées, essai pas terminé -> reste
  // ouverte.
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + 10 * 60 * 1000;
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, false);
  assert.deepStrictEqual(outcome, { closed: false });
  console.log('OK: test_decidePositionOutcome_stays_open');
}

function test_buildPositionFromSignal_with_pattern() {
  const signal = {
    status: 'achat', price: 3450, stopLoss: 3448, takeProfit: 3455,
    trend: 'baissier', pattern: { name: 'Marteau', direction: 'haussier' },
  };
  const now = new Date('2026-09-09T14:35:00.000Z').getTime();
  const position = buildPositionFromSignal(signal, now);
  assert.deepStrictEqual(position, {
    id: 'scalp-2026-09-09T14:35:00.000Z',
    direction: 'achat',
    status: 'open',
    entry_time: '2026-09-09T14:35:00.000Z',
    entry_price: 3450,
    stop_loss: 3448,
    take_profit: 3455,
    trend_at_entry: 'baissier',
    pattern_at_entry: 'Marteau',
    close_time: null,
    close_price: null,
    close_reason: null,
    return_usd: null,
    return_pct: null,
  });
  console.log('OK: test_buildPositionFromSignal_with_pattern');
}

function test_buildPositionFromSignal_without_pattern() {
  const signal = { status: 'vente', price: 3450, stopLoss: 3452, takeProfit: 3445, trend: 'haussier', pattern: null };
  const now = new Date('2026-09-09T14:35:00.000Z').getTime();
  const position = buildPositionFromSignal(signal, now);
  assert.strictEqual(position.pattern_at_entry, null);
  console.log('OK: test_buildPositionFromSignal_without_pattern');
}

async function test_runOnce_opens_position_on_new_signal() {
  const f = tempFile();
  // Réponse Twelve Data fabriquée avec assez de bougies (>= 36, le
  // SCALP_MIN_CANDLES de docs/scalping.js) pour que computeSignal ne
  // reste pas bloqué sur le garde-fou "pas assez de données". On ne
  // contrôle pas ici la valeur exacte du signal produit (achat/vente/
  // neutre dépend de la logique de confluence complète) ; ce test
  // vérifie seulement que runOnce se comporte correctement dans les deux
  // cas : soit une position est ouverte et trial_start est fixé, soit
  // aucune position n'est ouverte mais trial_start est fixé quand même.
  const values = [];
  for (let i = 0; i < 40; i++) {
    const t = new Date(Date.UTC(2026, 8, 9, 14, 0, 0) + i * 60000);
    const p = 3450 + Math.sin(i) * 2; // légère variation, sans importance pour ce test
    values.push({
      datetime: t.toISOString().slice(0, 19).replace('T', ' '),
      open: String(p), high: String(p + 0.5), low: String(p - 0.5), close: String(p),
    });
  }
  values.reverse(); // Twelve Data renvoie le plus récent en premier
  const fakeFetch = async () => ({ ok: true, json: async () => ({ status: 'ok', values }) });
  const now = Date.now();
  const data = await runOnce('fake-key', fakeFetch, now, f);
  assert.ok(data, 'runOnce doit renvoyer les données en cas de succès du fetch');
  assert.strictEqual(data.trial_start, new Date(now).toISOString());
  assert.ok(Array.isArray(data.positions));
  if (fs.existsSync(f)) fs.unlinkSync(f);
  console.log('OK: test_runOnce_opens_position_on_new_signal');
}

async function test_runOnce_does_not_write_on_fetch_failure() {
  const f = tempFile();
  if (fs.existsSync(f)) fs.unlinkSync(f);
  const fakeFetch = async () => { throw new Error('reseau indisponible'); };
  const result = await runOnce('fake-key', fakeFetch, Date.now(), f);
  assert.strictEqual(result, undefined);
  assert.strictEqual(fs.existsSync(f), false, 'aucun fichier ne doit etre cree si le fetch echoue');
  console.log('OK: test_runOnce_does_not_write_on_fetch_failure');
}

function test_decidePositionOutcome_stays_open_when_stale_despite_max_duration() {
  // Meme scenario que test_decidePositionOutcome_max_duration (2h ecoulees,
  // ni SL ni TP touche) MAIS isStale=true -> ne doit PAS se clore : les
  // clotures basees sur l'horloge murale sont suspendues tant que les
  // donnees sont perimees (marche ferme ou run tres en retard).
  const entryTime = new Date('2026-01-01T00:00:00.000Z').getTime();
  const now = entryTime + MAX_POSITION_DURATION_MS;
  const position = { direction: 'achat', stop_loss: 90, take_profit: 120, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 100, low: 99, close: 99.5 }];
  const outcome = decidePositionOutcome(position, candles, now, false, true);
  assert.deepStrictEqual(outcome, { closed: false });
  console.log('OK: test_decidePositionOutcome_stays_open_when_stale_despite_max_duration');
}

function test_decidePositionOutcome_sl_still_detected_when_stale() {
  // isStale=true n'empeche PAS la detection d'un vrai SL touche dans les
  // bougies recues (donnee historique fiable, independante de la
  // fraicheur du dernier point).
  const position = { direction: 'achat', stop_loss: 95, take_profit: 110, entry_time: '2026-01-01T00:00:00.000Z' };
  const candles = [{ high: 97, low: 93, close: 94 }];
  const outcome = decidePositionOutcome(position, candles, Date.now(), false, true);
  assert.deepStrictEqual(outcome, { closed: true, reason: 'sl_hit', price: 95 });
  console.log('OK: test_decidePositionOutcome_sl_still_detected_when_stale');
}

async function test_runOnce_does_not_open_position_on_stale_data() {
  const f = tempFile();
  // Bougies toutes horodatees il y a plus de STALE_THRESHOLD_MS par
  // rapport a `now` -> meme si computeSignal produisait un achat/vente
  // (peu importe ici lequel), aucune position ne doit s'ouvrir.
  const values = [];
  const baseTime = Date.UTC(2020, 0, 1, 0, 0, 0); // tres ancien, largement perime
  for (let i = 0; i < 40; i++) {
    const t = new Date(baseTime + i * 60000);
    const p = 3450 + Math.sin(i) * 2;
    values.push({
      datetime: t.toISOString().slice(0, 19).replace('T', ' '),
      open: String(p), high: String(p + 0.5), low: String(p - 0.5), close: String(p),
    });
  }
  values.reverse();
  const fakeFetch = async () => ({ ok: true, json: async () => ({ status: 'ok', values }) });
  const now = Date.now(); // "aujourd'hui", tres loin des bougies de 2020
  const data = await runOnce('fake-key', fakeFetch, now, f);
  assert.strictEqual(data.positions.length, 0, 'aucune position ne doit etre ouverte sur des donnees perimees');
  if (fs.existsSync(f)) fs.unlinkSync(f);
  console.log('OK: test_runOnce_does_not_open_position_on_stale_data');
}

async function test_runOnce_closes_open_position_on_tp_hit() {
  const f = tempFile();
  const openPosition = {
    id: 'scalp-test', direction: 'achat', status: 'open',
    entry_time: '2026-09-09T14:00:00.000Z', entry_price: 100,
    stop_loss: 95, take_profit: 110,
    trend_at_entry: 'baissier', pattern_at_entry: 'Marteau',
    close_time: null, close_price: null, close_reason: null,
    return_usd: null, return_pct: null,
  };
  saveTracking({ trial_start: '2026-09-09T13:00:00.000Z', trial_ended: false, positions: [openPosition] }, f);
  // Une bougie postérieure à l'entrée dont le high touche le TP (110), à
  // un horodatage récent par rapport à `now` pour éviter le garde-fou de
  // fraîcheur.
  const now = new Date('2026-09-09T14:10:00.000Z').getTime();
  const values = [{ datetime: '2026-09-09 14:05:00', open: '108', high: '112', low: '107', close: '111' }];
  const fakeFetch = async () => ({ ok: true, json: async () => ({ status: 'ok', values }) });
  const data = await runOnce('fake-key', fakeFetch, now, f);
  const closed = data.positions[0];
  assert.strictEqual(closed.status, 'closed');
  assert.strictEqual(closed.close_reason, 'tp_hit');
  assert.strictEqual(closed.close_price, 110);
  assert.strictEqual(closed.return_usd, 10);
  assert.strictEqual(closed.return_pct, 10);
  assert.strictEqual(closed.close_time, new Date(now).toISOString());
  if (fs.existsSync(f)) fs.unlinkSync(f);
  console.log('OK: test_runOnce_closes_open_position_on_tp_hit');
}

async function main() {
  test_loadTracking_returns_default_shape_when_file_missing();
  test_saveTracking_then_loadTracking_roundtrips();
  test_computeReturn_achat_gain();
  test_computeReturn_vente_gain();
  test_decidePositionOutcome_tp_hit();
  test_decidePositionOutcome_sl_hit();
  test_decidePositionOutcome_both_touched_same_candle_assumes_sl_first();
  test_decidePositionOutcome_vente_sl_hit();
  test_decidePositionOutcome_max_duration();
  test_decidePositionOutcome_trial_end_safety_net();
  test_decidePositionOutcome_stays_open();
  test_buildPositionFromSignal_with_pattern();
  test_buildPositionFromSignal_without_pattern();
  await test_runOnce_opens_position_on_new_signal();
  await test_runOnce_does_not_write_on_fetch_failure();
  test_decidePositionOutcome_stays_open_when_stale_despite_max_duration();
  test_decidePositionOutcome_sl_still_detected_when_stale();
  await test_runOnce_does_not_open_position_on_stale_data();
  await test_runOnce_closes_open_position_on_tp_hit();
  console.log('Tous les tests scalping_tracker sont passes.');
}

main().catch(e => { console.error(e); process.exit(1); });
