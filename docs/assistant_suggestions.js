// docs/assistant_suggestions.js
// Textes de guidage de l'assistant IA : exemples qui défilent dans la barre
// de l'accueil, guide « ce que je sais faire » (accueil), suggestions et
// libellé de contexte par page (agent). Listes statiques, logique pure.
// Chargé tel quel par index.html, avec module.exports pour Node
// (docs/assistant_suggestions.test.js).
// Voir docs/superpowers/specs/2026-10-24-accueil-agent-redesign-design.md.

// Partie en or du placeholder animé : « Demande-moi <exemple> ».
const PLACEHOLDER_EXAMPLES = [
  'les meilleures opportunités du CAC 40',
  'pourquoi le score de l\'or a bougé',
  'le score de LVMH',
  'les signaux d\'entrée du jour',
  'une analyse de mon portefeuille',
  'le prochain événement macro',
];

function nextPlaceholderIndex(i, n) {
  return (i + 1) % n;
}

// Guide « ce que je sais faire » de l'accueil (panneau au clic dans la
// barre) : un domaine par colonne, deux questions prêtes à envoyer chacune.
// {n} dans le texte Indices est remplacé par le vrai nombre d'entreprises.
const HOME_GUIDE = [
  { key: 'or', titre: 'Or', icone: 'icon-or', texte: 'Score macro quotidien, alertes, calendrier Fed.',
    questions: ['Pourquoi le score de l\'or a-t-il bougé ?', 'Quel est le prochain événement macro ?'] },
  { key: 'indices', titre: 'Indices', icone: 'icon-indices', texte: '{n} entreprises notées, repères d\'entrée et de sortie.',
    questions: ['Top 5 du CAC 40', 'Compare LVMH et Hermès'] },
  { key: 'portefeuille', titre: 'Portefeuille', icone: 'icon-portefeuille', texte: 'Tes positions, P&L, opportunités, bots.',
    questions: ['Analyse mon portefeuille', 'Quel est le statut des bots ?'] },
];

function normalizeText(s) {
  return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// Ne garde que les questions contenant chaque mot tapé (sans tenir compte
// des accents ni de la casse) ; une colonne sans question disparaît.
function filterGuide(guide, query) {
  const mots = normalizeText(query).split(/\s+/).filter(Boolean);
  if (!mots.length) return guide;
  return guide
    .map(col => ({ ...col, questions: col.questions.filter(q => mots.every(m => normalizeText(q).includes(m))) }))
    .filter(col => col.questions.length);
}

// Découpe d'un hash de route en { section, ticker, vue } — mêmes routes que
// parseRoute (index.html) : #or, #portefeuille, #indices, #indices/suivi,
// #indices/compare/A,B, #indices/TICKER, #indices/TICKER/analyse.
function decoupeRoute(hash) {
  const h = String(hash || '').replace(/^#/, '');
  if (h === 'or' || h === 'portefeuille' || h === 'indices') return { section: h };
  if (h === 'indices/suivi') return { section: 'suivi' };
  if (h.startsWith('indices/compare/')) return { section: 'compare' };
  if (h.startsWith('indices/')) {
    const reste = h.slice('indices/'.length);
    const analyse = reste.endsWith('/analyse');
    let ticker = analyse ? reste.slice(0, -'/analyse'.length) : reste;
    try { ticker = decodeURIComponent(ticker); } catch (e) { /* gardé tel quel */ }
    return { section: analyse ? 'analyse' : 'fiche', ticker };
  }
  return { section: 'accueil' };
}

// Deux suggestions de l'agent, liées à la page courante.
function suggestionsForRoute(hash, companyName) {
  const r = decoupeRoute(hash);
  const nom = companyName || r.ticker;
  switch (r.section) {
    case 'or': return ['Pourquoi ce score aujourd\'hui ?', 'Quel est le prochain événement macro ?'];
    case 'indices': return ['Top 5 du CAC 40', 'Quels signaux d\'entrée aujourd\'hui ?'];
    case 'suivi': return ['Quel est le taux de réussite des signaux ?', 'Quelles positions sont en perte ?'];
    case 'compare': return ['Laquelle est la plus solide ?', 'Explique les écarts de score'];
    case 'fiche':
    case 'analyse': return [`Explique le repère d'entrée de ${nom}`, `Critères Graham de ${nom} ?`];
    case 'portefeuille': return ['Analyse mon portefeuille', 'Quel est le statut des bots ?'];
    default: return ['Score de l\'or aujourd\'hui', 'Top 5 du CAC 40'];
  }
}

// Libellé de la puce de contexte de l'agent (« Fiche LVMH »…).
function contextLabel(hash, companyName) {
  const r = decoupeRoute(hash);
  const nom = companyName || r.ticker;
  return {
    or: 'Or', indices: 'Indices', suivi: 'Suivi des signaux', compare: 'Comparateur',
    fiche: `Fiche ${nom}`, analyse: `Analyse ${nom}`, portefeuille: 'Portefeuille',
  }[r.section] || 'Accueil';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PLACEHOLDER_EXAMPLES, nextPlaceholderIndex, HOME_GUIDE, normalizeText, filterGuide,
    decoupeRoute, suggestionsForRoute, contextLabel };
}
