// docs/compte/portefeuille.test.js
// Tests sans framework, exécutables via `node docs/compte/portefeuille.test.js`.
// Même convention que docs/portfolio.test.js. Simule window (événements),
// localStorage et un faux service compte (Compte.fetch) en mémoire, puis
// pilote le vrai docs/portfolio.js avec Compte.portefeuille.storage().
const assert = require('assert');

// --- Environnement navigateur minimal -------------------------------------
const cible = new EventTarget();
globalThis.addEventListener = cible.addEventListener.bind(cible);
globalThis.removeEventListener = cible.removeEventListener.bind(cible);
globalThis.dispatchEvent = cible.dispatchEvent.bind(cible);
globalThis.window = globalThis;

function fauxStockage() {
  const data = {};
  return {
    getItem: k => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: k => { delete data[k]; },
    _data: data,
  };
}
globalThis.localStorage = fauxStockage();

// --- Faux service compte -----------------------------------------------
const serveur = { positions: [], suivant: 1, appels: [], echouerSur: null };
class Erreur extends Error {
  constructor(statut, message) { super(message); this.statut = statut; }
}
globalThis.Compte = {
  membre: () => null,
  async fetch(chemin, options = {}) {
    const methode = options.method || 'GET';
    serveur.appels.push(`${methode} ${chemin}`);
    if (serveur.echouerSur && `${methode} ${chemin}`.startsWith(serveur.echouerSur)) {
      throw new Erreur(422, 'La date d’achat ne peut pas être dans le futur.');
    }
    const m = chemin.match(/^\/positions\/(\d+)(\/close)?$/);
    if (methode === 'GET' && chemin.startsWith('/positions')) {
      return serveur.positions.map(p => ({ ...p }));
    }
    if (methode === 'POST' && chemin === '/positions') {
      const p = { id: serveur.suivant++, ...options.body, sell_price: null, sell_date: null, created_at: 'x' };
      serveur.positions.push(p);
      return { ...p };
    }
    if (m) {
      const p = serveur.positions.find(x => x.id === Number(m[1]));
      if (!p) throw new Erreur(404, 'Position introuvable.');
      if (methode === 'DELETE') { serveur.positions = serveur.positions.filter(x => x !== p); return null; }
      Object.assign(p, options.body);
      return { ...p };
    }
    if (methode === 'POST' && chemin === '/import') {
      return { importees: { positions: options.body.positions.length, positions_cloturees: 0, historique_ia: 0, reglages: 0 },
        ignorees: { positions: 0, positions_cloturees: 0, historique_ia: 0, reglages: 0 } };
    }
    throw new Erreur(404, 'inconnu');
  },
};

const P = require('../portfolio.js');
const C = require('./portefeuille.js');

const attendre = () => new Promise(r => setTimeout(r, 5));
async function stabiliser() { for (let i = 0; i < 10; i++) await attendre(); }
function connecter(membre) {
  globalThis.Compte.membre = () => membre;
  dispatchEvent(new CustomEvent('compte:change', { detail: membre }));
}

// --- Fonctions pures ----------------------------------------------------

function test_versSite_forme_exacte() {
  const ouverte = C.versSite({ id: 4, ticker: 'MC.PA', quantity: 2, buy_price: 10, buy_date: '2026-01-01',
    sell_price: null, sell_date: null, created_at: 'x' });
  assert.deepStrictEqual(ouverte, { id: 'srv-4', ticker: 'MC.PA', quantity: 2, buy_price: 10, buy_date: '2026-01-01' });
  const close = C.versSite({ id: 5, ticker: 'A', quantity: 1, buy_price: 1, buy_date: '2026-01-01',
    sell_price: 2, sell_date: '2026-02-01' });
  assert.strictEqual(close.sell_price, 2);
  assert.strictEqual(close.sell_date, '2026-02-01');
}

function test_idServeur() {
  assert.strictEqual(C.idServeur('srv-12'), 12);
  assert.strictEqual(C.idServeur('pos-1700-abc'), null);
  assert.strictEqual(C.idServeur('srv-abc'), null);
}

