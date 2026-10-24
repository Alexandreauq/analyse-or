# L'Analyste — backtest historique (6-12 mois) — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rejouer rétroactivement, sur une fenêtre de 6-12 mois, le même raisonnement IA + actus que le test prospectif ("l'Analyste") en cours, sur un échantillon de 30-50 signaux "entrée" reconstruits, avec une isolation stricte des données au point-in-temps et une résolution immédiate du rendement réalisé.

**Architecture:** Trois nouveaux fichiers. (A) Extension minimale de `assistant_ia/data_sources.py`/`news.py` pour borner GDELT par le haut (jamais d'actu postérieure à la date testée). (B) `analyste_backtest.py` (racine) : recalcule rétroactivement le score composite de `indices_score.py` en réutilisant **directement** ses fonctions de scoring (jamais de réimplémentation de la logique de calcul), avec un historique prix/volume tronqué et des actus classées point-in-temps ; échantillonne par tirage aléatoire avec remise jusqu'à obtenir 30-50 signaux "entrée" confirmés. (C) `analyste_replay.py` (racine) : pour chaque signal, construit le même raisonnement à 4 dimensions que le test prospectif via l'API Gemini, avec instruction anti-fuite explicite, puis résout immédiatement le rendement réalisé (le prix futur est déjà connu aujourd'hui) et écrit le tout dans un nouveau fichier d'expérience.

**Tech Stack:** Python 3, pytest, pandas (déjà une dépendance), `google-genai`/appel HTTP direct à l'API Gemini (clé déjà configurée en variable d'environnement `GEMINI_API_KEY`), `requests` (déjà utilisé), `yfinance` (déjà utilisé).

**Spec:** `docs/superpowers/specs/2026-10-24-analyste-backtest-design.md`

## Découverte de terrain importante (grounding, pas dans la spec initiale)

En préparant ce plan, deux précisions techniques, non prévues dans la spec,
changent légèrement l'articulation interne des composants (sans changer
aucune décision déjà validée avec l'utilisateur — fenêtre, échantillon,
modèle, stockage) :

