// docs/compte/alertes.js
// Onglet « Alertes » de l'espace membre : règles d'alerte (score au-dessus /
// en dessous d'un seuil, signal d'entrée), interrupteur du résumé quotidien
// par e-mail. Script classique (pas de module ES), dépend de
// docs/compte/api.js et docs/compte/session.js.
//
//   const demonter = window.Compte.monterAlertes(racine, indicesData);
//
// N'écrit dans le DOM que sous `racine`. indicesData = indices.json déjà
// chargé par le site (companies : ticker, name, score, alerts).
(function () {
  const MAX_SUGGESTIONS = 8;
  const LIBELLES = {
    score_above: 'Score au-dessus de',
    score_below: 'Score en dessous de',
    entry_signal: 'Signal d’entrée activé',
  };

  function esc(texte) {
    return String(texte == null ? '' : texte)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function nombre(v) {
    return (typeof v === 'number' && isFinite(v))
      ? v.toLocaleString('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
      : '—';
  }

  function dateCourte(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    return isNaN(d) ? null : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // Même définition que findOpportunities (docs/portfolio.js) et que la
  // tâche serveur : une alerte "entree" dans company.alerts.
  function signalEntree(c) {
    return !!c && (c.alerts || []).some(a => a && a.kind === 'entree');
  }

  function normaliser(t) {
    return String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  }

  function monterAlertes(racine, indicesData) {
    const entreprises = ((indicesData && indicesData.companies) || []).filter(c => c && c.ticker);
    const parTicker = new Map(entreprises.map(c => [c.ticker, c]));
    const etat = { regles: [], email: null, choix: null, kind: 'score_above', chargement: false, erreur: null };
    let jeton = 0;   // ignore les réponses d'un chargement dépassé

    racine.classList.add('alertes');

    function message(texte, type) {
      const zone = racine.querySelector('.alertes-message');
      if (!zone) return;
      zone.textContent = texte || '';
      zone.dataset.type = type || '';
      zone.hidden = !texte;
    }

    function erreurDe(e) {
      return (e && e.message) || 'Une erreur est survenue, réessaie.';
    }

    // --- Rendu -----------------------------------------------------------

    function rendreVisiteur() {
      racine.innerHTML = `
        <div class="alertes-vide">
          <h3 class="alertes-titre">Alertes</h3>
          <p>Connecte-toi pour être prévenu par e-mail quand le score d’une entreprise franchit un seuil
             ou qu’un signal d’entrée apparaît.</p>
        </div>`;
    }

    function ligneRegle(r) {
      const c = parTicker.get(r.target);
      const nom = c ? c.name : r.target;
      const regle = r.kind === 'entry_signal' ? LIBELLES.entry_signal : `${LIBELLES[r.kind]} ${nombre(r.threshold)}`;
      const actuel = c
        ? (r.kind === 'entry_signal'
          ? (signalEntree(c) ? 'Signal actif aujourd’hui' : 'Pas de signal aujourd’hui')
          : `Score actuel : ${nombre(c.score)}`)
        : 'Entreprise plus suivie';
      const dernier = dateCourte(r.dernier_declenchement);
      return `
        <li class="alertes-regle${r.active ? '' : ' est-inactive'}" data-id="${esc(r.id)}">
          <div class="alertes-regle-texte">
            <a class="alertes-regle-nom" href="#indices/${encodeURIComponent(r.target)}">${esc(nom)}</a>
            <span class="alertes-ticker">${esc(r.target)}</span>
            <span class="alertes-regle-condition">${esc(regle)}</span>
            <span class="alertes-regle-meta">${esc(actuel)}${dernier ? ` · Dernier déclenchement : ${esc(dernier)}` : ''}</span>
          </div>
          <div class="alertes-regle-actions">
            <label class="alertes-interrupteur" title="${r.active ? 'Désactiver' : 'Activer'} cette alerte">
              <input type="checkbox" data-action="basculer" ${r.active ? 'checked' : ''}>
              <span class="alertes-interrupteur-piste" aria-hidden="true"></span>
              <span class="alertes-sr">Alerte active</span>
            </label>
            <button type="button" class="alertes-bouton-texte" data-action="supprimer">Supprimer</button>
          </div>
        </li>`;
    }

    function rendreMembre() {
      const email = etat.email;
      const nonConfirme = email && email.email_verified === false;
      racine.innerHTML = `
        <h3 class="alertes-titre">Alertes</h3>
        <p class="alertes-message" role="status" hidden></p>

        <section class="alertes-bloc">
          <label class="alertes-interrupteur alertes-email">
            <input type="checkbox" data-action="email" ${email && email.alerts_email_enabled ? 'checked' : ''}
                   ${email ? '' : 'disabled'}>
            <span class="alertes-interrupteur-piste" aria-hidden="true"></span>
            <span>Recevoir le résumé quotidien par e-mail</span>
          </label>
          ${nonConfirme ? `<p class="alertes-avertissement">Ton adresse e-mail n’est pas encore confirmée :
            les e-mails d’alerte ne partent pas tant que tu n’as pas cliqué sur le lien de confirmation.</p>` : ''}
        </section>

        <form class="alertes-bloc alertes-formulaire" novalidate>
          <h4 class="alertes-sous-titre">Nouvelle alerte</h4>
          <div class="alertes-champ alertes-recherche">
            <label for="alertes-entreprise">Entreprise</label>
            <input id="alertes-entreprise" type="search" autocomplete="off" placeholder="Nom ou ticker"
                   role="combobox" aria-expanded="false" aria-controls="alertes-suggestions"
                   value="${etat.choix ? esc(`${etat.choix.name} (${etat.choix.ticker})`) : ''}">
            <ul id="alertes-suggestions" class="alertes-suggestions" role="listbox" hidden></ul>
          </div>
          <div class="alertes-champ">
            <label for="alertes-type">Type</label>
            <select id="alertes-type">
              <option value="score_above"${etat.kind === 'score_above' ? ' selected' : ''}>${LIBELLES.score_above}…</option>
              <option value="score_below"${etat.kind === 'score_below' ? ' selected' : ''}>${LIBELLES.score_below}…</option>
              <option value="entry_signal"${etat.kind === 'entry_signal' ? ' selected' : ''}>${LIBELLES.entry_signal}</option>
            </select>
          </div>
          <div class="alertes-champ alertes-champ-seuil"${etat.kind === 'entry_signal' ? ' hidden' : ''}>
            <label for="alertes-seuil">Seuil (-100 à 100)</label>
            <input id="alertes-seuil" type="number" min="-100" max="100" step="0.5" inputmode="decimal">
          </div>
          <button type="submit" class="alertes-bouton">Ajouter</button>
        </form>

        <section class="alertes-bloc">
          <h4 class="alertes-sous-titre">Mes alertes <span class="alertes-compteur">${etat.regles.length}/50</span></h4>
          ${etat.chargement ? '<p class="alertes-meta">Chargement…</p>'
            : etat.regles.length
              ? `<ul class="alertes-liste">${etat.regles.map(ligneRegle).join('')}</ul>`
              : '<p class="alertes-meta">Aucune alerte pour l’instant. Choisis une entreprise ci-dessus.</p>'}
        </section>`;
      if (etat.erreur) message(etat.erreur, 'erreur');
      brancherFormulaire();
    }

    // --- Recherche d'entreprise -------------------------------------------

    function brancherFormulaire() {
      const champ = racine.querySelector('#alertes-entreprise');
      const liste = racine.querySelector('#alertes-suggestions');
      const type = racine.querySelector('#alertes-type');
      let suggestions = [];
      let actif = -1;

      function fermer() {
        liste.hidden = true;
        champ.setAttribute('aria-expanded', 'false');
        actif = -1;
      }

      function afficher() {
        const q = normaliser(champ.value.trim());
        if (!q) { suggestions = []; fermer(); return; }
        suggestions = entreprises
          .filter(c => normaliser(c.ticker).startsWith(q) || normaliser(c.name).includes(q))
          .slice(0, MAX_SUGGESTIONS);
        liste.innerHTML = suggestions.length
          ? suggestions.map((c, i) => `
              <li role="option" id="alertes-option-${i}" data-index="${i}" aria-selected="${i === actif}">
                <span>${esc(c.name)}</span><span class="alertes-ticker">${esc(c.ticker)}</span>
              </li>`).join('')
          : '<li class="alertes-meta" aria-disabled="true">Aucune entreprise trouvée</li>';
        liste.hidden = false;
        champ.setAttribute('aria-expanded', 'true');
      }

      function choisir(c) {
        etat.choix = c;
        champ.value = `${c.name} (${c.ticker})`;
        fermer();
      }

      champ.addEventListener('input', () => { etat.choix = null; actif = -1; afficher(); });
      champ.addEventListener('keydown', e => {
        if (liste.hidden || !suggestions.length) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          actif = (actif + (e.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length;
          afficher();
          champ.setAttribute('aria-activedescendant', `alertes-option-${actif}`);
        } else if (e.key === 'Enter' && actif >= 0) {
          e.preventDefault();
          choisir(suggestions[actif]);
        } else if (e.key === 'Escape') {
          fermer();
        }
      });
      champ.addEventListener('blur', () => setTimeout(fermer, 150));
      // mousedown plutôt que click : passe avant le blur du champ.
      liste.addEventListener('mousedown', e => {
        const li = e.target.closest('li[data-index]');
        if (!li) return;
        e.preventDefault();
        choisir(suggestions[Number(li.dataset.index)]);
      });

      type.addEventListener('change', () => {
        etat.kind = type.value;
        racine.querySelector('.alertes-champ-seuil').hidden = etat.kind === 'entry_signal';
      });
    }

    // --- Actions ------------------------------------------------------------

    async function ajouter(form) {
      if (!etat.choix) { message('Choisis une entreprise dans la liste.', 'erreur'); return; }
      const corps = { kind: etat.kind, target: etat.choix.ticker };
      if (etat.kind !== 'entry_signal') {
        const brut = form.querySelector('#alertes-seuil').value.replace(',', '.');
        const seuil = Number(brut);
        if (brut === '' || !isFinite(seuil) || seuil < -100 || seuil > 100) {
          message('Indique un seuil entre -100 et 100.', 'erreur');
          return;
        }
        corps.threshold = seuil;
      }
      const bouton = form.querySelector('button[type="submit"]');
      bouton.disabled = true;
      try {
        const regle = await window.Compte.fetch('/alerts', { method: 'POST', body: corps });
        etat.regles.push(regle);
        etat.choix = null;
        etat.erreur = null;
        rendreMembre();
        message('Alerte ajoutée.', 'ok');
      } catch (e) {
        bouton.disabled = false;
        message(erreurDe(e), 'erreur');
      }
    }

    async function basculerRegle(id, active, case_) {
      const regle = etat.regles.find(r => r.id === id);
      if (!regle) return;
      case_.disabled = true;
      try {
        Object.assign(regle, await window.Compte.fetch(`/alerts/${id}`, { method: 'PATCH', body: { active } }));
        rendreMembre();
      } catch (e) {
        case_.checked = !active;   // retour arrière
        case_.disabled = false;
        message(erreurDe(e), 'erreur');
      }
    }

    async function supprimerRegle(id, bouton) {
      bouton.disabled = true;
      try {
        await window.Compte.fetch(`/alerts/${id}`, { method: 'DELETE' });
        etat.regles = etat.regles.filter(r => r.id !== id);
        rendreMembre();
        message('Alerte supprimée.', 'ok');
      } catch (e) {
        bouton.disabled = false;
        message(erreurDe(e), 'erreur');
      }
    }

    async function basculerEmail(case_) {
      const voulu = case_.checked;
      case_.disabled = true;
      try {
        etat.email = await window.Compte.fetch('/alerts/email', {
          method: 'PUT', body: { alerts_email_enabled: voulu },
        });
        rendreMembre();
        message(voulu ? 'Résumé quotidien activé.' : 'Tu ne recevras plus de résumé par e-mail.', 'ok');
      } catch (e) {
        case_.checked = !voulu;
        case_.disabled = false;
        message(erreurDe(e), 'erreur');
      }
    }

    // Délégation : un seul jeu d'écouteurs sur la racine, quel que soit le rendu.
    function surSoumission(e) {
      if (!e.target.classList.contains('alertes-formulaire')) return;
      e.preventDefault();
      ajouter(e.target);
    }

    function surChangementCase(e) {
      const action = e.target.dataset && e.target.dataset.action;
      if (action === 'email') basculerEmail(e.target);
      if (action === 'basculer') {
        const li = e.target.closest('.alertes-regle');
        basculerRegle(Number(li.dataset.id), e.target.checked, e.target);
      }
    }

    function surClic(e) {
      const bouton = e.target.closest('[data-action="supprimer"]');
      if (!bouton) return;
      const li = bouton.closest('.alertes-regle');
      supprimerRegle(Number(li.dataset.id), bouton);
    }

    racine.addEventListener('submit', surSoumission);
    racine.addEventListener('change', surChangementCase);
    racine.addEventListener('click', surClic);

    // --- Chargement selon l'état de connexion ------------------------------

    async function charger() {
      const moi = ++jeton;
      etat.chargement = true;
      etat.erreur = null;
      rendreMembre();
      try {
        const [regles, email] = await Promise.all([
          window.Compte.fetch('/alerts'), window.Compte.fetch('/alerts/email'),
        ]);
        if (moi !== jeton) return;
        etat.regles = regles || [];
        etat.email = email;
      } catch (e) {
        if (moi !== jeton) return;
        if (e.statut === 401) { rendreVisiteur(); return; }
        etat.erreur = erreurDe(e);
      }
      etat.chargement = false;
      rendreMembre();
    }

    function surCompte(membre) {
      if (membre) {
        charger();
      } else {
        jeton++;
        etat.regles = [];
        etat.email = null;
        rendreVisiteur();
      }
    }

    const ecouteur = e => surCompte(e.detail);
    window.addEventListener('compte:change', ecouteur);
    surCompte(window.Compte.membre ? window.Compte.membre() : null);

    // Démontage : retire les écouteurs et vide la racine.
    return function demonter() {
      jeton++;
      window.removeEventListener('compte:change', ecouteur);
      racine.removeEventListener('submit', surSoumission);
      racine.removeEventListener('change', surChangementCase);
      racine.removeEventListener('click', surClic);
      racine.classList.remove('alertes');
      racine.innerHTML = '';
    };
  }

  window.Compte = window.Compte || {};
  window.Compte.monterAlertes = monterAlertes;
})();
