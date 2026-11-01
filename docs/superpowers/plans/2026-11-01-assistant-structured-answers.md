# Réponses structurées de l'assistant IA (titres + actus réelles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L'assistant `/ask` structure ses réponses développées en
courts paragraphes titrés (toujours conclus par « Mon avis ») et cite
les vraies actualités (titre, résumé, source, date, lien) qui ont
nourri sa réponse, dans une bulle dédiée.

**Architecture:** Deux ajouts additifs au contrat SSE déjà existant
(`texte`/`liens`/`navigation`/`erreur`/`fin`) : le prompt système
autorise des lignes `## Titre` dans le texte déjà streamé (aucun
nouveau type d'événement), et un nouvel événement `actus` (même
mécanisme que `liens` : collecté pendant la boucle d'outils, émis une
fois à la fin). Côté client, `docs/answer_reveal.js` stylise les
lignes `## ` une fois la réponse terminée (jamais pendant le flux mot
par mot, pour ne jamais passer par `innerHTML`), et `docs/index.html`
reçoit/rend les cartes actus avec le logo (favicon) de chaque source.

**Tech Stack:** Python (assistant_ia, pytest), JavaScript vanille sans
framework (docs/index.html, docs/answer_reveal.js, tests Node sans
framework via `assert`).

**Spec:** `docs/superpowers/specs/2026-11-01-assistant-structured-answers-design.md`

## Global Constraints

- Le texte de l'assistant ne passe JAMAIS par `innerHTML` côté client
  (voir `docs/answer_reveal.js:7-8`) — toute nouvelle manipulation du
  texte révélé reste strictement `textContent`/`createElement`.
- Les nouvelles cartes actus (titre, résumé, source) passent par
  `escHtml` avant toute insertion HTML (elles, via `innerHTML`, suivent
  le même motif que `homeSearchLinksHtml` existant).
- Plafond de 5 articles au total par réponse (`news.MAX_ARTICLES`),
  dédoublonnés par URL.
- Aucun nouvel événement SSE pour les titres — ils voyagent dans
  l'événement `texte` déjà existant, au format `## Titre` sur sa
  propre ligne.
- Le titre de conclusion est exactement `## Mon avis` (comparaison
  insensible à la casse et aux espaces côté client).

---

### Task 1 : Backend — collecte et émission des actualités (`assistant_ia/assistant.py`)

**Files:**
- Modify: `assistant_ia/assistant.py:26,81-83,116-125,145,184-200,203-209`
- Test: `tests/assistant_ia/test_assistant.py`

**Interfaces:**
- Consomme : `assistant_ia.news.MAX_ARTICLES` (constante déjà
  existante, `assistant_ia/news.py:17`, vaut `5`) ; le format déjà
  renvoyé par les outils `actualites_entreprise`/`actualites_marche`
  (`{"ticker"?, "articles": [{"titre", "source", "date", "resume_court",
  "url"}, ...]}` ou `{"erreur": ...}`).
- Produit : un nouvel événement SSE `{"type": "actus", "articles": [...]}`,
  émis au plus une fois par appel à `run_assistant_loop`, consommé par
  la Tâche 3 (frontend).

- [ ] **Step 1 : Écrire les tests qui échouent**

Ouvrir `tests/assistant_ia/test_assistant.py`. Ajouter ces 5 fonctions
juste après `test_run_assistant_loop_collects_proposer_lien_calls_separately`
(qui se termine à la ligne 167 actuelle, juste avant la ligne vide qui
précède `test_run_assistant_loop_stops_at_the_iteration_cap`) :