1. **`fetch_company_financials` (indices_score.py) n'a aucun paramètre de
   date** — elle appelle toujours `t.financials`/`t.quarterly_financials`/
   `t.balance_sheet`/`t.cashflow` (les derniers états connus AUJOURD'HUI) et
   `history(period="6y")` (relatif à l'instant de l'appel, pas à une date
   passée). **Décision de ce plan** : ne JAMAIS modifier
   `fetch_company_financials`/`build_company_entry` (fichier de production
   critique, alimente le site + les deux bots chaque jour) — à la place,
   `analyste_backtest.py` reconstruit sa propre collecte de prix/volume
   tronquée (avec `start=`/`end=` plutôt que `period=`) et appelle
   **directement** les fonctions de calcul pur (`classify_weinstein_stage`,
   `extract_ratios`/`extract_ratios_financial`, les `score_*`,
   `compute_composite`, `recalibrate_scores_by_profile`,
   `compute_company_alerts`, `estimate_valuation_targets`) — c'est la
   LOGIQUE DE CALCUL qui est réutilisée sans réimplémentation (l'intention
   de la spec), pas la glu d'orchestration de `build_company_entry`, qui n'a
   de toute façon pas de point d'extension pour une date passée.
2. **`score_actualite_recente` (poids 10% du composite) a besoin d'actus
   classées (sentiment/importance)**, et sa seule source aujourd'hui
   (`fetch_news`, via le flux RSS Google News) ne peut pas être bornée dans
   le temps — un flux RSS ne renvoie que les articles les plus récents, pas
   un historique à une date choisie. **Décision de ce plan** : réutiliser
   les mêmes articles GDELT bornés par date (déjà nécessaires pour le
   raisonnement du Composant C) comme source pour ce facteur aussi, classés
   avec les mêmes fonctions que la production (`fetch_article_text` +
   `summarize_news_item`, toutes deux dans `indices_score.py`, basées sur
   `ANTHROPIC_API_KEY` — distinct de la clé Gemini utilisée pour le
   raisonnement du Composant C). Ça évite une fuite via ce facteur précis et
   réutilise la même classification que la production, sans dupliquer
   l'appel GDELT entre le score quantitatif et le raisonnement qualitatif.

## Global Constraints

- Fenêtre : dates cibles tirées entre **180 et 365 jours avant aujourd'hui**
  (6-12 mois), jamais au-delà.
- Échantillon : entre **30 et 50** signaux "entrée" confirmés, répartis sur
  les 8 indices du périmètre (`CAC40, DAX, NASDAQ, DOW, FTSE, SMI, IBEX35,
  FTSEMIB` — exactement `ibkr_bot.signals.INDICES_IN_SCOPE`, même liste).
- Graine aléatoire fixée (`GRAINE_ALEATOIRE = 20261024`) pour que
  l'échantillonnage soit reproductible entre deux exécutions sur les mêmes
  données.
- Aucune donnée postérieure à la date cible ne doit jamais atteindre le
  contexte fourni au LLM, ni le facteur `score_actualite_recente`, ni le
  calcul technique — isolation stricte, testée explicitement à chaque
  tâche qui y touche.
- Horizon de résolution : ~1 mois après la date cible (même ordre de
  grandeur que le test prospectif), résolu immédiatement (le prix est déjà
  connu aujourd'hui).
- Stockage : `docs/superpowers/experiments/2026-10-24-analyste-backtest-6-12-mois.json`,
  jamais mélangé avec les fichiers `*-ia-news-reasoning-test.json` du test
  prospectif. Champ `"methode": "backtest_retroactif"` sur chaque entrée.
- Modèle LLM du raisonnement (Composant C) : Gemini (`gemini-2.5-flash` par
  défaut), jamais Claude en session pour ce lot — scriptable, pas de
  consommation de tours de conversation.
- Ne jamais modifier `indices_score.py::fetch_company_financials` ni
  `build_company_entry` — voir "Découverte de terrain" ci-dessus.

---

### Task 1 : `assistant_ia/data_sources.py::fetch_gdelt_articles` — borne haute

**Files:**
- Modify: `assistant_ia/data_sources.py:117-131`
- Test: `tests/assistant_ia/test_data_sources.py`

**Interfaces:**
- Produces: `fetch_gdelt_articles(requete, debut, ttl_seconds, cache_key, *, fin: datetime | None = None, http_get=..., now_fn=..., cache=...)` — nouveau paramètre optionnel `fin`, consommé par la Tâche 2.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/assistant_ia/test_data_sources.py` :

```python
def test_fetch_gdelt_articles_sends_enddatetime_when_fin_is_provided():
    appels = []
    def http_get_factice(url, params=None, headers=None, timeout=None):
        appels.append(params)
        class Reponse:
            def raise_for_status(self): pass
            def json(self): return {"articles": []}
        return Reponse()

    debut = datetime(2026, 1, 1)
    fin = datetime(2026, 1, 15)
    data_sources.fetch_gdelt_articles(
        "test", debut, 60, "cle-test", fin=fin,
        http_get=http_get_factice, now_fn=lambda: 0.0, cache={})

    assert appels[0]["enddatetime"] == "20260115000000"


def test_fetch_gdelt_articles_omits_enddatetime_when_fin_is_absent():
    appels = []
    def http_get_factice(url, params=None, headers=None, timeout=None):
        appels.append(params)
        class Reponse:
            def raise_for_status(self): pass
            def json(self): return {"articles": []}
        return Reponse()

    data_sources.fetch_gdelt_articles(
        "test", datetime(2026, 1, 1), 60, "cle-test-2",
        http_get=http_get_factice, now_fn=lambda: 0.0, cache={})

    assert "enddatetime" not in appels[0]
```

Importer `datetime` en tête du fichier de test s'il n'y est pas déjà.

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/assistant_ia/test_data_sources.py -v -k enddatetime`
Expected: FAIL (`TypeError: fetch_gdelt_articles() got an unexpected keyword argument 'fin'`)

- [ ] **Step 3: Implémenter**

Dans `assistant_ia/data_sources.py`, remplacer la signature et le corps de
`fetch_gdelt_articles` (lignes 117-130) par :

```python
def fetch_gdelt_articles(
    requete: str, debut: datetime, ttl_seconds: int, cache_key: str,
    *, fin: datetime | None = None, http_get=requests.get, now_fn=time.time,
    cache: dict | None = None,
) -> dict:
    """Articles GDELT (`{"articles": [...]}`) ou {"erreur": str}. Source sans
    cle. GDELT limite a une requete toutes les 5 secondes : un refus arrive
    en texte brut, non JSON, donc renvoye comme erreur et non mis en cache.

    `fin` (optionnel, borne haute) : ajoute `enddatetime` a la requete GDELT
    quand il est fourni -- sans lui, GDELT renvoie les articles les plus
    RECENTS correspondant a la requete (tries datetimedesc), ce qui
    inclurait des articles posterieurs a `debut` sans aucune limite --
    necessaire pour une recherche point-in-temps (voir analyste_backtest.py),
    jamais utilise par les appelants de fraicheur existants (assistant_ia/
    news.py), qui ne le passent pas et conservent le comportement actuel."""
    params = {
        "query": requete, "mode": "artlist", "format": "json", "maxrecords": 25,
        "sort": "datetimedesc", "startdatetime": debut.strftime("%Y%m%d%H%M%S"),
    }
    if fin is not None:
        params["enddatetime"] = fin.strftime("%Y%m%d%H%M%S")
    return _appel_actualites(
        GDELT_DOC_URL, params, None, ttl_seconds, cache_key,
        http_get=http_get, now_fn=now_fn, cache=cache)
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/assistant_ia/test_data_sources.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/data_sources.py tests/assistant_ia/test_data_sources.py
git commit -m "feat(assistant-ia): fetch_gdelt_articles accepte une borne haute (enddatetime) pour une recherche point-in-temps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2 : `assistant_ia/news.py` — actus d'entreprise bornées point-in-temps

**Files:**
- Modify: `assistant_ia/news.py`
- Test: `tests/assistant_ia/test_news.py`

**Interfaces:**
- Consumes: `data_sources.fetch_gdelt_articles(..., fin=...)` (Tâche 1).
- Produces: `actualites_entreprise_point_in_temps(ticker, nom, date_cible, fenetre_jours=14, *, gdelt_source=..., now=...) -> dict` — consommée par la Tâche 3.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/assistant_ia/test_news.py` :

```python
def test_actualites_entreprise_point_in_temps_bounds_both_sides():
    appels = []
    def gdelt_source_factice(requete, debut, ttl, cle, fin=None):
        appels.append((debut, fin))
        return {"articles": [
            {"title": "Entreprise Test annonce un partenariat", "seendate": "20260601T100000Z",
             "domain": "lemonde.fr", "url": "https://lemonde.fr/a"},
        ]}

    date_cible = datetime(2026, 6, 15, tzinfo=timezone.utc)
    resultat = news.actualites_entreprise_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, fenetre_jours=14,
        gdelt_source=gdelt_source_factice)

    debut_appele, fin_appele = appels[0]
    assert fin_appele == date_cible
    assert debut_appele == date_cible - timedelta(days=14)
    assert len(resultat["articles"]) == 1


def test_actualites_entreprise_point_in_temps_rejects_article_after_date_cible():
    """Garde-fou anti-fuite defensif : meme si la source GDELT renvoyait
    (a tort) un article posterieur a date_cible, il ne doit jamais
    atteindre le resultat -- ne jamais se fier uniquement a enddatetime
    cote API, toujours re-filtrer cote code (meme philosophie que
    ibkr_bot/odds_check.py : jamais confiance aveugle a une seule couche
    de garde-fou)."""
    def gdelt_source_avec_fuite(requete, debut, ttl, cle, fin=None):
        return {"articles": [
            {"title": "Entreprise Test annonce un partenariat", "seendate": "20260601T100000Z",
             "domain": "lemonde.fr", "url": "https://lemonde.fr/a"},
            {"title": "Entreprise Test chute en bourse", "seendate": "20260620T100000Z",
             "domain": "lemonde.fr", "url": "https://lemonde.fr/b"},
        ]}

    date_cible = datetime(2026, 6, 15, tzinfo=timezone.utc)
    resultat = news.actualites_entreprise_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, fenetre_jours=14,
        gdelt_source=gdelt_source_avec_fuite)

    dates_resultat = [a["date"] for a in resultat["articles"]]
    assert "2026-06-20T10:00" not in dates_resultat
    assert len(resultat["articles"]) == 1
```

Importer `timezone` en tête du fichier de test si absent.

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/assistant_ia/test_news.py -v -k point_in_temps`
Expected: FAIL (`AttributeError: module 'assistant_ia.news' has no attribute 'actualites_entreprise_point_in_temps'`)

- [ ] **Step 3: Implémenter**

Dans `assistant_ia/news.py`, ajouter après `actualites_entreprise` (après la
ligne 159) :

```python
def actualites_entreprise_point_in_temps(
    ticker: str, nom: str, date_cible: datetime, fenetre_jours: int = 14,
    *, gdelt_source=data_sources.fetch_gdelt_articles,
) -> dict:
    """Variante point-in-temps de actualites_entreprise : actus GDELT
    bornees STRICTEMENT par date_cible (jamais apres), sur une fenetre de
    `fenetre_jours` avant elle -- utilisee par analyste_backtest.py pour
    reconstituer ce qui etait reellement connaissable a une date passee,
    jamais par les appelants de fraicheur existants. Finnhub est exclu ICI
    (contrairement a actualites_entreprise) : son offre gratuite ne
    supporte pas de recherche historique bornee par date de la meme facon
    que GDELT -- voir data_sources.fetch_finnhub_company_news, qui prend
    `debut_iso`/`fin_iso` mais dont la couverture reelle de l'offre
    gratuite se limite a l'actualite recente, non verifiable comme fiable
    pour une date d'il y a 6-12 mois.

    Pas de cache ni de TTL (contrairement a actualites_entreprise) : une
    requete point-in-temps pour (ticker, date_cible) donne doit toujours
    renvoyer le meme resultat, jamais une version perimee d'un appel
    precedent a une autre date -- l'appelant (analyste_backtest.py) gere
    son propre cache sur disque si besoin, voir sa Tache."""
    debut = date_cible - timedelta(days=fenetre_jours)
    brut = gdelt_source(f'"{nom}"', debut, 0, f"point_in_temps:{ticker}:{date_cible.isoformat()}", fin=date_cible)
    if "erreur" in brut:
        return brut
    articles = articles_gdelt(brut, nom=nom)
    # Garde-fou defensif : ne jamais se fier uniquement a enddatetime cote
    # API GDELT -- re-filtre explicitement ici, meme si Tache 1 l'a deja
    # fait cote requete (deux couches, jamais une seule).
    articles = [a for a in articles if a["date"] <= date_cible]
    return {"ticker": ticker, "articles": fusionne([articles], debut)}
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/assistant_ia/test_news.py -v`
Expected: PASS (tous les tests, anciens et nouveaux)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/news.py tests/assistant_ia/test_news.py
git commit -m "feat(assistant-ia): actualites_entreprise_point_in_temps, actus bornees strictement a une date passee

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3 : `analyste_backtest.py` — historique prix/volume tronqué + actus classées

**Files:**
- Create: `analyste_backtest.py` (racine du dépôt)
- Test: `tests/test_analyste_backtest.py` (nouveau)

**Interfaces:**
- Consumes: `assistant_ia.news.actualites_entreprise_point_in_temps` (Tâche 2) ; `indices_score.fetch_article_text`, `indices_score.summarize_news_item` (déjà existantes, réutilisées telles quelles).
- Produces: `historique_prix_volume_point_in_temps(ticker, date_cible, history_ticker=None, annees=6) -> pd.DataFrame` ; `actus_classifiees_point_in_temps(ticker, nom, date_cible) -> list[dict]` — consommées par la Tâche 4.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/test_analyste_backtest.py` :

```python
import pandas as pd
import pytest
from datetime import datetime, timezone

import analyste_backtest


class _TickerFactice:
    def __init__(self, historique):
        self._historique = historique

    def history(self, start=None, end=None):
        masque = (self._historique.index >= start) & (self._historique.index < end)
        return self._historique.loc[masque]


def test_historique_point_in_temps_never_includes_a_candle_after_date_cible():
    index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    historique = pd.DataFrame(
        {"Close": range(400), "Volume": [1000] * 400}, index=index)
    date_cible = datetime(2025, 12, 1, tzinfo=timezone.utc)

    resultat = analyste_backtest.historique_prix_volume_point_in_temps(
        "TEST.PA", date_cible, ticker_source=lambda t: _TickerFactice(historique))

    assert resultat.index.max() < date_cible


def test_historique_point_in_temps_window_is_six_years_ending_at_date_cible():
    index = pd.date_range("2018-01-01", periods=3000, freq="D", tz="UTC")
    historique = pd.DataFrame(
        {"Close": range(3000), "Volume": [1000] * 3000}, index=index)
    date_cible = datetime(2025, 12, 1, tzinfo=timezone.utc)
    bornes = {}

    def ticker_source_qui_enregistre(t):
        class Capteur(_TickerFactice):
            def history(self, start=None, end=None):
                bornes["start"], bornes["end"] = start, end
                return super().history(start=start, end=end)
        return Capteur(historique)

    analyste_backtest.historique_prix_volume_point_in_temps(
        "TEST.PA", date_cible, ticker_source=ticker_source_qui_enregistre)

    assert bornes["end"] == date_cible
    assert (date_cible - bornes["start"]).days in range(6 * 365 - 2, 6 * 365 + 2)


def test_actus_classifiees_point_in_temps_never_includes_date_after_date_cible(monkeypatch):
    def actualites_source_factice(ticker, nom, date_cible, fenetre_jours=14, **kw):
        return {"ticker": ticker, "articles": [
            {"titre": "Titre", "source": "lemonde.fr", "date": "2025-11-20T10:00",
             "resume_court": "", "url": "https://lemonde.fr/a"},
        ]}

    def fetch_article_text_factice(url):
        return "Texte de l'article"

    def summarize_news_item_factice(titre, nom, texte):
        return {"summary": "resume", "sentiment": 0.5, "importance": "notable"}

    date_cible = datetime(2025, 12, 1, tzinfo=timezone.utc)
    resultat = analyste_backtest.actus_classifiees_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible,
        actualites_source=actualites_source_factice,
        fetch_article_text=fetch_article_text_factice,
        summarize_news_item=summarize_news_item_factice)

    assert len(resultat) == 1
    assert resultat[0]["sentiment"] == 0.5
    assert resultat[0]["importance"] == "notable"
    assert resultat[0]["date"] == "2025-11-20"
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_backtest.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'analyste_backtest'`)

- [ ] **Step 3: Implémenter**

Créer `analyste_backtest.py` :

```python
# analyste_backtest.py
# "L'Analyste" -- backtest historique (6-12 mois) du raisonnement IA +
# actus deja teste en avant (voir docs/superpowers/specs/
# 2026-10-24-analyste-backtest-design.md). Reutilise DIRECTEMENT les
# fonctions de calcul de indices_score.py (classify_weinstein_stage,
# les score_*, compute_composite, recalibrate_scores_by_profile,
# compute_company_alerts, estimate_valuation_targets) -- jamais de
# reimplementation de cette logique. Ne modifie JAMAIS
# indices_score.py::fetch_company_financials/build_company_entry
# (code de production critique, sans point d'extension pour une date
# passee) : ce module reconstruit sa propre collecte de prix/volume
# tronquee et sa propre collecte d'actus point-in-temps, puis appelle
# les memes fonctions de calcul pur que la production.
import pandas as pd
import yfinance as yf
from dateutil.relativedelta import relativedelta

import assistant_ia.news as news
import indices_score


def historique_prix_volume_point_in_temps(
    ticker: str, date_cible, history_ticker: str | None = None, annees: int = 6,
    *, ticker_source=yf.Ticker,
) -> pd.DataFrame:
    """Historique prix/volume tronque STRICTEMENT a date_cible -- jamais
    une bougie posterieure, meme garde en double que Tache 2 (requete
    bornee par `end`, puis re-filtre defensif ci-dessous). Fenetre de
    `annees` ans se terminant a date_cible (6 ans par defaut, meme duree
    que indices_score.py::fetch_company_financials, mais avec start/end
    explicites plutot que period="6y" -- period est relatif a l'instant
    de l'appel, inutilisable pour une date passee).
    `history_ticker` (optionnel) : meme usage que
    PRICE_HISTORY_TICKER_OVERRIDE dans indices_score.py, pour les tickers
    dont l'historique de prix se lit sous un autre symbole."""
    cible = ticker_source(history_ticker or ticker)
    debut = date_cible - relativedelta(years=annees)
    historique = cible.history(start=debut, end=date_cible)
    # Garde-fou defensif : ne jamais se fier uniquement a `end=` cote
    # yfinance -- re-filtre explicitement ici (meme philosophie que
    # assistant_ia.news.actualites_entreprise_point_in_temps, Tache 2).
    return historique[historique.index < date_cible]


def actus_classifiees_point_in_temps(
    ticker: str, nom: str, date_cible,
    *, actualites_source=news.actualites_entreprise_point_in_temps,
    fetch_article_text=indices_score.fetch_article_text,
    summarize_news_item=indices_score.summarize_news_item,
) -> list[dict]:
    """Actus GDELT bornees point-in-temps (Tache 2), classees
    (sentiment/importance) avec les MEMES fonctions que la production
    (fetch_article_text + summarize_news_item, indices_score.py,
    ANTHROPIC_API_KEY) -- jamais fetch_news (flux RSS Google News, non
    bornable dans le temps, voir Decouverte de terrain en tete de plan).
    Renvoie la forme attendue par indices_score.score_actualite_recente :
    [{"date": "YYYY-MM-DD", "sentiment": float, "importance": str}, ...],
    utilisee aussi comme contexte actu du raisonnement LLM (Tache 6)."""
    brut = actualites_source(ticker, nom, date_cible)
    if "erreur" in brut:
        return []
    classifiees = []
    for article in brut["articles"]:
        texte = fetch_article_text(article["url"])
        classification = summarize_news_item(article["titre"], nom, texte)
        classifiees.append({
            "date": article["date"][:10],
            "sentiment": classification["sentiment"],
            "importance": classification["importance"],
            "titre": article["titre"],
            "resume": classification["summary"],
            "url": article["url"],
        })
    return classifiees
```

(`indices_score.fetch_article_text`/`summarize_news_item` sont déjà des
fonctions de module, importées tel quel — pas besoin de les redéfinir.)

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analyste_backtest.py tests/test_analyste_backtest.py
git commit -m "feat(analyste): historique prix/volume et actus classifiees point-in-temps, isolation stricte a une date passee

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4 : `analyste_backtest.py` — score composite rétroactif d'une entreprise

**Files:**
- Modify: `analyste_backtest.py`
- Test: `tests/test_analyste_backtest.py`

**Interfaces:**
- Consumes: Tâche 3 (`historique_prix_volume_point_in_temps`, `actus_classifiees_point_in_temps`) ; fonctions déjà existantes de `indices_score.py` (`classify_weinstein_stage`, `extract_ratios`, `extract_ratios_financial`, `score_rentabilite`, `score_structure_financiere`, `score_croissance`, `score_generation_cash`, `score_valorisation`, `score_rentabilite_financiere`, `score_structure_financiere_bancaire`/`_trust`, `score_croissance_financiere`, `score_generation_cash_financiere`/`_trust`, `score_valorisation_financiere`/`_trust`, `score_dynamique_recente`, `score_actualite_recente`, `compute_composite`, `estimate_valuation_targets`, `estimate_wacc`, `estimate_cost_of_equity`, `_fetch_statement_with_retry`, `FINANCIAL_SECTOR_TICKERS`, `TRUST_TICKERS`, `COST_OF_CAPITAL_PROXY`).
- Produces: `score_point_in_temps(ticker, name, date_cible, index_key, risk_free_rate=None, fx_rate_to_usd=1.0) -> dict | None` — consommée par la Tâche 5.

**Rappel exact (déjà vérifié, voir le rapport de terrain) de ce que
`fetch_company_financials`/`build_company_entry` font aujourd'hui, pour
que l'implémenteur reproduise fidèlement la même logique :**
les fondamentaux (`financials`, `balance_sheet`, `cashflow`,
`quarterly_financials`) viennent de `t.financials` etc. **tels quels,
non tronqués** (décision acceptée de la spec : fenêtre 6-12 mois, biais
fondamental négligeable). Seuls `current_price`/`ma200`/
`ecart_pct_ma200`/le stage Weinstein/`closes_by_year` viennent de
l'historique tronqué (Tâche 3). `is_financial`/`is_trust` sélectionnent
la branche de facteurs exactement comme `build_company_entry`
(`indices_score.py:6022-6072`) — 7 facteurs dans tous les cas, les deux
derniers (`score_dynamique_recente`, `score_actualite_recente`)
identiques dans les trois branches.

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à `tests/test_analyste_backtest.py` (ce test utilise de vraies
fonctions de `indices_score.py`, pas des factices, pour vérifier
l'intégration réelle — seules les sources réseau sont injectées) :

```python
def test_score_point_in_temps_builds_a_composite_with_seven_factors(monkeypatch):
    historique = pd.DataFrame({
        "Close": [100.0 + i * 0.1 for i in range(1500)],
        "Volume": [10000] * 1500,
    }, index=pd.date_range("2022-01-01", periods=1500, freq="D", tz="UTC"))

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    class StatementFactice:
        columns = []
        def __getitem__(self, item):
            raise KeyError(item)

    def fetch_statement_factice(ticker, nom_attribut):
        return StatementFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = StatementFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology")

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert resultat is not None
    assert len(resultat["factors"]) == 7
    assert "score_raw" in resultat
    assert "stage_label" in resultat
    assert "entry_price" in resultat
    assert resultat["actus"] == []
```

Ce test est volontairement minimal (fondamentaux vides, couvre le chemin
"données manquantes dégrade sans lever") — il vérifie que le pipeline
tourne de bout en bout et produit la forme attendue, pas des valeurs
précises (déjà couvert par les tests existants de `indices_score.py`
pour chaque fonction de calcul elle-même).

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_backtest.py -v -k score_point_in_temps`
Expected: FAIL (`AttributeError: module 'analyste_backtest' has no attribute 'score_point_in_temps'`)

- [ ] **Step 3: Implémenter**

Ajouter à `analyste_backtest.py` :

```python
def _weekly_series_jusqua(historique: pd.DataFrame, date_cible) -> tuple[pd.Series, pd.Series]:
    """Reproduit exactement indices_score.py:3843-3889 (dropna, resample
    hebdomadaire closes=.last() volumes=.mean()), a partir d'un historique
    deja tronque a date_cible (Tache 3) plutot que period="6y"."""
    closes = historique["Close"].dropna()
    volumes = (historique["Volume"] if "Volume" in historique.columns
               else pd.Series(dtype=float, index=historique.index)).reindex(closes.index)
    return closes.resample("W").last().dropna(), volumes.resample("W").mean()


def score_point_in_temps(
    ticker: str, name: str, date_cible, index_key: str,
    risk_free_rate: float | None = None, fx_rate_to_usd: float | None = 1.0,
    *, historique_source=historique_prix_volume_point_in_temps,
    actus_source=actus_classifiees_point_in_temps,
    fetch_statement=indices_score._fetch_statement_with_retry,
    ticker_fondamentaux_source=yf.Ticker,
) -> dict | None:
    """Score composite rétroactif d'une entreprise à `date_cible`, en
    réutilisant directement les fonctions de calcul de indices_score.py.
    Renvoie None si le prix courant à date_cible n'est pas calculable
    (historique insuffisant) -- jamais d'exception, même philosophie que
    main()'s try/except par entreprise."""
    historique = historique_source(ticker, date_cible)
    if historique.empty:
        return None
    current_price = float(historique["Close"].dropna().iloc[-1]) if not historique["Close"].dropna().empty else None
    if current_price is None:
        return None

    weekly_closes, weekly_volumes = _weekly_series_jusqua(historique, date_cible)
    weinstein = indices_score.classify_weinstein_stage(weekly_closes, weekly_volumes)

    ma200_serie = historique["Close"].dropna().rolling(200).mean()
    ma200 = float(ma200_serie.iloc[-1]) if len(ma200_serie) >= 200 and not pd.isna(ma200_serie.iloc[-1]) else None
    ecart_pct_ma200 = ((current_price - ma200) / ma200 * 100) if ma200 else None

    financials = fetch_statement(ticker, "financials")
    balance_sheet = fetch_statement(ticker, "balance_sheet")
    cashflow = fetch_statement(ticker, "cashflow")
    t = ticker_fondamentaux_source(ticker)
    quarterly_financials = t.quarterly_financials
    shares_outstanding = t.info.get("sharesOutstanding") or 0.0
    beta = t.info.get("beta")
    sector = t.info.get("sector")

    is_financial = ticker in indices_score.FINANCIAL_SECTOR_TICKERS
    is_trust = ticker in indices_score.TRUST_TICKERS
    closes_by_year = {}
    for col in financials.columns:
        try:
            closes_by_year[col] = float(historique["Close"].dropna().asof(col))
        except Exception:
            closes_by_year[col] = None
    if is_financial or is_trust:
        ratios = indices_score.extract_ratios_financial(
            financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
    else:
        ratios = indices_score.extract_ratios(
            financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
    ratios["is_financial"] = is_financial
    ratios["is_trust"] = is_trust
    ratios["sector"] = sector
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["shares_outstanding"] = shares_outstanding
    ratios["beta"] = beta
    ratios["stage"] = weinstein["stage"]
    ratios["stage_label"] = weinstein["stage_label"]
    try:
        ratios["quarterly_yoy_growth_ca"] = indices_score.extract_quarterly_growth(quarterly_financials)
    except KeyError:
        ratios["quarterly_yoy_growth_ca"] = None

    actus = actus_source(ticker, name, date_cible)
    market_cap = current_price * shares_outstanding if shares_outstanding else None
    wacc = indices_score.estimate_wacc(
        risk_free_rate, beta, market_cap, ratios.get("total_debt"), ratios.get("tax_rate"),
        fx_rate_to_usd, ratios.get("implied_cost_of_debt"))
    cost_of_capital = wacc if wacc is not None else indices_score.COST_OF_CAPITAL_PROXY
    cost_of_equity = indices_score.estimate_cost_of_equity(risk_free_rate, beta, market_cap, fx_rate_to_usd)
    cost_of_equity_for_roe = cost_of_equity if cost_of_equity is not None else cost_of_capital

    if is_financial:
        factors = [
            indices_score.score_rentabilite_financiere(ratios.get("roe"), cost_of_equity_for_roe),
            indices_score.score_structure_financiere_bancaire(ratios.get("leverage_ratio")),
            indices_score.score_croissance_financiere(ratios.get("cagr_ca"), ratios.get("cagr_net_income")),
            indices_score.score_generation_cash_financiere(ratios.get("cash_conversion")),
            indices_score.score_valorisation_financiere(ratios.get("current_pb"), ratios.get("roe"), cost_of_equity_for_roe),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            indices_score.score_actualite_recente(actus),
        ]
    elif is_trust:
        factors = [
            indices_score.score_rentabilite_financiere(ratios.get("roe"), cost_of_equity_for_roe),
            indices_score.score_structure_financiere_trust(ratios.get("net_debt"), ratios.get("equity")),
            indices_score.score_croissance_financiere(ratios.get("cagr_ca"), ratios.get("cagr_net_income")),
            indices_score.score_generation_cash_trust(),
            indices_score.score_valorisation_trust(ratios.get("current_pb")),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            indices_score.score_actualite_recente(actus),
        ]
    else:
        factors = [
            indices_score.score_rentabilite(ratios.get("roce"), ratios.get("roe"), cost_of_capital),
            indices_score.score_structure_financiere(ratios.get("net_debt_ebitda"), ratios.get("icr")),
            indices_score.score_croissance(ratios.get("cagr_ca"), ratios.get("cagr_ebitda")),
            indices_score.score_generation_cash(ratios.get("fcf_conversion"), data_available=ratios.get("fcf_conversion") is not None),
            indices_score.score_valorisation(ratios.get("current_ev_ebitda"), ratios.get("avg_ev_ebitda_5y"), ratios.get("cagr_ebitda")),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            indices_score.score_actualite_recente(actus),
        ]
    composite = indices_score.compute_composite(factors)
    valuation_targets = indices_score.estimate_valuation_targets(ratios, cost_of_capital, cost_of_equity, risk_free_rate)

    return {
        "ticker": ticker, "name": name, "index": index_key, "date_cible": str(date_cible)[:10],
        "score_raw": composite, "factors": [f.__dict__ for f in factors],
        "current_price": current_price, "ma200": ma200, "ecart_pct_ma200": ecart_pct_ma200,
        "stage": weinstein["stage"], "stage_label": weinstein["stage_label"],
        "volume_confirme": weinstein["volume_confirme"], "sector": sector,
        "is_financial": is_financial, "is_trust": is_trust,
        "fair_value": valuation_targets["fair_value"], "entry_price": valuation_targets["entry_price"],
        "exit_price": valuation_targets["exit_price"], "actus": actus,
    }
```

(`"actus": actus` : la même liste déjà calculée ci-dessus pour
`score_actualite_recente` — embarquée dans le résultat pour que la Tâche 6
réutilise le contexte actu du prompt LLM sans refetcher GDELT une seconde
fois pour le même (ticker, date_cible).)

(Note pour l'implémenteur : certaines clés `ratios.get(...)` ci-dessus
— `total_debt`, `tax_rate`, `implied_cost_of_debt`, `roe`, `leverage_ratio`,
`cagr_net_income`, `cash_conversion`, `current_pb`, `net_debt`, `equity`,
`roce`, `net_debt_ebitda`, `icr`, `cagr_ebitda`, `fcf_conversion`,
`current_ev_ebitda`, `avg_ev_ebitda_5y` — viennent de `extract_ratios`/
`extract_ratios_financial`, déjà appelées ci-dessus : lire ces deux
fonctions, `indices_score.py:2893` et `3247`, pour confirmer les noms de
clés exacts qu'elles posent sur le dict `ratios` qu'elles renvoient, et
corriger ce brief si un nom diffère légèrement — ne jamais deviner un nom
de clé, la fonction source fait foi.)

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analyste_backtest.py tests/test_analyste_backtest.py
git commit -m "feat(analyste): score_point_in_temps, recalcule le score composite rétroactif en réutilisant la logique de scoring existante

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5 : `analyste_backtest.py` — échantillonnage de 30-50 signaux "entrée"

**Files:**
- Modify: `analyste_backtest.py`
- Test: `tests/test_analyste_backtest.py`

**Interfaces:**
- Consumes: Tâche 4 (`score_point_in_temps`) ; `indices_score.recalibrate_scores_by_profile`, `indices_score.compute_company_alerts` (déjà existantes).
- Produces: `genere_signaux_historiques(companies, risk_free_rate_by_currency, fx_rate_by_currency, graine=20261024) -> list[dict]` — le point d'entrée principal de ce script, consommé par la Tâche 6 (fichier de sortie du Composant 1+2).

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à `tests/test_analyste_backtest.py` :

```python
def test_genere_signaux_historiques_stops_once_target_sample_is_reached(monkeypatch):
    compteur = {"appels": 0}

    def score_point_in_temps_factice(ticker, name, date_cible, index_key, **kw):
        compteur["appels"] += 1
        # Un signal sur deux declenche "entree" (composite au-dessus du
        # seuil, pres du prix d'entree) ; l'autre, non.
        favorable = compteur["appels"] % 2 == 0
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10],
            "score_raw": 30.0 if favorable else -30.0,
            "stage_label": "Achat" if favorable else "Neutre",
            "current_price": 100.0, "entry_price": 99.0 if favorable else 50.0,
            "exit_price": 150.0, "factors": [], "score": 30.0 if favorable else -30.0,
        }

    companies = [{"ticker": f"T{i}.PA", "name": f"Entreprise {i}", "index": "CAC40"} for i in range(200)]

    resultats = analyste_backtest.genere_signaux_historiques(
        companies, {}, {}, cible_echantillon=5, max_tentatives=100,
        score_source=score_point_in_temps_factice, graine=42)

    assert 5 <= len(resultats) <= 5
    assert compteur["appels"] <= 100
    assert all(r["ticker"] for r in resultats)


def test_genere_signaux_historiques_respects_the_fixed_seed_for_reproducibility():
    def score_point_in_temps_factice(ticker, name, date_cible, index_key, **kw):
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10], "score_raw": 30.0,
            "stage_label": "Achat", "current_price": 100.0, "entry_price": 99.0,
            "exit_price": 150.0, "factors": [], "score": 30.0,
        }

    companies = [{"ticker": f"T{i}.PA", "name": f"Entreprise {i}", "index": "CAC40"} for i in range(50)]

    resultats_1 = analyste_backtest.genere_signaux_historiques(
        companies, {}, {}, cible_echantillon=3, max_tentatives=50,
        score_source=score_point_in_temps_factice, graine=20261024)
    resultats_2 = analyste_backtest.genere_signaux_historiques(
        companies, {}, {}, cible_echantillon=3, max_tentatives=50,
        score_source=score_point_in_temps_factice, graine=20261024)

    assert [r["ticker"] for r in resultats_1] == [r["ticker"] for r in resultats_2]
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_backtest.py -v -k genere_signaux_historiques`
Expected: FAIL (`AttributeError`)

- [ ] **Step 3: Implémenter**

Ajouter à `analyste_backtest.py` (en tête du fichier, après les imports
existants) :

```python
import random
from datetime import timedelta

