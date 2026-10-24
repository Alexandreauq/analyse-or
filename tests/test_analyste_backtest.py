import random

import pandas as pd
import pytest
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import analyste_backtest
import indices_score


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
    # Exact calendar window: 2019-12-01 -> 2025-12-01 spans two leap days (2192 days).
    assert bornes["start"] == date_cible - relativedelta(years=6)


def test_historique_point_in_temps_refiltre_meme_si_la_source_ignore_end():
    index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    historique = pd.DataFrame(
        {"Close": range(400), "Volume": [1000] * 400}, index=index)
    date_cible = datetime(2025, 12, 1, tzinfo=timezone.utc)

    class SourceQuiIgnoreEnd:
        def history(self, start=None, end=None):
            return historique  # fuite volontaire : renvoie aussi les bougies posterieures

    resultat = analyste_backtest.historique_prix_volume_point_in_temps(
        "TEST.PA", date_cible, ticker_source=lambda t: SourceQuiIgnoreEnd())

    assert resultat.index.max() < date_cible
    assert len(resultat) == (date_cible - index[0].to_pydatetime()).days


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


def test_actus_classifiees_point_in_temps_throttles_gdelt_calls_to_5_seconds():
    """FIX 7 (revue finale) : la spec exige de respecter le rythme GDELT
    (~1 requete toutes les 5 secondes) -- rien ne le faisait jusqu'ici,
    risquant des 429 silencieux sur un echantillonnage de jusqu'a 2000
    tentatives. Reinitialise explicitement l'etat module (partage entre
    appels/tests, par design -- throttle global, pas par instance) pour
    un test deterministe independant de l'ordre d'execution."""
    # -10.0 (pas 0.0) : plus de 5s avant l'horloge de depart (0.0), pour
    # que le premier appel du test ne declenche pas lui-meme une attente.
    analyste_backtest._DERNIER_APPEL_GDELT[0] = -10.0
    horloge = {"t": 0.0}

    def time_fn():
        return horloge["t"]

    attentes = []

    def sleep_fn(secondes):
        attentes.append(secondes)
        horloge["t"] += secondes

    def actualites_source_factice(ticker, nom, date_cible, **kw):
        return {"ticker": ticker, "articles": []}

    kwargs = dict(
        actualites_source=actualites_source_factice,
        fetch_article_text=lambda url: "", summarize_news_item=lambda titre, nom, texte: {},
        sleep_fn=sleep_fn, time_fn=time_fn)
    date_cible = datetime(2025, 12, 1, tzinfo=timezone.utc)

    analyste_backtest.actus_classifiees_point_in_temps("TEST.PA", "Entreprise Test", date_cible, **kwargs)
    assert attentes == []  # premier appel (dernier appel deja >5s dans le passe) : jamais d'attente

    analyste_backtest.actus_classifiees_point_in_temps("TEST.PA", "Entreprise Test", date_cible, **kwargs)
    assert len(attentes) == 1
    assert attentes[0] == pytest.approx(5.0, abs=0.01)

    horloge["t"] += 6.0  # avance l'horloge de 6s -- plus besoin d'attendre
    analyste_backtest.actus_classifiees_point_in_temps("TEST.PA", "Entreprise Test", date_cible, **kwargs)
    assert len(attentes) == 1  # pas de nouvelle attente


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
    # FIX 6 : cette fixture est volontairement minimale (fondamentaux vides,
    # extract_ratios leve sur ce StatementFactice) -- couvre le chemin
    # "donnees manquantes degrade sans lever" (voir le plan, Tache 4) ;
    # fondamentaux_degrades doit donc etre True, et present (pas absent).
    assert resultat["fondamentaux_degrades"] is True
    # FIX 8 : rendement 5j/20j + ratio volume, calcules sur l'historique
    # deja tronque point-in-temps (aucune fuite supplementaire).
    assert resultat["rendement_5j_pct"] is not None
    assert resultat["rendement_20j_pct"] is not None
    assert resultat["volume_ratio_5j_vs_20j"] == pytest.approx(1.0)


def _historique_minimale(tz="UTC"):
    return pd.DataFrame({
        "Close": [100.0 + i * 0.1 for i in range(1500)],
        "Volume": [10000] * 1500,
    }, index=pd.date_range("2022-01-01", periods=1500, freq="D", tz=tz))


class _StatementVideFactice:
    columns = []
    def __getitem__(self, item):
        raise KeyError(item)


