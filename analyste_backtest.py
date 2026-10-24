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
import time as _time
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

# FIX 7 (revue finale) : horodatage (monotonic) du dernier appel GDELT --
# throttle GLOBAL au module (pas par appelant), horloge partagee par
# design entre toutes les tentatives d'un meme run (voire entre tests,
# d'ou la reinitialisation explicite dans tests/test_analyste_backtest.py)
# pour respecter le rythme GDELT documente (~1 requete/5 secondes) sur un
# echantillonnage de jusqu'a MAX_TENTATIVES_ECHANTILLONNAGE tentatives.
_DERNIER_APPEL_GDELT = [0.0]


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
    sleep_fn=_time.sleep, time_fn=_time.monotonic,
) -> list[dict]:
    """Actus GDELT bornees point-in-temps (Tache 2), classees
    (sentiment/importance) avec les MEMES fonctions que la production
    (fetch_article_text + summarize_news_item, indices_score.py,
    ANTHROPIC_API_KEY) -- jamais fetch_news (flux RSS Google News, non
    bornable dans le temps, voir Decouverte de terrain en tete de plan).
    Renvoie la forme attendue par indices_score.score_actualite_recente :
    [{"date": "YYYY-MM-DD", "sentiment": float, "importance": str}, ...],
    utilisee aussi comme contexte actu du raisonnement LLM (Tache 6).

    FIX 7 (revue finale) : pace chaque appel GDELT a au moins 5 secondes
    d'ecart (limite documentee de l'API, jamais respectee jusqu'ici dans
    ce module -- jusqu'a MAX_TENTATIVES_ECHANTILLONNAGE appels potentiels,
    un 429 silencieux degraderait le facteur actualite a neutre sans
    distinction possible entre "vraiment aucune actu" et "rate-limited")."""
    attente = 5.0 - (time_fn() - _DERNIER_APPEL_GDELT[0])
    if attente > 0:
        sleep_fn(attente)
    brut = actualites_source(ticker, nom, date_cible)
    _DERNIER_APPEL_GDELT[0] = time_fn()
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