function test_calculerOperations() {
  const a = { id: 'srv-1', ticker: 'A', quantity: 1, buy_price: 1, buy_date: '2026-01-01' };
  const b = { id: 'srv-2', ticker: 'B', quantity: 1, buy_price: 1, buy_date: '2026-01-01' };
  const c = { id: 'srv-3', ticker: 'C', quantity: 1, buy_price: 1, buy_date: '2026-01-01' };
  const avant = { ouvertes: [a, b, c], cloturees: [] };
  const nouvelle = { id: 'pos-9-x', ticker: 'D', quantity: 3, buy_price: 4, buy_date: '2026-01-02' };
  const apres = {
    ouvertes: [{ ...a, quantity: 5 }, nouvelle],
    cloturees: [{ ...b, sell_price: 2, sell_date: '2026-02-01' }],
  };
  const ops = C.calculerOperations(avant, apres);
  assert.deepStrictEqual(ops.map(o => o.type), ['supprimer', 'modifier', 'cloturer', 'creer']);
  assert.strictEqual(ops[0].id, 3);
  assert.deepStrictEqual(ops[1].champs, { quantity: 5 });
  assert.deepStrictEqual(ops[2].vente, { sell_price: 2, sell_date: '2026-02-01' });
  assert.deepStrictEqual(ops[3].achat, { ticker: 'D', quantity: 3, buy_price: 4, buy_date: '2026-01-02' });
  assert.strictEqual(ops[3].vente, null);
  assert.deepStrictEqual(C.calculerOperations(avant, avant), []);
  // Rouvrir une position clôturée : refusé.
  assert.throws(() => C.calculerOperations({ ouvertes: [], cloturees: [{ ...a, sell_price: 1, sell_date: '2026-02-01' }] },
    { ouvertes: [a], cloturees: [] }), C.ModificationNonPriseEnCharge);
}

// --- Bout en bout avec docs/portfolio.js ------------------------------------

async function test_deconnecte_utilise_localStorage() {
  assert.strictEqual(Compte.portefeuille.storage(), localStorage);
  P.addPosition('LOCAL.PA', 1, 1, '2026-01-01');
  assert.strictEqual(P.loadPortfolio(localStorage).length, 1);
}

async function test_connexion_charge_le_cache() {
  serveur.positions.push({ id: serveur.suivant++, ticker: 'MC.PA', quantity: 2, buy_price: 600,
    buy_date: '2026-01-10', sell_price: null, sell_date: null, created_at: 'x' });
  let redessins = 0;
  addEventListener('compte:portefeuille', () => { redessins++; });
  connecter({ id: 7 });
  await Compte.portefeuille.pret;
  const s = Compte.portefeuille.storage();
  assert.strictEqual(s, Compte.portefeuilleStorage);
  const positions = P.loadPortfolio(s);
  assert.deepStrictEqual(positions, [{ id: 'srv-1', ticker: 'MC.PA', quantity: 2, buy_price: 600, buy_date: '2026-01-10' }]);
  assert.ok(redessins >= 1);
  // Le localStorage n'est jamais touché par le portefeuille du compte.
  assert.strictEqual(P.loadPortfolio(localStorage)[0].ticker, 'LOCAL.PA');
}

async function test_ajout_modification_cloture_suppression() {
  const s = Compte.portefeuille.storage();
  serveur.appels = [];
  const r = P.addPosition('AIR.PA', 3, 150, '2026-02-01', s);
  assert.ok(r.ok);
  // Le cache est à jour tout de suite (appel synchrone).
  assert.strictEqual(P.loadPortfolio(s).length, 2);
  assert.ok(P.loadPortfolio(s)[1].id.startsWith('pos-'));
  await stabiliser();
  assert.deepStrictEqual(serveur.appels.filter(a => a !== 'GET /positions?statut=toutes'), ['POST /positions']);
  // Après synchro, la page voit l'id du serveur.
  const air = P.loadPortfolio(s)[1];
  assert.strictEqual(air.id, 'srv-2');

  serveur.appels = [];
  P.updatePosition(air.id, 4, 151, '2026-02-01', s);
  await stabiliser();
  assert.ok(serveur.appels.includes('PATCH /positions/2'));
  assert.strictEqual(serveur.positions.find(p => p.id === 2).quantity, 4);

  serveur.appels = [];
  const fermee = P.closePosition('srv-2', 170, '2026-03-01', s);
  assert.ok(fermee.ok);
  await stabiliser();
  // Une clôture, pas une suppression suivie d'une création.
  assert.deepStrictEqual(serveur.appels.filter(a => !a.startsWith('GET')), ['PATCH /positions/2/close']);
  assert.strictEqual(P.loadClosedPortfolio(s)[0].sell_price, 170);
  assert.strictEqual(P.loadPortfolio(s).length, 1);

  serveur.appels = [];
  P.removePosition('srv-1', s);
  await stabiliser();
  assert.deepStrictEqual(serveur.appels.filter(a => !a.startsWith('GET')), ['DELETE /positions/1']);
  assert.strictEqual(P.loadPortfolio(s).length, 0);
}

