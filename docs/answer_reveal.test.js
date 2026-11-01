// docs/answer_reveal.test.js
// Tests JS sans framework, exécutables via `node docs/answer_reveal.test.js`
// (comme docs/chart_patterns.test.js).
const assert = require('assert');
const { tokenizeWords, pendingWords, createWordReveal, finalizeStructuredText } = require('./answer_reveal.js');

function test_tokenizeWords_garde_les_espaces_colles_au_mot() {
  assert.deepStrictEqual(tokenizeWords('Le score  est\n+11,2'), ['Le ', 'score  ', 'est\n', '+11,2']);
  assert.strictEqual(tokenizeWords('a <b>x</b>').join(''), 'a <b>x</b>');
  assert.deepStrictEqual(tokenizeWords(''), []);
}

function test_pendingWords_retient_le_mot_incomplet_tant_que_le_flux_continue() {
  assert.deepStrictEqual(pendingWords('Le ', 'Le score est', false), ['score ']);
  assert.deepStrictEqual(pendingWords('Le ', 'Le score est', true), ['score ', 'est']);
}

function test_pendingWords_prefixe_different_signale_un_remplacement() {
  assert.deepStrictEqual(pendingWords('Autre', 'Le score', true), null);
}

// Faux DOM minimal : le module n'utilise sur la cible que appendChild,
// replaceChildren et classList.toggle, et document.createElement.
global.document = { createElement: () => ({ textContent: '', className: '' }) };
const fakeTarget = () => ({
  nodes: [],
  appendChild(n) { this.nodes.push(n); },
  replaceChildren() { this.nodes = []; },
  classList: { toggle() {} },
  get text() { return this.nodes.map(n => n.textContent).join(''); },
});

function test_cancel_fige_le_texte_et_ignore_la_suite_du_flux() {
  const t = fakeTarget();
  const r = createWordReveal(t, { reduceMotion: false });
  r.setText('Bonjour le monde ');
  r.cancel();
  r.setText('Bonjour le monde et plus');
  assert.strictEqual(t.text, 'Bonjour le monde ');
  assert.ok(r.done());
}

function test_fix_affiche_le_html_comme_du_texte() {
  const t = fakeTarget();
  createWordReveal(t, { reduceMotion: true }).fix('a <script>x</script>');
  assert.strictEqual(t.text, 'a <script>x</script>');
}

function test_finalizeStructuredText_stylise_les_titres_et_mon_avis() {
  const t = fakeTarget();
  finalizeStructuredText(t, '## Le mecanisme\nPhrase un.\n\n## Mon avis\nConclusion.');
  // 4 lignes non vides : titre, paragraphe, titre "Mon avis", paragraphe.
  assert.strictEqual(t.nodes.length, 4);
  assert.strictEqual(t.nodes[0].className, 'ai-section-title');
  assert.strictEqual(t.nodes[0].textContent, 'Le mecanisme');
  assert.strictEqual(t.nodes[1].className, 'ai-paragraph');
  assert.strictEqual(t.nodes[1].textContent, 'Phrase un.');
  assert.strictEqual(t.nodes[2].className, 'ai-section-title-avis');
  assert.strictEqual(t.nodes[2].textContent, 'Mon avis');
  assert.strictEqual(t.nodes[3].className, 'ai-paragraph');
  assert.strictEqual(t.nodes[3].textContent, 'Conclusion.');
}

function test_finalizeStructuredText_mon_avis_insensible_a_la_casse() {
  const t = fakeTarget();
  finalizeStructuredText(t, '##   MON AVIS  \nTexte.');
  assert.strictEqual(t.nodes[0].className, 'ai-section-title-avis');
}

function test_finalizeStructuredText_sans_titre_reste_un_seul_paragraphe() {
  const t = fakeTarget();
  finalizeStructuredText(t, 'Une reponse courte, sans titre.');
  assert.strictEqual(t.nodes.length, 1);
  assert.strictEqual(t.nodes[0].className, 'ai-paragraph');
  assert.strictEqual(t.text, 'Une reponse courte, sans titre.');
}

function test_finalizeStructuredText_jamais_de_html_interprete() {
  const t = fakeTarget();
  finalizeStructuredText(t, '## Titre <script>x</script>\nTexte <b>gras</b>.');
  assert.strictEqual(t.nodes[0].textContent, 'Titre <script>x</script>');
  assert.strictEqual(t.nodes[1].textContent, 'Texte <b>gras</b>.');
}

async function test_flux_anime_affiche_tout_le_texte_apres_finish() {
  const t = fakeTarget();
  let done = 0;
  const r = createWordReveal(t, { reduceMotion: false, onDone: () => { done++; } });
  r.setText('Le score de ');
  r.setText('Le score de LVMH est +11,2');
  r.finish();
  await new Promise(res => setTimeout(res, 700));
  assert.strictEqual(t.text, 'Le score de LVMH est +11,2');
  // finalizeStructuredText (appelee automatiquement a la fin du flux,
  // voir Tache 2) remplace les spans rv-w par les noeuds structures
  // finaux — une seule ligne ici, sans titre "## ", donc un seul
  // noeud ai-paragraph.
  assert.ok(t.nodes.every(n => n.className === 'ai-paragraph'));
  assert.strictEqual(done, 1);
}

(async () => {
  test_tokenizeWords_garde_les_espaces_colles_au_mot();
  test_pendingWords_retient_le_mot_incomplet_tant_que_le_flux_continue();
  test_pendingWords_prefixe_different_signale_un_remplacement();
  test_cancel_fige_le_texte_et_ignore_la_suite_du_flux();
  test_fix_affiche_le_html_comme_du_texte();
  test_finalizeStructuredText_stylise_les_titres_et_mon_avis();
  test_finalizeStructuredText_mon_avis_insensible_a_la_casse();
  test_finalizeStructuredText_sans_titre_reste_un_seul_paragraphe();
  test_finalizeStructuredText_jamais_de_html_interprete();
  await test_flux_anime_affiche_tout_le_texte_apres_finish();
  console.log('OK');
})().catch(e => { console.error(e); process.exit(1); });
