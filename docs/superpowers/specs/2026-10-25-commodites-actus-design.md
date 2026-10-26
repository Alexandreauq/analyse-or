# Actualités + analyse IA par commodité — Design

## Contexte

Le chantier "Commodités" (mergé sur `main`, specs/plans
`2026-10-25-commodites-score-design.md`/`-plan.md`) a généralisé le score
macro-fondamental de l'or à l'argent, au pétrole (WTI) et au gaz naturel, et
construit l'onglet "Commodités" avec sa sous-navigation Or/Argent/Pétrole/Gaz.

Demande utilisateur (verbatim, deux messages consécutifs) :
> "j'aimerais qu'il y ait un onglet actus associé à chacun d'entre eux"
> "et une analyse actuelle de pourquoi la commodités a évolué ainsi, quels
> sont les actus majeurs et tout"

Ce chantier ajoute, pour chacune des 4 commodités, un 3e panneau dépliant
"Actualités" contenant :
1. Une analyse IA (texte structuré) qui croise les facteurs du score
   composite ET les actualités récentes pour expliquer l'évolution récente
   de la commodité.
2. La liste des actualités récentes qui ont servi de matière à cette
   analyse.

## Pattern existant réutilisé (ne rien réinventer)

`indices_score.py` a déjà ce pipeline complet pour les ~700 entreprises
de l'onglet Indices :

- `NEWS_RSS_URL = "https://news.google.com/rss/search"`, `NEWS_MAX_ITEMS = 5`
  (ligne 3983-3984)
- `parse_news_rss(xml_bytes) -> list[dict]` (ligne 3987) : extrait
  title/date/link/source des N premiers `<item>`.
- `fetch_article_text(url) -> str | None` (ligne 4033) : télécharge et
  extrait le texte principal via trafilatura, `None` sur tout échec,
  tronqué à `ARTICLE_TEXT_MAX_CHARS = 4000`.
- `fetch_news(company_name, previous_classifications=None) -> list[dict]`
  (ligne 4004) : interroge le RSS, réutilise une classification en cache
  par lien (évite de refaire classifier un article déjà vu), sinon appelle
  `summarize_news_item`.
- `summarize_news_item(title, company_name, article_text) -> dict`
  (ligne 4061) : appel Anthropic, modèle `claude-haiku-4-5-20251001`,
  renvoie `{"summary": str, "sentiment": -1|0|1, "importance": "mineure"|
  "notable"|"majeure"}`. Jamais d'exception — repli neutre si clé API
  absente ou échec.
- `generate_financial_analysis(company_name, financial_context,
  ratios_summary) -> str | None` (ligne 4163) : appel Anthropic, modèle
  `claude-opus-5`, thinking adaptatif effort élevé, renvoie du HTML
  restreint (`<h3><p><ul><li><strong>` uniquement) sous forme de JSON
  `{"analysis_html": "..."}`. `None` sur tout échec, jamais d'exception,
  logge le corps de la réponse d'erreur en diagnostic.
- `NEWS_IMPORTANCE_LEVELS = ("mineure", "notable", "majeure")` (ligne 2628).
- Secret GH Actions déjà configuré : `secrets.CLAUDE_API_KEY` → env
  `ANTHROPIC_API_KEY`, utilisé dans `.github/workflows/indices.yml`.
- Carry-forward sur échec : si `generate_financial_analysis` renvoie
  `None`, l'entreprise garde l'analyse de la veille (`previous.get(
  "financial_analysis_html")`) plutôt que de perdre son contenu — même
  logique pour `previous.get("news_classifications", {})`, qui évite de
  reclassifier un article déjà vu.
