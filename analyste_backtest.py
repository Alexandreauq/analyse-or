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
import random
from datetime import timedelta

import pandas as pd
import yfinance as yf
from dateutil.relativedelta import relativedelta

import assistant_ia.news as news
import indices_score

FENETRE_JOURS_MIN = 180
FENETRE_JOURS_MAX = 365
ECHANTILLON_CIBLE = 40  # milieu de la fourchette 30-50 de la spec
MAX_TENTATIVES_ECHANTILLONNAGE = 2000
GRAINE_ALEATOIRE = 20261024


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


def _weekly_series_jusqua(historique: pd.DataFrame, date_cible) -> tuple[pd.Series, pd.Series]:
    """Reproduit exactement indices_score.py:3843-3889 (dropna, resample
    hebdomadaire closes=.last() volumes=.mean()), a partir d'un historique
    deja tronque a date_cible (Tache 3) plutot que period="6y"."""
    closes = historique["Close"].dropna()
    volumes = (historique["Volume"] if "Volume" in historique.columns
               else pd.Series(dtype=float, index=historique.index)).reindex(closes.index)
    return closes.resample("W").last().dropna(), volumes.resample("W").mean()


def _score_actualite_recente_point_in_temps(news_items: list[dict]) -> "indices_score.FactorResult":
    """Reimplementation de indices_score.score_actualite_recente SANS son
    filtre de fraicheur interne (`datetime.now() - 14 jours`) -- ce filtre
    est incompatible par construction avec un date_cible historique (aucun
    parametre de date n'existe pour l'injecter dans la fonction reelle) :
    pour un date_cible vieux de plusieurs mois, `item_date.date() >=
    (datetime.now() - 14j).date()` rejette TOUT item, quel que soit son
    contenu -- la fonction reelle degrade silencieusement vers son cas
    neutre pour n'importe quel backtest retroactif, pas seulement dans de
    rares cas limites.

    Tous les `news_items` sont deja bornes point-in-temps en amont par
    actus_classifiees_point_in_temps (Taches 2-3, fenetre de 14 jours
    AVANT date_cible) -- donc "recent" au sens de cette fonction, ici,
    signifie simplement "tout ce qui a ete fourni", sans re-filtrer par
    une horloge reelle qui n'a aucun sens pour une date passee. Le reste
    de la formule (ponderation par importance, clamp, forme du resultat)
    reproduit exactement indices_score.score_actualite_recente -- cette
    fonction retire le filtre de fraicheur ET la validation de format de
    date qui l'accompagnait dans la fonction reelle (la boucle complete
    `try: datetime.strptime(...)/if item_date.date() >= cutoff`, pas
    seulement la ligne de comparaison de date) -- sans risque ici puisque
    actus_classifiees_point_in_temps (Taches 2-3) garantit deja un champ
    'date' propre en amont. Exception deliberee et isolee a "ne jamais
    reimplementer la logique de scoring" (contrairement aux 12 autres
    fonctions score_* reutilisees telles quelles) : la fonction reelle
    est structurellement inutilisable ici, pas seulement moins pratique
    a appeler."""
    if not news_items:
        return indices_score.FactorResult(
            "Actualité récente", 0.0, indices_score.WEIGHTS["actualite_recente"],
            "Aucune actualité récente exploitable",
        )
    weighted_sum = 0.0
    total_weight = 0.0
    for item in news_items:
        weight = indices_score.NEWS_IMPORTANCE_WEIGHTS.get(item.get("importance", "mineure"), 1.0)
        weighted_sum += item.get("sentiment", 0) * weight
        total_weight += weight
    avg_sentiment = weighted_sum / total_weight if total_weight else 0.0
    score = indices_score._clamp(avg_sentiment * 10)
    majeure_count = sum(1 for item in news_items if item.get("importance") == "majeure")
    majeure_note = f", dont {majeure_count} majeure(s)" if majeure_count else ""
    return indices_score.FactorResult(
        "Actualité récente", score, indices_score.WEIGHTS["actualite_recente"],
        f"Ton moyen pondéré des {len(news_items)} actualités point-in-temps : {avg_sentiment:+.2f}{majeure_note}",
    )


# Repli neutre si extract_ratios/extract_ratios_financial leve une exception
# (fondamentaux vides ou trop incomplets chez la source, ex : tous les
# exercices manquants) -- union des cles des deux fonctions
# (indices_score.py:2893 et 3247), valeurs neutres par convention (0.0,
# memes conventions que ces deux fonctions elles-memes pour une donnee
# manquante) et drapeaux *_available a False pour que les fonctions de
# score degradent vers leur branche "donnee indisponible" plutot que de
# lire un faux zero comme une vraie performance. Ni extract_ratios ni
# extract_ratios_financial ne font ce garde elles-memes (leur appelant de
# production, fetch_company_financials, laisse l'exception se propager
# jusqu'au except par entreprise de main(), qui supprime l'entreprise
# entiere de docs/indices.json) -- score_point_in_temps a besoin d'un
# comportement different : jamais d'exception, degrader plutot que de
# renvoyer None, pour qu'une entreprise sans fondamentaux exploitables a
# cette date passee reste quand meme notee sur ses facteurs non
# fondamentaux (dynamique recente, actualite recente).
_RATIOS_FALLBACK_NEUTRES = {
    "roce": 0.0, "roce_available": False, "roe": 0.0,
    "net_debt_ebitda": 0.0, "icr": 0.0, "structure_available": False,
    "cagr_ca": 0.0, "cagr_ebitda": 0.0, "cagr_net_income": 0.0,
    "fcf_conversion": 0.0, "fcf_conversion_available": False,
    "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
    "current_pe": 0.0, "avg_pe_5y": 0.0,
    "current_pb": 0.0, "avg_pb_5y": 0.0,
    "current_ratio": 0.0, "no_loss_years": False,
    "valuation_available": False,
    "fcf": 0.0, "fcf_normalized": 0.0,
    "net_debt": 0.0, "equity": 0.0,
    "tax_rate": None, "total_debt": None, "implied_cost_of_debt": None,
    "leverage_ratio": 0.0, "cash_conversion": 0.0,
}


