# Actualités + analyse IA par commodité — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter, pour chacune des 4 commodités (Or/Argent/Pétrole/Gaz),
un 3e panneau dépliant "Actualités" sur le site, contenant une analyse IA
qui croise les facteurs du score composite avec les actualités récentes,
et la liste de ces actualités.

**Architecture:** Nouveau module `commodity_news.py` (racine du repo) qui
importe directement depuis `indices_score.py` (jamais dupliqué) le
pipeline RSS/classification déjà utilisé pour les entreprises, et ajoute
une fonction d'orchestration partagée `build_news_and_analysis_section`
appelée à l'identique par `gold_score.py` (ajout pur, jamais de logique
existante réécrite) et `commodity_score.py`. Le frontend réutilise les
classes CSS déjà existantes (`.news-item`, `.ai-analysis`) de la fiche
entreprise.

**Tech Stack:** Python 3, pytest, `requests` (déjà une dépendance), JS
vanilla (`docs/index.html`).

**Spec:** `docs/superpowers/specs/2026-10-25-commodites-actus-design.md`

## Global Constraints

- **Ne jamais modifier la logique existante de `gold_score.py`** — uniquement
  des ajouts (nouvel import, nouvelles lignes dans `main()`, nouvelles clés
  dans `payload`). Si un bug bloquant y est découvert, s'arrêter et
  documenter plutôt que de corriger silencieusement.
- **Ne jamais modifier `indices_score.py`** — uniquement importé depuis
  (`NEWS_RSS_URL`, `NEWS_MAX_ITEMS`, `parse_news_rss`, `fetch_article_text`,
  `ANTHROPIC_API_URL`, `NEWS_IMPORTANCE_LEVELS`), jamais dupliqué ni réécrit.
- **Ne jamais toucher à `gold_bot/`, `deploy/gold-bot-*.service`.**
- **Ne jamais toucher à `#portefeuilleTabOr`** ni au suivi de performance
  des signaux "entrée" de l'onglet Indices (fonctionnalités séparées).
- Modèles Anthropic exacts (copiés verbatim d'`indices_score.py`, ne pas
  en inventer d'autres) : `claude-haiku-4-5-20251001` pour la
  classification d'actu, `claude-opus-5` (thinking adaptatif, effort
  élevé) pour l'analyse.
- Secret GH Actions déjà configuré, ne pas en créer un nouveau :
  `secrets.CLAUDE_API_KEY` → env `ANTHROPIC_API_KEY`.
- Requêtes RSS Google News par commodité, EXACTES (disambiguées, ne pas
  utiliser un mot seul type "or"/"argent"/"gaz") :
  - Or : `cours de l'or`, libellé sujet `l'or`
  - Argent : `cours de l'argent métal`, libellé sujet `l'argent (métal)`
  - Pétrole : `prix du pétrole WTI`, libellé sujet `le pétrole WTI`
  - Gaz : `prix du gaz naturel`, libellé sujet `le gaz naturel`
- L'appel réseau (RSS + API Anthropic) ne doit avoir lieu que sur un run
  quotidien (`daily_snapshot=True`) — jamais à chaque run horaire. Sur un
  run intrajournalier, `news`/`analysis_html`/`news_classifications` sont
  repris tels quels du payload précédent, sans aucun appel réseau.
- Carry-forward sur échec de génération : si `generate_commodity_analysis`
  renvoie `None`, garder l'`analysis_html` précédent plutôt que de le
  perdre.
- Tests : toute fonction de calcul/formatage pur doit avoir un test
  unitaire avec des données injectées (mock de `requests.get`/
  `requests.post`, jamais d'appel réseau réel dans les tests) — TDD,
  écrire le test avant le code à chaque étape.
- `news_classifications` n'est jamais affiché côté site (clé interne de
  cache, comme pour les entreprises) — seulement `news`/`analysis_html`.

---

### Task 1 : `commodity_news.py` — pipeline actus + analyse, fonction d'orchestration partagée

**Files:**
- Create: `commodity_news.py`
- Test: `tests/test_commodity_news.py`

**Interfaces:**
- Consumes (import direct depuis `indices_score.py`, existant, ne pas
  modifier) : `NEWS_RSS_URL`, `NEWS_MAX_ITEMS`, `parse_news_rss(xml_bytes) -> list[dict]`,
  `fetch_article_text(url) -> str | None`, `ANTHROPIC_API_URL`,
  `NEWS_IMPORTANCE_LEVELS`.
- Produces : `summarize_commodity_news_item(title, subject_label, article_text) -> dict`,
  `fetch_commodity_news(query, subject_label, previous_classifications=None) -> list[dict]`,
  `build_factors_summary(factors) -> str`, `build_news_summary(news) -> str`,
  `generate_commodity_analysis(subject_label, factors_summary, news_summary) -> str | None`,
  `build_news_and_analysis_section(subject_label, news_query, factors, daily_snapshot, previous_payload) -> tuple[list, str | None, dict]`
  (tous consommés par les Tâches 2 et 3).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/test_commodity_news.py` :

```python
import pytest
from unittest.mock import patch, MagicMock

