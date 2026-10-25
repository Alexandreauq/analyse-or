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