FENETRE_JOURS_MIN = 180
FENETRE_JOURS_MAX = 365
ECHANTILLON_CIBLE = 40  # milieu de la fourchette 30-50 de la spec
MAX_TENTATIVES_ECHANTILLONNAGE = 2000
GRAINE_ALEATOIRE = 20261024
```

Puis, en fin de fichier :

```python
def _tire_date_cible(generateur: random.Random, aujourdhui) -> object:
    """Date aleatoire entre FENETRE_JOURS_MIN et FENETRE_JOURS_MAX avant
    aujourdhui -- jamais au-dela (fenetre validee avec l'utilisateur)."""
    jours_avant = generateur.randint(FENETRE_JOURS_MIN, FENETRE_JOURS_MAX)
    return aujourdhui - timedelta(days=jours_avant)


def genere_signaux_historiques(
    companies: list[dict], risk_free_rate_by_currency: dict, fx_rate_by_currency: dict,
    cible_echantillon: int = ECHANTILLON_CIBLE, max_tentatives: int = MAX_TENTATIVES_ECHANTILLONNAGE,
    graine: int = GRAINE_ALEATOIRE, *, score_source=score_point_in_temps, aujourdhui=None,
) -> list[dict]:
    """Tire au hasard des (entreprise, date_cible) jusqu'a obtenir
    `cible_echantillon` signaux "entree" confirmes (meme condition que
    l'alerte live, compute_company_alerts), ou jusqu'a `max_tentatives`
    essais -- jamais un balayage exhaustif de toutes les dates x tous les
    tickers, trop couteux en appels yfinance (voir spec, option "pilote
    resserre"). `aujourdhui` injectable pour les tests (defaut : date
    reelle du jour)."""
    import datetime as _dt
    aujourdhui = aujourdhui or _dt.datetime.now(_dt.timezone.utc)
    generateur = random.Random(graine)
    retenus: list[dict] = []
    for _ in range(max_tentatives):
        if len(retenus) >= cible_echantillon:
            break
        company = generateur.choice(companies)
        date_cible = _tire_date_cible(generateur, aujourdhui)
        resultat = score_source(
            company["ticker"], company["name"], date_cible, company["index"],
            risk_free_rate=risk_free_rate_by_currency.get(company["index"]),
            fx_rate_to_usd=fx_rate_by_currency.get(company["index"], 1.0),
        )
        if resultat is None:
            continue
        resultat["score"] = resultat["score_raw"]  # pas de recalibration par pool ici (voir note)
        alerts = indices_score.compute_company_alerts(
            resultat["ticker"], resultat["score_raw"], resultat["current_price"],
            resultat["entry_price"], [], news_items=None, stage_label=resultat["stage_label"],
            score_recalibrated=False, score=resultat["score"],
        )
        if any(a["kind"] == "entree" for a in alerts):
            retenus.append(resultat)
    return retenus