from commodity_news import (
    summarize_commodity_news_item,
    fetch_commodity_news,
    build_factors_summary,
    build_news_summary,
    generate_commodity_analysis,
    build_news_and_analysis_section,
)
from gold_score import FactorResult


def _fake_anthropic_response(parsed_json_text):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"content": [{"type": "text", "text": parsed_json_text}]}
    return resp


def test_summarize_commodity_news_item_returns_neutral_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = summarize_commodity_news_item("Titre test", "l'or", "contenu")
    assert result == {"summary": "", "sentiment": 0, "importance": "mineure"}


def test_summarize_commodity_news_item_parses_valid_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"summary": "Résumé test", "sentiment": 1, "importance": "notable"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = summarize_commodity_news_item("Titre", "l'or", "contenu")
    assert result == {"summary": "Résumé test", "sentiment": 1, "importance": "notable"}


def test_summarize_commodity_news_item_falls_back_to_mineure_on_bad_importance(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"summary": "x", "sentiment": 0, "importance": "catastrophique"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = summarize_commodity_news_item("Titre", "l'or", "contenu")
    assert result["importance"] == "mineure"


def test_summarize_commodity_news_item_never_raises_on_network_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    with patch("commodity_news.requests.post", side_effect=Exception("network down")):
        result = summarize_commodity_news_item("Titre", "l'or", None)
    assert result == {"summary": "", "sentiment": 0, "importance": "mineure"}


def test_fetch_commodity_news_reuses_cached_classification(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rss_resp = MagicMock()
    rss_resp.raise_for_status.return_value = None
    rss_resp.content = b"<rss></rss>"
    parsed_items = [{"title": "T1", "date": "2026-10-25", "link": "https://x/1", "source": "S"}]
    previous = {"https://x/1": {"summary": "déjà vu", "sentiment": 1, "importance": "notable"}}
    with patch("commodity_news.requests.get", return_value=rss_resp), \
         patch("commodity_news.parse_news_rss", return_value=parsed_items), \
         patch("commodity_news.summarize_commodity_news_item") as mock_summarize:
        result = fetch_commodity_news("cours de l'or", "l'or", previous)
    mock_summarize.assert_not_called()
    assert result[0]["summary"] == "déjà vu"
    assert result[0]["sentiment"] == 1
    assert result[0]["importance"] == "notable"


def test_fetch_commodity_news_classifies_new_article(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    rss_resp = MagicMock()
    rss_resp.raise_for_status.return_value = None
    rss_resp.content = b"<rss></rss>"
    parsed_items = [{"title": "T2", "date": "2026-10-25", "link": "https://x/2", "source": "S"}]
    with patch("commodity_news.requests.get", return_value=rss_resp), \
         patch("commodity_news.parse_news_rss", return_value=parsed_items), \
         patch("commodity_news.fetch_article_text", return_value="texte"), \
         patch("commodity_news.summarize_commodity_news_item",
               return_value={"summary": "nouveau", "sentiment": -1, "importance": "majeure"}) as mock_summarize:
        result = fetch_commodity_news("cours de l'or", "l'or", {})
    mock_summarize.assert_called_once_with("T2", "l'or", "texte")
    assert result[0]["summary"] == "nouveau"
    assert result[0]["importance"] == "majeure"


def test_build_factors_summary_formats_each_factor():
    factors = [
        FactorResult("Taux réels US 10 ans", -2.0, 0.30, "2.64% (il y a ~45j : 2.55%)"),
        FactorResult("Dollar (DXY)", -10.0, 0.15, "101.81 (il y a 2 mois : 98.92)"),
    ]
    out = build_factors_summary(factors)
    assert "Taux réels US 10 ans" in out
    assert "poids 30%" in out
    assert "-10.0" in out or "-10,0" in out or "score -10.0" in out


def test_build_factors_summary_skips_malformed_factor_without_raising():
    class Broken:
        name = "Cassé"
    out = build_factors_summary([Broken()])
    assert "Aucun facteur disponible." in out or out == ""


def test_build_news_summary_formats_each_item_with_importance():
    news = [{"title": "Titre 1", "summary": "Résumé 1", "importance": "majeure"}]
    out = build_news_summary(news)
    assert "[majeure]" in out
    assert "Titre 1" in out
    assert "Résumé 1" in out


def test_build_news_summary_handles_empty_list():
    out = build_news_summary([])
    assert "Aucune actualité récente disponible." in out


def test_generate_commodity_analysis_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert generate_commodity_analysis("l'or", "facteurs", "actus") is None


def test_generate_commodity_analysis_parses_valid_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    payload = '{"analysis_html": "<h3>Contexte</h3><p>Test</p>"}'
    with patch("commodity_news.requests.post", return_value=_fake_anthropic_response(payload)):
        result = generate_commodity_analysis("l'or", "facteurs", "actus")
    assert result == "<h3>Contexte</h3><p>Test</p>"


def test_generate_commodity_analysis_returns_none_on_network_error(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    with patch("commodity_news.requests.post", side_effect=Exception("boom")):
        result = generate_commodity_analysis("l'or", "facteurs", "actus")
    assert result is None
    assert "l'or" in capsys.readouterr().out


def test_build_news_and_analysis_section_skips_network_on_non_daily_run():
    previous_payload = {
        "news": [{"title": "ancien"}],
        "analysis_html": "<p>ancienne analyse</p>",
        "news_classifications": {"https://x/1": {"summary": "s", "sentiment": 0, "importance": "mineure"}},
    }
    with patch("commodity_news.fetch_commodity_news") as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis") as mock_generate:
        news, analysis_html, classifications = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=False, previous_payload=previous_payload,
        )
    mock_fetch.assert_not_called()
    mock_generate.assert_not_called()
    assert news == previous_payload["news"]
    assert analysis_html == previous_payload["analysis_html"]
    assert classifications == previous_payload["news_classifications"]


def test_build_news_and_analysis_section_fetches_on_daily_run():
    fake_news = [{"title": "T", "link": "https://x/1", "summary": "s", "sentiment": 1, "importance": "notable"}]
    with patch("commodity_news.fetch_commodity_news", return_value=fake_news) as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis", return_value="<p>analyse</p>") as mock_generate:
        news, analysis_html, classifications = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload={},
        )
    mock_fetch.assert_called_once_with("cours de l'or", "l'or", {})
    assert news == fake_news
    assert analysis_html == "<p>analyse</p>"
    assert classifications == {"https://x/1": {"summary": "s", "sentiment": 1, "importance": "notable"}}


def test_build_news_and_analysis_section_carries_forward_analysis_on_generation_failure():
    fake_news = [{"title": "T", "link": "https://x/1", "summary": "s", "sentiment": 0, "importance": "mineure"}]
    previous_payload = {"analysis_html": "<p>ancienne analyse valide</p>"}
    with patch("commodity_news.fetch_commodity_news", return_value=fake_news), \
         patch("commodity_news.generate_commodity_analysis", return_value=None):
        _, analysis_html, _ = build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload=previous_payload,
        )
    assert analysis_html == "<p>ancienne analyse valide</p>"


def test_build_news_and_analysis_section_passes_previous_classifications_to_fetch():
    previous_payload = {"news_classifications": {"https://x/1": {"summary": "s", "sentiment": 0, "importance": "mineure"}}}
    with patch("commodity_news.fetch_commodity_news", return_value=[]) as mock_fetch, \
         patch("commodity_news.generate_commodity_analysis", return_value=None):
        build_news_and_analysis_section(
            "l'or", "cours de l'or", [], daily_snapshot=True, previous_payload=previous_payload,
        )
    mock_fetch.assert_called_once_with("cours de l'or", "l'or", previous_payload["news_classifications"])
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_commodity_news.py -v`
Expected: `ModuleNotFoundError: No module named 'commodity_news'` (le fichier
n'existe pas encore).

- [ ] **Step 3 : Créer `commodity_news.py`**

```python
"""
Actualités + analyse IA par commodité
=======================================

Étend le pipeline actualités/analyse déjà utilisé pour les entreprises de
l'onglet Indices (indices_score.py : fetch_news/summarize_news_item/
generate_financial_analysis) aux commodités (Or/Argent/Pétrole/Gaz), qui
n'ont ni comptes trimestriels ni ratios -- l'analyse ici croise les
facteurs du score composite (déjà calculés par gold_score.py/
commodity_score.py) avec les actualités récentes pour expliquer
l'évolution du score, demande explicite de l'utilisateur.

build_news_and_analysis_section() est la fonction d'orchestration
partagée appelée à l'identique par gold_score.py et commodity_score.py :
elle seule décide si un appel réseau a lieu (gate daily_snapshot) et gère
le carry-forward sur échec, pour ne jamais dupliquer cette logique.

Voir docs/superpowers/specs/2026-10-25-commodites-actus-design.md.
"""

import json
import os

import requests

from indices_score import (  # noqa: F401 — NEWS_MAX_ITEMS réexporté, utilisé indirectement via parse_news_rss
    NEWS_RSS_URL,
    NEWS_MAX_ITEMS,
    parse_news_rss,
    fetch_article_text,
    ANTHROPIC_API_URL,
    NEWS_IMPORTANCE_LEVELS,
)

ANTHROPIC_MODEL_NEWS = "claude-haiku-4-5-20251001"
ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"

COMMODITY_ANALYSIS_SYSTEM_PROMPT = """Tu es un analyste macro qui explique \
l'évolution récente du prix d'une matière première à partir d'un score \
composite déjà calculé (facteurs macro-fondamentaux pondérés) et des \
actualités récentes. Rédige une analyse structurée en français, factuelle, \
sans conseil d'investissement ni recommandation d'achat/vente, à partir des \
seules données fournies."""


# ---------------------------------------------------------------------------
# 1. Classification d'une actu (modèle léger) — même contrat que
#    indices_score.summarize_news_item, prompt adapté à une commodité.
# ---------------------------------------------------------------------------

def summarize_commodity_news_item(title: str, subject_label: str, article_text: str | None) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "", "sentiment": 0, "importance": "mineure"}

    if article_text:
        prompt = (
            f"Voici un article de presse concernant {subject_label}, "
            f"titré « {title} ».\n\nContenu de l'article :\n{article_text}\n\n"
            "En 1 à 2 phrases en français, résume le contexte et l'enjeu "
            f"principal de cet article pour le prix de {subject_label}. Sois "
            "factuel et neutre, sans donner de conseil d'investissement."
        )
    else:
        prompt = (
            f"Voici uniquement le titre d'un article de presse concernant "
            f"{subject_label} : « {title} ».\n\n"
            "Le contenu de l'article n'est pas disponible. En 1 phrase en "
            "français, propose un contexte prudent et hypothétique à partir "
            "de ce titre seul (formule-le comme une supposition, par exemple "
            "« Cet article suggère que... », sans jamais affirmer de faits "
            "que le titre seul ne permet pas de confirmer)."
        )

    prompt += (
        "\n\nÉvalue aussi l'importance de cette actu pour le prix de "
        f"{subject_label}, sur 3 niveaux :\n"
        "- \"majeure\" : décision OPEP+/banque centrale à fort impact sur "
        "le prix, choc d'offre ou de demande majeur, rupture géopolitique "
        "touchant directement l'approvisionnement ou la devise de "
        "cotation.\n"
        "- \"notable\" : données d'inventaire/production significatives, "
        "déclaration officielle influente, mouvement de marché corrélé "
        "pertinent.\n"
        "- \"mineure\" : tout le reste (actualité générale, simple "
        "mention, communication mineure).\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : "
        '{"summary": "...", "sentiment": -1|0|1, "importance": '
        '"mineure"|"notable"|"majeure"} '
        f"où sentiment vaut -1 si l'actu est plutôt défavorable pour le "
        f"prix de {subject_label}, 0 si neutre ou mixte, 1 si plutôt "
        "favorable."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL_NEWS,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        summary = str(parsed.get("summary", "")).strip()
        sentiment = parsed.get("sentiment", 0)
        importance = parsed.get("importance", "mineure")
        if importance not in NEWS_IMPORTANCE_LEVELS:
            importance = "mineure"
        if sentiment not in (-1, 0, 1):
            sentiment = 0
        return {"summary": summary, "sentiment": sentiment, "importance": importance}
    except Exception:
        return {"summary": "", "sentiment": 0, "importance": "mineure"}


# ---------------------------------------------------------------------------
# 2. Flux RSS — même contrat que indices_score.fetch_news.
# ---------------------------------------------------------------------------

def fetch_commodity_news(query: str, subject_label: str, previous_classifications: dict | None = None) -> list[dict]:
    params = {"q": query, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = parse_news_rss(resp.content)
    previous_classifications = previous_classifications or {}
    for item in items:
        cached = previous_classifications.get(item["link"])
        if cached is not None:
            item["summary"] = cached.get("summary")
            item["sentiment"] = cached.get("sentiment")
            item["importance"] = cached.get("importance") or "mineure"
            continue
        article_text = fetch_article_text(item["link"])
        result = summarize_commodity_news_item(item["title"], subject_label, article_text)
        item["summary"] = result["summary"]
        item["sentiment"] = result["sentiment"]
        item["importance"] = result["importance"]
    return items


# ---------------------------------------------------------------------------
# 3. Formatage pour le prompt d'analyse.
# ---------------------------------------------------------------------------

def build_factors_summary(factors: list) -> str:
    lines = []
    for f in factors:
        try:
            lines.append(f"- {f.name} : score {f.score:+.1f} (poids {f.weight:.0%}) — {f.raw_value}")
        except Exception:
            continue
    return "\n".join(lines) if lines else "Aucun facteur disponible."


def build_news_summary(news: list[dict]) -> str:
    lines = []
    for n in news:
        title = n.get("title", "")
        if not title:
            continue
        summary = n.get("summary") or ""
        importance = n.get("importance", "mineure")
        lines.append(f"- [{importance}] {title}" + (f" — {summary}" if summary else ""))
    return "\n".join(lines) if lines else "Aucune actualité récente disponible."


# ---------------------------------------------------------------------------
# 4. Analyse IA (modèle lourd) — même contrat que
#    indices_score.generate_financial_analysis, prompt adapté.
# ---------------------------------------------------------------------------

def generate_commodity_analysis(subject_label: str, factors_summary: str, news_summary: str) -> str | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Sujet : {subject_label}\n\n"
        f"Facteurs du score composite déjà calculés (ne pas les "
        f"recalculer, les interpréter) :\n{factors_summary}\n\n"
        f"Actualités récentes déjà classifiées :\n{news_summary}\n\n"
        "Rédige une analyse structurée avec ces sections, dans cet "
        "ordre : contexte récent (2-3 phrases), facteurs déterminants "
        "(quels facteurs pèsent le plus sur le score actuellement et "
        "pourquoi), actualités à retenir (les actus notables ou "
        "majeures et leur lien avec l'évolution du score), synthèse.\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : {\"analysis_html\": \"...\"} où la valeur "
        "est le texte de l'analyse en HTML, en utilisant uniquement "
        "les balises <h3>, <p>, <ul>, <li>, <strong> (aucune autre "
        "balise, aucun style inline, aucun script)."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL_ANALYSIS,
                "max_tokens": 16000,
                "system": COMMODITY_ANALYSIS_SYSTEM_PROMPT,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text_block = next(
            (b["text"] for b in data["content"] if b.get("type") == "text"), None
        )
        if text_block is None:
            return None
        text = text_block.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        return parsed.get("analysis_html")
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", None)
        print(f"Erreur génération analyse commodité pour {subject_label} : {e}"
              + (f" — réponse API : {body[:500]}" if body else ""))
        return None


# ---------------------------------------------------------------------------
# 5. Orchestration partagée — gate quotidien + carry-forward.
# ---------------------------------------------------------------------------

def build_news_and_analysis_section(
    subject_label: str, news_query: str, factors: list, daily_snapshot: bool,
    previous_payload: dict,
) -> tuple[list, str | None, dict]:
    """Renvoie (news, analysis_html, news_classifications) à fusionner
    dans le payload de sortie. Hors run quotidien, ne fait AUCUN appel
    réseau et reprend tel quel ce qu'il y avait avant. Sur un run
    quotidien, rappelle le flux RSS + l'analyse IA ; si l'analyse échoue
    (None), garde l'ancienne plutôt que de la perdre. previous_payload
    est {} ou absent de clés attendues sans jamais lever (c'est à
    l'appelant de dégrader vers {} si le fichier précédent n'existe pas
    ou est invalide)."""
    previous_payload = previous_payload or {}
    previous_news = previous_payload.get("news") or []
    previous_analysis_html = previous_payload.get("analysis_html")
    previous_news_classifications = previous_payload.get("news_classifications") or {}

    if not daily_snapshot:
        return previous_news, previous_analysis_html, previous_news_classifications

    news = fetch_commodity_news(news_query, subject_label, previous_news_classifications)
    news_classifications = {
        n["link"]: {"summary": n.get("summary"), "sentiment": n.get("sentiment"), "importance": n.get("importance")}
        for n in news if n.get("link")
    }
    factors_summary = build_factors_summary(factors)
    news_summary = build_news_summary(news)
    analysis_html = generate_commodity_analysis(subject_label, factors_summary, news_summary)
    if analysis_html is None:
        analysis_html = previous_analysis_html
    return news, analysis_html, news_classifications
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python3 -m pytest tests/test_commodity_news.py -v`
Expected: 17 tests PASS.

- [ ] **Step 5 : Commit**

```bash
git add commodity_news.py tests/test_commodity_news.py
git commit -m "feat(commodites): pipeline actus + analyse IA partage (commodity_news.py)"
```

---

### Task 2 : Intégration dans `gold_score.py` (ajout uniquement)

**Files:**
- Modify: `gold_score.py`
- Test: `tests/test_gold_score.py` (nouveau fichier — gold_score.py n'a
  aujourd'hui aucun test ; ce fichier ne teste QUE la nouvelle fonction
  ajoutée dans cette tâche, pas l'intégralité de `main()`, qui reste hors
  périmètre de ce chantier)

**Interfaces:**
- Consumes : `build_news_and_analysis_section` (Tâche 1).
- Produces : `load_previous_payload(path) -> dict` (nouvelle petite
  fonction utilitaire, ajoutée à `gold_score.py`, réutilisée telle quelle
  par la Tâche 3 via un import direct — ne jamais la dupliquer dans
  `commodity_score.py`).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/test_gold_score.py` :

```python
import json

from gold_score import load_previous_payload


def test_load_previous_payload_returns_empty_dict_when_file_missing(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_previous_payload(str(missing)) == {}


def test_load_previous_payload_returns_empty_dict_on_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json {{{", encoding="utf-8")
    assert load_previous_payload(str(bad)) == {}


def test_load_previous_payload_returns_parsed_content(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"analysis_html": "<p>x</p>"}), encoding="utf-8")
    assert load_previous_payload(str(good)) == {"analysis_html": "<p>x</p>"}
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_gold_score.py -v`
Expected: `ImportError: cannot import name 'load_previous_payload'`.

- [ ] **Step 3 : Ajouter à `gold_score.py`**

Juste avant `def main():` (ligne ~745 au 2026-10-25, relocaliser avec
`grep -n "^def main"` — ne pas se fier au numéro), ajouter :

```python
def load_previous_payload(path: str) -> dict:
    """Lit le payload JSON écrit lors d'un run précédent (pour le
    carry-forward actus/analyse) — {} si le fichier n'existe pas ou est
    invalide, jamais d'exception. Réutilisée telle quelle par
    commodity_score.py (import direct, jamais dupliquée)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
```

Dans `main()`, juste après la ligne `daily_snapshot = os.environ.get(...)`
(ligne ~751) et avant la construction de `factors` (ligne ~759), ajouter :

```python
    previous_payload = load_previous_payload(OUTPUT_JSON_PATH)
```

Ajouter l'import en haut du fichier, à côté des imports existants (après
`import requests`, ligne ~37) :

```python
from commodity_news import build_news_and_analysis_section
```

Juste après la construction de `calendar` et avant la construction de
`history`/`alerts` (ligne ~793-809 au 2026-10-25 — repère-toi avec
`grep -n "history = load_history()"`), ajouter :

```python
    news, analysis_html, news_classifications = build_news_and_analysis_section(
        "l'or", "cours de l'or", factors, daily_snapshot, previous_payload,
    )
```

Dans la construction de `payload` (ligne ~824-836), ajouter les 3
nouvelles clés après `"alerts": alerts,` :

```python
        "alerts": alerts,
        "news": news,
        "analysis_html": analysis_html,
        "news_classifications": news_classifications,
```

(ne touche à AUCUNE autre ligne de `payload` ni de `main()` — c'est un
ajout pur, la ligne `json.dump(payload, ...)` existante écrit
automatiquement les nouvelles clés puisqu'elles font maintenant partie du
dict `payload`).

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python3 -m pytest tests/test_gold_score.py tests/test_commodity_news.py -v`
Expected: tous PASS (3 + 17 = 20 tests).

- [ ] **Step 5 : Vérification manuelle (syntaxe + import, pas d'appel réseau réel)**

Run: `python3 -c "import gold_score; print('import OK')"`
Expected : `import OK`, pas d'exception (confirme l'absence de cycle
d'import et l'absence d'erreur de syntaxe).

- [ ] **Step 6 : Commit**

```bash
git add gold_score.py tests/test_gold_score.py
git commit -m "feat(commodites): branche actus+analyse IA dans gold_score.py (ajout pur)"
```

---

### Task 3 : Intégration dans `commodity_score.py` (ajout uniquement)

**Files:**
- Modify: `commodity_score.py`
- Test: `tests/test_commodity_score.py`

**Interfaces:**
- Consumes : `build_news_and_analysis_section` (Tâche 1),
  `load_previous_payload` (Tâche 2, importée depuis `gold_score.py`,
  jamais dupliquée).
- Produces : aucune nouvelle interface pour d'autres tâches — `run_commodity`
  écrit simplement 3 clés de plus dans son payload JSON déjà existant.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_commodity_score.py` :

```python
from unittest.mock import patch

from commodity_score import COMMODITY_CONFIGS, run_commodity


def test_commodity_configs_have_news_query_and_subject_label():
    expected = {
        "argent": ("cours de l'argent métal", "l'argent (métal)"),
        "petrole": ("prix du pétrole WTI", "le pétrole WTI"),
        "gaz": ("prix du gaz naturel", "le gaz naturel"),
    }
    for key, (query, label) in expected.items():
        assert COMMODITY_CONFIGS[key]["news_query"] == query
        assert COMMODITY_CONFIGS[key]["news_subject_label"] == label


def test_run_commodity_includes_news_and_analysis_in_payload(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)), \
         patch("commodity_score.get_spot_and_ma200", return_value=(100.0, 95.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]), \
         patch("commodity_score.load_previous_payload", return_value={}), \
         patch("commodity_score.build_news_and_analysis_section",
               return_value=([{"title": "T"}], "<p>analyse</p>", {})) as mock_build:
        payload = run_commodity("argent", api_key="fake-key", daily_snapshot=True)
    mock_build.assert_called_once_with(
        "l'argent (métal)", "cours de l'argent métal", fake_factors, True, {},
    )
    assert payload["news"] == [{"title": "T"}]
    assert payload["analysis_html"] == "<p>analyse</p>"
    assert payload["news_classifications"] == {}


def test_run_commodity_reads_previous_payload_for_carry_forward(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)), \
         patch("commodity_score.get_spot_and_ma200", return_value=(100.0, 95.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]), \
         patch("commodity_score.load_previous_payload", return_value={"analysis_html": "<p>ancienne</p>"}) as mock_load, \
         patch("commodity_score.build_news_and_analysis_section", return_value=([], "<p>ancienne</p>", {})):
        run_commodity("gaz", api_key="fake-key", daily_snapshot=False)
    mock_load.assert_called_once()
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python3 -m pytest tests/test_commodity_score.py -v`
Expected: `AttributeError`/`AssertionError` sur `news_query`/
`news_subject_label` absents de `COMMODITY_CONFIGS`, et `run_commodity`
ne produit pas encore `news`/`analysis_html`/`news_classifications`.

- [ ] **Step 3 : Modifier `commodity_score.py`**

Ajouter l'import en haut du fichier (à côté des imports existants
`from gold_score import (...)`) :

