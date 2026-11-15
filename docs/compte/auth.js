// docs/compte/auth.js
// Interface de connexion : bouton « Se connecter » ou menu du membre,
// fenêtre connexion / inscription / mot de passe oublié / nouveau mot de
// passe, « Continuer avec Google », et traitement des liens reçus par
// e-mail (?verify=, ?reset=) ou du retour Google en échec (?compte_erreur=).
// Script classique. Dépend de docs/compte/api.js et docs/compte/session.js.
// Styles : docs/compte/auth.css.
(function () {
  const C = window.Compte = window.Compte || {};
  const URL_GOOGLE = '/api/compte/google/start';
  let compteurId = 0;

  // Petit fabricant d'éléments : jamais d'innerHTML avec des données.
  function el(tag, attrs, enfants) {
    const noeud = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([cle, valeur]) => {
      if (valeur === null || valeur === undefined || valeur === false) return;
      if (cle === 'class') noeud.className = valeur;
      else if (cle === 'text') noeud.textContent = valeur;
      else if (cle.startsWith('on')) noeud.addEventListener(cle.slice(2), valeur);
      else noeud.setAttribute(cle, valeur === true ? '' : valeur);
    });
    (enfants || []).forEach(e => { if (e) noeud.append(e); });
    return noeud;
  }

  function nouvelId(prefixe) {
    compteurId += 1;
    return `compte-${prefixe}-${compteurId}`;
  }

  // Champ avec <label> lié et aide éventuelle (aria-describedby).
  function champ(libelle, attrs, aide) {
    const id = nouvelId('champ');
    const idAide = aide ? id + '-aide' : null;
    const input = el('input', { id, class: 'compte-input', 'aria-describedby': idAide, ...attrs });
    return {
      input,
      bloc: el('div', { class: 'compte-champ' }, [
        el('label', { for: id, text: libelle }),
        input,
        aide ? el('p', { id: idAide, class: 'compte-aide', text: aide }) : null,
      ]),
    };
  }

  // ------------------------------------------------------------ fenêtre modale

  let modale = null;   // {voile, boite, corps, titre, retourFocus}

  function focusables(racine) {
    return Array.from(racine.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]):not([tabindex="-1"]), select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(n => n.offsetParent !== null || n === document.activeElement);
  }

  function surTouche(e) {
    if (!modale) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      fermer();
      return;
    }
    if (e.key !== 'Tab') return;
    // Focus piégé : Tab et Maj+Tab bouclent dans la fenêtre.
    const liste = focusables(modale.boite);
    if (!liste.length) { e.preventDefault(); return; }
    const premier = liste[0];
    const dernier = liste[liste.length - 1];
    if (e.shiftKey && (document.activeElement === premier || !modale.boite.contains(document.activeElement))) {
      e.preventDefault();
      dernier.focus();
    } else if (!e.shiftKey && document.activeElement === dernier) {
      e.preventDefault();
      premier.focus();
    }
  }

  function ouvrirModale() {
    if (modale) return modale;
    const idTitre = nouvelId('titre');
    const titre = el('h2', { id: idTitre, class: 'compte-titre' });
    const corps = el('div', { class: 'compte-corps' });
    const boite = el('div', {
      class: 'compte-modale', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idTitre,
    }, [
      el('div', { class: 'compte-entete' }, [
        titre,
        el('button', { type: 'button', class: 'compte-fermer', 'aria-label': 'Fermer', text: '×', onclick: fermer }),
      ]),
      corps,
    ]);
    const voile = el('div', { class: 'compte-voile' }, [boite]);
    voile.addEventListener('mousedown', e => { if (e.target === voile) fermer(); });
    modale = { voile, boite, corps, titre, retourFocus: document.activeElement };
    document.body.append(voile);
    document.documentElement.classList.add('compte-modale-ouverte');
    document.addEventListener('keydown', surTouche, true);
    return modale;
  }

  function fermer() {
    if (!modale) return;
    const { voile, retourFocus } = modale;
    modale = null;
    voile.remove();
    document.documentElement.classList.remove('compte-modale-ouverte');
    document.removeEventListener('keydown', surTouche, true);
    if (retourFocus && typeof retourFocus.focus === 'function' && document.contains(retourFocus)) {
      retourFocus.focus();
    }
  }

  // Remplit la fenêtre (ouverte au besoin) et place le focus au bon endroit.
  function afficher(titre, contenu, focusSur) {
    const m = ouvrirModale();
    m.titre.textContent = titre;
    m.corps.replaceChildren(...contenu);
    const cible = focusSur || focusables(m.corps)[0] || m.boite.querySelector('.compte-fermer');
    if (cible) cible.focus();
  }

  // Zone annoncée aux lecteurs d'écran (erreurs et confirmations).
  function zoneMessage() {
    return el('p', { class: 'compte-message', role: 'status', 'aria-live': 'polite' });
  }

  function annoncer(zone, texte, type) {
    zone.textContent = texte || '';
    zone.dataset.type = type || '';
  }

  function lienMode(texte, mode) {
    return el('button', { type: 'button', class: 'compte-lien', text: texte, onclick: () => ouvrirConnexion(mode) });
  }

  function boutonGoogle() {
    return el('button', {
      type: 'button', class: 'compte-bouton compte-bouton-google', text: 'Continuer avec Google',
      onclick: () => { window.location.href = URL_GOOGLE; },
    });
  }

  // Formulaire générique : envoi à l'API, bouton désactivé pendant l'appel,
  // erreur du service affichée dans la zone de message.
  function formulaire(champs, libelleEnvoi, envoyer) {
    const zone = zoneMessage();
    const bouton = el('button', { type: 'submit', class: 'compte-bouton compte-bouton-principal', text: libelleEnvoi });
    const form = el('form', { class: 'compte-form', novalidate: true }, [
      ...champs.map(c => c.bloc), zone, bouton,
    ]);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const invalide = champs.find(c => !c.input.checkValidity());
      if (invalide) {
        annoncer(zone, messageValidation(invalide.input), 'erreur');
        invalide.input.setAttribute('aria-invalid', 'true');
        invalide.input.focus();
        return;
      }
      champs.forEach(c => c.input.removeAttribute('aria-invalid'));
      bouton.disabled = true;
      bouton.setAttribute('aria-busy', 'true');
      annoncer(zone, '', '');
      try {
        await envoyer(zone);
      } catch (err) {
        annoncer(zone, (err && err.message) || 'Une erreur est survenue, réessaie.', 'erreur');
      } finally {
        bouton.disabled = false;
        bouton.removeAttribute('aria-busy');
      }
    });
    return { form, zone };
  }

  function messageValidation(input) {
    const v = input.validity;
    if (v.valueMissing) return 'Ce champ est obligatoire.';
    if (v.typeMismatch) return 'Adresse e-mail invalide.';
    if (v.tooShort) return `Au moins ${input.minLength} caractères.`;
    if (v.tooLong) return `Au plus ${input.maxLength} caractères.`;
    return input.validationMessage || 'Valeur invalide.';
  }

  function champEmail() {
    return champ('Adresse e-mail', { type: 'email', name: 'email', autocomplete: 'email', required: true, maxlength: '254' });
  }

  function champNouveauMotDePasse(libelle) {
    return champ(libelle || 'Mot de passe', {
      type: 'password', name: 'password', autocomplete: 'new-password', required: true,
      minlength: '10', maxlength: '128',
    }, '10 caractères minimum. Évite les mots de passe courants et ton adresse e-mail.');
  }

  function separateur() {
    return el('p', { class: 'compte-separateur' }, [el('span', { text: 'ou' })]);
  }

  // ------------------------------------------------------------ écrans

  function ecranConnexion() {
    const email = champEmail();
    const mdp = champ('Mot de passe', { type: 'password', name: 'password', autocomplete: 'current-password', required: true, maxlength: '1024' });
    const { form } = formulaire([email, mdp], 'Se connecter', async () => {
      const membre = await C.fetch('/login', { method: 'POST', body: { email: email.input.value, password: mdp.input.value } });
      C.definirMembre(membre);
      fermer();
    });
    afficher('Se connecter', [
      form,
      el('p', { class: 'compte-actions' }, [lienMode('Mot de passe oublié ?', 'oubli')]),
      separateur(),
      boutonGoogle(),
      el('p', { class: 'compte-bascule' }, ['Pas encore de compte ? ', lienMode('Créer un compte', 'inscription')]),
    ], email.input);
  }

  function ecranInscription() {
    const nom = champ('Nom affiché (facultatif)', { type: 'text', name: 'display_name', autocomplete: 'nickname', maxlength: '80' });
    const email = champEmail();
    const mdp = champNouveauMotDePasse();
    // Champ piège : invisible pour un humain, rempli par les robots.
    const piege = el('input', { type: 'text', name: 'site_web', tabindex: '-1', autocomplete: 'off' });
    const blocPiege = el('div', { class: 'compte-piege', 'aria-hidden': 'true' }, [
      el('label', { text: 'Ne pas remplir' }, [piege]),
    ]);
    const { form } = formulaire([nom, email, mdp], 'Créer mon compte', async () => {
      const membre = await C.fetch('/register', {
        method: 'POST',
        body: { email: email.input.value, password: mdp.input.value, display_name: nom.input.value || null, site_web: piege.value },
      });
      if (membre && membre.id) C.definirMembre(membre);
      afficherMessage('Compte créé',
        `Bienvenue ! Un e-mail de confirmation vient d’être envoyé à ${email.input.value.trim()}. ` +
        'Ouvre le lien qu’il contient pour activer les alertes et l’assistant IA.');
    });
    form.insertBefore(blocPiege, form.firstChild);
    afficher('Créer un compte', [
      boutonGoogle(),
      separateur(),
      form,
      el('p', { class: 'compte-bascule' }, ['Déjà un compte ? ', lienMode('Se connecter', 'connexion')]),
    ]);
  }

  function ecranOubli() {
    const email = champEmail();
    const { form } = formulaire([email], 'Envoyer le lien', async zone => {
      const reponse = await C.fetch('/password/forgot', { method: 'POST', body: { email: email.input.value } });
      annoncer(zone, (reponse && reponse.detail) || 'E-mail envoyé.', 'succes');
    });
    afficher('Mot de passe oublié', [
      el('p', { class: 'compte-texte', text: 'Indique ton adresse : tu recevras un lien pour choisir un nouveau mot de passe (valable 1 heure).' }),
      form,
      el('p', { class: 'compte-bascule' }, [lienMode('Retour à la connexion', 'connexion')]),
    ], email.input);
  }

  function ecranReinitialisation(jeton) {
    const mdp = champNouveauMotDePasse('Nouveau mot de passe');
    const confirmation = champ('Confirme le mot de passe', {
      type: 'password', name: 'confirmation', autocomplete: 'new-password', required: true, maxlength: '128',
    });
    const { form } = formulaire([mdp, confirmation], 'Enregistrer', async zone => {
      if (mdp.input.value !== confirmation.input.value) {
        confirmation.input.setAttribute('aria-invalid', 'true');
        annoncer(zone, 'Les deux mots de passe ne sont pas identiques.', 'erreur');
        confirmation.input.focus();
        return;
      }
      const membre = await C.fetch('/password/reset', { method: 'POST', body: { token: jeton, password: mdp.input.value } });
      C.definirMembre(membre);
      afficherMessage('Mot de passe modifié', 'Ton nouveau mot de passe est enregistré et tu es connecté. Tes autres appareils ont été déconnectés.');
    });
    afficher('Nouveau mot de passe', [form], mdp.input);
  }

  function afficherMessage(titre, texte, estErreur) {
    const fermerBtn = el('button', { type: 'button', class: 'compte-bouton compte-bouton-principal', text: 'Fermer', onclick: fermer });
    afficher(titre, [
      el('p', { class: 'compte-texte' + (estErreur ? ' compte-texte-erreur' : ''), role: estErreur ? 'alert' : 'status', text: texte }),
      fermerBtn,
    ], fermerBtn);
  }

  // mode : 'connexion' (défaut), 'inscription', 'oubli'.
  function ouvrirConnexion(mode) {
    if (mode === 'inscription') ecranInscription();
    else if (mode === 'oubli') ecranOubli();
    else ecranConnexion();
  }

  // ------------------------------------------------------------ bouton / menu

  function monterBoutonCompte(racine) {
    const conteneur = el('div', { class: 'compte-zone' });
    racine.append(conteneur);
    let fermerMenu = () => {};

    function rendre(membre) {
      fermerMenu();
      conteneur.replaceChildren();
      if (!membre) {
        // Site servi sans relais (ex. GitHub Pages) : pas de bouton inutile.
        if (C.serviceDisponible && !C.serviceDisponible()) return;
        conteneur.append(el('button', {
          type: 'button', class: 'compte-bouton compte-bouton-connexion', text: 'Se connecter',
          onclick: () => ouvrirConnexion('connexion'),
        }));
        return;
      }
      const idMenu = nouvelId('menu');
      const nom = membre.display_name || membre.email;
      const declencheur = el('button', {
        type: 'button', class: 'compte-bouton compte-bouton-membre', 'aria-expanded': 'false', 'aria-controls': idMenu,
      }, [
        el('span', { class: 'compte-initiale', 'aria-hidden': 'true', text: (nom || '?').trim().charAt(0).toUpperCase() }),
        el('span', { class: 'compte-nom', text: nom }),
      ]);
      declencheur.setAttribute('aria-label', `Mon compte (${nom})`);
      const statut = el('p', { class: 'compte-message', role: 'status', 'aria-live': 'polite' });
      const actions = [];
      if (!membre.email_verified) {
        actions.push(el('button', {
          type: 'button', class: 'compte-menu-item', text: 'Renvoyer l’e-mail de confirmation',
          onclick: async e => {
            const btn = e.currentTarget;
            btn.disabled = true;
            try {
              const r = await C.fetch('/verify/resend', { method: 'POST' });
              annoncer(statut, (r && r.detail) || 'E-mail envoyé.', 'succes');
            } catch (err) {
              annoncer(statut, err.message, 'erreur');
            } finally {
              btn.disabled = false;
            }
          },
        }));
      }
      actions.push(el('button', {
        type: 'button', class: 'compte-menu-item', text: 'Se déconnecter',
        onclick: async () => {
          try { await C.fetch('/logout', { method: 'POST' }); } catch (e) { /* session déjà close */ }
          C.definirMembre(null);
        },
      }));
      const menu = el('div', { id: idMenu, class: 'compte-menu', hidden: true }, [
        el('p', { class: 'compte-menu-email', text: membre.email }),
        !membre.email_verified ? el('p', { class: 'compte-menu-alerte', text: 'Adresse e-mail non confirmée.' }) : null,
        ...actions,
        statut,
      ]);

      function basculer(ouvrir) {
        menu.hidden = !ouvrir;
        declencheur.setAttribute('aria-expanded', String(ouvrir));
        if (ouvrir) {
          document.addEventListener('mousedown', clicDehors, true);
          menu.addEventListener('keydown', echapMenu);
          const premier = menu.querySelector('button');
          if (premier) premier.focus();
        } else {
          document.removeEventListener('mousedown', clicDehors, true);
          menu.removeEventListener('keydown', echapMenu);
        }
      }
      function clicDehors(e) { if (!conteneur.contains(e.target)) basculer(false); }
      function echapMenu(e) {
        if (e.key === 'Escape') { e.stopPropagation(); basculer(false); declencheur.focus(); }
      }
      fermerMenu = () => basculer(false);
      declencheur.addEventListener('click', () => basculer(menu.hidden));
      declencheur.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) basculer(false); });
      conteneur.append(declencheur, menu);
    }

    if (typeof C.surChangement === 'function') C.surChangement(rendre);
    else rendre(null);
    return conteneur;
  }

  // ------------------------------------------------------------ liens des e-mails

  async function traiterLiensEmail() {
    const url = new URL(window.location.href);
    const verify = url.searchParams.get('verify');
    const reset = url.searchParams.get('reset');
    const erreur = url.searchParams.get('compte_erreur');
    if (!verify && !reset && !erreur) return;
    // Le jeton ne reste ni dans la barre d'adresse ni dans l'historique.
    ['verify', 'reset', 'compte_erreur'].forEach(p => url.searchParams.delete(p));
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);

    if (erreur === 'google') {
      afficherMessage('Connexion Google', 'La connexion avec Google n’a pas abouti. Réessaie, ou connecte-toi avec ton e-mail.', true);
    } else if (erreur) {
      afficherMessage('Connexion', 'La connexion n’a pas abouti. Réessaie.', true);
    }
    if (verify) {
      try {
        await C.fetch('/verify', { method: 'POST', body: { token: verify } });
        if (typeof C.chargerSession === 'function') C.chargerSession();
        afficherMessage('Adresse confirmée', 'Merci, ton adresse e-mail est confirmée.');
      } catch (err) {
        afficherMessage('Confirmation impossible',
          err.statut === 400 ? 'Ce lien est invalide ou a expiré. Connecte-toi puis redemande un e-mail de confirmation depuis ton menu.' : err.message,
          true);
      }
    } else if (reset) {
      ecranReinitialisation(reset);
    }
  }

  Object.assign(C, { monterBoutonCompte, ouvrirConnexion, traiterLiensEmail, fermerConnexion: fermer });
})();
