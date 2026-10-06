// docs/assistant_bubble.js
// Assistant IA présent sur toutes les pages : une sphère de particules en bas à
// droite. Un appui ouvre un panneau où l'on pose sa question à l'écrit ou à
// l'oral. La réponse peut être lue à voix haute. L'assistant connaît la page
// courante (contexte) et peut ouvrir une autre section ou une fiche (événement
// navigation). Chargé par index.html ; s'appuie sur getAiAssistantToken() défini là.
(function () {
  const ENDPOINT = 'https://goldbot.fr:8445/ask';
  const CLE_VOIX = 'analyse-or-assistant-voix-sortie';
  const TEAL = [45, 212, 191];
  const SECTIONS = { accueil: '#home', or: '#or', indices: '#indices', portefeuille: '#portefeuille' };
  const ETIQUETTES = { '#home': 'accueil', '#or': 'bot Or et scalping', '#indices': 'indices (scores et signaux)', '#portefeuille': 'portefeuille' };

  // ---- styles (injectés pour ne pas alourdir index.html) ----
  const style = document.createElement('style');
  style.textContent = [
    '.ab-bulle { position: fixed; right: 18px; bottom: 18px; width: 96px; height: 96px; border-radius: 50%;',
    '  border: 1px solid rgba(45,212,191,.25); background: rgba(10,18,22,.6); padding: 0; cursor: pointer; z-index: 30; }',
    '.ab-bulle canvas { width: 100%; height: 100%; display: block; border-radius: 50%; }',
    '.ab-bulle:focus-visible { outline: 2px solid rgba(45,212,191,.6); outline-offset: 3px; }',
    '.ab-panneau { position: fixed; right: 18px; bottom: 124px; width: min(400px, calc(100vw - 36px)); max-height: 62vh;',
    '  display: flex; flex-direction: column; gap: 10px; background: #161d30; border: 1px solid rgba(255,255,255,.1);',
    '  border-radius: 16px; padding: 14px; z-index: 31; box-shadow: 0 16px 40px rgba(0,0,0,.45); }',
    '.ab-panneau[hidden] { display: none; }',
    '.ab-entete { display: flex; justify-content: space-between; align-items: center; color: #9aa6c4; font-size: 13px; }',
    '.ab-entete button { background: none; border: 1px solid rgba(255,255,255,.12); color: #9aa6c4; border-radius: 10px; padding: 4px 10px; cursor: pointer; font: inherit; font-size: 12px; }',
    '.ab-messages { overflow-y: auto; display: flex; flex-direction: column; gap: 10px; min-height: 80px; padding-right: 4px; }',
    '.ab-msg { font-size: 15px; line-height: 1.6; color: #e8ecf5; white-space: pre-wrap; overflow-wrap: anywhere; }',
    '.ab-msg.ab-question { color: #9aa6c4; font-size: 13px; }',
    '.ab-msg.ab-erreur { color: #f0b3a8; }',
    '.ab-liens { display: flex; flex-wrap: wrap; gap: 8px; }',
    '.ab-liens button { background: rgba(45,212,191,.1); border: 1px solid rgba(45,212,191,.35); color: #7fe7d6; border-radius: 16px; padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px; }',
    '.ab-form { display: flex; gap: 8px; align-items: center; }',
    '.ab-form input { flex: 1; min-width: 0; height: 44px; border-radius: 22px; border: 1px solid rgba(255,255,255,.12); background: rgba(255,255,255,.04); color: #e8ecf5; padding: 0 16px; font: inherit; font-size: 15px; outline: none; }',
    '.ab-form button { height: 44px; min-width: 44px; border-radius: 22px; border: 1px solid rgba(255,255,255,.12); background: none; color: #9aa6c4; cursor: pointer; font: inherit; }',
    '.ab-form button[aria-pressed="true"] { background: rgba(42,91,255,.22); color: #e8ecf5; }',
    '.ab-voix-sortie { font-size: 12px; color: #9aa6c4; display: flex; align-items: center; gap: 6px; }',
    '@media (max-width: 600px) { .ab-bulle { width: 84px; height: 84px; right: 12px; bottom: 12px; } .ab-panneau { right: 12px; bottom: 108px; } }',
  ].join('\n');
  document.head.appendChild(style);

  // ---- bulle : sphère de particules ----
  const bulle = document.createElement('button');
  bulle.type = 'button';
  bulle.className = 'ab-bulle';
  bulle.setAttribute('aria-label', 'Ouvrir l\'assistant');
  bulle.setAttribute('aria-expanded', 'false');
  const cv = document.createElement('canvas');
  bulle.appendChild(cv);
  document.body.appendChild(bulle);

  const points = Array.from({ length: 420 }, () => ({
    a: Math.random() * Math.PI * 2, b: Math.random() * Math.PI - Math.PI / 2,
    jit: Math.random() - 0.5, taille: 0.5 + Math.random() * 1.1, phase: Math.random() * Math.PI * 2,
  }));
  const bruit = (x) => Math.sin(x) * 0.6 + Math.sin(x * 2.3 + 1.7) * 0.3 + Math.sin(x * 4.1 + 0.4) * 0.1;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const ctx = cv.getContext('2d');
  const taille = 96;
  cv.width = taille * dpr; cv.height = taille * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const reduit = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const debut = performance.now();
  let derniere = 0;

  function dessine(now) {
    requestAnimationFrame(dessine);
    // ~30 images par seconde : la bulle est toujours là, il faut ménager le téléphone.
    if (now - derniere < 33) return;
    derniere = now;
    const t = reduit ? 0 : (now - debut) / 1000;
    const parle = bulle.dataset.parle === '1';
    const amp = parle ? 1.7 : 1;
    const cx = taille / 2, cy = taille / 2;
    const R = 34 * (1 + 0.035 * Math.sin(t * 0.9));
    const tps = t * 0.35 * (parle ? 2 : 1);
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
      const sz = p.taille * persp;
      ctx.fillRect(cx + x3 * persp - sz / 2, cy + y3 * persp - sz / 2, sz, sz);
    }
  }
  requestAnimationFrame(dessine);

  // ---- panneau ----
  const panneau = document.createElement('div');
  panneau.className = 'ab-panneau';
  panneau.hidden = true;
  panneau.innerHTML = [
    '<div class="ab-entete"><span>Assistant</span>',
    '<span><label class="ab-voix-sortie"><input type="checkbox" id="abVoixSortie"> Réponse à voix haute</label>',
    ' <button type="button" id="abFermer">Fermer</button></span></div>',
    '<div class="ab-messages" id="abMessages" aria-live="polite"></div>',
    '<form class="ab-form" id="abForm" autocomplete="off">',
    '<button type="button" id="abMicro" aria-pressed="false" aria-label="Dicter la question" title="Dicter">🎙</button>',
    '<input type="search" id="abQuestion" placeholder="Que puis-je faire pour vous ?">',
    '<button type="submit" aria-label="Envoyer">➤</button></form>',
  ].join('');
  document.body.appendChild(panneau);
  const messages = panneau.querySelector('#abMessages');
  const champ = panneau.querySelector('#abQuestion');
  const formulaire = panneau.querySelector('#abForm');
  const micro = panneau.querySelector('#abMicro');
  const voixSortie = panneau.querySelector('#abVoixSortie');

  let voixLue = false;
  try { voixLue = localStorage.getItem(CLE_VOIX) === '1'; } catch (e) { voixLue = false; }
  voixSortie.checked = voixLue;
  voixSortie.addEventListener('change', () => {
    try { localStorage.setItem(CLE_VOIX, voixSortie.checked ? '1' : '0'); } catch (e) { /* ignoré */ }
    if (!voixSortie.checked && window.speechSynthesis) window.speechSynthesis.cancel();
  });

  function ouvre(ouvrir) {
    panneau.hidden = !ouvrir;
    bulle.setAttribute('aria-expanded', ouvrir ? 'true' : 'false');
    if (ouvrir) champ.focus();
  }
  bulle.addEventListener('click', () => ouvre(panneau.hidden));
  panneau.querySelector('#abFermer').addEventListener('click', () => ouvre(false));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panneau.hidden) ouvre(false); });

  // ---- contexte : la page courante, pour que l'assistant sache où on est ----
  function contexteCourant() {
    const h = location.hash || '#home';
    if (h.indexOf('#indices/') === 0) {
      let ticker = h.slice('#indices/'.length);
      try { ticker = decodeURIComponent(ticker); } catch (e) { /* on garde tel quel */ }
      return 'fiche de ' + ticker + ' (indices)';
    }
    return ETIQUETTES[h] || 'accueil';
  }

  function ajouteMessage(texte, classe) {
    const d = document.createElement('div');
    d.className = 'ab-msg ' + (classe || '');
    d.textContent = texte;
    messages.appendChild(d);
    messages.scrollTop = messages.scrollHeight;
    return d;
  }

  function ajouteLiens(liens) {
    if (!liens || !liens.length) return;
    const d = document.createElement('div');
    d.className = 'ab-liens';
    for (const l of liens) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = l.libelle || l.cible_valeur;
      b.addEventListener('click', () => allerVers(l.cible_type, l.cible_valeur));
      d.appendChild(b);
    }
    messages.appendChild(d);
  }

  function hashPour(cibleType, cibleValeur) {
    if (cibleType === 'ticker') return '#indices/' + encodeURIComponent(cibleValeur);
    return SECTIONS[cibleValeur] || '#' + cibleValeur;
  }

  function allerVers(cibleType, cibleValeur) {
    location.hash = hashPour(cibleType, cibleValeur);
  }

  // ---- voix de sortie : lit la réponse à voix haute si demandé ----
  function lisReponse(texte) {
    if (!voixSortie.checked || !window.speechSynthesis || !texte) return;
    const u = new SpeechSynthesisUtterance(texte);
    u.lang = 'fr-FR';
    u.onstart = () => { bulle.dataset.parle = '1'; };
    u.onend = () => { bulle.dataset.parle = '0'; };
    u.onerror = () => { bulle.dataset.parle = '0'; };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  // ---- voix d'entrée : même principe que la barre d'accueil ----
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

  // ---- envoi : streaming SSE, comme la barre d'accueil ----
  let historique = [];
  let enCours = false;

  async function pose(question) {
    if (enCours) return;
    const token = typeof getAiAssistantToken === 'function' ? getAiAssistantToken() : null;
    if (!token) { ajouteMessage('Saisis d\'abord ton jeton d\'accès dans la barre d\'accueil.', 'ab-erreur'); return; }
    enCours = true;
    ajouteMessage(question, 'ab-question');
    const reponse = ajouteMessage('…');
    let texte = '';
    let navigation = null;
    let erreur = false;
    try {
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Bot-Token': token },
        body: JSON.stringify({ question, history: historique, contexte: contexteCourant(), niveau: 'technique' }),
      });
      if (!res.ok || !res.body) {
        let detail = "L'assistant ne peut pas répondre pour le moment.";
        try { const corps = await res.json(); if (corps && corps.detail) detail = corps.detail; } catch (e) { /* message par défaut */ }
        throw new Error(detail);
      }
      reponse.textContent = '';
      const lecteur = res.body.getReader();
      const dec = new TextDecoder();
      let tampon = '';
      for (;;) {
        const { value, done } = await lecteur.read();
        if (done) break;
        tampon += dec.decode(value, { stream: true });
        let fin;
        while ((fin = tampon.indexOf('\n\n')) >= 0) {
          const bloc = tampon.slice(0, fin);
          tampon = tampon.slice(fin + 2);
          const ligne = bloc.split('\n').find((l) => l.indexOf('data: ') === 0);
          if (!ligne) continue;
          const ev = JSON.parse(ligne.slice(6));
          if (ev.type === 'texte') { texte += ev.texte; reponse.textContent = texte; messages.scrollTop = messages.scrollHeight; }
          else if (ev.type === 'liens') ajouteLiens(ev.liens);
          else if (ev.type === 'navigation') navigation = ev;
          else if (ev.type === 'erreur') { erreur = true; reponse.textContent = ev.detail || 'Erreur.'; reponse.classList.add('ab-erreur'); }
        }
      }
    } catch (e) {
      erreur = true;
      reponse.textContent = (e && e.message) ? e.message : "Je n'ai pas pu contacter l'assistant, réessaie dans un instant.";
      reponse.classList.add('ab-erreur');
    } finally {
      enCours = false;
    }
    if (!erreur && texte) {
      historique.push({ role: 'user', content: question });
      historique.push({ role: 'assistant', content: texte });
      lisReponse(texte);
    }
    if (navigation && !erreur) {
      ajouteMessage('Je vous ouvre la page…', 'ab-question');
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
})();