```python
from gold_score import load_previous_payload  # réutilisée telle quelle, jamais dupliquée
from commodity_news import build_news_and_analysis_section
```

Étendre `COMMODITY_CONFIGS` (repère-toi avec `grep -n "COMMODITY_CONFIGS = {"`)
pour ajouter `news_query`/`news_subject_label` à chacune des 3 entrées :

```python
COMMODITY_CONFIGS = {
    "argent": {
        "label": "Argent",
        "ticker": "SI=F",
        "unite": "$ / oz",
        "news_query": "cours de l'argent métal",
        "news_subject_label": "l'argent (métal)",
    },
    "petrole": {
        "label": "Pétrole (WTI)",
        "ticker": "CL=F",
        "unite": "$ / baril",
        "news_query": "prix du pétrole WTI",
        "news_subject_label": "le pétrole WTI",
    },
    "gaz": {
        "label": "Gaz naturel",
        "ticker": "NG=F",
        "unite": "$ / MMBtu",
        "news_query": "prix du gaz naturel",
        "news_subject_label": "le gaz naturel",
    },
}
```

Dans `run_commodity` (repère-toi avec `grep -n "def run_commodity"`),
juste après la ligne qui résout `cfg = COMMODITY_CONFIGS[commodity_key]`
et avant le bloc `spot = ma200 = None`, ajouter :

