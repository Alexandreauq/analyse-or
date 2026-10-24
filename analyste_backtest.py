# analyste_backtest.py
# "L'Analyste" -- backtest historique (6-12 mois) du raisonnement IA +
# actus deja teste en avant (voir docs/superpowers/specs/
# 2026-10-24-analyste-backtest-design.md). Ce module ne fait qu'une chose
# de bout en bout : reconstruire, pour une date passee donnee, EXACTEMENT
# ce qui etait connu ce jour-la, et rien de plus. Toute donnee posterieure
# a date_cible serait une fuite de l'avenir qui fausserait le backtest.
#
# Ce module ne touche JAMAIS indices_score.py::fetch_company_financials ni
# build_company_entry : ce sont du code de production critique, sans point
# d'extension pour une date passee (period="6y" est relatif a l'instant de
# l'appel, pas a une date historique). On reconstruit donc ici notre propre
# collecte de prix/volume tronquee (yfinance, bornes start/end explicites)
# et notre propre collecte d'actus point-in-temps (assistant_ia.news,
# Tache 2), puis on reutilise a l'identique les fonctions existantes de
# indices_score.py qui ne font que du calcul pur ou du traitement d'article
# (fetch_article_text, summarize_news_item).
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
