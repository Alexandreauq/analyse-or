// docs/scalping.js
// Module Scalping Or (1min) — calcul entièrement côté navigateur, aucune
// donnée ne transite par le pipeline Python/GitHub Actions. Voir
// docs/superpowers/specs/2026-09-09-gold-scalping-design.md pour le design
// complet (règles issues du cours d'Analyse Technique L3 Dauphine).
//
// Ce fichier est chargé en <script> classique (pas de module ES) pour
// rester cohérent avec docs/index.html. Le bloc `if (typeof module...)`
// en bas de fichier permet aussi de l'importer depuis Node pour les tests
// (docs/scalping.test.js), sans framework ni bundler.

const TWELVE_DATA_URL = 'https://api.twelvedata.com/time_series';

/**
 * Récupère les dernières bougies 1min XAU/USD via Twelve Data.
 * `fetchImpl` est injectable (tests) — vaut `fetch` par défaut (navigateur).
 * Renvoie un tableau chronologique (plus ancien en premier), jamais vide
 * en cas de succès. Rejette avec une Error au message clair en cas
 * d'échec réseau, de quota dépassé, ou de réponse Twelve Data invalide.
 */
async function fetchGoldCandles(apiKey, fetchImpl = fetch) {
  // timezone=UTC explicite : par défaut Twelve Data renvoie l'heure locale
  // de l'exchange (pas UTC) — sans ce paramètre, le contrôle de fraîcheur
  // des données (docs/index.html, refreshScalpSignal) comparerait des
  // horaires dans deux fuseaux différents et se tromperait systématiquement.
  const url = `${TWELVE_DATA_URL}?symbol=XAU/USD&interval=1min&outputsize=90&timezone=UTC&apikey=${encodeURIComponent(apiKey)}`;
  let response;
  try {
    response = await fetchImpl(url);
  } catch (e) {
    throw new Error(`Impossible de contacter Twelve Data : ${e.message}`);
  }
  if (!response.ok) {
    throw new Error(`Twelve Data a répondu ${response.status}`);
  }
  const data = await response.json();
  if (data.status === 'error' || !Array.isArray(data.values)) {
    throw new Error(`Réponse Twelve Data invalide : ${data.message || 'pas de données'}`);
  }
  // Twelve Data renvoie le plus récent en premier — on inverse pour avoir
  // un ordre chronologique, attendu par toutes les fonctions de calcul
  // de ce module (pivots, indicateurs, patterns).
  return data.values
    .map(v => ({
      time: v.datetime,
      open: parseFloat(v.open),
      high: parseFloat(v.high),
      low: parseFloat(v.low),
      close: parseFloat(v.close),
    }))
    .reverse();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fetchGoldCandles };
}
