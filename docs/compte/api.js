// docs/compte/api.js
// Client d'appel au service compte. Le site appelle /api/compte/... sur son
// propre domaine : le rewrite Vercel (ou scripts/dev_compte.py en local)
// relaie vers le VPS. Le cookie de session est HttpOnly : jamais lu ici.
(function () {
  const BASE = '/api/compte';

  // Erreur normalisée : {statut, message, donnees}. statut 0 = service
  // injoignable (pas de réseau, ou site servi sans relais, ex. GitHub Pages).
  class CompteErreur extends Error {
    constructor(statut, message, donnees) {
      super(message);
      this.statut = statut;
      this.donnees = donnees || null;
    }
  }

  async function compteFetch(chemin, options = {}) {
    const methode = (options.method || 'GET').toUpperCase();
    const enTetes = { Accept: 'application/json', ...(options.headers || {}) };
    let corps = options.body;
    if (corps !== undefined && typeof corps !== 'string') {
      corps = JSON.stringify(corps);
      enTetes['Content-Type'] = 'application/json';
    }
    // En-tête anti-CSRF exigé par le service sur toute écriture.
    if (methode !== 'GET') enTetes['X-Requested-With'] = 'compte';

    let reponse;
    try {
      reponse = await fetch(BASE + chemin, {
        method: methode, headers: enTetes, body: corps, credentials: 'same-origin',
      });
    } catch (e) {
      throw new CompteErreur(0, 'Service de compte injoignable pour l’instant.');
    }
    if (reponse.status === 204) return null;
    let donnees = null;
    try { donnees = await reponse.json(); } catch (e) { /* réponse vide ou non JSON */ }
    if (!reponse.ok) {
      const message = (donnees && typeof donnees.detail === 'string')
        ? donnees.detail
        : (reponse.status === 404 ? 'Service de compte indisponible.' : 'Une erreur est survenue, réessaie.');
      throw new CompteErreur(reponse.status === 404 && !donnees ? 0 : reponse.status, message, donnees);
    }
    return donnees;
  }

  window.Compte = window.Compte || {};
  window.Compte.fetch = compteFetch;
  window.Compte.Erreur = CompteErreur;
})();