```python
def test_run_assistant_loop_collects_actualites_entreprise_articles():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="actualites_entreprise", input={"ticker": "MC.PA"})
    first_final = _FakeMessage(content=[tool_use_1], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Reponse.")], stop_reason="end_turn", usage=_FakeUsage(10, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Reponse."], second_final)])
    article = {"titre": "LVMH recule", "source": "Reuters", "date": "2026-11-01T10:00+00:00",
               "resume_court": "Resume.", "url": "https://reuters.com/a"}

    def fake_dispatch(name, tool_input):
        return {"ticker": "MC.PA", "articles": [article]}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    actus_events = [e for e in evenements if e["type"] == "actus"]
    assert len(actus_events) == 1
    assert actus_events[0]["articles"] == [article]


def test_run_assistant_loop_does_not_collect_an_actualites_error():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="actualites_marche", input={})
    first_final = _FakeMessage(content=[tool_use_1], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Reponse.")], stop_reason="end_turn", usage=_FakeUsage(10, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Reponse."], second_final)])

    def fake_dispatch(name, tool_input):
        return {"erreur": "actualites indisponibles (HTTPError)"}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    assert [e for e in evenements if e["type"] == "actus"] == []


def test_run_assistant_loop_deduplicates_actualites_by_url_across_calls():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="actualites_entreprise", input={"ticker": "MC.PA"})
    tool_use_2 = _FakeBlock("tool_use", id="tu_2", name="actualites_marche", input={})
    first_final = _FakeMessage(content=[tool_use_1, tool_use_2], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Reponse.")], stop_reason="end_turn", usage=_FakeUsage(10, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Reponse."], second_final)])
    article_commun = {"titre": "Marche global", "source": "AFP", "date": "2026-11-01T09:00+00:00",
                       "resume_court": "R.", "url": "https://afp.com/a"}

    def fake_dispatch(name, tool_input):
        # Le meme article (meme url) revient des deux outils dans ce test —
        # cas reel : actualites_entreprise et actualites_marche peuvent
        # toutes deux remonter un meme article GDELT.
        return {"articles": [article_commun]}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    actus_events = [e for e in evenements if e["type"] == "actus"]
    assert len(actus_events) == 1
    assert actus_events[0]["articles"] == [article_commun]


def test_run_assistant_loop_caps_actualites_at_max_articles():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="actualites_entreprise", input={"ticker": "MC.PA"})
    tool_use_2 = _FakeBlock("tool_use", id="tu_2", name="actualites_marche", input={})
    first_final = _FakeMessage(content=[tool_use_1, tool_use_2], stop_reason="tool_use", usage=_FakeUsage(50, 5))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Reponse.")], stop_reason="end_turn", usage=_FakeUsage(10, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Reponse."], second_final)])

    def fake_dispatch(name, tool_input):
        suffixe = "ent" if name == "actualites_entreprise" else "marche"
        return {"articles": [
            {"titre": f"Titre {suffixe} {i}", "source": "S", "date": "2026-11-01T09:00+00:00",
             "resume_court": "R", "url": f"https://x.com/{suffixe}-{i}"}
            for i in range(4)
        ]}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    actus_events = [e for e in evenements if e["type"] == "actus"]
    assert len(actus_events) == 1
    assert len(actus_events[0]["articles"]) == assistant.news.MAX_ARTICLES


def test_run_assistant_loop_emits_actus_even_when_the_iteration_cap_is_hit():
    # Pas besoin de monkeypatcher usage.record_usage ici : l'autouse
    # fixture _no_real_usage_writes (haut de ce fichier) le fait deja
    # pour tous les tests de ce module.
    tool_use_actus = _FakeBlock("tool_use", id="tu_1", name="actualites_entreprise", input={"ticker": "X"})
    tool_use_fiche = _FakeBlock("tool_use", id="tu_2", name="fiche_entreprise", input={"ticker": "X"})
    premier_tour = _FakeMessage(content=[tool_use_actus, tool_use_fiche], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    tour_suivant = _FakeMessage(content=[tool_use_fiche], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    client = _FakeClient(
        [_FakeStream([], premier_tour)]
        + [_FakeStream([], tour_suivant) for _ in range(assistant.MAX_TOOL_ITERATIONS - 1)]
    )
    article = {"titre": "T", "source": "S", "date": "2026-11-01T09:00+00:00", "resume_court": "R",
               "url": "https://x.com/a"}

    def fake_dispatch(name, tool_input):
        if name == "actualites_entreprise":
            return {"articles": [article]}
        return {"ok": True}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    actus_events = [e for e in evenements if e["type"] == "actus"]
    assert len(actus_events) == 1
    assert actus_events[0]["articles"] == [article]
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_assistant.py -k actualites -v`
Expected: 5 échecs — `AssertionError` (longueur de `actus_events` à 0
partout), car `run_assistant_loop` n'émet encore aucun événement
`actus`.

