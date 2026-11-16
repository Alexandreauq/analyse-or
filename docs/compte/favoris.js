// docs/compte/favoris.js
// Favoris du membre : indices (clé type CAC40) et entreprises (ticker).
// Dépend de docs/compte/api.js et docs/compte/session.js (chargés avant).
//
// Expose sur window.Compte :
//   favoris = { charger(), estFavori(kind, key), basculer(kind, key), liste() }
//   monterEtoile(element, kind, key)      bouton étoile dans `element`
//   monterMesSuivis(racine, indicesData)  section « Mes suivis » dans `racine`
// Émet l'événement window 'compte:favoris' (detail = { liste }) après chaque
// changement du cache (chargement, ajout, retrait, retour arrière, déconnexion).
(function () {
  const Compte = window.Compte = window.Compte || {};

  const MSG_CONNEXION = 'Connecte-toi pour suivre tes favoris.';
  const MSG_ECHEC = 'Impossible de mettre à jour tes favoris, réessaie.';

  // ---------------------------------------------------------------- cache

  let cache = new Map();      // "kind:key" -> {kind, key, created_at}
  let charge = false;         // la liste du membre connecté a-t-elle été reçue ?
  let generation = 0;         // ignore une réponse arrivée après un changement de session
  const enCours = new Map();  // "kind:key" -> promesse de la requête en cours

  const id = (kind, key) => kind + ':' + key;

  function liste() {
    return [...cache.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  }

  function emettre() {
    window.dispatchEvent(new CustomEvent('compte:favoris', { detail: { liste: liste() } }));
  }

  function vider() {
    generation++;
    cache = new Map();
    charge = false;
    enCours.clear();
    emettre();
  }

  // Recharge la liste depuis le service. En cas d'échec (service injoignable),
  // le cache reste tel quel : le site continue de fonctionner sans favoris.
  async function charger() {
    if (!Compte.estConnecte || !Compte.estConnecte()) {
      vider();
      return [];
    }
    const gen = ++generation;
    try {
      const donnees = await Compte.fetch('/favorites');
      if (gen !== generation) return liste();
      cache = new Map((donnees || []).map(f => [id(f.kind, f.key), f]));
      charge = true;
    } catch (e) {
      if (gen !== generation) return liste();
      charge = true;  // on affiche ce qu'on a plutôt qu'un « chargement » sans fin
    }
    emettre();
    return liste();
  }

  function estFavori(kind, key) {
    return cache.has(id(kind, key));
  }

  // Ajoute ou retire, avec mise à jour optimiste du cache. Rejette avec une
  // erreur {statut, message} si le service refuse (le cache est alors remis
  // dans son état précédent). Un second appel sur la même cible pendant
  // qu'une requête est en cours renvoie la même promesse (pas de course).
  function basculer(kind, key) {
    const k = id(kind, key);
    if (enCours.has(k)) return enCours.get(k);
    if (!Compte.estConnecte || !Compte.estConnecte()) {
      const err = Compte.Erreur ? new Compte.Erreur(401, MSG_CONNEXION) : new Error(MSG_CONNEXION);
      err.statut = 401;
      return Promise.reject(err);
    }
    const gen = generation;
    const avant = cache.get(k);
    const chemin = '/favorites/' + encodeURIComponent(kind) + '/' + encodeURIComponent(key);
    if (avant) {
      cache.delete(k);
    } else {
      cache.set(k, { kind, key, created_at: new Date().toISOString() });
    }
    emettre();

    const promesse = (async () => {
      try {
        if (avant) {
          await Compte.fetch(chemin, { method: 'DELETE' });
        } else {
          const fav = await Compte.fetch(chemin, { method: 'PUT' });
          if (gen === generation && fav) {
            cache.set(k, fav);
            emettre();
          }
        }
        return !avant;
      } catch (e) {
        // Retour arrière, sauf si la session a changé entre-temps.
        if (gen === generation) {
          if (avant) cache.set(k, avant); else cache.delete(k);
          emettre();
        }
        throw e;
      } finally {
        if (enCours.get(k) === promesse) enCours.delete(k);
      }
    })();
    enCours.set(k, promesse);
    return promesse;
  }

  Compte.favoris = { charger, estFavori, basculer, liste };

  // Rechargé à chaque changement de session, vidé à la déconnexion.
  if (typeof Compte.surChangement === 'function') {
    let dernierMembre = null;
    Compte.surChangement(membre => {
      const idMembre = membre ? membre.id : null;
      // Autre membre (ou déconnexion) : on n'affiche jamais les favoris du précédent.
      if (idMembre !== dernierMembre) vider();
      dernierMembre = idMembre;
      if (membre) charger();
    });
  }

  // ----------------------------------------------------------- utilitaires

  function echapper(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function inviterConnexion(afficherMessage) {
    if (typeof Compte.ouvrirConnexion === 'function') {
      Compte.ouvrirConnexion('connexion');
    } else {
      afficherMessage(MSG_CONNEXION);
    }
  }

  // ----------------------------------------------------------------- étoile

  const SVG_ETOILE = '<svg class="compte-etoile-icone" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    + '<path d="M12 2.8l2.8 5.7 6.3.9-4.55 4.43 1.07 6.27L12 17.14 6.38 20.1l1.07-6.27L2.9 9.4l6.3-.9z"/></svg>';

  function rendreEtoile(bouton) {
    const actif = estFavori(bouton.dataset.favKind, bouton.dataset.favKey);
    const libelle = actif ? 'Retirer des favoris' : 'Ajouter aux favoris';
    bouton.setAttribute('aria-pressed', actif ? 'true' : 'false');
    bouton.setAttribute('aria-label', libelle);
    bouton.title = libelle;
  }

  // Une seule mise à jour globale pour toutes les étoiles de la page : pas
  // d'écouteur par étoile, donc rien à nettoyer quand une liste est
  // réaffichée (les étoiles retirées du DOM disparaissent simplement).
  function rendreToutesLesEtoiles() {
    document.querySelectorAll('button.compte-etoile[data-fav-kind]').forEach(rendreEtoile);
  }
  window.addEventListener('compte:favoris', rendreToutesLesEtoiles);
  window.addEventListener('compte:change', rendreToutesLesEtoiles);

  function monterEtoile(element, kind, key) {
    if (!element) return null;
    element.classList.add('compte-etoile-hote');
    element.innerHTML = '';

    const bouton = document.createElement('button');
    bouton.type = 'button';
    bouton.className = 'compte-etoile';
    bouton.dataset.favKind = kind;
    bouton.dataset.favKey = key;
    bouton.innerHTML = SVG_ETOILE;

    const message = document.createElement('span');
    message.className = 'compte-etoile-message';
    message.setAttribute('role', 'status');
    message.hidden = true;

    let minuterie = null;
    function afficherMessage(texte) {
      message.textContent = texte;
      message.hidden = false;
      clearTimeout(minuterie);
      minuterie = setTimeout(() => { message.hidden = true; message.textContent = ''; }, 3500);
    }

    // L'étoile vit souvent dans une ligne-lien (liste d'indices, aperçu) :
    // le clic ne doit jamais ouvrir la fiche.
    bouton.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      if (!Compte.estConnecte || !Compte.estConnecte()) {
        inviterConnexion(afficherMessage);
        return;
      }
      basculer(kind, key).catch(err => {
        afficherMessage(err && err.statut === 401 ? MSG_CONNEXION : ((err && err.message) || MSG_ECHEC));
      });
    });

    element.appendChild(bouton);
    element.appendChild(message);
    rendreEtoile(bouton);
    return bouton;
  }

  Compte.monterEtoile = monterEtoile;

  // ------------------------------------------------------------ mes suivis

  // Même logique de couleur que le site (companyRowHtml de index.html) :
  // vert si score >= 0, rouille sinon ; même format (+13.7).
  function scoreHtml(c) {
    if (c && typeof c.score === 'number') {
      const classe = c.score >= 0 ? 'compte-score-positif' : 'compte-score-negatif';
      return `<span class="compte-suivi-score ${classe}">${c.score > 0 ? '+' : ''}${c.score}</span>`;
    }
    if (c && c.is_etf && c.etf_info && typeof c.etf_info.ter_pct === 'number') {
      return `<span class="compte-suivi-score compte-suivi-ter">TER ${c.etf_info.ter_pct.toLocaleString('fr-FR')}%</span>`;
    }
    return '<span class="compte-suivi-score compte-suivi-ter">—</span>';
  }

  function nomsIndices(donnees) {
    // INDEX_DISPLAY_NAMES (index.html) en repli si le script le voit, puis
    // les noms fournis par indices.json, puis la clé brute.
    const locaux = (typeof INDEX_DISPLAY_NAMES !== 'undefined') ? INDEX_DISPLAY_NAMES : {};  // eslint-disable-line no-undef
    return { ...locaux, ...((donnees && donnees.index_names) || {}) };
  }

  function ligneEntreprise(fav, entreprises, noms) {
    const c = entreprises[fav.key];
    if (!c) {
      return `<li class="compte-suivi compte-suivi-absent">
        <span class="compte-suivi-lien">
          <span class="compte-suivi-nom">${echapper(fav.key)}</span>
          <span class="compte-suivi-meta">Plus suivie par le site pour l’instant</span>
        </span>
        <span class="compte-suivi-etoile" data-kind="company" data-key="${echapper(fav.key)}"></span>
      </li>`;
    }
    const indice = c.index ? (noms[c.index] || c.index) : '';
    return `<li class="compte-suivi">
      <a class="compte-suivi-lien" href="#indices/${encodeURIComponent(c.ticker)}">
        <span class="compte-suivi-nom">${echapper(c.name || c.ticker)}</span>
        <span class="compte-suivi-meta">${echapper(c.ticker)}${indice ? ' · ' + echapper(indice) : ''}</span>
      </a>
      ${scoreHtml(c)}
      <span class="compte-suivi-etoile" data-kind="company" data-key="${echapper(fav.key)}"></span>
    </li>`;
  }

  function ligneIndice(fav, compteParIndice, noms) {
    const n = compteParIndice[fav.key] || 0;
    const meta = n ? `${n} ${fav.key === 'ETF' ? 'ETF' : `entreprise${n > 1 ? 's' : ''}`}` : 'Plus suivi par le site pour l’instant';
    // La liste des indices n'a pas d'adresse propre (#indices + panneau
    // déplié) : le lien mène à #indices et l'événement 'compte:ouvrir-indice'
    // indique quel panneau déplier (branché par index.html).
    return `<li class="compte-suivi${n ? '' : ' compte-suivi-absent'}">
      <a class="compte-suivi-lien" href="#indices" data-indice="${echapper(fav.key)}">
        <span class="compte-suivi-nom">${echapper(noms[fav.key] || fav.key)}</span>
        <span class="compte-suivi-meta">Indice · ${meta}</span>
      </a>
      <span class="compte-suivi-etoile" data-kind="index" data-key="${echapper(fav.key)}"></span>
    </li>`;
  }

  // `indicesData` : l'objet de indices.json, ou une fonction qui le renvoie
  // (pratique s'il n'est pas encore chargé au montage). Renvoie
  // { rafraichir(nouvellesDonnees?), detruire() }.
  function monterMesSuivis(racine, indicesData) {
    if (!racine) return null;
    let source = indicesData;
    let etaitDansLaPage = false;
    const titreId = 'compte-suivis-titre-' + Math.random().toString(36).slice(2, 8);
    racine.classList.add('compte-suivis');

    function donnees() {
      return typeof source === 'function' ? source() : source;
    }

    function corpsHtml() {
      if (!Compte.estConnecte || !Compte.estConnecte()) {
        return `<div class="compte-suivis-etat">
          <p>Connecte-toi pour retrouver ici les indices et les entreprises que tu suis.</p>
          ${typeof Compte.ouvrirConnexion === 'function'
            ? '<button type="button" class="compte-suivis-bouton" data-action="connexion">Se connecter</button>' : ''}
        </div>`;
      }
      if (!charge) {
        return '<div class="compte-suivis-etat"><p>Chargement de tes suivis…</p></div>';
      }
      const favoris = liste();
      if (!favoris.length) {
        return `<div class="compte-suivis-etat">
          <p>Tu ne suis encore aucun indice ni aucune entreprise.</p>
          <p class="compte-suivis-aide">Touche l’étoile d’une fiche entreprise ou d’un indice pour le retrouver ici.</p>
        </div>`;
      }
      const d = donnees() || {};
      const entreprises = {};
      const compteParIndice = {};
      (d.companies || []).forEach(c => {
        entreprises[c.ticker] = c;
        [c.index || 'Autre', ...(c.also_indices || [])].forEach(k => {
          compteParIndice[k] = (compteParIndice[k] || 0) + 1;
        });
      });
      const noms = nomsIndices(d);
      const indices = favoris.filter(f => f.kind === 'index');
      const societes = favoris.filter(f => f.kind === 'company');
      let html = '';
      if (indices.length) {
        html += `<h3 class="compte-suivis-groupe">Indices</h3>
          <ul class="compte-suivis-liste">${indices.map(f => ligneIndice(f, compteParIndice, noms)).join('')}</ul>`;
      }
      if (societes.length) {
        html += `<h3 class="compte-suivis-groupe">Entreprises</h3>
          <ul class="compte-suivis-liste">${societes.map(f => ligneEntreprise(f, entreprises, noms)).join('')}</ul>`;
      }
      return html;
    }

    function rendre() {
      if (racine.isConnected) etaitDansLaPage = true;
      racine.innerHTML = `<section class="compte-suivis-section" aria-labelledby="${titreId}">
        <h2 class="compte-suivis-titre" id="${titreId}">Mes suivis</h2>
        ${corpsHtml()}
      </section>`;
      racine.querySelectorAll('.compte-suivi-etoile').forEach(el => {
        monterEtoile(el, el.dataset.kind, el.dataset.key);
      });
    }

    function surEvenement() {
      // Racine retirée de la page après y avoir été : on se désabonne.
      if (etaitDansLaPage && !racine.isConnected) { detruire(); return; }
      rendre();
    }

    function surClic(e) {
      if (e.target.closest('[data-action="connexion"]')) {
        inviterConnexion(() => {});
        return;
      }
      const lien = e.target.closest('a[data-indice]');
      if (lien) {
        window.dispatchEvent(new CustomEvent('compte:ouvrir-indice', { detail: { key: lien.dataset.indice } }));
      }
    }

    function detruire() {
      window.removeEventListener('compte:favoris', surEvenement);
      window.removeEventListener('compte:change', surEvenement);
      racine.removeEventListener('click', surClic);
    }

    window.addEventListener('compte:favoris', surEvenement);
    window.addEventListener('compte:change', surEvenement);
    racine.addEventListener('click', surClic);
    rendre();

    return {
      rafraichir(nouvellesDonnees) {
        if (nouvellesDonnees !== undefined) source = nouvellesDonnees;
        rendre();
      },
      detruire,
    };
  }

  Compte.monterMesSuivis = monterMesSuivis;
})();
