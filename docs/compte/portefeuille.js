// docs/compte/portefeuille.js
// Portefeuille sur le compte + import des données de cet appareil.
//
// Le site appelle les fonctions de docs/portfolio.js de façon SYNCHRONE
// (loadPortfolio, savePortfolio, closePosition…) : leurs signatures ne
// changent pas. Quand le membre est connecté, la page leur passe en
// paramètre `storage` l'objet Compte.portefeuille.storage(), compatible
// localStorage (getItem/setItem) :
//   - getItem lit un cache en mémoire chargé depuis l'API à la connexion,
//     au format exact de portfolio.js ;
//   - setItem met le cache à jour tout de suite, puis synchronise avec
//     l'API en arrière-plan en calculant la différence (ajout,
//     modification, clôture, suppression). En cas d'échec, l'état du
//     serveur est restauré et l'événement 'compte:portefeuille-erreur'
//     est émis (detail.message, en français).
// Les identifiants du serveur sont exposés sous la forme 'srv-<id>',
// jamais mélangés avec les 'pos-…' créés localement par portfolio.js.
// Chaque changement du cache émet 'compte:portefeuille' : la page se
// redessine.
//
// Dépend de docs/compte/api.js et docs/compte/session.js. Script classique
// (pas de module ES) ; le bloc `module.exports` en bas sert aux tests Node
// (docs/compte/portefeuille.test.js).
(function (racineGlobale) {
  const CLE_OUVERTES = 'analyse-or-portfolio';
  const CLE_CLOTUREES = 'analyse-or-portfolio-closed';
  const CLE_HISTORIQUE = 'analyse-or-assistant-history';
  const CLE_FILTRES = 'analyse-or-saved-screeners';
  const CLE_NIVEAU = 'analyse-or-answer-level';
  const CLE_THEME = 'theme';
  const PREFIXE_SRV = 'srv-';
  const CLE_IMPORT_PROPOSE = 'analyse-or-import-propose-';
  const CHAMPS_ACHAT = ['ticker', 'quantity', 'buy_price', 'buy_date'];
  const CHAMPS_VENTE = ['sell_price', 'sell_date'];

  // --- Conversion et différence (fonctions pures, testées sous Node) -----

  // Position renvoyée par l'API -> forme de docs/portfolio.js.
  function versSite(p) {
    const position = {
      id: PREFIXE_SRV + p.id,
      ticker: p.ticker,
      quantity: p.quantity,
      buy_price: p.buy_price,
      buy_date: p.buy_date,
    };
    if (p.sell_date) {
      position.sell_price = p.sell_price;
      position.sell_date = p.sell_date;
    }
    return position;
  }

  function separer(positionsApi) {
    const etat = { ouvertes: [], cloturees: [] };
    (positionsApi || []).forEach(p => {
      (p.sell_date ? etat.cloturees : etat.ouvertes).push(versSite(p));
    });
    return etat;
  }

  function idServeur(id) {
    if (typeof id !== 'string' || !id.startsWith(PREFIXE_SRV)) return null;
    const n = Number(id.slice(PREFIXE_SRV.length));
    return Number.isInteger(n) && n > 0 ? n : null;
  }

  function champsModifies(avant, apres, champs) {
    const modifies = {};
    champs.forEach(c => {
      if (apres[c] !== avant[c]) modifies[c] = apres[c];
    });
    return modifies;
  }

  function extraire(position, champs) {
    const r = {};
    champs.forEach(c => { r[c] = position[c]; });
    return r;
  }

  class ModificationNonPriseEnCharge extends Error {}

  /**
   * Opérations à envoyer à l'API pour passer de `avant` (état confirmé par
   * le serveur) à `apres` (cache local), chacun {ouvertes, cloturees} au
   * format portfolio.js. Ordre : suppressions, modifications, clôtures,
   * créations. Lève ModificationNonPriseEnCharge pour un changement que
   * l'API ne sait pas faire (rouvrir une position clôturée).
   */
  function calculerOperations(avant, apres) {
    const connues = new Map();
    avant.ouvertes.forEach(p => connues.set(p.id, { position: p, close: false }));
    avant.cloturees.forEach(p => connues.set(p.id, { position: p, close: true }));
    const vues = new Set();
    const suppressions = [];
    const modifications = [];
    const clotures = [];
    const creations = [];

    const traiter = (position, close) => {
      if (!position || typeof position !== 'object') return;
      const id = idServeur(position.id);
      const connue = id !== null ? connues.get(position.id) : null;
      if (!connue) {
        // Nouvelle position (id local 'pos-…' de createPosition).
        creations.push({
          type: 'creer', idLocal: position.id,
          achat: extraire(position, CHAMPS_ACHAT),
          vente: close ? extraire(position, CHAMPS_VENTE) : null,
        });
        return;
      }
      vues.add(position.id);
      if (connue.close && !close) {
        throw new ModificationNonPriseEnCharge('Rouvrir une position clôturée n’est pas possible.');
      }
      const champs = connue.close ? CHAMPS_ACHAT.concat(CHAMPS_VENTE) : CHAMPS_ACHAT;
      const modifies = champsModifies(connue.position, position, champs);
      if (Object.keys(modifies).length) modifications.push({ type: 'modifier', id, champs: modifies });
      if (close && !connue.close) clotures.push({ type: 'cloturer', id, vente: extraire(position, CHAMPS_VENTE) });
    };
    (apres.ouvertes || []).forEach(p => traiter(p, false));
    (apres.cloturees || []).forEach(p => traiter(p, true));
    connues.forEach((_, idSite) => {
      if (!vues.has(idSite)) suppressions.push({ type: 'supprimer', id: idServeur(idSite) });
    });
    return suppressions.concat(modifications, clotures, creations);
  }

  // --- Cache et synchronisation ----------------------------------------

  const fenetre = racineGlobale.window || racineGlobale;
  const Compte = (fenetre.Compte = fenetre.Compte || {});

  let membreId = null;     // membre dont le cache est chargé
  let serveur = null;      // dernier état confirmé par l'API {ouvertes, cloturees}
  let cache = null;        // état courant vu par la page
  let sale = false;        // cache modifié depuis la dernière synchronisation
  let synchro = null;      // synchronisation en cours (Promise) ou null
  let minuterie = null;
  let generation = 0;      // change à chaque connexion/déconnexion
  const correspondances = new Map(); // 'pos-…' local -> 'srv-<id>' après création
  let pret = Promise.resolve();

  function copie(etat) {
    return { ouvertes: etat.ouvertes.slice(), cloturees: etat.cloturees.slice() };
  }

  function emettre(nom, detail) {
    if (typeof fenetre.dispatchEvent !== 'function' || typeof CustomEvent === 'undefined') return;
    fenetre.dispatchEvent(new CustomEvent(nom, { detail }));
  }

  function remplacerCache(etat) {
    const avant = cache ? JSON.stringify(cache) : null;
    cache = etat ? copie(etat) : null;
    if (JSON.stringify(cache) !== avant) emettre('compte:portefeuille', { connecte: cache !== null });
  }

  async function lireServeur() {
    return separer(await Compte.fetch('/positions?statut=toutes'));
  }

  function recharger() {
    const gen = generation;
    pret = lireServeur().then(etat => {
      if (gen !== generation) return;
      serveur = etat;
      sale = false;
      remplacerCache(etat);
    }).catch(e => {
      if (gen !== generation) return;
      emettre('compte:portefeuille-erreur', {
        message: 'Impossible de charger le portefeuille de ton compte. ' + (e && e.message ? e.message : ''),
      });
    });
    return pret;
  }

  async function appliquer(op) {
    if (op.type === 'supprimer') {
      try {
        await Compte.fetch(`/positions/${op.id}`, { method: 'DELETE' });
      } catch (e) {
        if (e.statut !== 404) throw e; // déjà supprimée ailleurs : objectif atteint
      }
    } else if (op.type === 'modifier') {
      await Compte.fetch(`/positions/${op.id}`, { method: 'PATCH', body: op.champs });
    } else if (op.type === 'cloturer') {
      await Compte.fetch(`/positions/${op.id}/close`, { method: 'PATCH', body: op.vente });
    } else if (op.type === 'creer') {
      const cree = await Compte.fetch('/positions', { method: 'POST', body: op.achat });
      if (op.idLocal) correspondances.set(op.idLocal, PREFIXE_SRV + cree.id);
      if (op.vente) await Compte.fetch(`/positions/${cree.id}/close`, { method: 'PATCH', body: op.vente });
    }
  }

  // Remplace les ids locaux déjà créés sur le serveur par leur 'srv-<id>'
  // (cas d'une position modifiée pendant que sa création était en cours).
  function remapper(etat) {
    const fixer = p => (p && correspondances.has(p.id) ? { ...p, id: correspondances.get(p.id) } : p);
    return { ouvertes: etat.ouvertes.map(fixer), cloturees: etat.cloturees.map(fixer) };
  }

  async function synchroniser() {
    const gen = generation;
    while (sale && gen === generation) {
      sale = false;
      const cible = remapper(cache);
      try {
        const operations = calculerOperations(serveur, cible);
        for (const op of operations) {
          if (gen !== generation) return;
          await appliquer(op);
        }
        const etat = operations.length ? await lireServeur() : serveur;
        if (gen !== generation) return;
        serveur = etat;
        // Rien de nouveau pendant l'envoi : la page reprend l'état du serveur
        // (ids 'srv-…' définitifs). Sinon, on repart pour un tour.
        if (!sale) remplacerCache(serveur);
      } catch (e) {
        if (gen !== generation) return;
        sale = false;
        try { serveur = await lireServeur(); } catch (e2) { /* on garde le dernier état connu */ }
        if (gen !== generation) return;
        remplacerCache(serveur);
        const detail = (e && e.message) ? e.message : 'erreur inconnue';
        emettre('compte:portefeuille-erreur', {
          message: `La modification du portefeuille n’a pas pu être enregistrée sur ton compte (${detail}). `
            + 'Le portefeuille a été rétabli tel qu’il est enregistré.',
        });
        return;
      }
    }
  }

  function planifierSynchro() {
    sale = true;
    // closePosition appelle savePortfolio puis saveClosedPortfolio coup sur
    // coup : on attend la fin du code synchrone pour voir une clôture (et
    // non une suppression suivie d'un ajout).
    if (minuterie) return;
    minuterie = setTimeout(() => {
      minuterie = null;
      if (synchro) return; // la boucle en cours verra `sale`
      synchro = synchroniser().finally(() => {
        synchro = null;
        if (sale && cache) planifierSynchro();
      });
    }, 0);
  }

  // Objet compatible localStorage, à passer comme `storage` à portfolio.js.
  const portefeuilleStorage = {
    getItem(cle) {
      if (cle === CLE_OUVERTES) return cache ? JSON.stringify(cache.ouvertes) : null;
      if (cle === CLE_CLOTUREES) return cache ? JSON.stringify(cache.cloturees) : null;
      return typeof localStorage !== 'undefined' ? localStorage.getItem(cle) : null;
    },
    setItem(cle, valeur) {
      if (cle !== CLE_OUVERTES && cle !== CLE_CLOTUREES) {
        if (typeof localStorage !== 'undefined') localStorage.setItem(cle, valeur);
        return;
      }
      // Pas encore chargé : on refuse (portfolio.js affiche alors « La
      // sauvegarde a échoué ») plutôt que d'écraser le compte.
      if (!cache) throw new Error('Portefeuille du compte pas encore chargé.');
      const liste = JSON.parse(valeur);
      if (!Array.isArray(liste)) throw new Error('Format de portefeuille invalide.');
      cache = cle === CLE_OUVERTES
        ? { ouvertes: liste, cloturees: cache.cloturees }
        : { ouvertes: cache.ouvertes, cloturees: liste };
      emettre('compte:portefeuille', { connecte: true });
      planifierSynchro();
    },
    removeItem(cle) {
      if (cle === CLE_OUVERTES || cle === CLE_CLOTUREES) this.setItem(cle, '[]');
      else if (typeof localStorage !== 'undefined') localStorage.removeItem(cle);
    },
  };

  function changerMembre(membre) {
    const id = membre ? membre.id : null;
    if (id === membreId) return;
    generation++;
    membreId = id;
    serveur = null;
    sale = false;
    correspondances.clear();
    if (minuterie) { clearTimeout(minuterie); minuterie = null; }
    if (id === null) {
      pret = Promise.resolve();
      remplacerCache(null);
      return;
    }
    remplacerCache(null);
    recharger();
  }

  if (typeof fenetre.addEventListener === 'function') {
    fenetre.addEventListener('compte:change', e => changerMembre(e.detail));
  }
  // Session déjà chargée avant ce script : on rattrape l'état actuel.
  if (typeof Compte.membre === 'function' && Compte.membre()) changerMembre(Compte.membre());

  Compte.portefeuilleStorage = portefeuilleStorage;
  Compte.portefeuille = {
    // Promise résolue quand le portefeuille du membre est chargé (ou tout
    // de suite si personne n'est connecté).
    get pret() { return pret; },
    recharger,
    // À passer en `storage` à loadPortfolio, savePortfolio, addPosition…
    storage() {
      return membreId !== null ? portefeuilleStorage : (typeof localStorage !== 'undefined' ? localStorage : null);
    },
  };

  // --- Import des données de cet appareil --------------------------------

  function lireLocal(cle) {
    try { return localStorage.getItem(cle); } catch (e) { return null; }
  }

  function lireListeLocale(cle) {
    try {
      const v = JSON.parse(lireLocal(cle) || '[]');
      return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
  }

  // Données locales au format attendu par POST /import (rien n'est modifié
  // ni supprimé dans le navigateur).
  function donneesLocales() {
    const reglages = {};
    const theme = lireLocal(CLE_THEME);
    if (theme === 'light' || theme === 'dark') reglages.theme = theme;
    const niveau = lireLocal(CLE_NIVEAU);
    if (niveau === 'technique' || niveau === 'grand_public') reglages.niveau_reponse = niveau;
    const filtres = lireListeLocale(CLE_FILTRES).filter(f => f && typeof f.name === 'string');
    if (filtres.length) reglages.filtres_sauvegardes = filtres;
    return {
      positions: lireListeLocale(CLE_OUVERTES),
      positions_cloturees: lireListeLocale(CLE_CLOTUREES),
      historique_ia: lireListeLocale(CLE_HISTORIQUE),
      reglages,
    };
  }

  function pluriel(n, singulier, plurielMot) {
    return `${n} ${n > 1 ? plurielMot : singulier}`;
  }

  // Résumé lisible ; liste vide = rien qui mérite une proposition. Le thème
  // et le niveau de réponse seuls ne déclenchent pas la fenêtre (ils sont
  // quand même envoyés avec le reste).
  function resumer(d) {
    const lignes = [];
    if (d.positions.length) lignes.push(pluriel(d.positions.length, 'position ouverte', 'positions ouvertes'));
    if (d.positions_cloturees.length) {
      lignes.push(pluriel(d.positions_cloturees.length, 'position clôturée', 'positions clôturées'));
    }
    if (d.historique_ia.length) {
      lignes.push(pluriel(d.historique_ia.length, 'conversation avec l’assistant', 'conversations avec l’assistant'));
    }
    if (d.reglages.filtres_sauvegardes) {
      lignes.push(pluriel(d.reglages.filtres_sauvegardes.length, 'filtre sauvegardé', 'filtres sauvegardés'));
    }
    return lignes;
  }

  function resumerResultat(r) {
    const i = r.importees || {};
    const g = r.ignorees || {};
    const morceaux = [];
    if (i.positions) morceaux.push(pluriel(i.positions, 'position ouverte', 'positions ouvertes'));
    if (i.positions_cloturees) morceaux.push(pluriel(i.positions_cloturees, 'position clôturée', 'positions clôturées'));
    if (i.historique_ia) morceaux.push(pluriel(i.historique_ia, 'conversation', 'conversations'));
    if (i.reglages) morceaux.push(pluriel(i.reglages, 'réglage', 'réglages'));
    let texte = morceaux.length ? `Importé sur ton compte : ${morceaux.join(', ')}.` : 'Rien de nouveau à importer.';
    const ignores = Object.values(g).reduce((s, n) => s + (n || 0), 0);
    if (ignores) texte += ` ${pluriel(ignores, 'élément ignoré', 'éléments ignorés')} (déjà présent ou invalide).`;
    return texte + ' Les données de cet appareil restent en place.';
  }

  function cleProposition(id) { return CLE_IMPORT_PROPOSE + id; }

  function creer(tag, classe, texte) {
    const el = document.createElement(tag);
    if (classe) el.className = classe;
    if (texte !== undefined) el.textContent = texte;
    return el;
  }

  /**
   * À la première connexion sur un appareil qui a des données locales,
   * propose de les importer sur le compte. Une seule proposition par
   * membre et par appareil (mémorisée en localStorage), sauf
   * {forcer: true} (ex. bouton dans « Mon compte »). Ne supprime jamais
   * les données locales. Résout avec le résultat de /import, ou null.
   */
  function proposerImport(options) {
    const opts = options || {};
    const membre = Compte.membre ? Compte.membre() : null;
    if (!membre) return Promise.resolve(null);
    const cle = cleProposition(membre.id);
    if (!opts.forcer && lireLocal(cle)) return Promise.resolve(null);
    const donnees = donneesLocales();
    const lignes = resumer(donnees);
    if (!lignes.length) return Promise.resolve(null);
    try { localStorage.setItem(cle, new Date().toISOString()); } catch (e) { /* re-proposé plus tard */ }

    return new Promise(resolve => {
      const racine = opts.racine || document.body;
      const voile = creer('div', 'compte-import-voile');
      const boite = creer('div', 'compte-import');
      boite.setAttribute('role', 'dialog');
      boite.setAttribute('aria-modal', 'true');
      boite.setAttribute('aria-labelledby', 'compteImportTitre');
      const titre = creer('h2', 'compte-import-titre', 'Importer les données de cet appareil sur ton compte ?');
      titre.id = 'compteImportTitre';
      const intro = creer('p', 'compte-import-texte', 'Cet appareil contient :');
      const liste = creer('ul', 'compte-import-liste');
      lignes.forEach(l => liste.appendChild(creer('li', null, l)));
      const note = creer('p', 'compte-import-note',
        'Les doublons sont ignorés et rien n’est effacé de cet appareil.');
      const message = creer('p', 'compte-import-message');
      message.setAttribute('role', 'status');
      message.hidden = true;
      const actions = creer('div', 'compte-import-actions');
      const plusTard = creer('button', 'compte-import-btn', 'Plus tard');
      plusTard.type = 'button';
      const importer = creer('button', 'compte-import-btn compte-import-btn-principal', 'Importer');
      importer.type = 'button';
      actions.append(plusTard, importer);
      boite.append(titre, intro, liste, note, message, actions);
      voile.appendChild(boite);
      racine.appendChild(voile);
      importer.focus();

      let resultat = null;
      const fermer = () => {
        document.removeEventListener('keydown', surTouche);
        voile.remove();
        resolve(resultat);
      };
      const surTouche = e => { if (e.key === 'Escape' && !importer.disabled) fermer(); };
      document.addEventListener('keydown', surTouche);
      plusTard.addEventListener('click', fermer);
      voile.addEventListener('click', e => { if (e.target === voile && !importer.disabled) fermer(); });

      importer.addEventListener('click', async () => {
        if (resultat) { fermer(); return; }
        importer.disabled = true;
        plusTard.disabled = true;
        importer.textContent = 'Import en cours…';
        message.hidden = true;
        try {
          resultat = await Compte.fetch('/import', { method: 'POST', body: donnees });
          message.textContent = resumerResultat(resultat);
          message.className = 'compte-import-message';
          message.hidden = false;
          intro.hidden = liste.hidden = note.hidden = true;
          titre.textContent = 'Import terminé';
          plusTard.hidden = true;
          importer.textContent = 'Fermer';
          importer.disabled = false;
          importer.focus();
          emettre('compte:import', resultat);
          recharger();
        } catch (e) {
          message.textContent = `L’import a échoué : ${e.message || 'erreur inconnue'}. Tu peux réessayer.`;
          message.className = 'compte-import-message compte-import-erreur';
          message.hidden = false;
          importer.textContent = 'Réessayer';
          importer.disabled = false;
          plusTard.disabled = false;
        }
      });
    });
  }

  Compte.proposerImport = proposerImport;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      versSite, separer, idServeur, calculerOperations, ModificationNonPriseEnCharge,
      donneesLocales, resumer, resumerResultat, portefeuilleStorage,
    };
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
