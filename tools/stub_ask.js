// tools/stub_ask.js
// Outil de dev (non servi) : à passer à tools/capture.mjs via --eval-file pour
// simuler une réponse SSE de l'assistant (/ask) sans backend. Les autres URL
// passent au fetch d'origine.
localStorage.setItem('aiAssistantToken', 'x');
const fetchOrigine = window.fetch.bind(window);
window.fetch = (url, options) => {
  if (!String(url).includes('/ask')) return fetchOrigine(url, options);
  const enc = new TextEncoder();
  const evenements = [
    { type: 'texte', texte: 'Le score de LVMH est de +11,2 : rentabilité solide mais ' },
    // Comme le vrai serveur : chaque événement 'texte' est un morceau, pas le cumul.
    { type: 'texte', texte: '<b>valorisation</b> tendue et dynamique récente négative.' },
    { type: 'liens', liens: [{ libelle: 'Fiche LVMH', cible_type: 'ticker', cible_valeur: 'MC.PA' }] },
  ];
  const flux = new ReadableStream({
    async start(c) {
      for (const e of evenements) {
        await new Promise(r => setTimeout(r, 300));
        c.enqueue(enc.encode('data: ' + JSON.stringify(e) + '\n\n'));
      }
      c.close();
    },
  });
  return Promise.resolve(new Response(flux, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
};
