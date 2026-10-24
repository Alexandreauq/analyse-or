// docs/home_today.test.js
// Tests JS sans framework, exécutables via `node docs/home_today.test.js`.
const assert = require('assert');
const { formatSigned, computeSignalSummary, buildHomeToday, homeTagline, homeTodayShort } = require('./home_today.js');

const SAMPLE = {
  score: { composite_score: -23.4, interpretation: "Défavorable — pas d'entrée même sur signal technique" },
  companies: [{ name: 'Edenred', score: 98.6 }, { name: 'AXA', score: 84.7 }],
  signals: { winRate: 65, avgAlpha: 2.9, openCount: 56 },
  botOr: { kill_switch: false, dry_run: true },
  botActions: { statut: 'termine' },
  botActionsLabels: { termine: 'Terminé' },
};

function test_formatSigned_signe_moins_typographique_et_virgule() {
  assert.strictEqual(formatSigned(-23.4), '−23,4');
  assert.strictEqual(formatSigned(98.6), '+98,6');
  assert.strictEqual(formatSigned(0), '0,0');
  assert.strictEqual(formatSigned(null), '—');
}

function test_computeSignalSummary_perimetre_du_bot_seulement() {
  const s = computeSignalSummary(
    [{ index: 'CAC40', status: 'closed' }, { index: 'CAC40', status: 'open' }, { index: 'NIKKEI225', status: 'open' }],
    p => ({ CAC40: 5 }[p.index] ?? -3) * (p.status === 'open' ? -1 : 1),
    () => 1,
    new Set(['NIKKEI225']));
  assert.deepStrictEqual(s, { winRate: 50, avgAlpha: -1, openCount: 1 });
}

function test_buildHomeToday_valeurs_du_jour() {
  const items = buildHomeToday(SAMPLE);
  assert.deepStrictEqual(items.map(i => i.key), ['or', 'top', 'signaux', 'bots']);
  assert.deepStrictEqual(items.map(i => i.value), ['−23,4', '+98,6', '65 %', 'Or en simulation']);
  assert.strictEqual(items[1].detail, 'Edenred, meilleur score sur 2 entreprises notées.');
  assert.strictEqual(items[2].detail, 'de réussite, +2,9 % vs indice en moyenne. 56 en cours.');
  assert.strictEqual(items[3].detail, 'Bot Actions : Terminé');
  assert.deepStrictEqual(items.map(i => i.href), ['#or', '#indices', '#indices/suivi', '#portefeuille']);
  assert.deepStrictEqual(items.map(i => i.tone), ['neg', 'pos', 'gold', 'neutral']);
}

function test_buildHomeToday_donnees_manquantes_sans_NaN() {
  const vide = buildHomeToday({ score: null, companies: null, signals: null, botOr: null, botActions: null, botActionsLabels: {} });
  assert.strictEqual(vide.length, 4);
  assert.ok(vide.every(i => i.value === '—' || i.value === 'Indisponible'));
  assert.ok(vide.every(i => !/NaN|undefined|null/.test(i.value + i.detail)));
  const sansStats = buildHomeToday({ ...SAMPLE, signals: { winRate: null, avgAlpha: null, openCount: 0 } });
  assert.strictEqual(sansStats[2].value, '—');
}

function test_homeTagline_avec_et_sans_nombre() {
  assert.strictEqual(homeTagline(1299),
    `L'IA note chaque jour l'or et ${(1299).toLocaleString('fr-FR')} entreprises mondiales, repère les points d'entrée et suit ton portefeuille.`);
  assert.ok(!/null|undefined/.test(homeTagline(null)));
}

function test_homeTodayShort_libelles_carte_mobile() {
  const items = buildHomeToday(SAMPLE);
  assert.deepStrictEqual(items.map(homeTodayShort), [
    { label: 'Or', sub: 'défavorable' },
    { label: 'Top du jour', sub: 'Edenred' },
    { label: 'Signaux', sub: 'réussite' },
    { label: 'Bots', sub: 'Or en simulation' },
  ]);
}

test_formatSigned_signe_moins_typographique_et_virgule();
test_computeSignalSummary_perimetre_du_bot_seulement();
test_buildHomeToday_valeurs_du_jour();
test_buildHomeToday_donnees_manquantes_sans_NaN();
test_homeTagline_avec_et_sans_nombre();
test_homeTodayShort_libelles_carte_mobile();
console.log('OK');