- Frontend (`docs/index.html`, `renderCompanyDetail`/
  `renderCompanyAnalysis`) :
  - `.news-item` / `.news-title` / `.news-meta` / `.news-summary` pour
    chaque actu (titre lien, `source · date`, résumé optionnel). Le
    sentiment/l'importance ne sont PAS affichés visuellement par article
    dans cette vue (ils servent ailleurs, ex. le badge "Actu majeure
    récente" du hero) — ne pas en ajouter ici, rester cohérent.
  - `.ai-analysis` pour le conteneur de `financial_analysis_html`, avec
    repli `<p>Analyse non disponible pour le moment.</p>` si `null`.

## Ce qui est nouveau pour les commodités

Les commodités ne sont pas des entreprises : pas de `financial_context`
(comptes trimestriels), pas de ratios. L'analyse à écrire est différente
par nature — elle doit expliquer le **score composite** (ses facteurs,
déjà calculés par `commodity_score.py`/`gold_score.py`) à la lumière des
**actualités récentes**, ce qui est explicitement la demande de
l'utilisateur. Un nouveau module est donc nécessaire plutôt qu'un import
direct des fonctions entreprise telles quelles.

### Nouveau module : `commodity_news.py` (racine du repo)

Importe directement depuis `indices_score.py` (jamais dupliqué) :
`NEWS_RSS_URL`, `NEWS_MAX_ITEMS`, `parse_news_rss`, `fetch_article_text`,
`ANTHROPIC_API_URL`, `NEWS_IMPORTANCE_LEVELS`.

Ajoute :

- `fetch_commodity_news(query, subject_label, previous_classifications=None) -> list[dict]`
  — même structure que `fetch_news`, mais paramétrée par une requête RSS
  (`query`) et un libellé de sujet (`subject_label`, ex. "l'or", "le
  pétrole WTI") utilisé dans le prompt de classification. Appelle
  `summarize_commodity_news_item` au lieu de `summarize_news_item`.
- `summarize_commodity_news_item(title, subject_label, article_text) -> dict`
  — même contrat que `summarize_news_item` (même modèle, même schéma
  JSON de sortie, même repli neutre), prompt reformulé : "cet article
  concernant {subject_label}" au lieu de "cette entreprise {company_name}",
  et la grille d'importance adaptée au contexte commodité (majeure :
  décision OPEP+/banque centrale à fort impact sur le prix, choc d'offre
  ou de demande majeur, rupture géopolitique touchant directement
  l'approvisionnement ; notable : données d'inventaire/production
  significatives, déclaration officielle influente ; mineure : le reste).
- `generate_commodity_analysis(subject_label, factors_summary, news_summary) -> str | None`
  — même contrat que `generate_financial_analysis` (modèle, thinking
  adaptatif effort élevé, HTML restreint identique, jamais d'exception,
  log du corps d'erreur). Prompt différent : croise explicitement
  `factors_summary` (les facteurs du score composite, formatés en texte,
  ex. "Taux réels US 10 ans : -2 (poids 30%) — 2.64% (il y a ~45j :
  2.55%)") et `news_summary` (titres + résumés des actus déjà
  classifiées) pour expliquer l'évolution récente du score et identifier
  les actualités majeures. Sections demandées : contexte récent (2-3
  phrases), facteurs déterminants (quels facteurs pèsent le plus et
  pourquoi), actualités à retenir (les actus notables/majeures et leur
  lien avec le score), synthèse.

Ajoute aussi une fonction d'orchestration **partagée** (appelée à
l'identique par `gold_score.py` et `commodity_score.py`, pour ne jamais
dupliquer la logique de carry-forward/gate quotidien à deux endroits) :

- `build_news_and_analysis_section(subject_label, news_query, factors, daily_snapshot, previous_payload) -> tuple[list, str | None, dict]`
  — renvoie `(news, analysis_html, news_classifications)` à fusionner
  dans le payload de sortie. Si `daily_snapshot` est faux (run horaire
  intrajournalier), renvoie tel quel ce qui existait déjà dans
  `previous_payload` (`news`/`analysis_html`/`news_classifications`),
  sans aucun appel réseau — seul le run quotidien rappelle
  `fetch_commodity_news`/`generate_commodity_analysis`. Si
  `generate_commodity_analysis` échoue (`None`), garde
  `previous_payload.get("analysis_html")` (carry-forward). `factors` est
  la liste déjà calculée (post-renormalisation pour les commodités),
  formatée via `build_factors_summary`. `previous_payload` est `{}` ou le
  payload JSON déjà écrit lors d'un run précédent (jamais levé si le
  fichier n'existe pas encore ou est invalide — c'est à l'appelant de
  dégrader vers `{}` dans ce cas, pas à cette fonction).

Requêtes RSS retenues par commodité (en français, disambiguées pour
éviter le bruit — "argent"/"or"/"gaz" sont des mots très ambigus seuls en
français) :

| Commodité | `query` (RSS) | `subject_label` (prompt) |
|---|---|---|
| Or | `cours de l'or` | "l'or" |
| Argent | `cours de l'argent métal` | "l'argent (métal)" |
| Pétrole | `prix du pétrole WTI` | "le pétrole WTI" |
| Gaz | `prix du gaz naturel` | "le gaz naturel" |

### Intégration dans `gold_score.py` (ajout uniquement, Global Constraint)

`main()` lit déjà `daily_snapshot` (ligne 751) et construit déjà
`payload` (ligne 824-836) avant de l'écrire (ligne 838-843). Ajouts,
sans toucher à la logique existante :

1. Avant la construction de `payload`, lire `OUTPUT_JSON_PATH` (le
   `score.json` existant, s'il existe — `{}` sinon, jamais d'exception
   propagée) pour obtenir `previous_payload`.
