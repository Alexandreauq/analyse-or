// docs/answer_reveal.js
// Rédaction des réponses de l'assistant IA mot par mot, en fondu (chaque mot
// passe du flou au net, voir .rv-w dans index.html) — utilisée par la barre
// de l'accueil ET par l'agent (assistant_bubble.js), pour un rendu identique.
// Le rythme suit le flux SSE : on n'affiche jamais un mot pas encore reçu, et
// une fois le flux terminé la file restante se vide en REVEAL_FLUSH_MS au plus.
// Chaque mot passe par textContent (jamais innerHTML) : le HTML éventuel d'une
// réponse s'affiche comme du texte. Chargé tel quel par index.html, avec
// module.exports pour Node (docs/answer_reveal.test.js).
// Voir docs/superpowers/specs/2026-10-24-accueil-agent-redesign-design.md (4.4 bis).

const REVEAL_WORD_MS = 35;    // cadence d'apparition pendant le flux
const REVEAL_FLUSH_MS = 300;  // durée max pour vider la file après la fin du flux
const REVEAL_FADE_MS = 220;   // durée du fondu d'un mot (reprise par --rv-fade en CSS)
const REVEAL_LAG_WORDS = 40;  // au-delà, on rattrape plusieurs mots par pas

// Découpe en mots en gardant les espaces / sauts de ligne collés à la fin du
// mot qui les précède : tokens.join('') === text.
function tokenizeWords(text) {
  return text.match(/\S+\s*|\s+/g) || [];
}

// Mots de fullText pas encore pris en charge (shownText = ce qui est déjà
// affiché ou en file). Tant que le flux continue (final = false), un dernier
// mot sans espace final peut encore grandir : il est retenu. Préfixe
// différent (texte remplacé, ex. événement d'erreur) → null.
function pendingWords(shownText, fullText, final) {
  if (!fullText.startsWith(shownText)) return null;
  const words = tokenizeWords(fullText.slice(shownText.length));
  if (!final && words.length && !/\s$/.test(words[words.length - 1])) words.pop();
  return words;
}

function createWordReveal(target, opts) {
  const reduceMotion = !!(opts && opts.reduceMotion);
  const onProgress = (opts && opts.onProgress) || (() => {});
  const onDone = (opts && opts.onDone) || (() => {});
  let full = '';        // texte complet reçu
  let taken = '';       // texte déjà affiché ou en file
  let queue = [];       // mots en attente d'affichage
  let final = false;    // flux SSE terminé
  let frozen = false;   // fix()/cancel() : plus rien ne s'ajoute
  let timer = null;
  let doneFired = false;

  const isDone = () => frozen || (final && !queue.length && taken === full);

  const sync = () => {
    target.classList.toggle('rv-streaming', !isDone());
    if (isDone() && !doneFired) { doneFired = true; onDone(); }
  };

  const appendWord = (word, animated) => {
    const span = document.createElement('span');
    span.className = animated ? 'rv-w' : 'rv-w rv-static';
    span.textContent = word;
    target.appendChild(span);
  };

  const stopTimer = () => { if (timer !== null) { clearTimeout(timer); timer = null; } };

  const tick = () => {
    timer = null;
    if (frozen || !queue.length) { sync(); return; }
    const n = queue.length > REVEAL_LAG_WORDS ? Math.ceil(queue.length / 10) : 1;
    queue.splice(0, n).forEach(w => appendWord(w, true));
    onProgress();
    schedule();
    sync();
  };

  // Cadence de vidage figée au moment où le flux se termine : recalculée à
  // chaque mot sur une file qui rétrécit, elle s'allongerait au fil des pas.
  let flushDelay = REVEAL_WORD_MS;

  function schedule() {
    if (frozen || timer !== null || !queue.length) return;
    timer = setTimeout(tick, final ? flushDelay : REVEAL_WORD_MS);
  }

  // Affiche d'un coup tout ce qui a été reçu mais pas encore affiché.
  const flushNow = () => {
    stopTimer();
    const shown = taken.slice(0, taken.length - queue.join('').length);
    const rest = full.startsWith(shown) ? full.slice(shown.length) : full;
    if (!full.startsWith(shown)) target.replaceChildren();
    if (rest) appendWord(rest, false);
    queue = [];
    taken = full;
    onProgress();
  };

  const enqueue = () => {
    const words = pendingWords(taken, full, final);
    if (words === null) { restart(); return; }
    queue.push(...words);
    taken += words.join('');
    if (reduceMotion) { flushNow(); return; }
    schedule();
  };

  function restart() {
    stopTimer();
    target.replaceChildren();
    taken = '';
    queue = [];
    enqueue();
  }

  return {
    // Nouveau texte cumulé reçu (événement SSE 'texte').
    setText(t) {
      if (frozen) return;
      full = t;
      enqueue();
      sync();
    },
    // Texte remplacé (événement 'erreur') : on repart de zéro.
    replace(t) {
      if (frozen) return;
      full = t;
      restart();
      sync();
    },
    // Fin du flux : le dernier mot est libéré, la file se vide vite.
    finish() {
      if (frozen) return;
      final = true;
      enqueue();
      stopTimer();
      flushDelay = Math.max(16, Math.floor(REVEAL_FLUSH_MS / Math.max(1, queue.length)));
      schedule();
      sync();
    },
    // Affichage complet et immédiat (erreurs, historique rouvert).
    fix(t) {
      full = t;
      final = true;
      stopTimer();
      target.replaceChildren();
      taken = '';
      queue = [];
      if (t) appendWord(t, false);
      taken = t;
      frozen = true;
      onProgress();
      sync();
    },
    // Arrêt net (nouvelle question, retour accueil) : ce qui est reçu s'affiche.
    cancel() {
      if (frozen) return;
      flushNow();
      frozen = true;
      sync();
    },
    text() { return full; },
    done() { return isDone(); },
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { REVEAL_WORD_MS, REVEAL_FLUSH_MS, REVEAL_FADE_MS, tokenizeWords, pendingWords, createWordReveal };
}
