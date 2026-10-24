import pandas as pd
import pytest
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

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