```

**Note pour l'implémenteur, à documenter dans le commit** :
`recalibrate_scores_by_profile` compare le score d'une entreprise au pool
de TOUTES les entreprises du même profil **scorées la même date** — ici,
chaque tirage porte sur une date différente, il n'y a jamais de vrai pool
comparable d'un coup. Ce plan accepte cette simplification (score brut
non recalibré, `score_recalibrated=False` passé à `compute_company_alerts`,
qui désactive alors le filtre de percentile et ne garde que
`score_favorable`/`near_entry`/`stage_label` — voir la docstring de
`compute_company_alerts`) plutôt que de reconstruire un pool historique
complet pour chaque date tirée, hors du périmètre validé avec
l'utilisateur (fenêtre réduite, pas une refonte de la méthodologie de
recalibration). Documenter ce choix dans le message de commit, pas
seulement dans le code.

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_backtest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analyste_backtest.py tests/test_analyste_backtest.py
git commit -m "feat(analyste): genere_signaux_historiques, tirage aleatoire reproductible jusqu'a 30-50 signaux entree confirmes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6 : `analyste_replay.py` — raisonnement Gemini point-in-temps

**Files:**
- Create: `analyste_replay.py` (racine du dépôt)
- Test: `tests/test_analyste_replay.py` (nouveau)

**Interfaces:**
- Consumes: la forme produite par la Tâche 5 (`genere_signaux_historiques`) ; `analyste_backtest.actus_classifiees_point_in_temps` (Tâche 3, réutilisée pour le contexte actu du prompt — déjà calculée une fois par `score_point_in_temps`, Tâche 4 ; ce composant la relit depuis le signal plutôt que de la refetcher).
- Produces: `construit_prompt(signal: dict, actus: list[dict]) -> str` ; `appelle_gemini(prompt: str, *, cle_api=None, modele="gemini-2.5-flash") -> dict` — consommées par la Tâche 7.

**Rappel (déjà câblé en Tâche 4, pas à refaire ici)** : `score_point_in_temps`
embarque déjà `resultat["actus"]` (la liste classifiée calculée pour
`score_actualite_recente`) dans son dict de retour — `genere_signaux_historiques`
(Tâche 5) la transmet telle quelle puisqu'elle renvoie `resultat` sans le
modifier. Cette tâche réutilise donc `signal["actus"]` directement, sans
jamais refetcher GDELT une seconde fois pour le même (ticker, date_cible).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/test_analyste_replay.py` :