def score_point_in_temps(
    ticker: str, name: str, date_cible, index_key: str,
    risk_free_rate: float | None = None, fx_rate_to_usd: float | None = 1.0,
    *, historique_source=historique_prix_volume_point_in_temps,
    actus_source=actus_classifiees_point_in_temps,
    fetch_statement=indices_score._fetch_statement_with_retry,
    ticker_fondamentaux_source=yf.Ticker,
) -> dict | None:
    """Score composite retroactif d'une entreprise a `date_cible`, en
    reutilisant directement les fonctions de calcul de indices_score.py.
    Renvoie None si le prix courant a date_cible n'est pas calculable
    (historique insuffisant) -- jamais d'exception, meme philosophie que
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
    try:
        if is_financial or is_trust:
            ratios = indices_score.extract_ratios_financial(
                financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
        else:
            ratios = indices_score.extract_ratios(
                financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
    except Exception:
        ratios = dict(_RATIOS_FALLBACK_NEUTRES)
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
            indices_score.score_valorisation_financiere(
                ratios.get("current_pe"), ratios.get("avg_pe_5y"),
                ratios.get("current_pb"), ratios.get("avg_pb_5y"), ratios.get("cagr_net_income")),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            _score_actualite_recente_point_in_temps(actus),
        ]
    elif is_trust:
        factors = [
            indices_score.score_rentabilite_financiere(ratios.get("roe"), cost_of_equity_for_roe),
            indices_score.score_structure_financiere_trust(ratios.get("net_debt"), ratios.get("equity")),
            indices_score.score_croissance_financiere(ratios.get("cagr_ca"), ratios.get("cagr_net_income")),
            indices_score.score_generation_cash_trust(),
            indices_score.score_valorisation_trust(ratios.get("current_pb")),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            _score_actualite_recente_point_in_temps(actus),
        ]
    else:
        factors = [
            indices_score.score_rentabilite(
                ratios.get("roce"), ratios.get("roe"), cost_of_capital,
                data_available=ratios.get("roce_available", False)),
            indices_score.score_structure_financiere(
                ratios.get("net_debt_ebitda"), ratios.get("icr"), sector,
                data_available=ratios.get("structure_available", False)),
            indices_score.score_croissance(ratios.get("cagr_ca"), ratios.get("cagr_ebitda")),
            indices_score.score_generation_cash(
                ratios.get("fcf_conversion"), data_available=ratios.get("fcf_conversion_available", False)),
            indices_score.score_valorisation(
                ratios.get("current_ev_ebitda"), ratios.get("avg_ev_ebitda_5y"),
                ratios.get("current_pe"), ratios.get("avg_pe_5y"), ratios.get("cagr_ebitda"),
                data_available=ratios.get("valuation_available", False)),
            indices_score.score_dynamique_recente(ecart_pct_ma200, ratios.get("quarterly_yoy_growth_ca"), ratios.get("cagr_ca")),
            _score_actualite_recente_point_in_temps(actus),
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
    reelle du jour).

    Note methodologique (a documenter aussi dans le commit) :
    indices_score.recalibrate_scores_by_profile compare le score d'une
    entreprise au pool de TOUTES les entreprises du meme profil scorees
    LA MEME DATE -- ici, chaque tirage porte sur une date differente, il
    n'y a jamais de vrai pool comparable d'un coup. Ce module accepte
    cette simplification : score brut non recalibre (`score_recalibrated=
    False` passe a compute_company_alerts ci-dessous), qui desactive le
    filtre de percentile et ne garde que score_favorable/near_entry/
    stage_label -- voir la docstring de compute_company_alerts -- plutot
    que de reconstruire un pool historique complet pour chaque date
    tiree, hors du perimetre valide avec l'utilisateur (fenetre reduite,
    pas une refonte de la methodologie de recalibration)."""
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
        resultat["score"] = resultat["score_raw"]  # pas de recalibration par pool ici (voir note ci-dessus)
        alerts = indices_score.compute_company_alerts(
            resultat["ticker"], resultat["score_raw"], resultat["current_price"],
            resultat["entry_price"], [], news_items=None, stage_label=resultat["stage_label"],
            score_recalibrated=False, score=resultat["score"],
        )
        if any(a["kind"] == "entree" for a in alerts):
            retenus.append(resultat)
    return retenus