def test_score_point_in_temps_converts_gbp_pence_to_pounds(monkeypatch):
    """FIX 2a : certains tickers londoniens cotent en pence (info["currency"]
    == "GBp") alors que les comptes sont en livres -- sans division par
    100, current_price (et tout ce qui en derive) est faux d'un facteur
    ~100."""
    historique = pd.DataFrame({
        "Close": [10000.0 + i for i in range(1500)],  # pence
        "Volume": [10000] * 1500,
    }, index=pd.date_range("2022-01-01", periods=1500, freq="D", tz="UTC"))

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    def fetch_statement_factice(ticker, nom_attribut):
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology",
                            currency="GBp", financialCurrency="GBP")

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.L", "Entreprise Test", date_cible, "FTSE",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    attendu = (10000.0 + 1499) / 100.0
    assert resultat["current_price"] == pytest.approx(attendu)


def test_score_point_in_temps_converts_financial_currency_mismatch(monkeypatch):
    """FIX 2b : financialCurrency != devise de cotation -- les lignes
    MONETAIRES de financials/balance_sheet/cashflow doivent etre
    multipliees par le taux de change, les lignes non-monetaires
    (NON_MONETARY_FINANCIALS_ROWS, ex "Tax Rate For Calcs") jamais."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    colonne = pd.Timestamp("2025-01-01")
    financials = pd.DataFrame(
        {colonne: [1000.0, 0.25]}, index=["Total Revenue", "Tax Rate For Calcs"])
    balance_sheet = pd.DataFrame({colonne: [500.0]}, index=["Total Assets"])
    cashflow = pd.DataFrame({colonne: [200.0]}, index=["Free Cash Flow"])
    quarterly_financials_fixture = pd.DataFrame({colonne: [250.0]}, index=["Total Revenue"])
    statements = {"financials": financials, "balance_sheet": balance_sheet, "cashflow": cashflow}

    def fetch_statement_factice(ticker, nom_attribut):
        return statements[nom_attribut]

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = quarterly_financials_fixture
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology",
                            currency="USD", financialCurrency="EUR")

    monkeypatch.setattr(indices_score, "fetch_fx_rate", lambda frm, to: 2.0)

    captured = {}

    def extract_ratios_factice(financials_arg, balance_sheet_arg, cashflow_arg,
                                closes_by_year, shares_outstanding, current_price):
        captured["financials"] = financials_arg.copy()
        captured["cashflow"] = cashflow_arg.copy()
        return dict(analyste_backtest._RATIOS_FALLBACK_NEUTRES)

    monkeypatch.setattr(indices_score, "extract_ratios", extract_ratios_factice)

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert captured["financials"].loc["Total Revenue", colonne] == pytest.approx(2000.0)
    assert captured["financials"].loc["Tax Rate For Calcs", colonne] == pytest.approx(0.25)
    assert captured["cashflow"].loc["Free Cash Flow", colonne] == pytest.approx(400.0)


def test_score_point_in_temps_closes_by_year_survives_tz_aware_vs_naive_comparison(monkeypatch):
    """FIX 2d : historique.index tz-aware (yfinance) vs financials.columns
    tz-naive (cas normal) faisait lever TypeError, attrape par un except
    Exception trop large qui degradait closes_by_year a None pour TOUT
    col -- la comparaison doit se faire sur des .date(), jamais sur des
    Timestamps bruts."""
    historique = pd.DataFrame({
        "Close": [100.0 + i * 0.1 for i in range(1500)],
        "Volume": [10000] * 1500,
    }, index=pd.date_range("2022-01-01", periods=1500, freq="D", tz="America/New_York"))

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    financials = pd.DataFrame(
        {pd.Timestamp("2025-01-01"): [1000.0], pd.Timestamp("2024-01-01"): [900.0]},
        index=["Total Revenue"])

    def fetch_statement_factice(ticker, nom_attribut):
        if nom_attribut == "financials":
            return financials
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology")

    captured = {}

    def extract_ratios_factice(financials_arg, balance_sheet_arg, cashflow_arg,
                                closes_by_year, shares_outstanding, current_price):
        captured["closes_by_year"] = dict(closes_by_year)
        return dict(analyste_backtest._RATIOS_FALLBACK_NEUTRES)

    monkeypatch.setattr(indices_score, "extract_ratios", extract_ratios_factice)

    date_cible = pd.Timestamp("2026-06-01", tz="America/New_York")
    analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert captured["closes_by_year"]
    assert all(v is not None for v in captured["closes_by_year"].values())


def test_score_point_in_temps_applies_sector_override_by_ticker(monkeypatch):
    """Port de indices_score.py:3935 (SECTOR_OVERRIDE_BY_TICKER) : quelques
    tickers ont un secteur yfinance absent/faux, corrige manuellement."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    def fetch_statement_factice(ticker, nom_attribut):
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector=None)

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "ML.PA", "Michelin", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert resultat["sector"] == indices_score.SECTOR_OVERRIDE_BY_TICKER["ML.PA"]