```python
import json
import pytest

import analyste_replay


def _signal_exemple():
    return {
        "ticker": "TEST.PA", "name": "Entreprise Test", "index": "CAC40",
        "date_cible": "2026-04-01", "score_raw": 25.0, "score": 25.0,
        "stage_label": "Achat", "current_price": 100.0, "entry_price": 98.0,
        "exit_price": 150.0, "sector": "Technology",
        "actus": [{"date": "2026-03-28", "sentiment": 0.6, "importance": "notable",
                   "titre": "Entreprise Test annonce un contrat majeur", "resume": "...",
                   "url": "https://lemonde.fr/a"}],
    }


def test_construit_prompt_includes_anti_leakage_instruction_and_date_cible():
    prompt = analyste_replay.construit_prompt(_signal_exemple())
    assert "2026-04-01" in prompt
    assert "ne raisonne qu" in prompt.lower() or "n'utilise jamais" in prompt.lower()
    assert "Entreprise Test" in prompt


def test_construit_prompt_never_mentions_a_date_after_date_cible():
    signal = _signal_exemple()
    signal["actus"].append({
        "date": "2026-05-01", "sentiment": -0.8, "importance": "majeure",
        "titre": "Entreprise Test en difficulte", "resume": "...", "url": "https://lemonde.fr/b"})
    prompt = analyste_replay.construit_prompt(signal)
    assert "2026-05-01" not in prompt


def test_appelle_gemini_retries_on_429(monkeypatch):
    appels = {"n": 0}

    def client_factice(prompt, cle_api, modele):
        appels["n"] += 1
        if appels["n"] < 3:
            raise analyste_replay.ErreurLimiteDebit("quota depasse")
        return {"appel": "neutre", "confiance": "moyenne", "raison": "..."}

    resultat = analyste_replay.appelle_gemini(
        "prompt test", cle_api="fausse-cle", client=client_factice,
        sleep_fn=lambda s: None)

    assert appels["n"] == 3
    assert resultat["appel"] == "neutre"
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_replay.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'analyste_replay'`)

