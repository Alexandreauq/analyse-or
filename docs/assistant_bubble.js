// docs/assistant_bubble.js
// Agent IA présent sur toutes les pages sauf l'accueil (la barre centrale y
// fait déjà ce travail). Desktop : petite sphère dorée en bas à droite qui
// ouvre un panneau flottant. Mobile : ouvert par l'onglet central « IA » de la
// barre d'onglets, en feuille qui monte du bas. L'agent connaît la page
// courante (contexte envoyé à l'API + puce « Fiche LVMH »), propose des
// suggestions liées à la page, et peut ouvrir une autre section (événement
// navigation). Conversation indépendante de celle de l'accueil.
// S'appuie sur index.html (chargé avant, celui-ci est en defer) :
// getAiAssistantToken, niveauReponse / setNiveauReponse, wireAnswerActions,
// lireAVoixHaute, et sur answer_reveal.js / assistant_suggestions.js.
// Composants visuels : UI kit 2026-10 (index.html). Voir
// docs/superpowers/specs/2026-10-24-accueil-agent-redesign-design.md (4.6, 4.7).
(function () {
  const ENDPOINT = 'https://goldbot.fr:8445/ask';
  const CLE_VOIX = 'analyse-or-assistant-voix-sortie';
  const SECTIONS = { accueil: '#home', or: '#or', indices: '#indices', portefeuille: '#portefeuille' };
  const ETIQUETTES = { '#home': 'accueil', '#or': 'bot Or et scalping', '#indices': 'indices (scores et signaux)', '#portefeuille': 'portefeuille' };
  const reduit = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- styles propres à l'agent (couleurs : variables du thème uniquement) ----
  const style = document.createElement('style');
  style.textContent = `
    /* Lanceur : la sphère de particules turquoise d'origine, qui tourne sur
       elle-même (voir dessineSphere), sur toutes les pages desktop. */
    .ab-launch {
      position: fixed; right: max(18px, var(--safe-right)); bottom: max(18px, var(--safe-bottom)); z-index: 30;
      width: 96px; height: 96px; border-radius: 50%; padding: 0; overflow: hidden;
      border: 1px solid var(--agent-soft); background: var(--agent-orb-bg);
      box-shadow: 0 0 24px var(--agent-glow);
      transition: transform .15s var(--ease), box-shadow .2s ease;
    }
    .ab-launch canvas { width: 100%; height: 100%; display: block; }
    .ab-launch:hover { transform: scale(1.04); }
    .ab-launch[aria-expanded="true"] { box-shadow: 0 0 0 2px var(--agent), 0 0 30px var(--agent-glow); }
    .ab-launch:focus-visible { outline: 2px solid var(--agent); outline-offset: 3px; }
    .ab-launch[hidden], .ab-panel[hidden], .ab-scrim[hidden] { display: none; }

    /* Identité turquoise de l'agent (≠ or de la barre de l'accueil) : on
       redéfinit les variables du kit à l'intérieur du panneau seulement. */
    .ab-panel {
      --ai-ring-a: var(--agent); --ai-ring-b: var(--agent-hi); --ai-glow: var(--agent-glow);
      --ai-send-from: var(--agent-hi); --ai-send-to: var(--agent); --ai-send-ink: #04211d;
      --ai-user-bubble: var(--agent-bubble);
      --gold: var(--agent); --gold-100: var(--agent-hi); --gold-700: var(--agent-deep);
    }
    .ab-panel .ai-avatar { background: radial-gradient(circle at 35% 35%, var(--agent-hi), var(--agent) 50%, var(--agent-deep) 100%); box-shadow: 0 0 10px var(--agent-glow); }
    .ab-panel .ai-link { background: var(--agent-soft); }
    .ab-panel .ui-seg button[aria-pressed="true"] { color: var(--agent-hi); background: var(--agent-bubble); }
    .ab-head-orb { width: 30px; height: 30px; border-radius: 50%; overflow: hidden; flex-shrink: 0; background: var(--agent-orb-bg); }
    .ab-head-orb canvas { width: 100%; height: 100%; display: block; }
    .ab-panel {
      position: fixed; right: max(18px, var(--safe-right)); bottom: calc(max(18px, var(--safe-bottom)) + 110px); z-index: 31;
      width: min(380px, calc(100vw - 40px)); max-height: min(620px, calc(100vh - 140px));
      display: flex; flex-direction: column; text-align: left;
      background: var(--bg-2); border: 1px solid var(--border); border-radius: 20px; box-shadow: var(--shadow-3);
      animation: content-fade-in .25s var(--ease) both;
    }
    .ab-grab { display: none; }
    .ab-head { display: flex; align-items: center; gap: 8px; padding: 12px 10px 10px 14px; border-bottom: 1px solid var(--border); }
    .ab-title { font-weight: 600; font-size: 13.5px; color: var(--text); }
    .ab-ctx {
      display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 9px; min-width: 0;
      border-radius: 999px; background: var(--bg-3); color: var(--muted); font-size: 11px; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis;
    }
    .ab-ctx::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--green-300); flex-shrink: 0; }
    .ab-sp { flex: 1; }
    .ab-head .ab-chevron { display: none; }
    .ab-thread { flex: 1; min-height: 120px; padding: 14px; }
    .ab-thread .ai-msg-text { font-size: 14px; line-height: 1.6; }
    .ab-thread .ai-msg-user { font-size: 13.5px; }
    .ab-empty { color: var(--muted); font-size: 13px; line-height: 1.5; margin: auto 0; text-align: center; padding: 8px 12px; }
    .ab-foot { display: flex; flex-direction: column; gap: 9px; padding: 10px 12px 12px; border-top: 1px solid var(--border); }
    .ab-sugg { display: flex; gap: 6px; overflow-x: auto; scrollbar-width: none; }
    .ab-sugg::-webkit-scrollbar { display: none; }
    .ab-sugg .ui-chip { height: 28px; font-size: 11.5px; flex-shrink: 0; }
    .ab-foot .ai-pill-bar { height: 46px; padding: 0 5px 0 16px; }
    .ab-foot .ai-pill-bar input { font-size: 14px; }
    .ab-foot .ai-round { width: 36px; height: 36px; }
    .ab-level { align-self: center; }
    .ab-level.ui-seg { height: 30px; }
    .ab-level.ui-seg button { font-size: 11.5px; padding: 0 11px; }

    /* Mobile / tablette : pas de sphère flottante (l'onglet « IA » de la barre
       d'onglets ouvre l'agent), le panneau devient une feuille qui monte du
       bas presque plein écran ; la page reste visible en haut, assombrie. */
    @media (max-width: 1023px) {
      .ab-launch { display: none; }
      .ab-scrim {
        position: fixed; inset: 0; z-index: 30; background: var(--scrim);
        animation: ab-fade-in .25s ease both;
      }
      .ab-panel {
        left: 0; right: 0; bottom: 0; top: calc(var(--safe-top) + 56px);
        width: auto; max-height: none; border-radius: 24px 24px 0 0;
        border: 0; border-top: 1px solid var(--border-strong);
        animation: ab-sheet-in .32s var(--ease) both;
        touch-action: pan-y;
      }
      .ab-panel.is-dragging { animation: none; transition: none; }
      .ab-panel.is-settling { transition: transform .2s var(--ease); }
      .ab-grab { display: block; width: 40px; height: 4px; border-radius: 2px; background: var(--border-strong); margin: 8px auto 0; }
      .ab-head { padding-top: 8px; touch-action: none; }
      .ab-head .ab-close { display: none; }
      .ab-head .ab-chevron { display: grid; }
      .ab-foot { padding-bottom: calc(12px + var(--safe-bottom)); }
    }
    @keyframes ab-sheet-in { from { transform: translateY(100%); } to { transform: none; } }
    @keyframes ab-fade-in { from { opacity: 0; } to { opacity: 1; } }
    @media (prefers-reduced-motion: reduce) { .ab-panel, .ab-scrim { animation: none; } }
  `;
  document.head.appendChild(style);

  const SVG = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICONES = {
    voix: '<path d="M4 9v6h4l5 4V5L8 9z M16 9a4 4 0 0 1 0 6"/>',
    nouvelle: '<path d="M12 5v14M5 12h14"/>',
    fermer: '<path d="M6 6l12 12M18 6L6 18"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    micro: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    envoyer: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  };

  // ---- sphère de particules turquoise (sphère d'origine de l'agent) ----
  // Points répartis sur une sphère qui tourne sur elle-même, ondulation
  // légère ; plus agitée quand l'agent réfléchit ou parle. ~30 images/s, en
  // pause quand l'onglet est caché ou le canvas invisible ; figée si
  // l'utilisateur a demandé moins d'animations.
  const TEAL = [45, 212, 191];
  const etatSphere = { reflechit: false, parle: false };
  const bruit = (x) => Math.sin(x) * 0.6 + Math.sin(x * 2.3 + 1.7) * 0.3 + Math.sin(x * 4.1 + 0.4) * 0.1;
  function dessineSphere(cv, taille, nbPoints) {
    const points = Array.from({ length: nbPoints }, () => ({
      a: Math.random() * Math.PI * 2, b: Math.random() * Math.PI - Math.PI / 2,
      jit: Math.random() - 0.5, taille: 0.5 + Math.random() * 1.1, phase: Math.random() * Math.PI * 2,
    }));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const ctx = cv.getContext('2d');
    cv.width = taille * dpr; cv.height = taille * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const debut = performance.now();
    const echelle = taille / 96;
    let derniere = 0;
    function frame(now) {
      requestAnimationFrame(frame);
      if (now - derniere < 33 || document.hidden || !cv.offsetParent) return;
      derniere = now;
      const t = reduit ? 0 : (now - debut) / 1000;
      const amp = etatSphere.parle ? 1.7 : (etatSphere.reflechit ? 1.25 : 1);
      const cx = taille / 2, cy = taille / 2;
      const R = 34 * echelle * (1 + 0.035 * Math.sin(t * 0.9));
      const tps = t * 0.35 * (etatSphere.parle ? 2 : (etatSphere.reflechit ? 1.6 : 1));
      const rot = t * 0.25;
      ctx.clearRect(0, 0, taille, taille);
      for (const p of points) {
        const onde = 1 + 0.1 * amp * bruit(p.a * 3 + tps) + 0.04 * amp * Math.sin(p.phase + t * 1.3);
        const r = R * onde * (1 + 0.05 * amp * p.jit);
        const x3 = r * Math.cos(p.b) * Math.cos(p.a + rot);
        const y3 = r * Math.sin(p.b);
        const z3 = r * Math.cos(p.b) * Math.sin(p.a + rot);
        const persp = 1 / (1 - z3 / (R * 3.2));
        const alpha = Math.max(0.08, Math.min(1, 0.35 + 0.55 * (z3 / (R * 1.4) + 0.5)));
        ctx.fillStyle = 'rgba(' + TEAL.join(',') + ',' + alpha + ')';
        const sz = p.taille * persp * Math.max(0.6, echelle);
        ctx.fillRect(cx + x3 * persp - sz / 2, cy + y3 * persp - sz / 2, sz, sz);
      }
    }
    requestAnimationFrame(frame);
  }
  function sphereDans(conteneur, taille, nbPoints) {
    const cv = document.createElement('canvas');
    cv.setAttribute('aria-hidden', 'true');
    conteneur.appendChild(cv);
    dessineSphere(cv, taille, nbPoints);
  }

  // ---- lanceur (desktop, toutes les pages) ----
  const lanceur = document.createElement('button');
  lanceur.type = 'button';
  lanceur.className = 'ab-launch';
  lanceur.setAttribute('aria-label', 'Ouvrir l\'assistant');
  lanceur.setAttribute('aria-expanded', 'false');
  document.body.appendChild(lanceur);
  sphereDans(lanceur, 96, 420);
  // Onglet « IA » de la barre d'onglets mobile : même sphère.
  const orbeOnglet = document.querySelector('#bottomNavIa .nav-orb');
  if (orbeOnglet) sphereDans(orbeOnglet, 54, 240);

  // ---- voile (mobile, voir Task 8) + panneau ----
  const voile = document.createElement('div');
  voile.className = 'ab-scrim';
  voile.hidden = true;
  document.body.appendChild(voile);

  const panneau = document.createElement('div');
  panneau.className = 'ab-panel';
  panneau.setAttribute('role', 'dialog');
  panneau.setAttribute('aria-label', 'Assistant IA');
  panneau.hidden = true;
  panneau.innerHTML = `
    <div class="ab-grab" aria-hidden="true"></div>
    <div class="ab-head">
      <span class="ab-head-orb" id="abHeadOrb"></span>
      <span class="ab-title">Assistant</span>
      <span class="ab-ctx" id="abCtx"></span>
      <span class="ab-sp"></span>
      <button type="button" class="ai-icon-btn" id="abVoix" aria-pressed="false" aria-label="Lire les réponses à voix haute" title="Lecture à voix haute">${SVG(ICONES.voix)}</button>
      <button type="button" class="ai-icon-btn" id="abNouvelle" aria-label="Nouvelle conversation" title="Nouvelle conversation">${SVG(ICONES.nouvelle)}</button>
      <button type="button" class="ai-icon-btn ab-close" id="abFermer" aria-label="Fermer l'assistant" title="Fermer">${SVG(ICONES.fermer)}</button>
      <button type="button" class="ai-icon-btn ab-chevron" id="abReplier" aria-label="Fermer l'assistant" title="Fermer">${SVG(ICONES.chevron)}</button>
    </div>
    <div class="ai-thread ab-thread" id="abMessages" aria-live="polite"></div>
    <div class="ab-foot">
      <div class="ab-sugg" id="abSugg"></div>
      <form class="ai-pill is-active" id="abForm" autocomplete="off">
        <div class="ai-pill-bar">
          <input type="search" id="abQuestion" enterkeyhint="send" aria-label="Question à l'assistant">
          <button type="button" class="ai-round ai-mic" id="abMicro" aria-pressed="false" aria-label="Dicter la question" title="Dicter">${SVG(ICONES.micro)}</button>
          <button type="submit" class="ai-round ai-send" aria-label="Envoyer">${SVG(ICONES.envoyer)}</button>
        </div>
      </form>
      <div class="ui-seg ab-level" role="group" aria-label="Niveau de réponse">
        <button type="button" data-niveau="technique">Technique</button>
        <button type="button" data-niveau="grand_public">Grand public</button>
      </div>
    </div>`;
  document.body.appendChild(panneau);
  sphereDans(panneau.querySelector('#abHeadOrb'), 30, 140);
  const messages = panneau.querySelector('#abMessages');
  const champ = panneau.querySelector('#abQuestion');
  const formulaire = panneau.querySelector('#abForm');
  const micro = panneau.querySelector('#abMicro');
  const boutonVoix = panneau.querySelector('#abVoix');

  // ---- lecture vocale automatique (bascule d'en-tête, mémorisée) ----
  let voixLue = false;
  try { voixLue = localStorage.getItem(CLE_VOIX) === '1'; } catch (e) { voixLue = false; }
  boutonVoix.setAttribute('aria-pressed', String(voixLue));
  boutonVoix.addEventListener('click', () => {
    voixLue = !voixLue;
    boutonVoix.setAttribute('aria-pressed', String(voixLue));
    try { localStorage.setItem(CLE_VOIX, voixLue ? '1' : '0'); } catch (e) { /* ignoré */ }
    if (!voixLue && window.speechSynthesis) window.speechSynthesis.cancel();
  });

  // ---- niveau de réponse (même réglage que l'accueil) ----
  panneau.querySelectorAll('.ab-level button').forEach(b => {
    b.addEventListener('click', () => { if (typeof setNiveauReponse === 'function') setNiveauReponse(b.dataset.niveau); });
  });
  const niveauCourant = () => (typeof niveauReponse === 'function' ? niveauReponse() : 'technique');

  // ---- contexte : la page courante ----
  function routeCourante() { return location.hash || '#home'; }
  function nomEntreprise() {
    const r = decoupeRoute(routeCourante());
    if (!r.ticker || typeof indicesData === 'undefined' || !indicesData || !Array.isArray(indicesData.companies)) return null;
    const c = indicesData.companies.find(x => x.ticker === r.ticker);
    return c ? c.name : null;
  }
  // Libellé envoyé à l'API (inchangé depuis la première version de l'agent).
  function contexteCourant() {
    const h = routeCourante();
    if (h.indexOf('#indices/') === 0) {
      let ticker = h.slice('#indices/'.length);
      try { ticker = decodeURIComponent(ticker); } catch (e) { /* on garde tel quel */ }
      return 'fiche de ' + ticker + ' (indices)';
    }
    return ETIQUETTES[h] || 'accueil';
  }

  function majContexte() {
    const nom = nomEntreprise();
    panneau.querySelector('#abCtx').textContent = contextLabel(routeCourante(), nom);
    champ.placeholder = nom ? `Pose une question sur ${nom}…` : 'Pose ta question…';
    const sugg = panneau.querySelector('#abSugg');
    sugg.innerHTML = '';
    suggestionsForRoute(routeCourante(), nom).forEach(texte => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ui-chip';
      b.textContent = texte;
      b.addEventListener('click', () => pose(texte));
      sugg.appendChild(b);
    });
    if (!messages.children.length) {
      messages.innerHTML = '<p class="ab-empty">Pose une question sur cette page, ou choisis une suggestion ci-dessous.</p>';
    }
  }

  // ---- ouverture / fermeture ----
  function ouvre(ouvrir) {
    panneau.hidden = !ouvrir;
    voile.hidden = !ouvrir;
    lanceur.setAttribute('aria-expanded', String(ouvrir));
    lanceur.setAttribute('aria-label', ouvrir ? 'Fermer l\'assistant' : 'Ouvrir l\'assistant');
    const onglet = document.getElementById('bottomNavIa');
    if (onglet) onglet.classList.toggle('active', ouvrir);
    if (ouvrir) {
      if (typeof setNiveauReponse === 'function') setNiveauReponse(niveauCourant());
      majContexte();
      champ.focus();
    }
  }
  // Présent sur toutes les pages, accueil compris (conversation distincte de
  // la barre de l'accueil) : seuls le contexte et les suggestions changent.
  function majVisibilite() {
    if (!panneau.hidden) majContexte();
  }
  lanceur.addEventListener('click', () => ouvre(panneau.hidden));
  voile.addEventListener('click', () => ouvre(false));
  panneau.querySelector('#abFermer').addEventListener('click', () => ouvre(false));
  panneau.querySelector('#abReplier').addEventListener('click', () => ouvre(false));
  panneau.querySelector('#abNouvelle').addEventListener('click', () => {
    abandonneRequete();
    historique = [];
    messages.innerHTML = '';
    majContexte();
    champ.focus();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panneau.hidden) ouvre(false); });

  // Feuille mobile : glisser la poignée / l'en-tête vers le bas pour fermer
  // (au-delà de 80px), sinon elle revient en place.
  const SEUIL_FERMETURE = 80;
  let depart = null;
  const zoneGlisser = (cible) => cible.closest('.ab-grab, .ab-head') && !cible.closest('button');
  panneau.addEventListener('pointerdown', (e) => {
    if (!window.matchMedia('(max-width: 1023px)').matches || !zoneGlisser(e.target)) return;
    depart = e.clientY;
    panneau.classList.add('is-dragging');
    panneau.setPointerCapture(e.pointerId);
  });
  panneau.addEventListener('pointermove', (e) => {
    if (depart === null) return;
    panneau.style.transform = `translateY(${Math.max(0, e.clientY - depart)}px)`;
  });
  const finGlisser = (e) => {
    if (depart === null) return;
    const dy = e.clientY - depart;
    depart = null;
    panneau.classList.remove('is-dragging');
    if (dy > SEUIL_FERMETURE) { panneau.style.transform = ''; ouvre(false); return; }
    panneau.classList.add('is-settling');
    panneau.style.transform = '';
    setTimeout(() => panneau.classList.remove('is-settling'), 220);
  };
  panneau.addEventListener('pointerup', finGlisser);
  panneau.addEventListener('pointercancel', finGlisser);
  window.addEventListener('hashchange', majVisibilite);
  majVisibilite();

  // ---- messages ----
  function ajouteQuestion(texte) {
    const vide = messages.querySelector('.ab-empty');
    if (vide) vide.remove();
    const d = document.createElement('div');
    d.className = 'ai-msg-user';
    d.textContent = texte;
    messages.appendChild(d);
  }
  function ajouteReponse() {
    messages.insertAdjacentHTML('beforeend', `
      <div class="ai-msg-ai">
        <span class="ai-avatar is-thinking" aria-hidden="true"></span>
        <div class="ai-msg-body"><div class="ai-msg-text"></div><div class="ai-links"></div><div class="ai-actions"></div></div>
      </div>`);
    const msg = messages.lastElementChild;
    return {
      avatar: msg.querySelector('.ai-avatar'),
      texte: msg.querySelector('.ai-msg-text'),
      liens: msg.querySelector('.ai-links'),
      actions: msg.querySelector('.ai-actions'),
    };
  }
  const suivre = () => {
    if (messages.scrollHeight - messages.scrollTop - messages.clientHeight < 96) {
      messages.scrollTo({ top: messages.scrollHeight, behavior: reduit ? 'auto' : 'smooth' });
    }
  };

  function hashPour(cibleType, cibleValeur) {
    if (cibleType === 'ticker') return '#indices/' + encodeURIComponent(cibleValeur);
    return SECTIONS[cibleValeur] || '#' + cibleValeur;
  }
  function allerVers(cibleType, cibleValeur) {
    location.hash = hashPour(cibleType, cibleValeur);
  }
  function rendLiens(el, liens) {
    el.innerHTML = '';
    (liens || []).forEach(l => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ai-link';
      b.textContent = l.libelle || l.cible_valeur;
      b.addEventListener('click', () => allerVers(l.cible_type, l.cible_valeur));
      el.appendChild(b);
    });
    el.classList.add('ai-reveal-after');
  }

  // ---- voix d'entrée : dictée ----
  const Reco = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Reco) {
    const reco = new Reco();
    reco.lang = 'fr-FR';
    reco.interimResults = true;
    reco.continuous = false;
    let base = '';
    reco.onstart = () => { base = champ.value ? champ.value.trim() + ' ' : ''; micro.setAttribute('aria-pressed', 'true'); };
    reco.onresult = (e) => {
      let texte = '';
      for (const r of e.results) texte += r[0].transcript;
      champ.value = (base + texte).trim();
    };
    reco.onend = () => { micro.setAttribute('aria-pressed', 'false'); champ.focus(); };
    reco.onerror = () => micro.setAttribute('aria-pressed', 'false');
    micro.addEventListener('click', () => {
      try {
        if (micro.getAttribute('aria-pressed') === 'true') reco.stop(); else reco.start();
      } catch (e) { micro.setAttribute('aria-pressed', 'false'); }
    });
  } else {
    micro.hidden = true;
  }

  // ---- envoi : streaming SSE ----
  let historique = [];
  let enCours = false;
  // Conversation courante : « Nouvelle conversation » l'incrémente et annule la
  // requête en cours, dont les événements en retard sont alors ignorés.
  let generation = 0;
  let requete = null;
  function abandonneRequete() {
    generation++;
    if (requete) { requete.abort(); requete = null; }
    enCours = false;
    etatSphere.reflechit = false;
  }

  async function pose(question) {
    if (enCours || !question) return;
    const maGeneration = generation;
    const depassee = () => maGeneration !== generation;
    const token = typeof getAiAssistantToken === 'function' ? getAiAssistantToken() : null;
    ajouteQuestion(question);
    const rep = ajouteReponse();
    etatSphere.reflechit = true;
    suivre();
    let liensRecus = [];
    let texte = '';
    let navigation = null;
    let erreur = false;
    const reveal = createWordReveal(rep.texte, {
      reduceMotion: reduit,
      onProgress: suivre,
      onDone: () => {
        rep.avatar.classList.remove('is-thinking');
        rendLiens(rep.liens, liensRecus);
        if (reveal.text() && !erreur && typeof wireAnswerActions === 'function') {
          wireAnswerActions(rep.actions, () => reveal.text());
          if (voixLue && !depassee() && !panneau.hidden && typeof lireAVoixHaute === 'function') {
            const ecouter = rep.actions.querySelector('.ai-listen');
            if (ecouter) lireAVoixHaute(reveal.text(), ecouter);
          }
        }
        suivre();
      },
    });
    if (!token) {
      erreur = true;
      rep.texte.classList.add('is-error');
      reveal.fix('Saisis d\'abord ton jeton d\'accès dans la barre d\'accueil.');
      return;
    }
    enCours = true;
    const controleur = new AbortController();
    requete = controleur;
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        signal: controleur.signal,
        headers: { 'Content-Type': 'application/json', 'X-Bot-Token': token },
        body: JSON.stringify({ question, history: historique, contexte: contexteCourant(), niveau: niveauCourant() }),
      });
      if (!res.ok || !res.body) {
        let detail = "L'assistant ne peut pas répondre pour le moment.";
        try { const corps = await res.json(); if (corps && corps.detail) detail = corps.detail; } catch (e) { /* message par défaut */ }
        throw new Error(detail);
      }
      const lecteur = res.body.getReader();
      const dec = new TextDecoder();
      let tampon = '';
      for (;;) {
        const { value, done } = await lecteur.read();
        if (depassee()) { lecteur.cancel().catch(() => {}); return; }
        if (done) break;
        tampon += dec.decode(value, { stream: true });
        let fin;
        while ((fin = tampon.indexOf('\n\n')) >= 0) {
          const bloc = tampon.slice(0, fin);
          tampon = tampon.slice(fin + 2);
          const ligne = bloc.split('\n').find((l) => l.indexOf('data: ') === 0);
          if (!ligne) continue;
          const ev = JSON.parse(ligne.slice(6));
          if (ev.type === 'texte') { rep.avatar.classList.remove('is-thinking'); etatSphere.reflechit = false; texte += ev.texte; reveal.setText(texte); }
          else if (ev.type === 'liens') liensRecus = ev.liens || [];
          else if (ev.type === 'navigation') navigation = ev;
          else if (ev.type === 'erreur') { erreur = true; rep.texte.classList.add('is-error'); reveal.replace(ev.detail || 'Erreur.'); }
        }
      }
      reveal.finish();
    } catch (e) {
      if (depassee()) return;
      erreur = true;
      rep.texte.classList.add('is-error');
      reveal.fix((e && e.message) ? e.message : "Je n'ai pas pu contacter l'assistant, réessaie dans un instant.");
    } finally {
      if (!depassee()) {
        enCours = false;
        etatSphere.reflechit = false;
        requete = null;
      }
    }
    if (depassee()) return;
    if (!erreur && texte) {
      historique.push({ role: 'user', content: question });
      historique.push({ role: 'assistant', content: texte });
    }
    if (navigation && !erreur) {
      const d = document.createElement('p');
      d.className = 'ab-empty';
      d.textContent = 'Je vous ouvre la page…';
      messages.appendChild(d);
      suivre();
      setTimeout(() => allerVers(navigation.cible_type, navigation.cible_valeur), 1200);
    }
  }

  formulaire.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = champ.value.trim();
    if (!q) return;
    champ.value = '';
    pose(q);
  });

  window.AssistantIA = {
    open() { ouvre(true); },
    close() { ouvre(false); },
    toggle() { ouvre(panneau.hidden); },
    isOpen() { return !panneau.hidden; },
  };
})();