2. Appeler `build_news_and_analysis_section("l'or", "cours de l'or",
   factors, daily_snapshot, previous_payload)` pour obtenir `(news,
   analysis_html, news_classifications)` — toute la logique de gate
   quotidien et de carry-forward vit dans cette fonction partagée
   (`commodity_news.py`), `gold_score.py` ne fait qu'appeler et fusionner
   le résultat.
3. Ajouter `news`, `analysis_html`, `news_classifications` au `payload`.

### Intégration dans `commodity_score.py` (ajout uniquement)

`run_commodity(commodity_key, *, api_key=None, daily_snapshot=None)`
suit exactement le même schéma que ci-dessus, avec la config RSS/label
par commodité ajoutée à `COMMODITY_CONFIGS` (nouvelles clés `news_query`
et `news_subject_label`) :

```python
COMMODITY_CONFIGS = {
    "argent": {"label": "Argent", "ticker": "SI=F", "unite": "$ / oz",
               "news_query": "cours de l'argent métal", "news_subject_label": "l'argent (métal)"},
    "petrole": {"label": "Pétrole (WTI)", "ticker": "CL=F", "unite": "$ / baril",
                "news_query": "prix du pétrole WTI", "news_subject_label": "le pétrole WTI"},
    "gaz": {"label": "Gaz naturel", "ticker": "NG=F", "unite": "$ / MMBtu",
            "news_query": "prix du gaz naturel", "news_subject_label": "le gaz naturel"},
}
```

`run_commodity` lit le `score_<commodity_key>.json` existant (racine du
repo, `OUTPUT_DIR`) avant de l'écraser, pour obtenir `previous_payload`
(`{}` si absent/invalide), puis appelle la même fonction partagée
`build_news_and_analysis_section(cfg["news_subject_label"],
cfg["news_query"], factors, daily_snapshot, previous_payload)` que
`gold_score.py`. `factors` est la liste déjà renvoyée par
`build_factors` (après renormalisation), donc `factors_summary` reflète
toujours les poids réellement affichés sur le site.

### `.github/workflows/daily.yml`

Ajoute `ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_API_KEY }}` à l'étape
`Calculer le score et envoyer l'email` (gold_score.py) ET à l'étape
`Calculer le score des autres commodités` (commodity_score.py) — même
secret que celui déjà utilisé par `indices.yml`, pas de nouveau secret à
créer. Le `git add` déjà étendu lors du chantier précédent n'a pas besoin
de changer (aucun nouveau fichier produit — `news`/`analysis_html` vivent
dans les `score_<key>.json` déjà suivis).

### Frontend (`docs/index.html`)

Dans `render(data)`, 3e bouton toggle dans `.btn-row`, après "Détail du
calcul" :

```html
<button class="toggle-btn" id="toggleActus" type="button" aria-expanded="false">
  Actualités <span class="chevron">▾</span>
</button>
```

Nouvelle section `actusWrap` (même structure que `actuWrap`/`detailWrap`
existants, ajoutée dans `.or-columns` ou en pleine largeur en dessous —
à trancher dans le plan selon l'espace disponible), contenant :

```html
<div class="ai-analysis">${data.analysis_html || '<p>Analyse non disponible pour le moment.</p>'}</div>
${newsHtml || '<p class="hero-sub">Aucune actualité récente.</p>'}
```

où `newsHtml` est construit avec le **même template** que
`renderCompanyDetail` (`.news-item`/`.news-title`/`.news-meta`/
`.news-summary`, titre lien `target="_blank" rel="noopener"`) — aucune
nouvelle classe CSS. `wireAccordionGroup` (déjà utilisé pour
`toggleActu`/`toggleDetail`) est étendu avec `['toggleActus',
'actusWrap']`.

## Hors périmètre

- Pas de scoring factor "actualité récente" pour les commodités (contrairement
  aux entreprises qui ont `score_actualite_recente`) — l'utilisateur n'a
  demandé qu'un affichage, pas un nouveau facteur de score. Si cette
  demande arrive plus tard, elle sera traitée comme un chantier séparé.
- `news_classifications` n'est jamais affiché côté site (clé interne,
  comme pour les entreprises) — seulement `news`/`analysis_html`.
- Le `json.dump` existant dans `gold_score.py` (ligne 838-843) n'a pas
  `allow_nan=False` aujourd'hui — préexistant, hors périmètre de ce
  chantier (on ne touche que par ajout, jamais en réécrivant l'appel
  existant). Sans objet de toute façon pour les nouvelles clés
  introduites ici (`news`, `analysis_html`, `news_classifications`) :
  ce sont des chaînes/entiers/listes de chaînes (titres, résumés,
  sentiment -1/0/1, niveau d'importance), jamais des flottants issus de
  yfinance — aucun risque de NaN à garder.