- [ ] **Step 3: Implémenter**

Créer `analyste_replay.py` :

```python
# analyste_replay.py
# Harnais de replay Gemini pour "l'Analyste" -- voir docs/superpowers/
# specs/2026-10-24-analyste-backtest-design.md. Consomme les signaux
# produits par analyste_backtest.py (deja isoles point-in-temps), construit
# le meme raisonnement a 4 dimensions que le test prospectif, avec une
# instruction anti-fuite explicite, et ecrit les resultats resolus
# immediatement (Tache 7).
import json
import os
import time

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent"


class ErreurLimiteDebit(Exception):
    """Leve par `client` sur un 429 Gemini -- distincte d'une erreur
    reseau quelconque pour que appelle_gemini sache quand reessayer."""


def _appel_http_gemini(prompt: str, cle_api: str, modele: str, *, http_post=None) -> dict:
    """Implementation reelle par defaut de `client` (appelle_gemini) :
    POST direct a l'API Gemini (meme endpoint verifie manuellement en
    session lors de la validation de la cle -- generativelanguage.
    googleapis.com/v1beta/models/{modele}:generateContent). Demande une
    reponse JSON structuree via responseMimeType (pas de parsing fragile
    d'un texte libre). Leve ErreurLimiteDebit sur un 429, toute autre
    erreur HTTP remonte telle quelle (pas de degrade silencieux -- une
    reponse de pari manquante doit etre visible, jamais remplacee par une
    valeur par defaut qui fausserait le backtest)."""
    import requests as _requests
    http_post = http_post or _requests.post
    url = GEMINI_API_URL.format(modele=modele)
    corps = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    reponse = http_post(url, params={"key": cle_api}, json=corps, timeout=30)
    if reponse.status_code == 429:
        raise ErreurLimiteDebit(reponse.text[:200])
    reponse.raise_for_status()
    texte = reponse.json()["candidates"][0]["content"]["parts"][0]["text"]
    import json as _json
    return _json.loads(texte)


def construit_prompt(signal: dict) -> str:
    """Meme raisonnement a 4 dimensions (actualite, technique, secteur,
    volume) que docs/superpowers/experiments/2026-10-19-ia-news-reasoning-
    test.json, avec une instruction anti-fuite explicite -- jamais de
    connaissance generale d'evenements posterieurs a date_cible. N'inclut
    QUE les actus de `signal["actus"]` (deja bornees point-in-temps par
    analyste_backtest.py, Taches 2-3) -- ne jamais ajouter de contexte non
    fourni explicitement ici."""
    date_cible = signal["date_cible"]
    actus_texte = "\n".join(
        f"- {a['date']} ({a['importance']}) : {a['titre']}" for a in signal["actus"]
    ) or "(aucune actualite recente exploitable)"
    return (
        f"Tu es un analyste financier. Nous sommes le {date_cible}. "
        f"Ne raisonne qu'a partir des donnees fournies ci-dessous. "
        f"N'utilise JAMAIS ta connaissance generale d'evenements posterieurs "
        f"au {date_cible} -- si tu en as une, ignore-la completement.\n\n"
        f"Entreprise : {signal['name']} ({signal['ticker']}, secteur {signal.get('sector', '?')})\n"
        f"Phase technique (Weinstein) : {signal['stage_label']}\n"
        f"Score fondamental composite : {signal['score']:.1f}\n"
        f"Prix actuel : {signal['current_price']:.2f}, repere d'entree : {signal['entry_price']:.2f}\n"
        f"Actualites recentes (bornees au {date_cible}) :\n{actus_texte}\n\n"
        f"En raisonnant sur ces 4 dimensions (actualite, technique, secteur, volume), "
        f"donne un appel (haussier / neutre / baissier), un niveau de confiance "
        f"(tres_faible / faible / moyenne / elevee), et la dimension dominante de "
        f"ta decision, en JSON : "
        f'{{"appel": "...", "confiance": "...", "dimension_dominante": "...", "raison": "..."}}'
    )


def appelle_gemini(
    prompt: str, *, cle_api: str | None = None, modele: str = "gemini-2.5-flash",
    client=_appel_http_gemini, max_tentatives: int = 5, sleep_fn=time.sleep,
) -> dict:
    """Appelle l'API Gemini avec backoff simple sur ErreurLimiteDebit
    (niveau gratuit : 5 requetes/minute). `client` injectable pour les
    tests (defaut : _appel_http_gemini, l'appel HTTP reel). Lit la cle
    depuis GEMINI_API_KEY si `cle_api` est None."""
    cle_api = cle_api or os.environ.get("GEMINI_API_KEY")
    derniere_erreur = None
    for tentative in range(max_tentatives):
        try:
            return client(prompt, cle_api, modele)
        except ErreurLimiteDebit as e:
            derniere_erreur = e
            if tentative < max_tentatives - 1:
                sleep_fn(60)  # niveau gratuit : 5 req/min, on attend une minute pleine
    raise derniere_erreur
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analyste_replay.py tests/test_analyste_replay.py
git commit -m "feat(analyste): construit_prompt et appelle_gemini, raisonnement a 4 dimensions avec instruction anti-fuite

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7 : `analyste_replay.py` — résolution immédiate et écriture du fichier d'expérience

**Files:**
- Modify: `analyste_replay.py`
- Test: `tests/test_analyste_replay.py`

**Interfaces:**
- Consumes: Tâche 6 (`appelle_gemini`) ; `yfinance` (déjà une dépendance) pour le prix réalisé ~1 mois après `date_cible` et le niveau de l'indice à la même date.
- Produces: `resout_pari(signal: dict, pari: dict, *, prix_source=...) -> dict` ; `ecrit_resultats(paris_resolus: list[dict], chemin: str) -> None` — point de sortie final de ce chantier.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `tests/test_analyste_replay.py` :

```python
def test_resout_pari_computes_return_and_alpha_vs_index():
    signal = _signal_exemple()
    pari = {"appel": "haussier", "confiance": "moyenne", "dimension_dominante": "technique", "raison": "..."}

    def prix_source_factice(ticker_ou_indice, date):
        valeurs = {
            ("TEST.PA", "2026-04-01"): 100.0, ("TEST.PA", "2026-05-01"): 110.0,
            ("^FCHI", "2026-04-01"): 8000.0, ("^FCHI", "2026-05-01"): 8080.0,
        }
        return valeurs[(ticker_ou_indice, date)]

    resultat = analyste_replay.resout_pari(
        signal, pari, index_yfinance_ticker="^FCHI", prix_source=prix_source_factice)

    assert resultat["return_pct"] == pytest.approx(10.0)
    assert resultat["index_return_pct"] == pytest.approx(1.0)
    assert resultat["methode"] == "backtest_retroactif"


