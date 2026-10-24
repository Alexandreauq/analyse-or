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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PLACEHOLDER_EXAMPLES, nextPlaceholderIndex, HOME_GUIDE, normalizeText, filterGuide };
}
