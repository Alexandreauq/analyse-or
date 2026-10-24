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