```python
    previous_payload = load_previous_payload(os.path.join(OUTPUT_DIR, f"score_{commodity_key}.json"))
```

Juste après la ligne `calendar = build_macro_calendar(api_key)` et avant
la construction du dict `payload`, ajouter :

```python
    news, analysis_html, news_classifications = build_news_and_analysis_section(
        cfg["news_subject_label"], cfg["news_query"], factors, daily_snapshot, previous_payload,
    )
```

Dans la construction du dict `payload`, ajouter les 3 nouvelles clés
après `"alerts": [],` :

```python
        "alerts": [],
        "news": news,
        "analysis_html": analysis_html,
        "news_classifications": news_classifications,
```

(ne touche à AUCUNE autre ligne de `run_commodity` — les deux
`json.dump(..., allow_nan=False)` existants écrivent automatiquement les
nouvelles clés).

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python3 -m pytest tests/test_commodity_score.py tests/test_gold_score.py tests/test_commodity_news.py -v`
Expected: tous PASS.

- [ ] **Step 5 : Commit**

```bash
git add commodity_score.py tests/test_commodity_score.py
git commit -m "feat(commodites): branche actus+analyse IA dans commodity_score.py (ajout pur)"
```

---

### Task 4 : Workflow GitHub Actions — secret `ANTHROPIC_API_KEY`

**Files:**
- Modify: `.github/workflows/daily.yml`

**Interfaces:**
- Consumes : `commodity_news.py` (Tâche 1, via `gold_score.py`/
  `commodity_score.py`), secret GH Actions `secrets.CLAUDE_API_KEY`
  (déjà configuré, utilisé par `indices.yml` — ne pas en créer un
  nouveau).

- [ ] **Step 1 : Ajouter le secret aux deux étapes concernées**

Dans `.github/workflows/daily.yml`, repère les deux étapes avec
`grep -n "name: Calculer le score"`. Ajouter `ANTHROPIC_API_KEY:
${{ secrets.CLAUDE_API_KEY }}` au bloc `env:` de CHACUNE des deux
étapes (celle qui lance `gold_score.py` ET celle qui lance
`commodity_score.py argent/petrole/gaz`) — ne touche à aucune autre
ligne de ces blocs `env:` (les variables `FRED_API_KEY`/`SMTP_USER`/
`SMTP_PASSWORD`/`DAILY_SNAPSHOT` existantes restent inchangées).

- [ ] **Step 2 : Vérifier la syntaxe YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/daily.yml', encoding='utf-8'))"`
Expected: pas d'exception.