def test_ecrit_resultats_writes_valid_json(tmp_path):
    chemin = tmp_path / "resultats.json"
    paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": 10.0}]

    analyste_replay.ecrit_resultats(paris, str(chemin))

    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    assert contenu["paris"][0]["ticker"] == "TEST.PA"
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_replay.py -v -k "resout_pari or ecrit_resultats"`
Expected: FAIL (`AttributeError`)

- [ ] **Step 3: Implémenter**

Ajouter à `analyste_replay.py` :

```python
HORIZON_RESOLUTION_JOURS = 30  # ~1 mois, meme ordre de grandeur que le test prospectif

INDEX_YFINANCE_TICKER = {
    "CAC40": "^FCHI", "DAX": "^GDAXI", "NASDAQ": "^IXIC", "DOW": "^DJI",
    "FTSE": "^FTSE", "SMI": "^SSMI", "IBEX35": "^IBEX", "FTSEMIB": "FTSEMIB.MI",
}


def _prix_a_date(ticker: str, date_str: str) -> float:
    import yfinance as yf
    historique = yf.Ticker(ticker).history(start=date_str, end=None)
    return float(historique["Close"].iloc[0])


def resout_pari(
    signal: dict, pari: dict, *, index_yfinance_ticker: str | None = None,
    prix_source=_prix_a_date,
) -> dict:
    """Resolution IMMEDIATE (le prix ~1 mois apres date_cible est deja
    connu aujourd'hui, contrairement au test prospectif qui doit attendre
    que le temps passe reellement). `index_yfinance_ticker` : resolu via
    INDEX_YFINANCE_TICKER[signal["index"]] si non fourni."""
    import datetime as _dt
    index_ticker = index_yfinance_ticker or INDEX_YFINANCE_TICKER[signal["index"]]
    date_cible = signal["date_cible"]
    date_resolution = (
        _dt.datetime.strptime(date_cible, "%Y-%m-%d") + _dt.timedelta(days=HORIZON_RESOLUTION_JOURS)
    ).strftime("%Y-%m-%d")

    prix_entree = prix_source(signal["ticker"], date_cible)
    prix_sortie = prix_source(signal["ticker"], date_resolution)
    index_entree = prix_source(index_ticker, date_cible)
    index_sortie = prix_source(index_ticker, date_resolution)

    return_pct = (prix_sortie - prix_entree) / prix_entree * 100
    index_return_pct = (index_sortie - index_entree) / index_entree * 100

    return {
        **signal, **pari, "methode": "backtest_retroactif",
        "date_resolution": date_resolution,
        "return_pct": return_pct, "index_return_pct": index_return_pct,
        "alpha_pct": return_pct - index_return_pct,
    }