- [ ] **Step 3 : Ajouter l'import de `news` et mettre à jour `SYSTEM_PROMPT`**

Dans `assistant_ia/assistant.py`, ligne 10-11 actuelles :

```python
import assistant_ia.tools as tools
import assistant_ia.usage as usage
```

remplacer par :

```python
import assistant_ia.news as news
import assistant_ia.tools as tools
import assistant_ia.usage as usage
```

Puis, lignes 81-83 actuelles :

```python
Reponds en texte brut, sans formatage markdown (pas de **gras**, pas de \
listes a tirets, pas de titres) — utilise des phrases completes et des \
retours a la ligne simples."""
```

remplacer par :

```python
Pour une reponse qui depasse une ou deux phrases, structure-la en \
plusieurs courts paragraphes, chacun precede d'un titre court (3 a 6 \
mots) seul sur sa ligne, au format "## Titre" (ex. "## Le mecanisme") \
— aucun autre formatage markdown (pas de **gras**, pas de listes a \
tirets). Termine TOUJOURS par un paragraphe precede du titre exact \
"## Mon avis", qui donne ta conclusion explicite et, si pertinent, ce \
qui la ferait changer. Pour une reponse courte (un chiffre, une \
confirmation simple, une seule idee), ces titres ne sont pas \
necessaires — n'en mets pas s'il n'y a qu'un seul paragraphe."""
```

- [ ] **Step 4 : Déclarer la collecte, dédoublonnée et plafonnée**

Ligne 145 actuelle :

```python
    liens_collectes: list[dict] = []
```

remplacer par :

```python
    liens_collectes: list[dict] = []
    actus_collectes: list[dict] = []
    urls_actus_vues: set[str] = set()
```

- [ ] **Step 5 : Collecter les articles dans la boucle de dispatch des outils**

Lignes 192-196 actuelles :

```python
            resultat = tool_dispatch(bloc.name, tool_input)
            if bloc.name == "proposer_lien" and "erreur" not in resultat:
                liens_collectes.append(resultat)
            if bloc.name == "aller_vers" and "erreur" not in resultat:
                navigation = resultat
```

remplacer par :

```python
            resultat = tool_dispatch(bloc.name, tool_input)
            if bloc.name == "proposer_lien" and "erreur" not in resultat:
                liens_collectes.append(resultat)
            if bloc.name == "aller_vers" and "erreur" not in resultat:
                navigation = resultat
            if bloc.name in ("actualites_entreprise", "actualites_marche") and "erreur" not in resultat:
                for article in resultat.get("articles", []):
                    url = article.get("url")
                    if url in urls_actus_vues or len(actus_collectes) >= news.MAX_ARTICLES:
                        continue
                    urls_actus_vues.add(url)
                    actus_collectes.append(article)
```

- [ ] **Step 6 : Émettre `actus` aux deux points de sortie de la boucle**

Lignes 176-177 actuelles (sortie normale) :

```python
            if liens_collectes:
                yield {"type": "liens", "liens": liens_collectes}
```

remplacer par :

```python
            if liens_collectes:
                yield {"type": "liens", "liens": liens_collectes}
            if actus_collectes:
                yield {"type": "actus", "articles": actus_collectes}
```

Lignes 203-204 actuelles (plafond `MAX_TOOL_ITERATIONS` atteint) :

```python
    if liens_collectes:
        yield {"type": "liens", "liens": liens_collectes}
```

remplacer par :

```python
    if liens_collectes:
        yield {"type": "liens", "liens": liens_collectes}
    if actus_collectes:
        yield {"type": "actus", "articles": actus_collectes}
```