async function test_echec_restaure_et_previent() {
  const s = Compte.portefeuille.storage();
  const avant = JSON.stringify([P.loadPortfolio(s), P.loadClosedPortfolio(s)]);
  let message = null;
  addEventListener('compte:portefeuille-erreur', e => { message = e.detail.message; });
  serveur.echouerSur = 'POST /positions';
  P.addPosition('BN.PA', 1, 50, '2026-01-01', s);
  assert.strictEqual(P.loadPortfolio(s).length, 1);
  await stabiliser();
  serveur.echouerSur = null;
  assert.ok(message && message.includes('futur'), message);
  assert.strictEqual(JSON.stringify([P.loadPortfolio(s), P.loadClosedPortfolio(s)]), avant);
}

async function test_ajout_puis_cloture_avant_synchro() {
  const s = Compte.portefeuille.storage();
  const r = P.addPosition('SAN.PA', 2, 90, '2026-01-05', s);
  const id = r.positions[r.positions.length - 1].id;
  P.closePosition(id, 95, '2026-01-20', s);
  await stabiliser();
  const sur = serveur.positions.find(p => p.ticker === 'SAN.PA');
  assert.strictEqual(sur.sell_price, 95);
  assert.ok(P.loadClosedPortfolio(s).some(p => p.id === 'srv-' + sur.id));
}

async function test_deconnexion_revient_au_localStorage() {
  connecter(null);
  await Compte.portefeuille.pret;
  assert.strictEqual(Compte.portefeuille.storage(), localStorage);
  assert.strictEqual(P.loadPortfolio(Compte.portefeuilleStorage).length, 0);
  assert.throws(() => Compte.portefeuilleStorage.setItem('analyse-or-portfolio', '[]'));
}

function test_donnees_locales_et_resume() {
  localStorage.setItem('analyse-or-assistant-history', JSON.stringify([{ date: '2026-11-01T10:00:00Z', question: 'Q', reponse: 'R' }]));
  localStorage.setItem('analyse-or-saved-screeners', JSON.stringify([{ name: 'F', state: {} }, { pas: 'de nom' }]));
  localStorage.setItem('analyse-or-answer-level', 'grand_public');
  localStorage.setItem('theme', 'light');
  const d = C.donneesLocales();
  assert.strictEqual(d.positions.length, 1);
  assert.strictEqual(d.historique_ia.length, 1);
  assert.deepStrictEqual(d.reglages, { theme: 'light', niveau_reponse: 'grand_public', filtres_sauvegardes: [{ name: 'F', state: {} }] });
  assert.deepStrictEqual(C.resumer(d), ['1 position ouverte', '1 conversation avec l’assistant', '1 filtre sauvegardé']);
  // Thème seul : pas de proposition.
  assert.deepStrictEqual(C.resumer({ positions: [], positions_cloturees: [], historique_ia: [], reglages: { theme: 'dark' } }), []);
  const texte = C.resumerResultat({ importees: { positions: 2, positions_cloturees: 0, historique_ia: 1, reglages: 0 },
    ignorees: { positions: 1, positions_cloturees: 0, historique_ia: 0, reglages: 0 } });
  assert.ok(texte.includes('2 positions ouvertes, 1 conversation'), texte);
  assert.ok(texte.includes('1 élément ignoré'), texte);
}

async function test_proposerImport_sans_membre() {
  globalThis.Compte.membre = () => null;
  assert.strictEqual(await Compte.proposerImport(), null);
}

async function main() {
  test_versSite_forme_exacte();
  test_idServeur();
  test_calculerOperations();
  await test_deconnecte_utilise_localStorage();
  await test_connexion_charge_le_cache();
  await test_ajout_modification_cloture_suppression();
  await test_echec_restaure_et_previent();
  await test_ajout_puis_cloture_avant_synchro();
  await test_deconnexion_revient_au_localStorage();
  test_donnees_locales_et_resume();
  await test_proposerImport_sans_membre();
  console.log('portefeuille.test.js : tous les tests passent');
}

main().catch(e => { console.error(e); process.exit(1); });