def test_score_point_in_temps_overrides_shares_outstanding_from_market_cap(monkeypatch):
    """FIX 2c (et correctif anti-fuite de la revue 2) : pour les tickers de
    SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS, shares_outstanding =
    marketCap / prix_aujourdhui -- marketCap vient de `info` (toujours la
    valeur du jour reel chez yfinance, jamais point-in-temps) donc DOIT
    etre divise par un prix lui aussi du jour reel (`currentPrice`/
    `regularMarketPrice`), jamais par `current_price` (le prix point-in-
    temps a date_cible). Diviser par `current_price` ferait fuiter tout le
    mouvement de prix entre date_cible et aujourd'hui dans
    shares_outstanding -- exactement la fuite que ce module existe pour
    empecher. `currentPrice` est ici volontairement tres different du prix
    point-in-temps (qui vaut ~249 d'apres l'historique factice ci-dessous)
    pour que le test distingue la formule correcte de la formule buguee."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    def fetch_statement_factice(ticker, nom_attribut):
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1.0, beta=1.0, sector="Technology",
                            marketCap=100_000_000.0, currentPrice=50.0)

    captured = {}

    def extract_ratios_factice(financials_arg, balance_sheet_arg, cashflow_arg,
                                closes_by_year, shares_outstanding, current_price):
        captured["shares_outstanding"] = shares_outstanding
        captured["current_price"] = current_price
        return dict(analyste_backtest._RATIOS_FALLBACK_NEUTRES)

    monkeypatch.setattr(indices_score, "extract_ratios", extract_ratios_factice)

    # VOW3.DE (Volkswagen) : dans SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS
    # mais PAS dans FINANCIAL_SECTOR_TICKERS/TRUST_TICKERS -- extract_ratios
    # (pas extract_ratios_financial) est donc bien la fonction appelee ici,
    # contrairement a un choix arbitraire dans l'ensemble (qui contient
    # aussi des tickers financiers, ex. les banques/assureurs .HK).
    ticker = "VOW3.DE"
    assert ticker in indices_score.SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS
    assert ticker not in indices_score.FINANCIAL_SECTOR_TICKERS
    assert ticker not in indices_score.TRUST_TICKERS
    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    analyste_backtest.score_point_in_temps(
        ticker, "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    # current_price point-in-temps (~249) != currentPrice du jour (50) :
    # les deux formules donnent des resultats bien distincts.
    assert captured["current_price"] != pytest.approx(50.0)
    assert captured["shares_outstanding"] == pytest.approx(100_000_000.0 / 50.0)
    assert captured["shares_outstanding"] != pytest.approx(
        100_000_000.0 / captured["current_price"])


def test_score_point_in_temps_weinstein_failure_degrades_instead_of_raising(monkeypatch):
    """FIX 5 : un echec isole de classify_weinstein_stage ne doit jamais
    faire tomber tout le score (meme garde-fou que indices_score.py:
    3882-3891)."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    def fetch_statement_factice(ticker, nom_attribut):
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology")

    def classify_weinstein_stage_factice(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(indices_score, "classify_weinstein_stage", classify_weinstein_stage_factice)

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert resultat is not None
    assert resultat["stage_label"] == "Neutre"
    assert resultat["stage"] is None


def test_score_point_in_temps_sets_fondamentaux_degrades_on_ratios_failure(monkeypatch):
    """FIX 6 : quand extract_ratios/extract_ratios_financial leve (replie
    sur _RATIOS_FALLBACK_NEUTRES), le signal doit porter
    fondamentaux_degrades=True -- visible dans le fichier de sortie, pas
    confondu avec un vrai score neutre."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def actus_factices(ticker, nom, date_cible, **kw):
        return []

    class StatementQuiLeve:
        columns = [pd.Timestamp("2025-01-01")]
        def __getitem__(self, item):
            raise KeyError(item)

    def fetch_statement_factice(ticker, nom_attribut):
        return StatementQuiLeve()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology")

    def extract_ratios_qui_leve(*a, **kw):
        raise ValueError("fondamentaux incomplets")

    monkeypatch.setattr(indices_score, "extract_ratios", extract_ratios_qui_leve)

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        historique_source=historique_factice, actus_source=actus_factices,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())

    assert resultat["fondamentaux_degrades"] is True


def _fixture_score_point_in_temps_base(monkeypatch, entry_price, stage_label):
    """Fabrique commune aux 3 tests de l'optimisation de cout ci-dessous :
    fixe entry_price via un estimate_valuation_targets factice (plutot que
    de reconstruire un DCF/actif net complet) et stage_label via un
    classify_weinstein_stage factice, independamment l'un de l'autre --
    exactement les deux entrees de la porte near_entry/stage_label que
    cette optimisation doit respecter."""
    historique = _historique_minimale()

    def historique_factice(ticker, date_cible, history_ticker=None, annees=6, **kw):
        return historique[historique.index < date_cible]

    def fetch_statement_factice(ticker, nom_attribut):
        return _StatementVideFactice()

    class InfoFactice(dict):
        pass

    class TickerFondamentauxFactice:
        quarterly_financials = _StatementVideFactice()
        info = InfoFactice(sharesOutstanding=1_000_000.0, beta=1.0, sector="Technology")

    def estimate_valuation_targets_factice(data, cost_of_capital, cost_of_equity=None, risk_free_rate=None):
        return {"fair_value": entry_price, "entry_price": entry_price, "exit_price": entry_price}

    monkeypatch.setattr(indices_score, "estimate_valuation_targets", estimate_valuation_targets_factice)

    def classify_weinstein_stage_factice(*a, **kw):
        return {"stage": 2, "stage_label": stage_label, "volume_confirme": True}

    monkeypatch.setattr(indices_score, "classify_weinstein_stage", classify_weinstein_stage_factice)

    return dict(
        historique_source=historique_factice,
        fetch_statement=fetch_statement_factice,
        ticker_fondamentaux_source=lambda t: TickerFondamentauxFactice())


def test_score_point_in_temps_skips_news_fetch_when_price_far_from_entry(monkeypatch):
    """Optimisation de cout (demande explicite utilisateur, 2026-10-24) :
    quand near_entry est deja faux (prix courant nettement au-dessus du
    repere d'entree), compute_company_alerts rejettera "entree" quel que
    soit le facteur actualite -- actus_source (GDELT + Haiku par article)
    ne doit donc jamais etre appele."""
    # current_price point-in-temps de _historique_minimale() vaut ~249.9
    # (100.0 + 1499 * 0.1) -- entry_price = 100.0 est nettement en
    # dessous, donc current_price > entry_price de bien plus que
    # NEAR_ENTRY_PCT (5.0) : near_entry est faux pour deux raisons
    # independantes (current_price > entry_price ET l'ecart > 5%).
    kwargs = _fixture_score_point_in_temps_base(monkeypatch, entry_price=100.0, stage_label="Achat")

    appels = []

    def actus_source_qui_compte(ticker, nom, date_cible, **kw):
        appels.append((ticker, nom, date_cible))
        return []

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        actus_source=actus_source_qui_compte, **kwargs)

    assert resultat is not None
    assert appels == []
    assert resultat["actus"] == []
    assert resultat["entry_price"] == pytest.approx(100.0)


def test_score_point_in_temps_fetches_news_when_near_entry_and_not_declin(monkeypatch):
    """Cas symetrique : near_entry vrai (prix a 1% sous le repere
    d'entree, dans la bande NEAR_ENTRY_PCT) et stage_label != "Declin" --
    la condition "entree" de compute_company_alerts reste possible selon
    le facteur actualite, donc actus_source DOIT etre appele."""
    historique = _historique_minimale()
    current_price_attendu = float(historique["Close"].iloc[-1])  # ~249.9
    # entry_price choisi pour que current_price soit juste en dessous,
    # a 1% du repere (< NEAR_ENTRY_PCT = 5.0).
    entry_price = current_price_attendu / 0.99
    kwargs = _fixture_score_point_in_temps_base(monkeypatch, entry_price=entry_price, stage_label="Achat")

    appels = []

    def actus_source_qui_compte(ticker, nom, date_cible, **kw):
        appels.append((ticker, nom, date_cible))
        return []

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        actus_source=actus_source_qui_compte, **kwargs)

    assert resultat is not None
    assert len(appels) == 1
    assert appels[0][0] == "TEST.PA"


def test_score_point_in_temps_skips_news_fetch_when_stage_is_declin(monkeypatch):
    """Cas stage_label == "Declin" alors meme que near_entry serait vrai
    au seul critere prix : compute_company_alerts rejette "entree" quel
    que soit le facteur actualite (stage_label != "Declin" est un ET
    independant de near_entry) -- actus_source ne doit donc pas etre
    appele non plus dans ce cas."""
    historique = _historique_minimale()
    current_price_attendu = float(historique["Close"].iloc[-1])
    entry_price = current_price_attendu / 0.99  # near_entry serait vrai...
    kwargs = _fixture_score_point_in_temps_base(monkeypatch, entry_price=entry_price, stage_label="Déclin")

    appels = []

    def actus_source_qui_compte(ticker, nom, date_cible, **kw):
        appels.append((ticker, nom, date_cible))
        return []

    date_cible = pd.Timestamp("2026-06-01", tz="UTC")
    resultat = analyste_backtest.score_point_in_temps(
        "TEST.PA", "Entreprise Test", date_cible, "CAC40",
        actus_source=actus_source_qui_compte, **kwargs)

    assert resultat is not None
    assert appels == []
    assert resultat["actus"] == []
    assert resultat["stage_label"] == "Déclin"


# Actu volontairement ancienne (bien plus de 14 jours avant "aujourd'hui",
# quelle que soit la date reelle d'execution de ce test) -- c'est
# exactement la situation d'un backtest sur un date_cible historique.
_ACTU_ANCIENNE_MAIS_IMPORTANTE = [
    {"date": "2025-06-01", "sentiment": 0.8, "importance": "majeure"},
]


def test_score_actualite_recente_reel_degrade_vers_neutre_sur_une_actu_ancienne():
    """Demontre le bug : indices_score.score_actualite_recente filtre par
    datetime.now() - 14 jours, donc une actu de point_in_temps (toujours
    datee dans le passe par construction) est systematiquement rejetee,
    quel que soit son sentiment ou son importance."""
    resultat = indices_score.score_actualite_recente(_ACTU_ANCIENNE_MAIS_IMPORTANTE)

    assert resultat.score == 0.0
    assert resultat.raw_value == "Aucune actualité récente exploitable"


def test_score_actualite_recente_point_in_temps_restitue_le_signal():
    """La version point-in-temps ne filtre pas par horloge reelle -- la
    meme actu ancienne doit produire un score reflettant son sentiment
    positif (0.8) et son importance ('majeure'), pas 0.0."""
    resultat = analyste_backtest._score_actualite_recente_point_in_temps(
        _ACTU_ANCIENNE_MAIS_IMPORTANTE)

    assert resultat.score > 0.0
    assert "majeure" in resultat.raw_value


def test_score_actualite_recente_point_in_temps_liste_vide_meme_forme_que_le_reel():
    """Parite avec le cas vide de la fonction reelle (name/score/weight/
    raw_value identiques) -- seul le filtre de fraicheur change, pas le
    cas neutre."""
    assert (
        analyste_backtest._score_actualite_recente_point_in_temps([])
        == indices_score.score_actualite_recente([])
    )


def test_genere_signaux_historiques_stops_once_target_sample_is_reached(monkeypatch):
    compteur = {"appels": 0}

    def score_point_in_temps_factice(ticker, name, date_cible, index_key, **kw):
        compteur["appels"] += 1
        # Un signal sur deux declenche "entree" (composite au-dessus du
        # seuil, cours a 1% au-dessous du repere d'entree -- near_entry
        # exige current_price <= entry_price, cf. compute_company_alerts) ;
        # l'autre, non (score defavorable ET cours loin du repere).
        favorable = compteur["appels"] % 2 == 0
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10],
            "score_raw": 30.0 if favorable else -30.0,
            "stage_label": "Achat" if favorable else "Neutre",
            "current_price": 99.0 if favorable else 100.0,
            "entry_price": 100.0 if favorable else 50.0,
            "exit_price": 150.0, "factors": [], "score": 30.0 if favorable else -30.0,
        }

    companies = [{"ticker": f"T{i}.PA", "name": f"Entreprise {i}", "index": "CAC40"} for i in range(200)]

    resultats = analyste_backtest.genere_signaux_historiques(
        companies, {}, {}, cible_echantillon=5, max_tentatives=100,
        score_source=score_point_in_temps_factice, graine=42)

    assert 5 <= len(resultats) <= 5
    assert compteur["appels"] <= 100
    assert all(r["ticker"] for r in resultats)


def test_tire_date_cible_snaps_to_midnight_utc():
    """FIX 3 (revue finale) : _tire_date_cible gardait l'heure de
    `aujourdhui` (celle a laquelle le sampler est lance), ce qui rend
    historique_prix_volume_point_in_temps's `historique.index < date_cible`
    dependant de l'heure d'execution, pas seulement de la date -- casse a
    la fois la reproductibilite stricte et la discipline "rien apres
    date_cible" (qui doit exclure TOUTE la journee D, pas seulement
    l'heure courante de cette journee)."""
    import datetime as _dt
    aujourdhui = _dt.datetime(2026, 10, 24, 15, 37, 42, tzinfo=_dt.timezone.utc)
    generateur = random.Random(42)

    date_cible = analyste_backtest._tire_date_cible(generateur, aujourdhui)

    assert date_cible.hour == 0
    assert date_cible.minute == 0
    assert date_cible.second == 0
    assert date_cible.microsecond == 0


def test_genere_signaux_historiques_resolves_rate_and_fx_by_currency_not_by_index():
    """FIX 1 (revue finale) : risk_free_rate_by_currency/fx_rate_by_currency
    sont construits par main() cles par DEVISE ("EUR", "USD"...), jamais par
    nom d'indice ("CAC40", "DAX"...) -- genere_signaux_historiques doit donc
    resoudre la devise de l'entreprise (indices_score.INDEX_CURRENCY) avant
    de chercher dans ces deux dicts, sinon risk_free_rate_by_currency.get(
    company["index"]) est toujours None et fx_rate_by_currency.get(
    company["index"], 1.0) retombe toujours sur 1.0, quel que soit le
    contenu reel des dicts."""
    captures = []

    def score_point_in_temps_factice(ticker, name, date_cible, index_key, **kw):
        captures.append(kw)
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10], "score_raw": 30.0,
            "stage_label": "Achat", "current_price": 99.0, "entry_price": 100.0,
            "exit_price": 150.0, "factors": [], "score": 30.0,
        }

    companies = [{"ticker": "T0.PA", "name": "Entreprise 0", "index": "CAC40"}]

    analyste_backtest.genere_signaux_historiques(
        companies, {"EUR": 0.03}, {"EUR": 1.1}, cible_echantillon=1, max_tentatives=1,
        score_source=score_point_in_temps_factice, graine=42)

    assert captures[0]["risk_free_rate"] == 0.03
    assert captures[0]["fx_rate_to_usd"] == 1.1


def test_genere_signaux_historiques_continues_past_a_score_source_exception(capsys):
    """FIX 5 (revue finale) : une seule exception transitoire (yfinance/
    FRED) sur une tentative ne doit jamais faire avorter tout
    l'echantillonnage, perdant les signaux deja trouves -- isole chaque
    tentative dans son propre try/except, continue sur l'erreur."""
    compteur = {"appels": 0}

    def score_point_in_temps_qui_leve_parfois(ticker, name, date_cible, index_key, **kw):
        compteur["appels"] += 1
        if compteur["appels"] % 3 == 0:
            raise RuntimeError("erreur transitoire yfinance")
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10], "score_raw": 30.0,
            "stage_label": "Achat", "current_price": 99.0, "entry_price": 100.0,
            "exit_price": 150.0, "factors": [], "score": 30.0,
        }

    companies = [{"ticker": f"T{i}.PA", "name": f"Entreprise {i}", "index": "CAC40"} for i in range(50)]

    resultats = analyste_backtest.genere_signaux_historiques(
        companies, {}, {}, cible_echantillon=5, max_tentatives=100,
        score_source=score_point_in_temps_qui_leve_parfois, graine=7)

    assert len(resultats) == 5
    # Le message doit etre visible (pas une erreur silencieusement ignoree).
    assert "erreur transitoire yfinance" in capsys.readouterr().out


def test_genere_signaux_historiques_respects_the_fixed_seed_for_reproducibility():
    def score_point_in_temps_factice(ticker, name, date_cible, index_key, **kw):
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "date_cible": str(date_cible)[:10], "score_raw": 30.0,
            "stage_label": "Achat",
            # current_price <= entry_price et a moins de 5% de celui-ci
            # (near_entry, compute_company_alerts) -- declenche "entree" a
            # chaque appel, pour que le tirage remplisse reellement
            # l'echantillon cible et exerce vraiment la graine fixe.
            "current_price": 99.0, "entry_price": 100.0,
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
    assert len(resultats_1) == 3