- [ ] **Step 7 : Mettre à jour la docstring de `run_assistant_loop`**

Lignes 120-122 actuelles :

```python
    """Genere les evenements de la conversation : {"type": "texte", ...}
    au fil du streaming, {"type": "liens", "liens": [...]} une fois tous
    les appels proposer_lien collectes, puis {"type": "fin"}. N'accede
```

remplacer par :

```python
    """Genere les evenements de la conversation : {"type": "texte", ...}
    au fil du streaming (peut contenir des lignes "## Titre", voir
    SYSTEM_PROMPT), {"type": "liens", "liens": [...]} une fois tous les
    appels proposer_lien collectes, {"type": "actus", "articles": [...]}
    une fois tous les articles actualites_entreprise/actualites_marche
    collectes (dedoublonnes par url, plafonnes a news.MAX_ARTICLES), puis
    {"type": "fin"}. N'accede
```

- [ ] **Step 8 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_assistant.py -k actualites -v`
Expected: 5 PASSED.

- [ ] **Step 9 : Suite complète du module + suite complète du projet**

Run: `python -m pytest tests/assistant_ia -q`
Expected: tous verts (aucune régression sur `liens`/`navigation`/usage).

Run: `python -m pytest -q`
Expected: tous verts.

- [ ] **Step 10 : Commit**

```bash
git add assistant_ia/assistant.py tests/assistant_ia/test_assistant.py
git commit -m "feat(assistant_ia): emet les actualites reelles collectees en evenement SSE actus"
```

---

### Task 2 : `docs/answer_reveal.js` — stylisation des titres une fois la réponse terminée

**Files:**
- Modify: `docs/answer_reveal.js:48-51,161-163`
- Test: `docs/answer_reveal.test.js`

**Interfaces:**
- Produit : `finalizeStructuredText(target, fullText)`, exportée via
  `module.exports` — remplace les enfants de `target` par un noeud par
  ligne non vide de `fullText` : une ligne `## Titre` devient
  `className: "ai-section-title"` (ou `"ai-section-title-avis"` si le
  titre, comparé insensible à la casse après `trim()`, vaut exactement
  `"mon avis"`) avec `textContent` = le titre sans le marqueur ; toute
  autre ligne non vide devient `className: "ai-paragraph"` avec
  `textContent` = la ligne telle quelle (non modifiée, espaces
  compris). Appelée automatiquement par le module lui-même (voir Step
  3), la Tâche 3 n'a RIEN à appeler explicitement pour ça.
- Consomme : rien de nouveau — utilise `document.createElement` déjà
  disponible dans ce fichier (voir `appendWord`, ligne 53-58).

- [ ] **Step 1 : Écrire les tests qui échouent**

Ouvrir `docs/answer_reveal.test.js`. Remplacer la ligne 5 actuelle :

```js
const { tokenizeWords, pendingWords, createWordReveal } = require('./answer_reveal.js');
```

par :

```js
const { tokenizeWords, pendingWords, createWordReveal, finalizeStructuredText } = require('./answer_reveal.js');
```

Ajouter ces fonctions juste avant la ligne `async function test_flux_anime_affiche_tout_le_texte_apres_finish()`
(ligne 49 actuelle) :

