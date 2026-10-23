# Refonte accueil + agent IA — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** Moderniser la page d'accueil (pilule de saisie lumineuse, tuiles live desktop, carte « Aujourd'hui » mobile, guide au clic, réponse en bulles avec rédaction mot par mot) et l'agent IA (panneau flottant desktop, onglet central « IA » + feuille montante mobile), avec une barre d'onglets en bas sur mobile.

**Architecture :** Site statique vanilla (`docs/`, GitHub Pages). La logique pure nouvelle vit dans 3 petits modules globaux + `module.exports` (même motif que `docs/chart_patterns.js`) testés sous Node ; le CSS et le markup restent dans `docs/index.html` (section commentée « UI kit 2026-10 ») ; l'agent reste dans `docs/assistant_bubble.js`. Les deux conversations (accueil, agent) gardent chacune leur moteur d'envoi ; seuls le CSS et l'animation de rédaction sont partagés.

**Tech Stack :** HTML/CSS/JS vanilla, Node 22 pour les tests (`node docs/x.test.js`, `assert`), Chrome headless piloté en CDP pour les captures.

**Spec :** `docs/superpowers/specs/2026-10-24-accueil-agent-redesign-design.md`

## Global Constraints

- Aucun changement backend : `/ask` (SSE `texte` / `liens` / `navigation` / `erreur`), JSON publiés, workflows intouchés.
- Pas de framework ni de build. Nouveaux fichiers JS : motif `if (typeof module !== 'undefined' && module.exports) { module.exports = {...}; }`.
- Couleurs des nouveaux composants **uniquement** via variables CSS, définies dans `:root` ET `html[data-theme="light"]`. Plus de turquoise (`45,212,191`) ni de bleu (`42,91,255`).
- Breakpoints : mobile/tablette `max-width: 1023px`, desktop `min-width: 1024px`.
- Safe areas iOS (`env(safe-area-inset-*)`) sur tout élément en haut ou en bas d'écran.
- `prefers-reduced-motion: reduce` coupe : rotation de la lueur, défilement du placeholder, fondu mot par mot, transitions d'écran.
- Icônes : SVG avec `viewBox="0 0 24 24"`, conteneurs ronds/pilules centrés (`display:grid; place-items:center` ou flex centré, hauteur fixe).
- Fonctions existantes à préserver côté accueil : dictée, historique local (`analyse-or-assistant-history`), niveau (`analyse-or-answer-level`), `matchInstantNav`, liens de navigation, Copier, annulation (`revientAccueil`), `manual_positions`.
- Fonctions existantes à préserver côté agent : `contexte` envoyé à l'API, événement `navigation`, lecture vocale (`analyse-or-assistant-voix-sortie`), dictée.
- Chaque livraison incrémente `CACHE_NAME` dans `docs/service-worker.js` (actuel `analyse-or-shell-v88`) et précache les nouveaux `.js`.
- Messages de commit en français, style du dépôt (`feat(site): …`), terminés par `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **iPhone à encoche / Dynamic Island** : bouton Thème, hero d'accueil et en-têtes d'écran jamais sous la barre d'état ; barre d'onglets et feuille IA au-dessus de l'indicateur d'accueil. → captures 393×852 avec `--safe-top 59 --safe-bottom 34` (Task 1, Task 8).
2. **Données manquantes** (`score.json`, `signal_tracking.json`, `ibkr_bot_status.json` absents ou API bot injoignable) : chaque tuile/ligne affiche « indisponible », jamais `NaN`/`undefined`, et la page reste utilisable. → tests `buildHomeToday` avec entrées `null` (Task 3).
3. **Retour accueil / nouvelle question pendant une réponse en cours** : l'ancienne animation s'arrête nette, pas de mots fantômes ajoutés après coup. → test `createWordReveal` cancel + `setText` ignoré (Task 2).
4. **Texte de réponse contenant du HTML** (`<script>`, `<b>`) : affiché comme texte, jamais interprété. → test `tokenizeWords` + rendu via `textContent` (Task 2).
5. **Thème clair** : pilule, tuiles, bulles, agent, barre d'onglets lisibles (contrastes) sans couleur codée en dur. → matrice de captures thème clair (Task 9).

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `tools/capture.mjs` (créé) | Captures d'écran CDP : viewport exact, thème, safe areas simulées, script à exécuter avant capture. Outil de dev, non servi. |
| `docs/answer_reveal.js` (+ `.test.js`) (créés) | Rédaction mot par mot (logique de découpage pure + rendu DOM), utilisée par l'accueil et l'agent. |
| `docs/home_today.js` (+ `.test.js`) (créés) | Agrégation pure des 4 infos du jour (tuiles desktop / carte mobile) + accroche + format des nombres. |
| `docs/assistant_suggestions.js` (+ `.test.js`) (créés) | Guide « ce que je sais faire », exemples du placeholder, suggestions et libellé de contexte par route. |
| `docs/index.html` (modifié) | Variables + UI kit CSS, navigation (barre du bas, fondu), accueil (markup + câblage), chargement des nouveaux scripts. |
| `docs/assistant_bubble.js` (modifié, réécriture UI) | Lanceur, panneau desktop, feuille mobile ; moteur d'envoi conservé. |
| `docs/service-worker.js` (modifié) | `CACHE_NAME`, précache, pas d'enregistrement en local. |

---

### Task 1 : Coque de navigation (barre d'onglets mobile, fondu, Thème en en-tête) + outil de capture

**Files :**
- Create : `tools/capture.mjs`
- Modify : `docs/index.html` (CSS nav l.183-268 et l.138-180 ; markup l.1457-1508 ; JS `renderRoute` l.5435-5476, `hidePane` l.5407, tiroir l.5596-5623, thème l.5625-5649), `docs/service-worker.js`

**Interfaces :**
- Produces :
  - `node tools/capture.mjs --path <hash|''> --width <n> --height <n> [--theme light|dark] [--safe-top <px>] [--safe-bottom <px>] [--eval-file <js file>] [--eval <js>] [--wait <ms>] --out <file.png>` : sert `docs/` sur un port libre, lance Chrome headless (`CHROME` env, défaut `C:/Program Files/Google/Chrome/Application/chrome.exe`), `Emulation.setDeviceMetricsOverride` (mobile si width < 1024), pose `localStorage.theme`, simule les safe areas en injectant `:root{--sim-safe-top:..px;--sim-safe-bottom:..px}` + un voile noir de cette hauteur en haut/bas (représente encoche / indicateur), exécute `--eval` puis capture.
  - Markup : `<nav class="bottom-nav">` avec 5 `.nav-item` : `data-route="home"` (Accueil), `data-route="or"`, `#bottomNavIa.nav-item-ia` (orbe + « IA »), `data-route="indices"`, `data-route="portefeuille"`.
  - Bouton Thème mobile : `.screen-theme-toggle` (un par écran : `#home`, `#or`, `#indices`, `#portefeuille`), icône `<svg class="nav-icon theme-icon"><use href="#icon-sun"/></svg>`. `applyTheme` met à jour **toutes** les `.theme-icon use`.
  - Variable CSS `--bottomnav-h: calc(68px + env(safe-area-inset-bottom))` (et `var(--sim-safe-bottom, 0px)` ajoutée dans les captures via la règle de l'outil, pas dans le site).

- [ ] **Step 1 : Écrire `tools/capture.mjs`** (Node 22 natif : `http`, `child_process`, `WebSocket`, `fetch` globaux ; aucune dépendance). Lance Chrome avec `--headless=new --remote-debugging-port=<port> --user-data-dir=<tmp>`, ouvre une cible via `/json/new`, enchaîne `Emulation.setDeviceMetricsOverride`, `Page.navigate`, attend `Page.loadEventFired` + `--wait` (défaut 2500), `Runtime.evaluate` (thème avant navigation via `Page.addScriptToEvaluateOnNewDocument`), `Page.captureScreenshot`, écrit le PNG, tue Chrome et le serveur.

- [ ] **Step 2 : Vérifier l'outil sur l'état actuel**
Run : `node tools/capture.mjs --path "" --width 393 --height 852 --out %TEMP%/cap-before.png`
Expected : PNG de 393×852 px (largeur réelle 393, pas rognée), accueil actuel avec bouton menu.

- [ ] **Step 3 : Barre d'onglets + retrait du tiroir et de Minières.** Reprendre de la branche `homepage` (`git show homepage:docs/index.html`) les règles `.bottom-nav`, `.bottom-nav .nav-item`, le `padding-bottom: var(--bottomnav-h)` des écrans mobiles, le remontage de `.indices-compare-bar` au-dessus de la barre. Supprimer `.menu-button`, `.drawer*`, leur markup, leur JS (`setDrawerOpen`…) et les deux boutons Minières + `#icon-minieres`. Ajouter `#icon-home`. Tailles validées : icône 27-28px dans une pastille 46×32 (rayon 16) en fond `--bg-3` si active, libellé 11-11.5px gras centré sous l'icône, couleur active `--gold`. Onglet IA : orbe 56px, `margin-top:-26px`, bordure 4px couleur du fond, dégradé radial or (`--gold-100` → `--gold` → `--gold-700`), libellé « IA » en `--gold-100`. Clic IA : `window.AssistantIA?.open()` sauf si route `home` → `document.getElementById('homeSearchInput').focus()` (le module `AssistantIA` arrive en Task 8 ; garder l'appel optionnel).

- [ ] **Step 4 : Transitions en fondu.** Reprendre de `homepage` : écrans `opacity:0; pointer-events:none; transition: opacity .22s ease`, `.screen-in` à 1 ; `SLIDING_PANES = ['home','or','indices','portefeuille']` ; `hidePane` attend `propertyName === 'opacity'` ; supprimer `.home.screen-back`. Reduced-motion : `transition: none`.

- [ ] **Step 5 : Safe areas et bouton Thème en en-tête.** Écrans mobiles : `padding-top: env(safe-area-inset-top)`. Le header de chaque écran (et le hero d'accueil) reçoit `.screen-theme-toggle` : 40×40, rond, bordure `--border`, `position:absolute; top: 12px; right: max(12px, env(safe-area-inset-right))` **à l'intérieur** du conteneur qui défile (il défile avec la page) ; masqué ≥1024px (la `.top-bar` garde `#themeToggleDesktop`). Retirer l'ancien `header { padding-top: 64px }` mobile au profit de `24px` (plus de bouton menu à éviter).

- [ ] **Step 6 : Service worker local.** Dans `index.html`, ne pas enregistrer le SW si `location.hostname` ∈ {`localhost`,`127.0.0.1`} (repris de `homepage`). `CACHE_NAME` → `analyse-or-shell-v89`.

- [ ] **Step 7 : Vérifier**
Run (4 captures) :
`node tools/capture.mjs --path "#or" --width 393 --height 852 --safe-top 59 --safe-bottom 34 --out %TEMP%/t1-or-m.png`
`node tools/capture.mjs --path "#indices" --width 393 --height 852 --theme light --safe-top 59 --safe-bottom 34 --out %TEMP%/t1-ind-m-light.png`
`node tools/capture.mjs --path "#portefeuille" --width 1440 --height 900 --out %TEMP%/t1-pf-d.png`
`node tools/capture.mjs --path "#indices/MC.PA" --width 393 --height 852 --out %TEMP%/t1-co-m.png`
Expected : barre d'onglets 5 entrées, onglet actif doré avec pastille, orbe IA centrée qui dépasse ; bouton Thème visible sous le voile de 59px, pas dessous ; aucun contenu caché derrière la barre ; desktop inchangé hors Minières. Puis `node docs/portfolio.test.js && node docs/scalping.test.js && node docs/chart_patterns.test.js && node docs/company_quote_widget.test.js` → tous OK.

- [ ] **Step 8 : Commit** — `feat(site): barre d'onglets mobile, transitions en fondu, thème dans l'en-tête`

---

### Task 2 : Rédaction mot par mot (`docs/answer_reveal.js`)

**Files :**
- Create : `docs/answer_reveal.js`, `docs/answer_reveal.test.js`

**Interfaces :**
- Produces :
  - `tokenizeWords(text: string) -> string[]` : découpe en mots **en gardant les espaces/sauts de ligne collés à la fin du mot** ; `tokens.join('') === text`.
  - `pendingWords(shownText: string, fullText: string) -> string[]` : si `fullText` commence par `shownText`, mots restants de `fullText.slice(shownText.length)` ; un mot encore incomplet en fin de flux (pas d'espace final) n'est renvoyé que si `final === true` → signature réelle `pendingWords(shownText, fullText, final: boolean)`.
  - `createWordReveal(target: HTMLElement, opts: { reduceMotion: boolean, onProgress?: () => void, onDone?: () => void }) -> { setText(full: string): void, replace(full: string): void, finish(): void, fix(full: string): void, cancel(): void, text(): string, done(): boolean }`
  - Constantes : `REVEAL_WORD_MS = 35` (cadence pendant le flux), `REVEAL_FLUSH_MS = 300` (durée max pour vider la file après `finish()`), `REVEAL_FADE_MS = 220`.
  - CSS (ajouté en Task 4, consommé ici par nom) : chaque mot est un `<span class="rv-w">` avec `animation: rv-in var(--rv-fade) ease-out both` (opacité 0 + `filter: blur(4px)` + `translateY(2px)` → net) ; le conteneur a `white-space: pre-wrap` ; pendant le flux le conteneur porte la classe `rv-streaming` dont le `::after` dessine le curseur. Le module n'utilise sur `target` que `appendChild`, `replaceChildren` et `classList.toggle`.

- [ ] **Step 1 : Tests qui échouent** (`docs/answer_reveal.test.js`, style des tests existants : fonctions `test_*`, `assert`, appel en fin de fichier, `console.log('OK')`) :
```js
assert.deepStrictEqual(tokenizeWords('Le score  est\n+11,2'), ['Le ', 'score  ', 'est\n', '+11,2']);
assert.strictEqual(tokenizeWords('a <b>x</b>').join(''), 'a <b>x</b>');
assert.deepStrictEqual(tokenizeWords(''), []);
assert.deepStrictEqual(pendingWords('Le ', 'Le score est', false), ['score ']);   // 'est' incomplet retenu
assert.deepStrictEqual(pendingWords('Le ', 'Le score est', true), ['score ', 'est']);
assert.deepStrictEqual(pendingWords('Autre', 'Le score', true), null);          // préfixe différent → null (remplacement)
```
Plus deux tests avec un faux DOM (annulation et HTML littéral) :
```js
global.document = { createElement: () => ({ textContent: '', className: '' }) };
const fakeTarget = () => ({ nodes: [], appendChild(n) { this.nodes.push(n); }, replaceChildren() { this.nodes = []; },
  classList: { toggle() {} }, get text() { return this.nodes.map(n => n.textContent).join(''); } });
const t1 = fakeTarget(); const r1 = createWordReveal(t1, { reduceMotion: false });
r1.setText('Bonjour le monde '); r1.cancel(); r1.setText('Bonjour le monde et plus');
assert.strictEqual(t1.text, 'Bonjour le monde '); assert.ok(r1.done());
const t2 = fakeTarget(); createWordReveal(t2, { reduceMotion: true }).fix('a <script>x</script>');
assert.strictEqual(t2.text, 'a <script>x</script>');
```

- [ ] **Step 2 : Run** `node docs/answer_reveal.test.js` → FAIL (`tokenizeWords is not defined` / module introuvable).

- [ ] **Step 3 : Implémenter.** `tokenizeWords` par regex `/\S+\s*|\s+/g`. `createWordReveal` : file de mots en attente alimentée par `pendingWords(textAffiché, cible, fluxFini)` ; minuterie qui ajoute 1 mot toutes les `REVEAL_WORD_MS` (plusieurs si le retard dépasse 40 mots) ; après `finish()`, vide la file en au plus `REVEAL_FLUSH_MS` ; chaque mot via `document.createElement('span')` + `textContent` (jamais `innerHTML`) ; `replace()` vide `target` et repart de zéro ; `fix()`/`cancel()` arrêtent la minuterie, écrivent tout le texte restant d'un coup sans animation, et ignorent tout `setText` ultérieur ; `reduceMotion` = comportement de `fix` à chaque `setText`. `onProgress` appelé après chaque ajout (défilement géré par l'appelant), `onDone` une fois quand flux fini ET file vide.

- [ ] **Step 4 : Run** `node docs/answer_reveal.test.js` → `OK`.

- [ ] **Step 5 : Commit** — `feat(site): rédaction des réponses IA mot par mot en fondu`

---

### Task 3 : Infos du jour (`docs/home_today.js`)

**Files :**
- Create : `docs/home_today.js`, `docs/home_today.test.js`

**Interfaces :**
- Produces :
  - `formatSigned(n: number|null, digits = 1) -> string` : `98.6 → '+98,6'`, `-23.4 → '−23,4'` (signe moins U+2212), `0 → '0,0'`, `null → '—'`.
  - `computeSignalSummary(positions: object[], currentReturn: (p) => number|null, currentIndexReturn: (p) => number|null, outOfScope: Set<string>) -> { winRate: number|null, avgAlpha: number|null, openCount: number }` — **même calcul que `buildSignalStatsHtml` (`docs/index.html` l.4976) sur le périmètre du bot** (positions dont `index` ∉ `outOfScope`) : winRate = % de rendements > 0 parmi les rendements non nuls ; avgAlpha = moyenne de (rendement − rendement indice).
  - `buildHomeToday(input: { score: {composite_score:number, interpretation:string}|null, companies: {name:string, score:number}[]|null, signals: {winRate, avgAlpha, openCount}|null, botOr: {kill_switch:boolean, dry_run:boolean}|null, botActions: {statut:string}|null, botActionsLabels: Record<string,string> }) -> HomeTodayItem[]` (toujours 4 éléments, dans l'ordre `or`, `top`, `signaux`, `bots`).
  - `HomeTodayItem = { key, label, detail, value, tone: 'pos'|'neg'|'gold'|'neutral', href }` avec : `or` → label `'Or · score macro'`, value `formatSigned(composite)`, detail = `interpretation`, href `'#or'`, tone `neg` si < 0 sinon `pos` ; `top` → label `'Indices · top du jour'`, value du meilleur score, detail `` `${nom}, meilleur score sur ${n} entreprises notées.` ``, href `'#indices'` ; `signaux` → label `'Signaux · perf réelle'`, value `` `${Math.round(winRate)} %` ``, detail `` `de réussite, ${formatSigned(avgAlpha)} % vs indice en moyenne. ${openCount} en cours.` ``, href `'#indices/suivi'`, tone `gold` ; `bots` → label `'Bots · statut'`, value `'Or coupé'|'Or en simulation'|'Or actif'|'Indisponible'`, detail `` `Bot Actions : ${label}` `` ou `'Bot Actions : statut indisponible'`, href `'#portefeuille'`, tone `gold` si actif sinon `neutral`.
  - Donnée manquante → `value: '—'`, `detail: 'Indisponible pour l\'instant.'`, href conservé.
  - `homeTagline(companyCount: number|null) -> string` : `` `L'IA note chaque jour l'or et ${n.toLocaleString('fr-FR')} entreprises mondiales, repère les points d'entrée et suit ton portefeuille.` `` ; `null` → même phrase sans le nombre (« …l'or et des centaines d'entreprises mondiales… »).
  - `homeTodayShort(item) -> { label: string, sub: string }` pour la carte mobile : `or` → `{label:'Or', sub: premier segment de l'interprétation avant ' — ' en minuscule}`, `top` → `{label:'Top du jour', sub: nom}`, `signaux` → `{label:'Signaux', sub:'réussite'}`, `bots` → `{label:'Bots', sub: value}`.

- [ ] **Step 1 : Tests qui échouent** (valeurs du jour du brainstorming) :
```js
assert.strictEqual(formatSigned(-23.4), '−23,4');
assert.strictEqual(formatSigned(98.6), '+98,6');
assert.strictEqual(formatSigned(null), '—');
const s = computeSignalSummary(
  [{index:'CAC40',status:'closed'},{index:'CAC40',status:'open'},{index:'NIKKEI225',status:'open'}],
  p => ({CAC40:5}[p.index] ?? -3) * (p.status==='open' ? -1 : 1), () => 1, new Set(['NIKKEI225']));
assert.deepStrictEqual(s, { winRate: 50, avgAlpha: -1, openCount: 1 });
const items = buildHomeToday({ score:{composite_score:-23.4, interpretation:'Défavorable — pas d\'entrée même sur signal technique'},
  companies:[{name:'Edenred',score:98.6},{name:'AXA',score:84.7}], signals:{winRate:65,avgAlpha:2.9,openCount:56},
  botOr:{kill_switch:false,dry_run:true}, botActions:{statut:'termine'}, botActionsLabels:{termine:'Terminé'} });
assert.deepStrictEqual(items.map(i => i.value), ['−23,4', '+98,6', '65 %', 'Or en simulation']);
assert.strictEqual(items[1].detail, 'Edenred, meilleur score sur 2 entreprises notées.');
assert.strictEqual(items[0].tone, 'neg');
const vide = buildHomeToday({ score:null, companies:null, signals:null, botOr:null, botActions:null, botActionsLabels:{} });
assert.ok(vide.every(i => i.value === '—' || i.value === 'Indisponible'));
assert.ok(vide.every(i => !/NaN|undefined/.test(i.value + i.detail)));
assert.strictEqual(homeTagline(1299), `L'IA note chaque jour l'or et ${(1299).toLocaleString('fr-FR')} entreprises mondiales, repère les points d'entrée et suit ton portefeuille.`);
```

- [ ] **Step 2 : Run** `node docs/home_today.test.js` → FAIL.
- [ ] **Step 3 : Implémenter** les fonctions ci-dessus dans `docs/home_today.js`.
- [ ] **Step 4 : Run** `node docs/home_today.test.js` → `OK`.
- [ ] **Step 5 : Commit** — `feat(site): agrégation des infos du jour pour l'accueil`

---

### Task 4 : UI kit CSS + pilule de saisie de l'accueil

**Files :**
- Create : `docs/assistant_suggestions.js` (partie placeholder seulement, complétée en Task 5), `docs/assistant_suggestions.test.js`
- Modify : `docs/index.html` (variables `:root` l.27-85 et `html[data-theme="light"]` l.87-115 ; CSS accueil l.270-460 ; markup `#home` l.1510-1553 ; `wireHomeSearch` l.4129, `wireVoiceSearch` l.4025, `setNiveauReponse` l.4015, `wireAssistantHistory` l.4110)

**Interfaces :**
- Consumes : rien de nouveau.
- Produces (noms de classes réutilisés par Tasks 5-8) :
  - Variables : `--ai-ring-idle` (= `--border-strong`), `--ai-ring-a` (= `--gold`), `--ai-ring-b` (= `--gold-100`), `--ai-ring-track` (= `--border`), `--ai-glow` (rgba or .22 sombre / émeraude .18 clair), `--ai-input-bg` (`#121a2c` sombre / `--bg-2` clair), `--ai-send-from` (`--gold-100`), `--ai-send-to` (`--gold`), `--ai-send-ink` (`--bg-0`), `--ai-user-bubble` (`--bg-4`), `--rv-fade: 220ms`.
  - `.ai-pill` (anneau 1.5px, `border-radius:999px`, fond `--ai-ring-idle`) ; `.ai-pill.is-active` (ou toujours en `max-width:1023px`) : `conic-gradient(from var(--ang), var(--ai-ring-a), var(--ai-ring-track) 22%, var(--ai-ring-track) 58%, var(--ai-ring-b) 78%, var(--ai-ring-a))`, `@property --ang` animée 6s linéaire, `box-shadow: 0 0 30px var(--ai-glow)`.
  - `.ai-pill-bar` (hauteur 54px desktop / 50px mobile, fond `--ai-input-bg`, input transparent `flex:1`), `.ai-pill-hint` (overlay du placeholder animé : `Demande-moi ` en `--muted` + `<b>` exemple en `--gold-100`), `.ai-kbd` (« / »), `.ai-round` (40×40 rond, grid centré), `.ai-round.ai-send` (dégradé or, flèche haut), `.ai-round.ai-mic` (transparent, `[aria-pressed=true]` fond `--green-soft`).
  - `.ui-chip` (hauteur 32-34px, `inline-flex`, centré, `line-height:1`), `.ui-seg` + boutons `[aria-pressed=true]` (fond `--bg-4`, texte `--gold-100`).
  - `.rv-w`, `.rv-streaming::after` (curseur), `@keyframes rv-in` (pour Task 2).
  - `PLACEHOLDER_EXAMPLES: string[]` dans `assistant_suggestions.js` (6 entrées : « les meilleures opportunités du CAC 40 », « pourquoi le score de l'or a bougé », « le score de LVMH », « les signaux d'entrée du jour », « une analyse de mon portefeuille », « le prochain événement macro ») et `nextPlaceholderIndex(i: number, n: number) -> number` (cyclique).
  - Markup accueil : `.home-hero` (wordmark + `#homeTagline`), `#homeHistoryInBar.ui-chip` (icône horloge + « Historique ») **au-dessus**, `.ai-pill#homePill > .ai-pill-bar` (`#homeSearchInput`, `#homePillHint`, `.ai-kbd`, `#homeMicButton.ai-round.ai-mic`, `#homeSendButton.ai-round.ai-send`), `.ui-seg.home-level` **en dessous** ; `#homeNewConv` conservé (visibilité gérée Task 6). IDs existants conservés pour ne pas casser le JS.

- [ ] **Step 1 : Test qui échoue** : `assert.strictEqual(PLACEHOLDER_EXAMPLES.length, 6); assert.strictEqual(nextPlaceholderIndex(5, 6), 0); assert.strictEqual(nextPlaceholderIndex(2, 6), 3);` → Run `node docs/assistant_suggestions.test.js` → FAIL.
- [ ] **Step 2 : Implémenter** `PLACEHOLDER_EXAMPLES` / `nextPlaceholderIndex` → Run → `OK`.
- [ ] **Step 3 : CSS UI kit + variables (sombre ET clair)** dans une section `/* ===== UI kit 2026-10 ===== */` ; remplacer les styles `#homeSearchInput`, `.home-mic`, `.home-level`, `.home-history-in`, `.home-newconv` actuels ; supprimer toute occurrence `42,91,255`.
- [ ] **Step 4 : Markup + câblage.** Charger `assistant_suggestions.js` en `<script>` avant le script principal. Bouton envoyer : même chemin que Entrée (factoriser le handler `keydown` existant en `submitHomeQuery()`). Placeholder animé : `#homePillHint` visible si input vide et non focus, rotation toutes les 4 s avec fondu (désactivée en reduced-motion → premier exemple fixe) ; au focus l'input garde `placeholder="Pose ta question…"`. Raccourci `/` : `keydown` global, si route `home` et cible pas déjà un champ → `preventDefault` + focus. `.is-active` posée sur `#homePill` au focus, pendant la saisie, et pendant `home.thinking` ; retirée au blur si input vide. Mobile : la règle `max-width:1023px` force la lueur.
- [ ] **Step 5 : Disposition.** Desktop : colonne centrée max 640px (pilule 600px). Mobile : hero en haut (`padding-top: calc(env(safe-area-inset-top) + 40px)`), bloc saisie remonté (≈ 58 % de la hauteur utile), Historique centré au-dessus, niveau centré dessous.
- [ ] **Step 6 : Vérifier**
`node tools/capture.mjs --path "" --width 1440 --height 900 --out %TEMP%/t4-d.png`
`node tools/capture.mjs --path "" --width 1440 --height 900 --eval "document.getElementById('homeSearchInput').focus()" --out %TEMP%/t4-d-focus.png`
`node tools/capture.mjs --path "" --width 393 --height 852 --safe-top 59 --safe-bottom 34 --out %TEMP%/t4-m.png`
`node tools/capture.mjs --path "" --width 393 --height 852 --theme light --out %TEMP%/t4-m-light.png`
Expected : pilule une ligne, micro + envoyer doré **centrés** dans leurs cercles, Historique au-dessus et Technique/Grand public dessous, textes centrés dans les pilules ; desktop repos = filet sobre, focus = lueur ; mobile = lueur ; thème clair lisible.
- [ ] **Step 7 : Commit** — `feat(site): pilule de saisie lumineuse sur l'accueil`

---

### Task 5 : Tuiles live (desktop), carte « Aujourd'hui » (mobile), guide au clic

**Files :**
- Modify : `docs/assistant_suggestions.js`, `docs/assistant_suggestions.test.js`, `docs/index.html` (markup `#home`, nouvelle fonction de chargement, CSS)

**Interfaces :**
- Consumes : `buildHomeToday`, `computeSignalSummary`, `homeTagline`, `homeTodayShort` (Task 3) ; `positionCurrentReturnPct`, `positionCurrentIndexReturnPct`, `SIGNAL_TRACKING_OUT_OF_SCOPE_INDICES`, `IBKR_BOT_STATUS_LABELS`, `loadBotStatus`/`botStatusData`, `ensureIndicesData`/`indicesData` (existants dans `index.html`) ; classes `.ai-pill`, `.ui-chip` (Task 4).
- Produces :
  - `HOME_GUIDE: { key: 'or'|'indices'|'portefeuille', titre: string, icone: string, texte: string, questions: string[] }[]` — or : « Score macro quotidien, alertes, calendrier Fed. » / [« Pourquoi le score de l'or a-t-il bougé ? », « Quel est le prochain événement macro ? »] ; indices : « 1 299 entreprises notées, repères d'entrée et de sortie. » (nombre remplacé à l'affichage par le vrai compte) / [« Top 5 du CAC 40 », « Compare LVMH et Hermès »] ; portefeuille : « Tes positions, P&L, opportunités, bots. » / [« Analyse mon portefeuille », « Quel est le statut des bots ? »].
  - `normalizeText(s: string) -> string` (minuscules, sans accents via `normalize('NFD')`).
  - `filterGuide(guide, query: string) -> guide` : query vide → guide inchangé ; sinon ne garde que les questions dont le texte normalisé contient chaque mot normalisé de la query, retire les colonnes vides.
  - `async loadHomeToday(): Promise<void>` dans `index.html` : charge en parallèle `score.json`, `indices.json` (via `ensureIndicesData`), `signal_tracking.json`, `ibkr_bot_status.json`, `loadBotStatus()` — chaque échec → `null` ; rend `#homeTiles` (desktop) et `#homeToday` (mobile) depuis le même `HomeTodayItem[]` ; remplit `#homeTagline`. Appelée au premier affichage de la route `home`.
  - Markup : `#homeTiles.home-tiles` (grille 4 colonnes, max 860px, `display:none` < 1024px) ; `#homeToday.home-today` (carte, `display:none` ≥ 1024px, en-tête « Aujourd'hui » + date `jj/mm`) ; `#homeGuide.home-guide` (panneau desktop 3 colonnes) ; `#homeGuideMobile` (liste des questions, remplace `#homeToday` au focus < 1024px).

- [ ] **Step 1 : Tests qui échouent**
```js
assert.strictEqual(normalizeText('Énergie Été'), 'energie ete');
const g = filterGuide(HOME_GUIDE, 'top');
assert.deepStrictEqual(g.map(c => c.key), ['indices']);
assert.deepStrictEqual(g[0].questions, ['Top 5 du CAC 40']);
assert.strictEqual(filterGuide(HOME_GUIDE, '').length, 3);
assert.deepStrictEqual(filterGuide(HOME_GUIDE, 'zzzz'), []);
assert.deepStrictEqual(filterGuide(HOME_GUIDE, 'evenement macro')[0].questions, ["Quel est le prochain événement macro ?"]);
```
Run `node docs/assistant_suggestions.test.js` → FAIL.
- [ ] **Step 2 : Implémenter** `HOME_GUIDE`, `normalizeText`, `filterGuide` → Run → `OK`.
- [ ] **Step 3 : Tuiles desktop.** Chaque tuile = `<a href>` : clé (10.5px majuscules `--text-faint`), valeur (Newsreader 28px, couleur selon `tone` : `--green-300`/`--rust-300`/`--gold`/`--text`), détail (11.5px `--muted`), lien « … → » (`--gold`) ; emplacement `.home-tile-spark` vide (courbe future). Repos : fond `--bg-2` à 45 % d'opacité, bordure `--border-subtle` ; hover : fond plein, bordure `--border-strong`, `translateY(-2px)`, ombre `--shadow-3`. Libellés de lien : « Voir l'analyse → », « Explorer les indices → », « Suivi des signaux → », « Portefeuille → ».
- [ ] **Step 4 : Carte mobile.** 4 lignes de 40px : carré 26px (`--bg-3`, rayon 8) avec icône (`icon-or`, `icon-indices`, courbe montante, robot), libellé + `<small>` précision (`homeTodayShort`), valeur Newsreader 17px colorée, chevron `›` `--border-strong`. Chaque ligne est un `<a href>`.
- [ ] **Step 5 : Guide au clic.** Desktop : au focus de `#homeSearchInput` (état repos uniquement), `#home.guide-open` : tuiles `opacity:.18; filter: blur(1.5px)`, `#homeGuide` visible sous la pilule (fond `--bg-2`, rayon 18, ombre), 3 colonnes (icône + titre or majuscule, texte, questions en boutons `--bg-3`), pied « ↑↓ pour choisir · ↵ pour envoyer · esc pour fermer ». À chaque `input` : re-rendu via `filterGuide` ; aucun résultat → panneau masqué. Clavier : ↑/↓ déplacent `.is-selected` parmi les boutons visibles ; Entrée avec sélection → envoie cette question (même chemin que `submitHomeQuery` avec ce texte) ; sinon comportement actuel ; Échap → ferme + blur. Clic sur une question → envoi. Fermeture au blur (délai 120 ms pour laisser passer le clic). Mobile : même logique mais `#homeGuideMobile` (liste compacte des questions) prend la place de `#homeToday`.
- [ ] **Step 6 : Vérifier**
`node tools/capture.mjs --path "" --width 1440 --height 900 --wait 4000 --out %TEMP%/t5-d.png`
`node tools/capture.mjs --path "" --width 1440 --height 900 --wait 4000 --eval "const i=document.getElementById('homeSearchInput');i.focus();i.value='Top';i.dispatchEvent(new Event('input'))" --out %TEMP%/t5-d-guide.png`
`node tools/capture.mjs --path "" --width 393 --height 852 --wait 4000 --safe-top 59 --safe-bottom 34 --out %TEMP%/t5-m.png`
Expected : 4 tuiles avec données réelles (pas de « undefined ») ; guide filtré n'affichant que « Top 5 du CAC 40 » ; carte « Aujourd'hui » 4 lignes entre l'accroche et la pilule, rien de masqué par la barre d'onglets.
- [ ] **Step 7 : Commit** — `feat(site): tuiles du jour, carte Aujourd'hui et guide de l'assistant sur l'accueil`

---

### Task 6 : Accueil — réponse en bulles et rédaction animée

**Files :**
- Modify : `docs/index.html` (`createAnswerTyper` l.3659-3792 remplacé, `askAssistant` l.3886, `setHomeState` l.3876, `reopenAssistantExchange` l.4097, `startHomeParticles` l.3817, `nouvelleConversation`/`revientAccueil` l.3626-3638, CSS `.home.thinking`/`.home.answered`/`.home-search-*` l.384-460)

**Interfaces :**
- Consumes : `createWordReveal` (Task 2), classes `.ai-pill`, `.ui-chip`, `.rv-w` (Task 4).
- Produces (partagées avec l'agent en Task 7 — mêmes noms de classes) :
  - `.ai-thread` (colonne de messages, `aria-live="polite"`), `.ai-msg-user` (bulle droite, fond `--ai-user-bubble`, rayon 16 16 4 16), `.ai-msg-ai` (ligne : `.ai-avatar` 24px sphère or + `.ai-msg-body`), `.ai-msg-text` (`white-space: pre-wrap`), `.ai-links` + `.ai-link` (pilule texte `--gold-100`, bordure `--gold-700`, fond or 8 %, libellé suivi de « → »), `.ai-actions` + `.ai-icon-btn` (24px, icônes copier / écouter).
  - `createHomeAnswer(thread: HTMLElement, question: string, reduceMotion: boolean) -> { texte(t), remplacer(t), liens(l), terminer(), fixer(t, l), annuler() }` — **même API que l'ancien `createAnswerTyper`**, pour que `askAssistant`, `revientAccueil`, `reopenAssistantExchange` ne changent que leur appel. Ajoute la bulle question puis la bulle réponse ; texte via `createWordReveal` ; liens + actions apparaissent en fondu quand `onDone`.
  - Écouter : `speechSynthesis` `fr-FR`, bascule lecture / arrêt sur le même bouton.

- [ ] **Step 1 : Remplacer `createAnswerTyper` par `createHomeAnswer`** (supprimer `ANSWER_TYPE_MS`/`ANSWER_TYPE_LAG`) ; liens rendus avec les mêmes `data-cible-type`/`data-cible-valeur` et câblés par `wireHomeSearchLinkButtons` (adapter son sélecteur à `.ai-link`) ; Copier garde les libellés « Copié » / « Copie refusée ».
- [ ] **Step 2 : États de l'accueil.** `thinking` : tuiles / carte / guide masqués (fondu), sphère de particules centrée, **couleur or** (`PARTICULES_TEAL` → lecture de `--gold` via `getComputedStyle`, nom `PARTICULES_COULEUR`), pilule active. `answered` : sphère masquée (fondu) — l'avatar des bulles prend le relais ; `#homeSearchPanel` devient `.ai-thread` centré (max 720px desktop, pleine largeur mobile), défilant, entre le hero (réduit au wordmark) et le bloc saisie fixé en bas (Historique, pilule, niveau) au-dessus de la barre d'onglets en mobile ; défilement automatique doux (`scrollTo({top, behavior:'smooth'})`) seulement si l'utilisateur est déjà à moins de 48px du bas. `#homeNewConv` (`.ui-chip`) visible **uniquement** en `answered`, placé à côté de l'Historique. `nouvelleConversation()` : vide le fil, retour repos (tuiles/carte reviennent).
- [ ] **Step 3 : Vérifier** avec un flux SSE simulé :
Le stub SSE vit dans un fichier pour éviter l'échappement en ligne de commande : créer `tools/stub_ask.js` (outil de dev) qui pose `localStorage.aiAssistantToken = 'x'` puis remplace `window.fetch` : toute URL contenant `/ask` renvoie `new Response(ReadableStream)` émettant `data: {"type":"texte","texte":"Le score de LVMH est de +11,2 : rentabilité solide mais <b>valorisation</b> tendue."}\n\n` puis `data: {"type":"liens","liens":[{"libelle":"Fiche LVMH","cible_type":"ticker","cible_valeur":"MC.PA"}]}\n\n` ; les autres URL passent au `fetch` d'origine. `capture.mjs` accepte `--eval-file <path>` (contenu exécuté avant `--eval`).
`node tools/capture.mjs --path "" --width 1440 --height 900 --wait 6000 --eval-file tools/stub_ask.js --eval "askAssistant('Pourquoi LVMH est neutre ?')" --out %TEMP%/t6-d.png`
Idem en `--width 393 --height 852`.
Expected : bulle question à droite, réponse à gauche avec avatar or, `<b>` affiché littéralement, pilule « Fiche LVMH → », actions copier/écouter, « Nouvelle conversation » visible, pilule en bas, tuiles absentes.
- [ ] **Step 4 : Non-régression manuelle consignée dans le commit** : historique (rouvrir une entrée → bulle sans animation), Échap/logo pendant une réponse → plus aucun mot ajouté, dictée toujours présente, niveau toujours envoyé (`niveau` dans le corps JSON). Run tous les `node docs/*.test.js` → OK.
- [ ] **Step 5 : Commit** — `feat(site): conversation de l'accueil en bulles avec rédaction animée`

---

### Task 7 : Agent desktop — panneau flottant

**Files :**
- Modify : `docs/assistant_bubble.js` (styles injectés l.14-40 → classes du kit + règles propres `ab-*` ; sphère canvas l.42-93 → lanceur ; panneau l.95-136 ; rendu messages l.149-170 ; `pose` l.222-289), `docs/assistant_suggestions.js` (+ test), `docs/index.html` (ordre de chargement des scripts)

**Interfaces :**
- Consumes : `createWordReveal` (Task 2) ; `suggestionsForRoute`, `contextLabel` (ce task) ; classes `.ai-pill*`, `.ai-round`, `.ui-chip`, `.ui-seg`, `.ai-thread`, `.ai-msg-*`, `.ai-link`, `.ai-actions`, `.ai-icon-btn` (Tasks 4, 6) ; `niveauReponse()` / `setNiveauReponse()` (index.html) ; `getAiAssistantToken()`.
- Produces :
  - `suggestionsForRoute(hash: string, companyName?: string) -> string[]` (2 entrées) : `#or` → [« Pourquoi ce score aujourd'hui ? », « Quel est le prochain événement macro ? »] ; `#indices` → [« Top 5 du CAC 40 », « Quels signaux d'entrée aujourd'hui ? »] ; `#indices/suivi` → [« Quel est le taux de réussite des signaux ? », « Quelles positions sont en perte ? »] ; `#indices/compare/…` → [« Laquelle est la plus solide ? », « Explique les écarts de score »] ; `#indices/<T>/analyse` et `#indices/<T>` → [`` `Explique le repère d'entrée de ${nom}` ``, `` `Critères Graham de ${nom} ?` ``] ; `#portefeuille` → [« Analyse mon portefeuille », « Quel est le statut des bots ? »] ; autre → [« Score de l'or aujourd'hui », « Top 5 du CAC 40 »]. `nom` absent → ticker.
  - `contextLabel(hash: string, companyName?: string) -> string` : `'Accueil'|'Or'|'Indices'|'Suivi des signaux'|'Comparateur'|`` `Fiche ${nom}` ``|`` `Analyse ${nom}` ``|'Portefeuille'`.
  - `window.AssistantIA = { open(): void, close(): void, toggle(): void, isOpen(): boolean }`.
  - DOM : `.ab-launch` (56px, sphère or, croix quand ouvert, `aria-expanded`), `.ab-panel` (370px, rayon 20, fond `--bg-2`, ombre forte) contenant `.ab-head` (avatar 20px, « Assistant », `.ab-ctx` point vert + `contextLabel`, `.ai-icon-btn` lecture vocale `aria-pressed`, nouvelle conversation, fermer), `.ai-thread`, `.ab-foot` (chips `suggestionsForRoute`, `.ai-pill` + `.ai-pill-bar` input « Pose une question sur {nom ou page}… », micro, envoyer ; `.ui-seg` Technique / Grand public).

- [ ] **Step 1 : Tests qui échouent** (`assistant_suggestions.test.js`) :
```js
assert.deepStrictEqual(suggestionsForRoute('#indices/MC.PA', 'LVMH'), ["Explique le repère d'entrée de LVMH", 'Critères Graham de LVMH ?']);
assert.deepStrictEqual(suggestionsForRoute('#indices/MC.PA'), ["Explique le repère d'entrée de MC.PA", 'Critères Graham de MC.PA ?']);
assert.strictEqual(contextLabel('#indices/MC.PA', 'LVMH'), 'Fiche LVMH');
assert.strictEqual(contextLabel('#indices/MC.PA/analyse', 'LVMH'), 'Analyse LVMH');
assert.strictEqual(contextLabel('#indices/suivi'), 'Suivi des signaux');
assert.strictEqual(contextLabel(''), 'Accueil');
assert.strictEqual(suggestionsForRoute('#xyz').length, 2);
```
Run → FAIL ; implémenter ; Run → `OK`.
- [ ] **Step 2 : Réécrire l'UI de `assistant_bubble.js`.** Supprimer `TEAL`, la sphère canvas 420 points et toutes les couleurs hex/rgba en dur (les règles injectées n'utilisent que des `var(--…)`). Nom d'entreprise pour le contexte : `indicesData?.companies.find(c => c.ticker === ticker)?.name` si dispo. Messages : bulles `.ai-msg-user` / `.ai-msg-ai` ; réponse via `createWordReveal` ; « Je réfléchis » remplacé par l'avatar qui pulse (`.ai-avatar.is-thinking`, reduced-motion : statique) jusqu'au premier texte. Sous chaque réponse : `.ai-links` (événement `liens`) puis `.ai-actions` copier / écouter. Le toggle global « lecture vocale » (clé `analyse-or-assistant-voix-sortie`) devient l'icône d'en-tête `aria-pressed`. Niveau : `.ui-seg` relié à `niveauReponse()`/`setNiveauReponse()` (même clé que l'accueil) et envoyé dans `niveau` au lieu de `'technique'` en dur. Conserver tel quel : `contexteCourant()` envoyé à l'API, historique séparé `historique`, événement `navigation` (« Je vous ouvre la page… » + délai 1200 ms), dictée, Échap ferme. Suggestions : clic → envoi direct.
- [ ] **Step 3 : Masquage sur l'accueil.** À chaque `hashchange` + au chargement : `.ab-launch` et `.ab-panel` masqués si route `home` (le panneau se ferme s'il était ouvert). Suggestions et contexte recalculés à l'ouverture.
- [ ] **Step 4 : Chargement.** `index.html` : `answer_reveal.js` et `assistant_suggestions.js` en `<script>` classiques avant le script principal ; `assistant_bubble.js` reste `defer` en dernier.
- [ ] **Step 5 : Vérifier**
`node tools/capture.mjs --path "#indices/MC.PA" --width 1440 --height 900 --wait 5000 --eval "AssistantIA.open()" --out %TEMP%/t7-d.png`
`node tools/capture.mjs --path "#indices/MC.PA" --width 1440 --height 900 --theme light --wait 5000 --eval "AssistantIA.open()" --out %TEMP%/t7-d-light.png`
`node tools/capture.mjs --path "" --width 1440 --height 900 --out %TEMP%/t7-home.png`
Expected : panneau au-dessus du lanceur (croix), puce « Fiche LVMH », 3 icônes centrées, suggestions LVMH, pilule identique à l'accueil, sélecteur de niveau ; thème clair lisible ; aucun lanceur sur l'accueil.
- [ ] **Step 6 : Commit** — `feat(site): agent IA en panneau flottant aux couleurs du site`

---

### Task 8 : Agent mobile — onglet IA + feuille montante

**Files :**
- Modify : `docs/assistant_bubble.js`, `docs/index.html` (CSS mobile, câblage `#bottomNavIa`)

**Interfaces :**
- Consumes : `window.AssistantIA` (Task 7), `#bottomNavIa` (Task 1).
- Produces : en `max-width:1023px`, `.ab-launch { display:none }` ; `.ab-panel` devient feuille : `position:fixed; left:0; right:0; bottom:0; top: calc(env(safe-area-inset-top) + 56px)`, rayon 24 24 0 0, bordure haute `--border-strong`, `.ab-grab` (poignée 40×4) en tête, `padding-bottom: env(safe-area-inset-bottom)` sur le pied, `z-index` au-dessus de `.bottom-nav` ; `.ab-scrim` (fond `--scrim`, définie dans `:root` : `rgba(6,9,17,.6)` sombre / `rgba(40,30,10,.35)` clair) ; icône fermer de l'en-tête = chevron bas.

- [ ] **Step 1 : Feuille + voile.** Ouverture : feuille `translateY(100%) → 0` (.3s, coupée en reduced-motion), voile en fondu ; focus input. Fermeture : chevron, clic sur le voile, Échap, ou glisser la poignée/l'en-tête vers le bas de plus de 80px (pointer events : suivre le doigt, relâcher < 80px → revient). Le clavier virtuel ne doit pas masquer la pilule : pied en `position: sticky; bottom: 0` dans la feuille.
- [ ] **Step 2 : Onglet IA.** `#bottomNavIa` : route `home` → focus `#homeSearchInput` ; sinon `AssistantIA.toggle()`. Onglet IA en état « actif » (libellé or) quand la feuille est ouverte.
- [ ] **Step 3 : Vérifier**
`node tools/capture.mjs --path "#indices/MC.PA" --width 393 --height 852 --safe-top 59 --safe-bottom 34 --wait 5000 --eval "AssistantIA.open()" --out %TEMP%/t8-m.png`
`node tools/capture.mjs --path "#or" --width 393 --height 852 --theme light --safe-top 59 --safe-bottom 34 --wait 5000 --eval "AssistantIA.open()" --out %TEMP%/t8-m-light.png`
`node tools/capture.mjs --path "#indices/MC.PA" --width 393 --height 852 --wait 4000 --out %TEMP%/t8-m-closed.png`
Expected : feuille sous le voile de 59px, pilule au-dessus du voile de 34px, page assombrie visible en haut ; fermé : aucune bulle flottante, orbe IA dans la barre.
- [ ] **Step 4 : Commit** — `feat(site): agent IA en feuille montante depuis l'onglet IA sur mobile`

---

### Task 9 : Finitions — thème clair, cache, matrice de captures

**Files :**
- Modify : `docs/service-worker.js`, `docs/index.html` (ajustements de contraste si besoin)

- [ ] **Step 1 : Service worker.** `CACHE_NAME` → `analyse-or-shell-v90` ; ajouter `./answer_reveal.js`, `./home_today.js`, `./assistant_suggestions.js`, `./assistant_bubble.js` (si absent) à la liste de précache.
- [ ] **Step 2 : Vérifier l'absence de couleurs en dur dans le nouveau code**
Run : `rg -n "45, ?212, ?191|42, ?91, ?255|#7fe7d6|#f0b3a8" docs/index.html docs/assistant_bubble.js`
Expected : aucune ligne.
- [ ] **Step 3 : Matrice de captures** (sombre + clair) : accueil repos / guide ouvert / réponse affichée, fiche LVMH avec agent ouvert, `#or`, `#portefeuille` — en 1440×900 et 393×852 (`--safe-top 59 --safe-bottom 34`). Corriger tout contraste illisible ou élément masqué, recapturer.
- [ ] **Step 4 : Tests** : `for f in docs/*.test.js scalping_tracker.test.js; do node "$f" || exit 1; done` → tous OK.
- [ ] **Step 5 : Commit** — `chore(site): cache v90 et finitions du thème clair pour la refonte accueil/agent`