def _weekly_series_jusqua(closes: pd.Series, historique: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Reproduit exactement indices_score.py:3843-3889 (resample
    hebdomadaire closes=.last() volumes=.mean()), a partir de `closes`
    deja nettoye/converti (dropna + GBp->GBP si besoin, FIX 2a de la revue
    finale) et de l'historique brut pour le Volume (jamais besoin de la
    meme conversion de devise qu'un prix)."""
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
    main()'s try/except par entreprise.

    Porte 4 corrections de indices_score.py::fetch_company_financials
    (revue finale du 2026-10-24, FIX 2a-2d) que cette collecte parallele
    avait omises, DANS LE MEME ORDRE RELATIF que la production (chaque
    correction depend de la precedente deja appliquee) : GBp->GBP (2a),
    reconciliation de devise des etats financiers (2b), override
    SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS (2c), closes_by_year par
    comparaison de dates tz-naive (2d)."""
    historique = historique_source(ticker, date_cible)
    if historique.empty:
        return None
    closes = historique["Close"].dropna()
    if closes.empty:
        return None

    # FIX 2a : `t`/`info` recuperes AVANT current_price/ma200/weekly_closes/
    # Weinstein (contrairement a l'ordre precedent) -- il faut connaitre
    # info.get("currency") avant de calculer quoi que ce soit a partir du
    # Close, meme ordre que fetch_company_financials (indices_score.py:
    # 3727-3744).
    t = ticker_fondamentaux_source(ticker)
    quarterly_financials = t.quarterly_financials
    shares_outstanding = t.info.get("sharesOutstanding") or 0.0
    beta = t.info.get("beta")
    sector = t.info.get("sector")

    if t.info.get("currency") == "GBp":
        # Meme correctif que indices_score.py:3744-3767 : certains tickers
        # londoniens cotent en pence (GBp) alors que les comptes sont en
        # livres (GBP) -- sans cette division, current_price/ma200 et tout
        # ratio derive de market_cap = price * shares_outstanding sont
        # faux d'un facteur ~100.
        closes = closes / 100.0

    current_price = float(closes.iloc[-1])

    weekly_closes, weekly_volumes = _weekly_series_jusqua(closes, historique)
    try:
        # FIX 5 : un echec isole de classify_weinstein_stage ne doit
        # jamais faire tomber tout le score de l'entreprise -- meme
        # garde-fou que indices_score.py:3882-3891.
        weinstein = indices_score.classify_weinstein_stage(weekly_closes, weekly_volumes)
    except Exception:
        weinstein = {"stage": None, "stage_label": "Neutre", "volume_confirme": False}

    ma200_serie = closes.rolling(200).mean()
    ma200 = float(ma200_serie.iloc[-1]) if len(ma200_serie) >= 200 and not pd.isna(ma200_serie.iloc[-1]) else None
    ecart_pct_ma200 = ((current_price - ma200) / ma200 * 100) if ma200 else None

    financials = fetch_statement(ticker, "financials")
    balance_sheet = fetch_statement(ticker, "balance_sheet")
    cashflow = fetch_statement(ticker, "cashflow")

    # FIX 2b : devise des etats financiers != devise de cotation (meme
    # correction que indices_score.py:3769-3826) -- convertit les lignes
    # MONETAIRES des 3 DataFrames vers la devise de cotation ; degrade
    # shares_outstanding a 0.0 si le taux de change est introuvable (meme
    # repli que la production, qui fait deja tomber proprement les ratios
    # prix/comptes en aval).
    quote_currency = "GBP" if t.info.get("currency") == "GBp" else t.info.get("currency")
    financial_currency = t.info.get("financialCurrency")
    currency_mismatch_unresolved = False
    if financial_currency and quote_currency and financial_currency != quote_currency:
        fx_rate = indices_score.fetch_fx_rate(financial_currency, quote_currency)
        if fx_rate is not None:
            monetary_rows = ~financials.index.isin(indices_score.NON_MONETARY_FINANCIALS_ROWS)
            financials.loc[monetary_rows] = financials.loc[monetary_rows] * fx_rate
            quarterly_monetary_rows = ~quarterly_financials.index.isin(indices_score.NON_MONETARY_FINANCIALS_ROWS)
            quarterly_financials.loc[quarterly_monetary_rows] = quarterly_financials.loc[quarterly_monetary_rows] * fx_rate
            balance_sheet_monetary_rows = ~balance_sheet.index.isin(indices_score.NON_MONETARY_BALANCE_SHEET_ROWS)
            balance_sheet.loc[balance_sheet_monetary_rows] = balance_sheet.loc[balance_sheet_monetary_rows] * fx_rate
            cashflow = cashflow * fx_rate
        else:
            currency_mismatch_unresolved = True
    if currency_mismatch_unresolved:
        shares_outstanding = 0.0

    # FIX 2c : override SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS, AVANT
    # extract_ratios/extract_ratios_financial -- meme ordre que
    # indices_score.py:3911-3914 (ces tickers ont un sharesOutstanding
    # reporte peu fiable, marketCap/prix est un meilleur proxy).
    if ticker in indices_score.SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS and not currency_mismatch_unresolved:
        market_cap_info = t.info.get("marketCap")
        # Correctif anti-fuite (revue 2) : `marketCap` chez yfinance est
        # TOUJOURS la valeur du jour reel, jamais point-in-temps -- la
        # diviser par `current_price` (le prix a date_cible) encoderait
        # dans shares_outstanding tout le mouvement de prix entre
        # date_cible et aujourd'hui, soit exactement la fuite du futur que
        # ce module existe pour empecher. En production (non retroactive,
        # indices_score.py:3911-3914), market_cap/current_price sont DEUX
        # valeurs du jour reel -- un ratio volontairement independant de
        # la date. On doit diviser par un prix lui aussi du jour reel pour
        # preserver cette independance a la date.
        prix_aujourdhui = t.info.get("currentPrice") or t.info.get("regularMarketPrice")
        if market_cap_info and prix_aujourdhui:
            shares_outstanding = market_cap_info / prix_aujourdhui

    is_financial = ticker in indices_score.FINANCIAL_SECTOR_TICKERS
    is_trust = ticker in indices_score.TRUST_TICKERS

    # FIX 2d : comparaison par .date() (tz-naive des deux cotes), jamais
    # par Timestamp brut -- historique.index est tz-aware (yfinance),
    # financials.columns est tz-naive (fin d'exercice fiscal) : les
    # comparer directement (ancien code, .asof(col) sous un except
    # Exception trop large) levait TypeError et degradait closes_by_year a
    # None pour TOUTE colonne, systematiquement, pas seulement en cas
    # limite -- meme logique que indices_score.py:3851-3857.
    closes_by_year = {}
    for col in financials.columns:
        target_date = col.date() if hasattr(col, "date") else col
        window = closes[closes.index.date <= target_date] if hasattr(closes.index, "date") else closes
        if len(window):
            closes_by_year[col] = float(window.iloc[-1])

    try:
        if is_financial or is_trust:
            ratios = indices_score.extract_ratios_financial(
                financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
        else:
            ratios = indices_score.extract_ratios(
                financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)
        fondamentaux_degrades = False
    except Exception:
        ratios = dict(_RATIOS_FALLBACK_NEUTRES)
        fondamentaux_degrades = True
    ratios["is_financial"] = is_financial
    ratios["is_trust"] = is_trust
    # Port de indices_score.py:3935 : quelques tickers ont un secteur
    # yfinance absent ou faux, corrige manuellement.
    ratios["sector"] = indices_score.SECTOR_OVERRIDE_BY_TICKER.get(ticker) or sector
    sector = ratios["sector"]
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

    market_cap = current_price * shares_outstanding if shares_outstanding else None
    wacc = indices_score.estimate_wacc(
        risk_free_rate, beta, market_cap, ratios.get("total_debt"), ratios.get("tax_rate"),
        fx_rate_to_usd, ratios.get("implied_cost_of_debt"))
    cost_of_capital = wacc if wacc is not None else indices_score.COST_OF_CAPITAL_PROXY
    cost_of_equity = indices_score.estimate_cost_of_equity(risk_free_rate, beta, market_cap, fx_rate_to_usd)
    cost_of_equity_for_roe = cost_of_equity if cost_of_equity is not None else cost_of_capital
    # valuation_targets est calcule ICI, AVANT actus_source -- il ne depend
    # jamais des actus (ratios/market_cap/wacc/cost_of_capital/
    # cost_of_equity ci-dessus), donc avancer son calcul permet au
    # pre-check near_entry/stage_label juste en dessous de decider s'il
    # faut fetcher les actus du tout, sans changer sa valeur.
    valuation_targets = indices_score.estimate_valuation_targets(ratios, cost_of_capital, cost_of_equity, risk_free_rate)

    entry_price = valuation_targets["entry_price"]
    pres_de_lentree = (
        current_price is not None and entry_price is not None and entry_price > 0
        and current_price <= entry_price
        and (entry_price - current_price) / entry_price * 100 < indices_score.NEAR_ENTRY_PCT
    )
    if pres_de_lentree and weinstein["stage_label"] != "Déclin":
        actus = actus_source(ticker, name, date_cible)
    else:
        # Optimisation de cout (demande explicite utilisateur, 2026-10-24) :
        # near_entry et stage_label ne dependent jamais des actus -- si l'un
        # des deux echoue, la condition "entree" de compute_company_alerts
        # (near_entry AND stage_label != "Declin" AND score_favorable, un ET
        # logique) est deja fausse quel que soit le facteur actualite, donc
        # sauter le fetch GDELT+Haiku ici ne peut jamais changer si ce signal
        # est retenu ou rejete -- uniquement une economie de cout/temps sur
        # les tentatives qui seraient rejetees de toute facon (~98% d'entre
        # elles au taux "entree" actuel mesure dans docs/indices.json).
        actus = []

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

    # FIX 8 (revue finale) : rendement 5j/20j + ratio volume 5j vs 20j,
    # calcules sur l'historique deja tronque point-in-temps (`closes`,
    # `historique`) -- aucune fuite supplementaire, reutilise par
    # analyste_replay.construit_prompt pour donner au LLM une vraie
    # donnee sur les dimensions "technique"/"volume" du raisonnement a 4
    # dimensions, au lieu de les laisser sans aucun chiffre.
    volumes = historique["Volume"].dropna() if "Volume" in historique.columns else pd.Series(dtype=float)
    rendement_5j_pct = (
        float((closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6] * 100) if len(closes) > 5 else None)
    rendement_20j_pct = (
        float((closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21] * 100) if len(closes) > 20 else None)
    volume_ratio_5j_vs_20j = (
        float(volumes.tail(5).mean() / volumes.tail(20).mean())
        if len(volumes) >= 20 and volumes.tail(20).mean() else None)

    return {
        "ticker": ticker, "name": name, "index": index_key, "date_cible": str(date_cible)[:10],
        "score_raw": composite, "factors": [f.__dict__ for f in factors],
        "current_price": current_price, "ma200": ma200, "ecart_pct_ma200": ecart_pct_ma200,
        "stage": weinstein["stage"], "stage_label": weinstein["stage_label"],
        "volume_confirme": weinstein["volume_confirme"], "sector": sector,
        "is_financial": is_financial, "is_trust": is_trust,
        "fair_value": valuation_targets["fair_value"], "entry_price": valuation_targets["entry_price"],
        "exit_price": valuation_targets["exit_price"], "actus": actus,
        "fondamentaux_degrades": fondamentaux_degrades,
        "rendement_5j_pct": rendement_5j_pct, "rendement_20j_pct": rendement_20j_pct,
        "volume_ratio_5j_vs_20j": volume_ratio_5j_vs_20j,
    }


def _tire_date_cible(generateur: random.Random, aujourdhui) -> object:
    """Date aleatoire entre FENETRE_JOURS_MIN et FENETRE_JOURS_MAX avant
    aujourdhui -- jamais au-dela (fenetre validee avec l'utilisateur).

    FIX 3 (revue finale) : le resultat est snappe a minuit UTC, pas laisse
    a l'heure de `aujourdhui` (celle a laquelle le sampler est lance) --
    sinon deux executions le meme jour a des heures differentes
    tronqueraient l'historique prix/volume (`historique.index <
    date_cible`) differemment, cassant la reproductibilite stricte et
    relachant legerement la discipline "rien apres date_cible" (qui doit
    exclure TOUTE la journee D, pas seulement l'heure courante)."""
    jours_avant = generateur.randint(FENETRE_JOURS_MIN, FENETRE_JOURS_MAX)
    return (aujourdhui - timedelta(days=jours_avant)).replace(
        hour=0, minute=0, second=0, microsecond=0)


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
        # FIX 1 (revue finale) : risk_free_rate_by_currency/fx_rate_by_currency
        # (construits par analyste_replay.main()) sont cles par DEVISE
        # ("EUR", "USD"...), jamais par nom d'indice -- il faut d'abord
        # resoudre la devise de l'entreprise via indices_score.INDEX_CURRENCY
        # avant de chercher dans ces deux dicts, sinon le lookup par
        # company["index"] ne trouve jamais rien (risk_free_rate toujours
        # None, fx_rate_to_usd toujours replie sur 1.0).
        devise = indices_score.INDEX_CURRENCY.get(company["index"])
        try:
            # FIX 5 (revue finale) : aucune isolation par tentative --
            # une seule erreur transitoire (yfinance/FRED) sur une
            # entreprise/date parmi potentiellement 2000 tentatives
            # faisait avorter tout l'echantillonnage, perdant les
            # signaux deja trouves et le budget d'appels API deja
            # depense pour les obtenir.
            resultat = score_source(
                company["ticker"], company["name"], date_cible, company["index"],
                risk_free_rate=risk_free_rate_by_currency.get(devise),
                fx_rate_to_usd=fx_rate_by_currency.get(devise, 1.0),
            )
        except Exception as e:
            print(f"Tentative ignoree ({company['ticker']}, {date_cible}) : {e!r}")
            continue
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