```js
function test_finalizeStructuredText_stylise_les_titres_et_mon_avis() {
  const t = fakeTarget();
  finalizeStructuredText(t, '## Le mecanisme\nPhrase un.\n\n## Mon avis\nConclusion.');
  assert.strictEqual(t.nodes.length, 3);
  assert.strictEqual(t.nodes[0].className, 'ai-section-title');
  assert.strictEqual(t.nodes[0].textContent, 'Le mecanisme');
  assert.strictEqual(t.nodes[1].className, 'ai-paragraph');
  assert.strictEqual(t.nodes[1].textContent, 'Phrase un.');
  assert.strictEqual(t.nodes[2].className, 'ai-section-title-avis');
  assert.strictEqual(t.nodes[2].textContent, 'Mon avis');
}

function test_finalizeStructuredText_mon_avis_insensible_a_la_casse() {
  const t = fakeTarget();
  finalizeStructuredText(t, '##   MON AVIS  \nTexte.');
  assert.strictEqual(t.nodes[0].className, 'ai-section-title-avis');
}

function test_finalizeStructuredText_sans_titre_reste_un_seul_paragraphe() {
  const t = fakeTarget();
  finalizeStructuredText(t, 'Une reponse courte, sans titre.');
  assert.strictEqual(t.nodes.length, 1);
  assert.strictEqual(t.nodes[0].className, 'ai-paragraph');
  assert.strictEqual(t.text, 'Une reponse courte, sans titre.');
}

function test_finalizeStructuredText_jamais_de_html_interprete() {
  const t = fakeTarget();
  finalizeStructuredText(t, '## Titre <script>x</script>\nTexte <b>gras</b>.');
  assert.strictEqual(t.nodes[0].textContent, 'Titre <script>x</script>');
  assert.strictEqual(t.nodes[1].textContent, 'Texte <b>gras</b>.');
}
```

Modifier `test_flux_anime_affiche_tout_le_texte_apres_finish` (ligne
49-60 actuelles) — la finalisation automatique (Step 3) remplace
maintenant les `<span class="rv-w">` du flux en direct par les noeuds
structurés finaux dès que la réponse est terminée, donc l'assertion
`t.nodes.every(n => n.className === 'rv-w')` n'est plus vraie :

```js
async function test_flux_anime_affiche_tout_le_texte_apres_finish() {
  const t = fakeTarget();
  let done = 0;
  const r = createWordReveal(t, { reduceMotion: false, onDone: () => { done++; } });
  r.setText('Le score de ');
  r.setText('Le score de LVMH est +11,2');
  r.finish();
  await new Promise(res => setTimeout(res, 700));
  assert.strictEqual(t.text, 'Le score de LVMH est +11,2');
  // finalizeStructuredText (appelee automatiquement a la fin du flux,
  // voir Tache 2) remplace les spans rv-w par les noeuds structures
  // finaux — une seule ligne ici, sans titre "## ", donc un seul
  // noeud ai-paragraph.
  assert.ok(t.nodes.every(n => n.className === 'ai-paragraph'));
  assert.strictEqual(done, 1);
}
```

Ajouter les nouveaux appels dans l'IIFE finale (lignes 62-70
actuelles), juste après `test_fix_affiche_le_html_comme_du_texte();` :

