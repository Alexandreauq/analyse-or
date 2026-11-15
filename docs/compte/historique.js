// docs/compte/historique.js
// Historique des recherches et des échanges avec l'assistant IA, questions à
// l'assistant via le service compte (quota quotidien), compteur de quota.
// Dépend de docs/compte/api.js et docs/compte/session.js. Script classique.
//
// Événements window émis :
//   'compte:quota'  detail = { restant }  (nombre, ou null = illimité)
(function () {
  const BASE = '/api/compte';
  // Questions restantes aujourd'hui : nombre, null (administrateur, illimité)
  // ou undefined (inconnu : visiteur non connecté).
  let quota;

  function publierQuota(restant) {
    quota = restant;
    window.dispatchEvent(new CustomEvent('compte:quota', { detail: { restant } }));
  }

  function lireQuotaEnTete(reponse) {
    const brut = reponse.headers.get('X-Quota-Restant');
    if (brut === null || brut === '') return undefined;
    const n = Number(brut);
    return Number.isFinite(n) ? n : undefined;
  }

  // Le quota suit l'état de connexion : /me donne quota_ia_restant (null
  // pour l'administrateur).
  function suivreMembre(membre) {
    if (!membre) { quota = undefined; return; }
    quota = membre.role === 'admin' ? null
      : (typeof membre.quota_ia_restant === 'number' ? membre.quota_ia_restant : undefined);
  }
  window.addEventListener('compte:change', (e) => suivreMembre(e.detail));
  // Session déjà chargée avant ce script : on reprend son état.
  if (window.Compte && typeof window.Compte.membre === 'function') suivreMembre(window.Compte.membre());

  // Découpe un flux SSE en événements JSON (même format que l'assistant :
  // {type: 'texte'|'liens'|'actus'|'navigation'|'erreur'|'fin', ...}).
  function lireBlocs(buffer, rappel) {
    const blocs = buffer.split('\n\n');
    const reste = blocs.pop();
    for (const bloc of blocs) {
      if (!bloc.startsWith('data: ')) continue;
      let evenement;
      try { evenement = JSON.parse(bloc.slice(6)); } catch (e) { continue; }
      rappel(evenement);
    }
    return reste;
  }

  // Pose une question à l'assistant au nom du membre connecté.
  // payload = {question, history?, manual_positions?, niveau?, contexte?}.
  // onEvenement(evenement) est rappelé pour chaque événement du flux, comme
  // le rendu actuel du site les lit. Résout {texte, erreur} à la fin du flux.
  // Rejette une window.Compte.Erreur ({statut, message, donnees}) : 401 non
  // connecté, 403 adresse non confirmée, 429 quota épuisé ou assistant
  // saturé, 503 assistant indisponible, 0 service injoignable. Une
  // annulation (signal) rejette l'AbortError d'origine.
  async function demanderIA(payload, options = {}) {
    const { onEvenement, signal } = options;
    const Erreur = window.Compte.Erreur;
    let reponse;
    try {
      reponse = await fetch(BASE + '/ai/ask', {
        method: 'POST',
        credentials: 'same-origin',
        signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'X-Requested-With': 'compte',
        },
        body: JSON.stringify(payload || {}),
      });
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new Erreur(0, 'Service de compte injoignable pour l’instant.');
    }

    const restant = lireQuotaEnTete(reponse);
    if (!reponse.ok) {
      let donnees = null;
      try { donnees = await reponse.json(); } catch (e) { /* réponse non JSON */ }
      if (donnees && typeof donnees.quota_restant === 'number') publierQuota(donnees.quota_restant);
      else if (restant !== undefined) publierQuota(restant);
      const message = (donnees && typeof donnees.detail === 'string')
        ? donnees.detail
        : (reponse.status === 404 ? 'Service de compte indisponible.' : "L'assistant ne peut pas répondre pour le moment.");
      throw new Erreur(reponse.status === 404 && !donnees ? 0 : reponse.status, message, donnees);
    }
    if (restant !== undefined) publierQuota(restant);

    let texte = '';
    let erreur = false;
    const traiter = (evenement) => {
      if (evenement.type === 'texte' && typeof evenement.texte === 'string') texte += evenement.texte;
      if (evenement.type === 'erreur') erreur = true;
      if (typeof onEvenement === 'function') onEvenement(evenement);
    };

    const lecteur = reponse.body.getReader();
    const decodeur = new TextDecoder();
    let buffer = '';
    try {
      while (true) {
        const { done, value } = await lecteur.read();
        if (done) break;
        buffer = lireBlocs(buffer + decodeur.decode(value, { stream: true }), traiter);
      }
      lireBlocs(buffer + decodeur.decode() + '\n\n', traiter);
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new Erreur(0, "La connexion avec l'assistant a été interrompue.");
    }
    // Sans aucun texte, le service rend la question : le compteur aussi.
    if (!texte && typeof restant === 'number') publierQuota(restant + 1);
    return { texte, erreur };
  }

  const historique = {
    // [{id, query, created_at}], plus récentes d'abord.
    recherches: () => window.Compte.fetch('/searches'),
    ajouterRecherche: (q) => window.Compte.fetch('/searches', { method: 'POST', body: { query: String(q || '') } }),
    effacerRecherche: (id) => window.Compte.fetch(`/searches/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    effacerRecherches: () => window.Compte.fetch('/searches', { method: 'DELETE' }),
    // [{id, question, answer, created_at}], plus récents d'abord (100 au plus).
    echangesIA: () => window.Compte.fetch('/ai/history'),
    effacerEchange: (id) => window.Compte.fetch(`/ai/history/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    effacerTout: () => window.Compte.fetch('/ai/history', { method: 'DELETE' }),
  };

  function libelleQuota(n) {
    if (n <= 0) return 'Plus de questions aujourd’hui : reviens demain !';
    return n === 1 ? '1 question restante aujourd’hui' : `${n} questions restantes aujourd’hui`;
  }

  // Compteur « 7 questions restantes aujourd'hui » dans `element` (vide et
  // masqué pour un visiteur ou l'administrateur).
  function monterQuota(element) {
    if (!element) return;
    element.classList.add('compte-quota');
    element.setAttribute('aria-live', 'polite');
    const rendre = () => {
      const visible = typeof quota === 'number';
      element.hidden = !visible;
      element.textContent = visible ? libelleQuota(quota) : '';
      element.classList.toggle('compte-quota--epuise', visible && quota <= 0);
    };
    // Écouteur ajouté après celui du module : quota est déjà à jour.
    window.addEventListener('compte:change', rendre);
    window.addEventListener('compte:quota', rendre);
    rendre();
  }

  window.Compte = window.Compte || {};
  Object.assign(window.Compte, {
    demanderIA,
    historique,
    monterQuota,
    quotaRestant: () => quota,
  });
})();
