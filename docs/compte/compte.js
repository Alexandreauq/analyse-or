// docs/compte/compte.js
// Page « Mon compte » : un panneau latéral (plein écran sur mobile) avec le
// profil, les suivis, les alertes, l'historique des questions à l'IA et les
// droits sur les données (export, import, suppression). Elle s'appuie sur
// les modules favoris.js, alertes.js, historique.js et portefeuille.js
// quand ils sont chargés.
//
//   Compte.definirSourceIndices(fn)  fn() -> Promise<indicesData> (fournie par le site)
//   Compte.ouvrirMonCompte(onglet?)  'profil' | 'suivis' | 'alertes' | 'historique' | 'donnees'
(function () {
  const C = (window.Compte = window.Compte || {});
  const ONGLETS = [
    ['profil', 'Profil'],
    ['suivis', 'Mes suivis'],
    ['alertes', 'Alertes'],
    ['historique', 'Historique IA'],
    ['donnees', 'Mes données'],
  ];
  let sourceIndices = async () => null;
  let panneau = null;   // {voile, boite, corps, onglets, demonter, retourFocus}

  function el(tag, attrs, enfants) {
    const noeud = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([cle, valeur]) => {
      if (valeur === undefined || valeur === null || valeur === false) return;
      if (cle === 'class') noeud.className = valeur;
      else if (cle === 'text') noeud.textContent = valeur;
      else if (cle.startsWith('on')) noeud.addEventListener(cle.slice(2), valeur);
      else noeud.setAttribute(cle, valeur === true ? '' : valeur);
    });
    (enfants || []).forEach(enfant => enfant && noeud.append(enfant));
    return noeud;
  }

  function zoneMessage() {
    return el('p', { class: 'compte-message', role: 'status', 'aria-live': 'polite' });
  }

  function annoncer(zone, texte, type) {
    zone.textContent = texte || '';
    zone.dataset.type = type || '';
  }

  function dateCourte(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  }

  // ------------------------------------------------------------- onglets

  function ongletProfil(corps, membre) {
    const message = zoneMessage();
    const champ = el('input', {
      class: 'compte-input', id: 'compteNomAffiche', type: 'text', maxlength: '80',
      value: membre.display_name || '', autocomplete: 'nickname',
    });
    const formulaire = el('form', { class: 'compte-form', novalidate: true }, [
      el('div', { class: 'compte-champ' }, [el('label', { for: 'compteNomAffiche', text: 'Nom affiché' }), champ]),
      el('button', { type: 'submit', class: 'compte-bouton compte-bouton-principal', text: 'Enregistrer' }),
      message,
    ]);
    formulaire.addEventListener('submit', async e => {
      e.preventDefault();
      const bouton = formulaire.querySelector('button[type="submit"]');
      bouton.disabled = true;
      try {
        const moi = await C.fetch('/me', { method: 'PATCH', body: { display_name: champ.value } });
        C.definirMembre(moi);
        annoncer(message, 'Nom enregistré.', 'succes');
      } catch (err) {
        annoncer(message, err.message, 'erreur');
      } finally {
        bouton.disabled = false;
      }
    });
    corps.append(
      el('dl', { class: 'moncompte-infos' }, [
        el('dt', { text: 'Adresse e-mail' }), el('dd', { text: membre.email }),
        el('dt', { text: 'Statut' }),
        el('dd', { text: membre.email_verified ? 'Adresse confirmée' : 'Adresse non confirmée : alertes et assistant IA désactivés' }),
        el('dt', { text: 'Questions à l’IA restantes aujourd’hui' }),
        el('dd', { text: !membre.email_verified ? 'Après confirmation de l’adresse'
          : (membre.quota_ia_restant == null ? 'Illimité' : String(membre.quota_ia_restant)) }),
      ]),
      formulaire,
    );
  }

  async function ongletAvecIndices(corps, monter) {
    corps.append(el('p', { class: 'compte-texte', text: 'Chargement…' }));
    let donnees = null;
    try { donnees = await sourceIndices(); } catch (e) { /* liste vide, le module l'indique */ }
    if (!panneau || !corps.isConnected) return null;
    corps.replaceChildren();
    const racine = el('div');
    corps.append(racine);
    return monter(racine, donnees);
  }

  async function ongletHistorique(corps) {
    if (!C.historique) {
      corps.append(el('p', { class: 'compte-texte', text: 'Historique indisponible.' }));
      return;
    }
    const message = zoneMessage();
    const liste = el('ul', { class: 'moncompte-liste' });
    const toutEffacer = el('button', {
      type: 'button', class: 'compte-lien', text: 'Tout effacer',
      onclick: async () => {
        if (!confirm('Effacer tout l’historique de tes questions à l’IA ?')) return;
        try { await C.historique.effacerTout(); await charger(); } catch (err) { annoncer(message, err.message, 'erreur'); }
      },
    });
    corps.append(el('div', { class: 'moncompte-barre' }, [el('p', { class: 'compte-texte', text: 'Tes 100 dernières questions à l’assistant.' }), toutEffacer]), message, liste);

    async function charger() {
      liste.replaceChildren(el('li', { class: 'compte-texte', text: 'Chargement…' }));
      try {
        const echanges = await C.historique.echangesIA();
        liste.replaceChildren();
        toutEffacer.hidden = !echanges.length;
        if (!echanges.length) {
          liste.append(el('li', { class: 'compte-texte', text: 'Aucune question pour l’instant.' }));
          return;
        }
        echanges.forEach(ech => {
          const details = el('details', { class: 'moncompte-echange' }, [
            el('summary', {}, [
              el('span', { class: 'moncompte-question', text: ech.question }),
              el('span', { class: 'moncompte-date', text: dateCourte(ech.created_at) }),
            ]),
            el('p', { class: 'moncompte-reponse', text: ech.answer }),
            el('button', {
              type: 'button', class: 'compte-lien', text: 'Supprimer',
              onclick: async () => {
                try { await C.historique.effacerEchange(ech.id); details.remove(); } catch (err) { annoncer(message, err.message, 'erreur'); }
              },
            }),
          ]);
          liste.append(el('li', {}, [details]));
        });
      } catch (err) {
        liste.replaceChildren();
        annoncer(message, err.message, 'erreur');
      }
    }
    await charger();
  }

  function ongletDonnees(corps, membre) {
    const message = zoneMessage();
    const exporter = el('button', {
      type: 'button', class: 'compte-bouton', text: 'Télécharger mes données',
      onclick: async () => {
        exporter.disabled = true;
        try {
          // Pas de C.fetch ici : la réponse est un fichier à enregistrer, pas du JSON à lire.
          const reponse = await fetch('/api/compte/export', { credentials: 'same-origin' });
          if (!reponse.ok) throw new Error('Export impossible pour l’instant, réessaie.');
          const blob = await reponse.blob();
          const lien = el('a', {
            href: URL.createObjectURL(blob),
            download: `ai-investment-mes-donnees-${new Date().toISOString().slice(0, 10)}.json`,
          });
          document.body.append(lien);
          lien.click();
          lien.remove();
          setTimeout(() => URL.revokeObjectURL(lien.href), 1000);
          annoncer(message, 'Fichier téléchargé.', 'succes');
        } catch (err) {
          annoncer(message, err.message, 'erreur');
        } finally {
          exporter.disabled = false;
        }
      },
    });
    const importer = typeof C.proposerImport === 'function' ? el('button', {
      type: 'button', class: 'compte-bouton', text: 'Importer les données de cet appareil',
      onclick: () => { fermer(); C.proposerImport({ forcer: true }); },
    }) : null;

    const formSuppression = suppressionHtml(membre, message);
    corps.append(
      el('section', { class: 'moncompte-bloc' }, [
        el('h3', { text: 'Exporter' }),
        el('p', { class: 'compte-texte', text: 'Un fichier JSON avec tout ce que ton compte contient : profil, favoris, portefeuille, historique, alertes.' }),
        exporter,
      ]),
      importer ? el('section', { class: 'moncompte-bloc' }, [
        el('h3', { text: 'Importer' }),
        el('p', { class: 'compte-texte', text: 'Ajoute à ton compte le portefeuille, l’historique et les réglages gardés dans ce navigateur. Rien n’est effacé de l’appareil.' }),
        importer,
      ]) : null,
      formSuppression,
      message,
      el('p', { class: 'compte-texte moncompte-legal' }, [
        el('a', { href: 'legal/confidentialite.html', text: 'Politique de confidentialité' }),
        document.createTextNode(' · '),
        el('a', { href: 'legal/mentions-legales.html', text: 'Mentions légales' }),
      ]),
    );
  }

  function suppressionHtml(membre, message) {
    const sansMotDePasse = membre.connexion_google_seule === true;
    const champ = el('input', {
      class: 'compte-input', id: 'compteSuppressionMdp', type: 'password', autocomplete: 'current-password',
    });
    const bouton = el('button', { type: 'submit', class: 'compte-bouton moncompte-danger', text: 'Supprimer définitivement mon compte' });
    const formulaire = el('form', { class: 'compte-form', novalidate: true }, [
      el('div', { class: 'compte-champ' }, [
        el('label', { for: 'compteSuppressionMdp', text: 'Mot de passe (laisse vide si tu te connectes avec Google)' }),
        champ,
      ]),
      bouton,
    ]);
    formulaire.addEventListener('submit', async e => {
      e.preventDefault();
      if (!confirm('Supprimer définitivement ton compte et toutes ses données ? Cette action est irréversible.')) return;
      bouton.disabled = true;
      try {
        await C.fetch('/account', { method: 'DELETE', body: champ.value ? { password: champ.value } : {} });
        fermer();
        C.definirMembre(null);
      } catch (err) {
        annoncer(message, err.message, 'erreur');
        bouton.disabled = false;
      }
    });
    return el('section', { class: 'moncompte-bloc moncompte-bloc-danger' }, [
      el('h3', { text: 'Supprimer mon compte' }),
      el('p', { class: 'compte-texte', text: sansMotDePasse
        ? 'Toutes tes données seront effacées. Par sécurité, reconnecte-toi avec Google juste avant.'
        : 'Toutes tes données (favoris, portefeuille, historique, alertes) seront effacées. Cette action est irréversible.' }),
      formulaire,
    ]);
  }

  // ------------------------------------------------------------- panneau

  function surTouche(e) {
    if (!panneau) return;
    if (e.key === 'Escape' && !e.defaultPrevented) { e.preventDefault(); fermer(); return; }
    if (e.key !== 'Tab') return;
    const liste = Array.from(panneau.boite.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), select, textarea, summary, [tabindex]:not([tabindex="-1"])',
    )).filter(n => n.offsetParent !== null);
    if (!liste.length) return;
    const premier = liste[0], dernier = liste[liste.length - 1];
    if (e.shiftKey && document.activeElement === premier) { e.preventDefault(); dernier.focus(); }
    else if (!e.shiftKey && document.activeElement === dernier) { e.preventDefault(); premier.focus(); }
  }

  async function afficherOnglet(nom) {
    if (!panneau) return;
    if (typeof panneau.demonter === 'function') panneau.demonter();
    panneau.demonter = null;
    panneau.onglets.forEach(b => b.setAttribute('aria-selected', String(b.dataset.onglet === nom)));
    const corps = panneau.corps;
    corps.replaceChildren();
    const membre = C.membre && C.membre();
    if (!membre) { fermer(); return; }
    if (nom === 'profil') ongletProfil(corps, membre);
    else if (nom === 'historique') await ongletHistorique(corps);
    else if (nom === 'donnees') ongletDonnees(corps, membre);
    else if (nom === 'suivis' && typeof C.monterMesSuivis === 'function') {
      const instance = await ongletAvecIndices(corps, (racine, donnees) => C.monterMesSuivis(racine, () => donnees));
      if (panneau) panneau.demonter = instance && instance.detruire;
    } else if (nom === 'alertes' && typeof C.monterAlertes === 'function') {
      const demonter = await ongletAvecIndices(corps, (racine, donnees) => C.monterAlertes(racine, donnees));
      if (panneau) panneau.demonter = demonter;
    } else {
      corps.append(el('p', { class: 'compte-texte', text: 'Section indisponible.' }));
    }
  }

  function ouvrirMonCompte(onglet) {
    if (!C.estConnecte || !C.estConnecte()) {
      if (typeof C.ouvrirConnexion === 'function') C.ouvrirConnexion('connexion');
      return;
    }
    if (!panneau) {
      const idTitre = 'moncompteTitre';
      const onglets = ONGLETS.map(([cle, libelle]) => el('button', {
        type: 'button', role: 'tab', class: 'moncompte-onglet', 'data-onglet': cle, text: libelle,
        onclick: () => afficherOnglet(cle),
      }));
      const corps = el('div', { class: 'moncompte-corps', role: 'tabpanel' });
      const boite = el('div', { class: 'moncompte', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idTitre }, [
        el('div', { class: 'compte-entete' }, [
          el('h2', { id: idTitre, class: 'compte-titre', text: 'Mon compte' }),
          el('button', { type: 'button', class: 'compte-fermer', 'aria-label': 'Fermer', text: '×', onclick: fermer }),
        ]),
        el('div', { class: 'moncompte-onglets', role: 'tablist' }, onglets),
        corps,
      ]);
      const voile = el('div', { class: 'compte-voile moncompte-voile' }, [boite]);
      voile.addEventListener('mousedown', e => { if (e.target === voile) fermer(); });
      panneau = { voile, boite, corps, onglets, demonter: null, retourFocus: document.activeElement };
      document.body.append(voile);
      document.documentElement.classList.add('compte-modale-ouverte');
      document.addEventListener('keydown', surTouche);
    }
    afficherOnglet(onglet || 'profil');
    // Le focus va sur l'onglet affiché, pas toujours sur « Profil ».
    const actif = panneau.onglets.find(b => b.getAttribute('aria-selected') === 'true');
    if (actif) actif.focus();
  }

  function fermer() {
    if (!panneau) return;
    const { voile, demonter, retourFocus } = panneau;
    if (typeof demonter === 'function') demonter();
    panneau = null;
    voile.remove();
    document.documentElement.classList.remove('compte-modale-ouverte');
    document.removeEventListener('keydown', surTouche);
    if (retourFocus && typeof retourFocus.focus === 'function' && document.contains(retourFocus)) retourFocus.focus();
  }

  // Déconnexion pendant que le panneau est ouvert : il se ferme.
  window.addEventListener('compte:change', e => { if (!e.detail) fermer(); });

  Object.assign(C, {
    ouvrirMonCompte,
    fermerMonCompte: fermer,
    definirSourceIndices(fn) { if (typeof fn === 'function') sourceIndices = fn; },
  });
})();