```js
  test_finalizeStructuredText_stylise_les_titres_et_mon_avis();
  test_finalizeStructuredText_mon_avis_insensible_a_la_casse();
  test_finalizeStructuredText_sans_titre_reste_un_seul_paragraphe();
  test_finalizeStructuredText_jamais_de_html_interprete();
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `node docs/answer_reveal.test.js`
Expected: `TypeError: finalizeStructuredText is not a function` (ou
`undefined`) — la fonction n'existe pas encore.

- [ ] **Step 3 : Implémenter `finalizeStructuredText` et la brancher automatiquement**

Dans `docs/answer_reveal.js`, ajouter juste avant `function
createWordReveal(target, opts) {` (ligne 34 actuelle) :

```js
const SECTION_TITLE_RE = /^## (.+)$/;

// Reconstruit `target` ligne par ligne a partir du texte final complet,
// toujours via textContent/createElement (jamais innerHTML, voir
// l'en-tete de ce fichier) : une ligne "## Titre" devient un titre de
// section stylise (vert si c'est exactement "Mon avis"), toute autre
// ligne non vide reste un paragraphe normal. Appelee une seule fois,
// quand le flux est termine (voir sync() plus bas) — jamais pendant la
// revelation mot par mot.
function finalizeStructuredText(target, fullText) {
  target.replaceChildren();
  for (const ligne of fullText.split('\n')) {
    if (!ligne.trim()) continue;
    const correspond = ligne.trim().match(SECTION_TITLE_RE);
    const div = document.createElement('div');
    if (correspond) {
      const titre = correspond[1].trim();
      div.className = titre.toLowerCase() === 'mon avis' ? 'ai-section-title-avis' : 'ai-section-title';
      div.textContent = titre;
    } else {
      div.className = 'ai-paragraph';
      div.textContent = ligne;
    }
    target.appendChild(div);
  }
}
```

Puis, ligne 48-51 actuelles :

```js
  const sync = () => {
    target.classList.toggle('rv-streaming', !isDone());
    if (isDone() && !doneFired) { doneFired = true; onDone(); }
  };
```

remplacer par :

```js
  const sync = () => {
    target.classList.toggle('rv-streaming', !isDone());
    if (isDone() && !doneFired) {
      doneFired = true;
      finalizeStructuredText(target, full);
      onDone();
    }
  };
```

- [ ] **Step 4 : Exporter la fonction**

Ligne 161-163 actuelles :

```js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { REVEAL_WORD_MS, REVEAL_FLUSH_MS, REVEAL_FADE_MS, tokenizeWords, pendingWords, createWordReveal };
}
```

remplacer par :

```js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    REVEAL_WORD_MS, REVEAL_FLUSH_MS, REVEAL_FADE_MS,
    tokenizeWords, pendingWords, createWordReveal, finalizeStructuredText,
  };
}
```

- [ ] **Step 5 : Lancer les tests, vérifier qu'ils passent**

Run: `node docs/answer_reveal.test.js`
Expected: `OK` (aucune trace d'erreur, code de sortie 0).

- [ ] **Step 6 : Commit**

```bash
git add docs/answer_reveal.js docs/answer_reveal.test.js
git commit -m "feat(assistant-ia-frontend): stylise les titres ## une fois la reponse terminee"
```

---

### Task 3 : `docs/index.html` — réception SSE, cartes actus, logos

**Files:**
- Modify: `docs/index.html:447-470,735-736,772-777,827-881,4834-4881,5003-5004,5067-5072,5084-5087`

**Interfaces:**
- Consomme : l'événement SSE `{"type": "actus", "articles": [...]}`
  (Tâche 1) ; `finalizeStructuredText` est déjà appelée automatiquement
  par `answer_reveal.js` (Tâche 2) — rien à appeler ici pour les titres.
- Produit : `homeSearchActusHtml(articles)`, une méthode `.actus(a)` sur
  l'objet retourné par `createHomeAnswer`.

- [ ] **Step 1 : Ajouter le conteneur `.ai-actus` au template de la bulle**

Dans `docs/index.html`, lignes 4834-4842 actuelles :

```javascript
  thread.insertAdjacentHTML('beforeend', `
    <div class="ai-msg-ai">
      <span class="ai-avatar" aria-hidden="true"></span>
      <div class="ai-msg-body">
        <div class="ai-msg-text"></div>
        <div class="ai-links"></div>
        <div class="ai-actions"></div>
      </div>
    </div>`);
  const msg = thread.lastElementChild;
  const textEl = msg.querySelector('.ai-msg-text');
  const linksEl = msg.querySelector('.ai-links');
  const actionsEl = msg.querySelector('.ai-actions');
```

remplacer par :

```javascript
  thread.insertAdjacentHTML('beforeend', `
    <div class="ai-msg-ai">
      <span class="ai-avatar" aria-hidden="true"></span>
      <div class="ai-msg-body">
        <div class="ai-msg-text"></div>
        <div class="ai-actus"></div>
        <div class="ai-links"></div>
        <div class="ai-actions"></div>
      </div>
    </div>`);
  const msg = thread.lastElementChild;
  const textEl = msg.querySelector('.ai-msg-text');
  const actusEl = msg.querySelector('.ai-actus');
  const linksEl = msg.querySelector('.ai-links');
  const actionsEl = msg.querySelector('.ai-actions');
```

- [ ] **Step 2 : Déclarer l'état `actus` et son affichage**

Lignes 4847-4848 actuelles :

```javascript
  let liens = [];
  let pret = false;