- [ ] **Step 3 : Commit**

```bash
git add .github/workflows/daily.yml
git commit -m "feat(commodites): cable le secret ANTHROPIC_API_KEY pour les actus/analyse"
```

---

### Task 5 : Frontend — panneau "Actualités" par commodité

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes : `data.news`, `data.analysis_html` (Tâches 2-3, présents dans
  `score.json`/`score_argent.json`/`score_petrole.json`/`score_gaz.json`).
- Produces : aucune — dernière tâche de ce plan.

- [ ] **Step 1 : Localiser précisément la zone à modifier**

Repère `function render(data)` par `grep -n "function render(data)"` (les
lignes ont bougé depuis le dernier commit — ne te fie à aucun numéro cité
ici). La fonction contient déjà :
- un `.btn-row` avec 2 boutons toggle (`toggleActu`, `toggleDetail`) ;
- un appel `wireAccordionGroup([['toggleActu', 'actuWrap'], ['toggleDetail', 'detailWrap']], ...)` ;
- les sections `actuWrap`/`detailWrap` dans `.or-columns`.

Repère aussi, dans `renderCompanyDetail` (`grep -n "function renderCompanyDetail"`),
le template exact de `newsHtml` (variable `const newsHtml = (company.news || []).map(...)`)
à réutiliser à l'identique (mêmes classes CSS `.news-item`/`.news-title`/
`.news-meta`/`.news-summary`), et le rendu de `.ai-analysis` dans
`renderCompanyAnalysis` (`const html = company.financial_analysis_html; ...`)
pour le même repli `<p>Analyse non disponible pour le moment.</p>`.