def ecrit_resultats(paris_resolus: list[dict], chemin: str) -> None:
    import json
    import os
    dirname = os.path.dirname(chemin)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as fh:
        json.dump({"methode": "backtest_retroactif", "paris": paris_resolus}, fh, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add analyste_replay.py tests/test_analyste_replay.py
git commit -m "feat(analyste): resolution immediate du rendement realise et ecriture du fichier d'experience

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8 : orchestration bout-en-bout et exécution réelle

**Files:**
- Modify: `analyste_backtest.py` (ajoute `score_point_in_temps`'s `actus` au résultat, si pas déjà fait en Tâche 6 — vérifier le code réel avant de commencer)
- Modify: `analyste_replay.py` (ajoute un point d'entrée `main()`)
- Test: `tests/test_analyste_replay.py`

**Interfaces:**
- Consumes: toutes les tâches précédentes.
- Produces: point d'entrée exécutable `python -m analyste_replay` (ou `python analyste_replay.py`) qui lance le pipeline complet.

- [ ] **Step 1: Écrire le test qui échoue**

```python
def test_main_wires_sampling_replay_and_resolution_together(monkeypatch):
    signal_exemple = _signal_exemple()
    monkeypatch.setattr(
        analyste_replay.analyste_backtest, "genere_signaux_historiques",
        lambda *a, **k: [signal_exemple])
    monkeypatch.setattr(
        analyste_replay, "appelle_gemini",
        lambda prompt, **k: {"appel": "haussier", "confiance": "moyenne",
                             "dimension_dominante": "technique", "raison": "..."})
    monkeypatch.setattr(
        analyste_replay, "resout_pari",
        lambda signal, pari, **k: {**signal, **pari, "return_pct": 5.0})
    chemins_ecrits = []
    monkeypatch.setattr(
        analyste_replay, "ecrit_resultats",
        lambda paris, chemin: chemins_ecrits.append((paris, chemin)))

    analyste_replay.main(chemin_sortie="sortie-test.json")

    assert len(chemins_ecrits) == 1
    assert chemins_ecrits[0][0][0]["return_pct"] == 5.0
```

- [ ] **Step 2: Run pour vérifier l'échec**

Run: `pytest tests/test_analyste_replay.py -v -k test_main`
Expected: FAIL (`AttributeError: module 'analyste_replay' has no attribute 'main'`)

- [ ] **Step 3: Implémenter**

Ajouter à `analyste_replay.py` (en tête du fichier, avec les imports) :

```python
import analyste_backtest
import indices_score
```

Puis, en fin de fichier :

```python
def main(chemin_sortie: str = "docs/superpowers/experiments/2026-10-24-analyste-backtest-6-12-mois.json") -> None:
    """Point d'entree bout-en-bout : echantillonnage (Tache 5) -> pour
    chaque signal, prompt + appel Gemini (Tache 6) -> resolution
    immediate (Tache 7) -> ecriture. Lance une fois, manuellement --
    pas de timer/cron (chantier ponctuel, pas un service)."""
    risk_free_rate_by_currency = {
        currency: indices_score.fetch_risk_free_rate(series_id)
        for currency, series_id in indices_score.RISK_FREE_SERIES_BY_CURRENCY.items()
    }
    fx_rate_by_currency = {
        currency: indices_score.fetch_fx_rate_to_usd(currency)
        for currency in set(indices_score.INDEX_CURRENCY.values())
    }
    signaux = analyste_backtest.genere_signaux_historiques(
        indices_score.COMPANIES, risk_free_rate_by_currency, fx_rate_by_currency)

    paris_resolus = []
    for signal in signaux:
        prompt = construit_prompt(signal)
        pari = appelle_gemini(prompt)
        paris_resolus.append(resout_pari(signal, pari))

    ecrit_resultats(paris_resolus, chemin_sortie)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run pour vérifier le succès**

Run: `pytest tests/test_analyste_replay.py -v`
Expected: PASS

- [ ] **Step 5: Run la suite complète**

Run: `pytest tests/ -v`
Expected: PASS intégralement, y compris `tests/test_indices_score.py`,
`tests/assistant_ia/`, `tests/gold_bot/`, `tests/ibkr_bot/` (ce chantier ne
modifie que `assistant_ia/data_sources.py`/`news.py`, deux extensions
additives — aucune autre régression attendue).

- [ ] **Step 6: Commit**

```bash
git add analyste_replay.py tests/test_analyste_replay.py
git commit -m "feat(analyste): point d'entree bout-en-bout du backtest historique

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

**Note finale, hors plan d'implémentation** : avant la toute première
exécution réelle de `python analyste_replay.py` (après que toutes les
tâches soient vertes), vérifier manuellement le rythme d'appel face au
niveau gratuit Gemini (5 requêtes/minute) sur un tout petit échantillon
(`cible_echantillon=2`) avant de lancer l'échantillon complet de 30-50 —
`_appel_http_gemini` (Tâche 6) n'implémente aucun throttling proactif,
seul le retry sur 429 côté `appelle_gemini` absorbe un dépassement
ponctuel.