```

remplacer par :

```javascript
  let liens = [];
  let actus = [];
  let pret = false;
```

Lignes 4856-4860 actuelles :

```javascript
  const afficherLiens = () => {
    linksEl.innerHTML = homeSearchLinksHtml(liens);
    linksEl.classList.add('ai-reveal-after');
    wireHomeSearchLinkButtons(linksEl);
  };
```

remplacer par :

```javascript
  const afficherLiens = () => {
    linksEl.innerHTML = homeSearchLinksHtml(liens);
    linksEl.classList.add('ai-reveal-after');
    wireHomeSearchLinkButtons(linksEl);
  };
  const afficherActus = () => {
    actusEl.innerHTML = homeSearchActusHtml(actus);
    actusEl.classList.add('ai-reveal-after');
  };
```

Lignes 4864-4869 actuelles :

```javascript
    onDone: () => {
      pret = true;
      afficherLiens();
      if (reveal.text()) wireAnswerActions(actionsEl, () => reveal.text());
      suivre();
    },
```

remplacer par :

```javascript
    onDone: () => {
      pret = true;
      afficherActus();
      afficherLiens();
      if (reveal.text()) wireAnswerActions(actionsEl, () => reveal.text());
      suivre();
    },
```

- [ ] **Step 3 : Exposer `.actus(a)` sur l'objet retourné**

Lignes 4873-4880 actuelles :

```javascript
  return {
    texte(t) { reveal.setText(t); },
    remplacer(t) { textEl.classList.add('is-error'); reveal.replace(t); },
    liens(l) { liens = l || []; if (pret) afficherLiens(); },
    terminer() { reveal.finish(); },
    fixer(t, l) { liens = l || []; reveal.fix(t); },
    annuler() { reveal.cancel(); },
  };
```

remplacer par :

```javascript
  return {
    texte(t) { reveal.setText(t); },
    remplacer(t) { textEl.classList.add('is-error'); reveal.replace(t); },
    liens(l) { liens = l || []; if (pret) afficherLiens(); },
    actus(a) { actus = a || []; if (pret) afficherActus(); },
    terminer() { reveal.finish(); },
    fixer(t, l) { liens = l || []; reveal.fix(t); },
    annuler() { reveal.cancel(); },
  };
```

- [ ] **Step 4 : Fonction de rendu des cartes actus**

Dans `docs/index.html`, ajouter juste après `function
homeSearchLinksHtml(liens) { ... }` (lignes 4772-4777 actuelles) :

```javascript
function faviconActuUrl(url) {
  try { return `https://www.google.com/s2/favicons?domain=${new URL(url).hostname}&sz=64`; }
  catch (e) { return null; }
}

