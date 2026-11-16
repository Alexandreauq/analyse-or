// docs/compte/session.js
// État de connexion partagé par toutes les fonctionnalités du compte.
// Chaque changement émet l'événement window 'compte:change' (detail = le
// membre connecté, ou null). Dépend de docs/compte/api.js.
(function () {
  let membre = null;
  let disponible = false;   // le service répond-il ? (faux sur un site sans relais)
  let chargement = null;

  function publier(nouveau) {
    membre = nouveau;
    window.dispatchEvent(new CustomEvent('compte:change', { detail: membre }));
  }

  // Interroge /me. Un 401 = visiteur non connecté ; un statut 0 = service
  // injoignable : dans les deux cas le site continue en mode local.
  async function chargerSession() {
    if (chargement) return chargement;
    chargement = (async () => {
      try {
        const moi = await window.Compte.fetch('/me');
        disponible = true;
        publier(moi);
      } catch (e) {
        disponible = e.statut !== 0;
        publier(null);
      } finally {
        chargement = null;
      }
      return membre;
    })();
    return chargement;
  }

  window.Compte = window.Compte || {};
  Object.assign(window.Compte, {
    chargerSession,
    membre: () => membre,
    estConnecte: () => membre !== null,
    serviceDisponible: () => disponible,
    // À appeler après une connexion, une déconnexion ou un changement de profil.
    definirMembre: publier,
    // Abonnement pratique : rappelle tout de suite avec l'état actuel.
    surChangement(fn) {
      window.addEventListener('compte:change', e => fn(e.detail));
      fn(membre);
    },
  });
})();