- [ ] **Step 2 : Ajouter le 3e bouton toggle**

Dans le `.btn-row` de `render(data)`, après le bouton `toggleDetail`,
ajouter :

```html
      <button class="toggle-btn" id="toggleActus" type="button" aria-expanded="false">
        Actualités <span class="chevron">▾</span>
      </button>
```

- [ ] **Step 3 : Ajouter la section `actusWrap`**

Dans `.or-columns`, après la fermeture de `detailWrap` (`</div>` qui
ferme `<div class="section-wrap" id="detailWrap">`), ajouter une
troisième `section-wrap` :

```html
    <div class="section-wrap" id="actusWrap">
      <h2>Actualités</h2>
      <div class="ai-analysis">${data.analysis_html || '<p>Analyse non disponible pour le moment.</p>'}</div>
      ${newsHtml || '<p class="hero-sub">Aucune actualité récente.</p>'}
    </div>
```

(cette `div` ferme `.or-columns` comme les deux autres déjà présentes,
avec la même structure).

- [ ] **Step 4 : Construire `newsHtml` dans `render(data)`**

Juste avant le template literal `document.getElementById('content').innerHTML = \`...\``
(donc avant la ligne qui construit `calendarHtml`/`alertsHtml`), ajouter,
avec le MÊME template que `renderCompanyDetail` :