function homeSearchActusHtml(articles) {
  return (articles || []).map(a => {
    const favicon = faviconActuUrl(a.url);
    const logoHtml = favicon
      ? `<div class="ai-actu-logo"><img src="${escHtml(favicon)}" alt="" loading="lazy" onerror="this.parentElement.style.display='none'"></div>`
      : '';
    const date = new Date(a.date);
    const dateTexte = isNaN(date) ? '' : date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
    return `
    <a class="ai-actu-card" href="${escHtml(a.url)}" target="_blank" rel="noopener">
      ${logoHtml}
      <div class="ai-actu-body">
        <div class="ai-actu-title">${escHtml(a.titre)}</div>
        ${a.resume_court ? `<div class="ai-actu-summary">${escHtml(a.resume_court)}</div>` : ''}
        <div class="ai-actu-meta">${escHtml(a.source)}${dateTexte ? ' · ' + escHtml(dateTexte) : ''}</div>
      </div>
    </a>`;
  }).join('');
}
```

- [ ] **Step 5 : CSS des cartes actus**

Dans la feuille de style de `docs/index.html`, juste après la règle
`.ai-link:hover { background: rgba(185,154,104,.16); }` (ligne 469
actuelle), ajouter :

```css
  .ai-actus { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
  .ai-actus:empty { display: none; }
  .ai-actu-card {
    display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px;
    border-radius: 12px; border: 1px solid var(--border); text-decoration: none; color: inherit;
  }
  .ai-actu-card:hover { background: rgba(185,154,104,.1); }
  /* Meme motif que .hero-logo-wrap/.hero-logo (fiches d'indices, ligne 735) :
     case clippee, sans fond ni bordure, l'image en object-fit:contain. */
  .ai-actu-logo { width: 28px; height: 28px; border-radius: 8px; overflow: hidden; flex-shrink: 0; }
  .ai-actu-logo img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .ai-actu-body { min-width: 0; flex: 1; }
  .ai-actu-title { font-size: 13.5px; font-weight: 500; color: var(--text); line-height: 1.4; }
  .ai-actu-summary { font-size: 12.5px; color: var(--muted); line-height: 1.5; margin-top: 3px; }
  .ai-actu-meta { font-size: 11.5px; color: var(--text-faint); margin-top: 4px; }
  .ai-section-title {
    font-size: 11.5px; font-weight: 600; color: var(--muted); text-transform: uppercase;
    letter-spacing: .03em; margin-top: 14px;
  }
  .ai-section-title:first-child { margin-top: 0; }
  .ai-section-title-avis {
    font-size: 11.5px; font-weight: 600; color: var(--gold); text-transform: uppercase;
    letter-spacing: .03em; margin-top: 14px;
  }
  .ai-paragraph { font-size: 15.5px; line-height: 1.65; color: var(--text); margin-top: 8px; }
  .ai-paragraph:first-child { margin-top: 0; }
```

- [ ] **Step 6 : Recevoir l'événement SSE `actus`**

Ligne 5003-5004 actuelles :

```javascript
  let texteAccumule = '';
  let liensRecus = [];
```

remplacer par :

```javascript
  let texteAccumule = '';
  let liensRecus = [];
  let actusRecus = [];
```

Lignes 5067-5072 actuelles :

```javascript
        if (evenement.type === 'liens') {
          liensRecus = evenement.liens;
          // Les liens seuls n'ouvrent pas la réponse : ils ne sont rendus
          // qu'une fois le texte révélé et le flux terminé.
          if (typer) typer.liens(liensRecus);
        }
```

remplacer par :

```javascript
        if (evenement.type === 'liens') {
          liensRecus = evenement.liens;
          // Les liens seuls n'ouvrent pas la réponse : ils ne sont rendus
          // qu'une fois le texte révélé et le flux terminé.
          if (typer) typer.liens(liensRecus);
        }
        if (evenement.type === 'actus') {
          actusRecus = evenement.articles;
          if (typer) typer.actus(actusRecus);
        }
```

Lignes 5084-5087 actuelles :

```javascript
    } else {
      typer.liens(liensRecus);
      typer.terminer();
    }
```

remplacer par :

```javascript
    } else {
      typer.liens(liensRecus);
      typer.actus(actusRecus);
      typer.terminer();
    }
```

- [ ] **Step 7 : Vérification manuelle (pas de harnais de test JS pour `index.html`)**

Aucun fichier de test n'existe pour les fonctions de rendu de
`docs/index.html` (seuls `answer_reveal.test.js` et
`chart_patterns.test.js` existent, voir Non-objectifs du spec) — même
situation que la Tâche 7 du plan `2026-10-30-financial-analysis-on-demand`
: vérification manuelle en navigateur réel après implémentation,
servie localement (`python -m http.server` depuis `docs/`), en
patchant `window.fetch` pour simuler un flux SSE avec un événement
`actus` et confirmer que les cartes s'affichent avec logo, titre,
résumé, source/date et lien cliquable, et que la bulle `.ai-actus`
reste vide (`display:none` via `:empty`) quand aucune actu n'est
reçue.

- [ ] **Step 8 : Commit**

```bash
git add docs/index.html
git commit -m "feat(assistant-ia-frontend): rend les actualites reelles (logo, resume, lien) dans la reponse"
```
