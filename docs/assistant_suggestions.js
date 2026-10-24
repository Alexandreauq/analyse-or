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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PLACEHOLDER_EXAMPLES, nextPlaceholderIndex };
}