```javascript
  const newsHtml = (data.news || []).map(n => `
    <div class="news-item">
      <a class="news-title" href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      <div class="news-meta">${[n.source, n.date].filter(Boolean).join(' · ')}</div>
      ${n.summary ? `<p class="news-summary">${n.summary}</p>` : ''}
    </div>`).join('');
```

- [ ] **Step 5 : Câbler le 3e bouton dans `wireAccordionGroup`**

Remplacer l'appel existant :

```javascript
  wireAccordionGroup([
    ['toggleActu', 'actuWrap'],
    ['toggleDetail', 'detailWrap'],
  ], (key, wrap) => {
    if (wrap.id === 'detailWrap') animateFactorBars();
  });
```

par :

```javascript
  wireAccordionGroup([
    ['toggleActu', 'actuWrap'],
    ['toggleDetail', 'detailWrap'],
    ['toggleActus', 'actusWrap'],
  ], (key, wrap) => {
    if (wrap.id === 'detailWrap') animateFactorBars();
  });
```

- [ ] **Step 6 : Vérifier la syntaxe des scripts inline**

Run (pattern déjà utilisé dans ce projet) :
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('docs/index.html', 'utf-8');
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/g;
let m, i=0, ok=true;
while ((m = re.exec(html))) {
  if (/\bsrc=/.test(m[1])) continue;
  i++;
  try { new Function(m[2]); } catch (e) { ok = false; console.log('Erreur script #' + i + ':', e.message); }
}
console.log(ok ? 'OK (' + i + ' scripts verifies)' : 'ERREURS');
"
```
Expected: `OK (...)`.

- [ ] **Step 7 : Test manuel local**

Servir `docs/` en local (`python3 -m http.server --directory docs`),
ouvrir l'onglet Commodités, cliquer sur "Actualités" pour chacun des 4
onglets Or/Argent/Pétrole/Gaz et vérifier l'absence d'erreur console.
Tant que `commodity_score.py`/`gold_score.py` n'ont pas tourné avec
`ANTHROPIC_API_KEY` configurée localement, `data.news`/`data.analysis_html`
seront absents des fichiers JSON existants — c'est attendu (le repli
"Analyse non disponible pour le moment."/"Aucune actualité récente."
s'affiche proprement), pas un bug de cette tâche.

- [ ] **Step 8 : Commit**

```bash
git add docs/index.html
git commit -m "feat(commodites): panneau Actualites (actus + analyse IA) par commodite"
```

---

### Task 6 : Vérification finale

**Files:**
- Modify (potentiellement) : tout fichier où une incohérence serait
  trouvée pendant cette vérification.

**Interfaces:** aucune nouvelle — tâche de vérification transverse.

- [ ] **Step 1 : Suite de tests Python complète**

Run: `python3 -m pytest tests/ -v`
Expected: tous les tests passent, y compris `tests/test_commodity_news.py`,
`tests/test_gold_score.py`, `tests/test_commodity_score.py`, et
l'intégralité de la suite existante (aucune régression, en particulier
sur `tests/gold_bot/` et `tests/test_indices_score.py`, intouchés par ce
plan).

- [ ] **Step 2 : Suites de tests JS**

Run: `node docs/real_price_chart.test.js && node docs/company_quote_widget.test.js`
(et tout autre `docs/*.test.js` présent — lister avec `ls docs/*.test.js`)
Expected: tous PASS (aucun test JS n'est ajouté par ce plan — ce sont
les suites existantes, pour confirmer l'absence de régression).

- [ ] **Step 3 : Grep de cohérence finale**

```bash
grep -n "news_classifications" docs/index.html
```
Expected : aucun résultat (clé interne, jamais affichée côté site).

```bash
grep -rn "indices_score\." commodity_news.py
```
Expected : uniquement des imports (`from indices_score import ...`),
jamais de redéfinition locale d'une fonction déjà importée.

```bash
grep -n "import gold_score\|from gold_score import" commodity_news.py
```
Expected : aucun résultat (`commodity_news.py` n'importe que depuis
`indices_score.py`, jamais depuis `gold_score.py` — c'est l'inverse,
`gold_score.py` importe depuis `commodity_news.py`, pour éviter tout
cycle).

- [ ] **Step 4 : requirements.txt**

Vérifier qu'aucune nouvelle dépendance n'a été ajoutée (ce plan n'en
nécessite aucune — `requests` déjà présent, `indices_score.py` important
déjà `trafilatura` pour `fetch_article_text`). Si une tâche précédente en
a ajouté une par erreur, la retirer et documenter pourquoi dans le
commit.

- [ ] **Step 5 : Commit final si des ajustements ont été faits**

```bash
git add -A
git commit -m "chore(commodites): verification finale actus+analyse IA"
```

(Ne rien committer si aucun ajustement n'a été nécessaire — cette étape
est conditionnelle.)
