import json
import math

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from commodity_score import (
    fetch_cftc_net_positions,
    factor_positionnement_cftc_generic,
    factor_ratio_or_argent,
    factor_spread_wti_brent,
    factor_saisonnalite_gaz,
    renormalize_weights,
    get_spot_and_ma200,
    COMMODITY_CONFIGS,
    build_factors,
    run_commodity,
    _fetch_gaz_seasonal_deviation,
    _fetch_gold_silver_ratio_history,
    _fetch_wti_brent_spread_series,
)
from gold_score import FactorResult


def _fake_response(json_data, status_ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    if not status_ok:
        resp.raise_for_status.side_effect = Exception("HTTP error")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_fetch_cftc_net_positions_computes_long_minus_short_most_recent_first():
    rows = [
        {"m_money_positions_long_all": "100", "m_money_positions_short_all": "40"},
        {"m_money_positions_long_all": "90", "m_money_positions_short_all": "50"},
    ]
    with patch("commodity_score.requests.get", return_value=_fake_response(rows)) as mock_get:
        result = fetch_cftc_net_positions(
            "https://example.test/resource.json", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
            "m_money_positions_long_all", "m_money_positions_short_all",
        )
    assert result == [60.0, 40.0]
    called_params = mock_get.call_args.kwargs["params"]
    assert called_params["$where"] == "market_and_exchange_names='WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE'"


def test_fetch_cftc_net_positions_raises_on_empty_rows():
    with patch("commodity_score.requests.get", return_value=_fake_response([])):
        with pytest.raises(ValueError):
            fetch_cftc_net_positions("https://example.test/resource.json", "X", "a", "b")


def test_factor_positionnement_cftc_generic_extreme_percentile_gives_negative_score():
    # Position nette actuelle = la plus haute de l'historique -> percentile 100 -> score -10
    history = [100.0, 10.0, 20.0, 30.0, 40.0]
    with patch("commodity_score.fetch_cftc_net_positions", return_value=history):
        result, percentile = factor_positionnement_cftc_generic(
            "Positionnement CFTC", 0.20, "https://x", "MARKET", "long", "short",
        )
    assert percentile == 100.0
    assert result.score == -10.0
    assert result.weight == 0.20
    assert result.name == "Positionnement CFTC"


def _fake_ticker_factory(series_by_symbol):
    """Fabrique un side_effect pour yf.Ticker(symbol) qui renvoie, pour
    .history(...)["Close"], la pandas Series injectée correspondant au
    symbole -- permet d'exercer le vrai calcul pandas (to_frame/join/
    division/dropna) avec des données de test, sans réseau."""
    def _ticker(symbol):
        t = MagicMock()
        t.history.return_value = {"Close": series_by_symbol[symbol]}
        return t
    return _ticker


def test_fetch_gold_silver_ratio_history_computes_ratio_and_current():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    gold = pd.Series([2000.0, 2010.0, 2020.0], index=dates)
    silver = pd.Series([25.0, 25.0, 20.2], index=dates)
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.side_effect = _fake_ticker_factory({"GC=F": gold, "SI=F": silver})
        history, current = _fetch_gold_silver_ratio_history()
    assert current == pytest.approx(2020.0 / 20.2)
    assert history[0] == pytest.approx(2000.0 / 25.0)
    assert history[-1] == pytest.approx(2020.0 / 20.2)


def test_fetch_gold_silver_ratio_history_drops_nan_rows():
    # Une ligne NaN côté or (ex. jour férié US non aligné avec l'argent) ne
    # doit jamais se retrouver dans l'historique de ratios -- .dropna()
    # appliqué avant le join doit l'exclure proprement.
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    gold = pd.Series([2000.0, float("nan"), 2020.0], index=dates)
    silver = pd.Series([25.0, 25.0, 20.2], index=dates)
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.side_effect = _fake_ticker_factory({"GC=F": gold, "SI=F": silver})
        history, current = _fetch_gold_silver_ratio_history()
    assert len(history) == 2
    assert all(math.isfinite(h) for h in history)
    assert current == pytest.approx(2020.0 / 20.2)


def test_fetch_wti_brent_spread_series_returns_current_and_past():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    wti = pd.Series([70.0, 71.0, 72.0], index=dates)
    brent = pd.Series([75.0, 74.0, 73.0], index=dates)
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.side_effect = _fake_ticker_factory({"CL=F": wti, "BZ=F": brent})
        current, past = _fetch_wti_brent_spread_series()
    assert current == pytest.approx(73.0 - 72.0)
    assert past == pytest.approx(75.0 - 70.0)


def test_fetch_wti_brent_spread_series_drops_nan_rows():
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    wti = pd.Series([70.0, float("nan"), 72.0], index=dates)
    brent = pd.Series([75.0, 74.0, 73.0], index=dates)
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.side_effect = _fake_ticker_factory({"CL=F": wti, "BZ=F": brent})
        current, past = _fetch_wti_brent_spread_series()
    assert math.isfinite(current)
    assert math.isfinite(past)


def test_fetch_gaz_seasonal_deviation_excludes_current_observation_from_average():
    # Reproduit le bug de dilution trouvé en revue finale : 3 lectures de
    # janvier des années précédentes (2.00/2.20/2.40, moyenne 2.20) + une
    # lecture "aujourd'hui" en janvier à 10.00 (outlier). Avant le fix, cette
    # dernière entrait dans la moyenne saisonnière (moyenne -> 4.15) ; après
    # le fix, elle doit en être exclue (moyenne -> 2.20). Ce test échoue
    # contre le code pré-fix (4.15 != 2.20).
    obs = [
        {"date": "2023-01-15", "value": "2.00"},
        {"date": "2024-01-15", "value": "2.20"},
        {"date": "2025-01-15", "value": "2.40"},
        {"date": "2026-01-20", "value": "10.00"},
    ]
    with patch("commodity_score.fetch_fred_series", return_value=obs):
        current, seasonal_average = _fetch_gaz_seasonal_deviation("fake-key")
    assert current == pytest.approx(10.00)
    assert seasonal_average == pytest.approx(2.20)


def test_fetch_gaz_seasonal_deviation_falls_back_to_current_when_no_prior_history():
    # Aucune observation antérieure pour ce mois calendaire -> la moyenne
    # saisonnière retombe sur le prix actuel (écart neutre), pas une
    # division par zéro ni une exception.
    obs = [{"date": "2026-03-01", "value": "3.50"}]
    with patch("commodity_score.fetch_fred_series", return_value=obs):
        current, seasonal_average = _fetch_gaz_seasonal_deviation("fake-key")
    assert current == pytest.approx(3.50)
    assert seasonal_average == pytest.approx(3.50)


def test_factor_ratio_or_argent_high_ratio_is_favorable_to_silver():
    # Ratio actuel au plus haut de l'historique -> percentile 100 -> favorable à l'argent -> score +10
    with patch("commodity_score._fetch_gold_silver_ratio_history", return_value=([100.0, 50.0, 60.0, 70.0], 100.0)):
        result = factor_ratio_or_argent(0.25)
    assert result.score == 10.0
    assert result.weight == 0.25
    assert "percentile" in result.raw_value.lower() or "ratio" in result.raw_value.lower()


def test_factor_spread_wti_brent_narrowing_spread_is_favorable():
    # Spread actuel plus petit que le spread passé -> resserrement -> favorable au WTI -> score positif
    with patch("commodity_score._fetch_wti_brent_spread_series", return_value=(2.0, 6.0)):
        result = factor_spread_wti_brent(0.15)
    assert result.score > 0
    assert result.weight == 0.15


def test_factor_saisonnalite_gaz_above_seasonal_average_is_favorable():
    with patch("commodity_score._fetch_gaz_seasonal_deviation", return_value=(3.80, 3.20)):
        result = factor_saisonnalite_gaz("fake-key", 0.25)
    assert result.score > 0
    assert result.weight == 0.25


def test_factor_saisonnalite_gaz_below_seasonal_average_is_unfavorable():
    with patch("commodity_score._fetch_gaz_seasonal_deviation", return_value=(2.50, 3.20)):
        result = factor_saisonnalite_gaz("fake-key", 0.25)
    assert result.score < 0


def test_renormalize_weights_scales_up_when_a_factor_is_missing():
    factors = [
        FactorResult("A", 5.0, 0.20, "a"),
        FactorResult("B", -2.0, 0.20, "b"),
        FactorResult("C", 0.0, 0.0, "placeholder, poids retiré"),
    ]
    out = renormalize_weights(factors)
    # Les poids non nuls doivent sommer à 1.0 après renormalisation ; le facteur
    # à poids nul (placeholder) reste à 0 et n'est jamais gonflé.
    assert sum(f.weight for f in out) == pytest.approx(1.0)
    assert out[2].weight == 0.0
    assert out[0].weight == pytest.approx(0.5)
    assert out[1].weight == pytest.approx(0.5)


def test_renormalize_weights_is_a_noop_when_nothing_missing():
    factors = [FactorResult("A", 1.0, 0.6, "a"), FactorResult("B", 1.0, 0.4, "b")]
    out = renormalize_weights(factors)
    assert out[0].weight == 0.6
    assert out[1].weight == 0.4


def test_factor_dynamique_recente_above_ma200_is_favorable():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=105.0, ma200=100.0, weight=0.20)
    assert result.score > 0
    assert result.weight == 0.20


def test_factor_dynamique_recente_below_ma200_is_unfavorable():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=95.0, ma200=100.0, weight=0.20)
    assert result.score < 0


def test_factor_dynamique_recente_clamps_at_plus_minus_10():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=200.0, ma200=100.0, weight=0.20)
    assert result.score == 10.0


def test_factor_dynamique_recente_neutral_when_technical_data_missing():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=None, ma200=None, weight=0.20)
    assert result.score == 0.0
    assert result.weight == 0.20


def test_factor_dynamique_recente_neutral_when_spot_is_nan():
    # Garde anti-NaN (revue finale) : un spot non-finite doit être traité
    # comme None, pas laissé propager jusqu'à min(10, nan)/max(-10, ...).
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=float("nan"), ma200=100.0, weight=0.20)
    assert result.score == 0.0
    assert result.weight == 0.20


def test_factor_dynamique_recente_neutral_when_ma200_is_inf():
    from commodity_score import factor_dynamique_recente
    result = factor_dynamique_recente(spot=100.0, ma200=float("inf"), weight=0.20)
    assert result.score == 0.0
    assert result.weight == 0.20


def test_commodity_configs_cover_argent_petrole_gaz():
    assert set(COMMODITY_CONFIGS.keys()) == {"argent", "petrole", "gaz"}
    for key, cfg in COMMODITY_CONFIGS.items():
        assert "ticker" in cfg and "unite" in cfg and "label" in cfg


def test_get_spot_and_ma200_uses_given_ticker():
    hist_mock = MagicMock()
    # .dropna() est maintenant appelé avant usage (garde anti-NaN) -- no-op
    # ici puisque le mock ne contient pas de NaN, il rechaîne sur lui-même.
    hist_mock.dropna.return_value = hist_mock
    hist_mock.tail.return_value.mean.return_value = 50.0
    hist_mock.iloc = [None] * 199 + [55.0]
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = {"Close": hist_mock}
        spot, ma200 = get_spot_and_ma200("SI=F")
        mock_yf.Ticker.assert_called_with("SI=F")


def test_build_factors_argent_includes_ratio_or_argent_and_no_eia_placeholder():
    # factor_dollar() mocké avec le poids réel de l'or (0.15) -- pas déjà
    # 0.20 -- pour que ce test échoue si le code cesse de forcer le poids
    # argent (0.20) et se contente de garder celui du mock (régression
    # possible sinon passée inaperçue, voir revue finale).
    with patch("commodity_score.factor_taux_reels", return_value=FactorResult("Taux réels US 10 ans", 1.0, 0.30, "x")), \
         patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.15, "x")), \
         patch("commodity_score.factor_positionnement_cftc_generic", return_value=(FactorResult("Positionnement CFTC", 1.0, 0.15, "x"), 50.0)), \
         patch("commodity_score.factor_ratio_or_argent", return_value=FactorResult("Ratio or/argent", 1.0, 0.25, "x")):
        factors, cftc_percentile = build_factors("argent", api_key="fake-key", spot=105.0, ma200=100.0)
    names = [f.name for f in factors]
    assert "Ratio or/argent" in names
    assert "Dynamique récente" in names
    assert "Stocks EIA" not in names
    assert cftc_percentile == 50.0
    dyn = next(f for f in factors if f.name == "Dynamique récente")
    assert dyn.score > 0  # spot > ma200 ci-dessus
    # Poids actifs avant renorm : 0.30 (taux) + 0.20 (dollar forcé) + 0.15
    # (cftc) + 0.25 (ratio) + 0.10 (dynamique) = 1.0 -> renormalize_weights
    # est un no-op, le poids forcé (0.20) doit donc ressortir tel quel.
    dollar_out = next(f for f in factors if f.name == "Dollar (DXY)")
    assert dollar_out.weight == pytest.approx(0.20)


def test_build_factors_petrole_includes_eia_placeholder_renormalized():
    # Même raisonnement que pour l'argent ci-dessus : factor_dollar() mocké
    # avec le poids réel de l'or (0.15), pas déjà 0.20, pour que ce test
    # échoue si le code cesse de forcer le poids pétrole (0.20).
    with patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.15, "x")), \
         patch("commodity_score.factor_positionnement_cftc_generic", return_value=(FactorResult("Positionnement CFTC", 1.0, 0.20, "x"), 50.0)), \
         patch("commodity_score.factor_spread_wti_brent", return_value=FactorResult("Spread WTI-Brent", 1.0, 0.15, "x")):
        factors, _ = build_factors("petrole", api_key="fake-key", spot=None, ma200=None)
    eia_factor = next(f for f in factors if f.name == "Stocks EIA")
    assert eia_factor.score == 0.0
    assert eia_factor.weight == 0.0
    assert "indisponible" in eia_factor.raw_value.lower()
    dyn = next(f for f in factors if f.name == "Dynamique récente")
    assert dyn.score == 0.0  # spot/ma200 absents ici -> neutre, pas une erreur
    assert sum(f.weight for f in factors) == pytest.approx(1.0)
    # Poids actifs avant renorm : 0.20 (dollar forcé) + 0.20 (cftc) + 0.15
    # (spread) + 0.20 (dynamique) = 0.75 -> scale 1/0.75 -> dollar final =
    # 0.20/0.75 (même pattern que le test gaz pour le même bug).
    dollar_out = next(f for f in factors if f.name == "Dollar (DXY)")
    assert dollar_out.weight == pytest.approx(0.20 / 0.75)


def test_build_factors_gaz_includes_eia_placeholder_and_reweighted_dollar():
    with patch("commodity_score.factor_positionnement_cftc_generic", return_value=(FactorResult("Positionnement CFTC", 1.0, 0.20, "x"), 50.0)), \
         patch("commodity_score.factor_saisonnalite_gaz", return_value=FactorResult("Saisonnalité (Henry Hub)", 1.0, 0.25, "x")), \
         patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.15, "x")):
        factors, cftc_percentile = build_factors("gaz", api_key="fake-key", spot=105.0, ma200=100.0)
    names = [f.name for f in factors]
    assert cftc_percentile == 50.0
    eia_factor = next(f for f in factors if f.name == "Stocks EIA")
    assert eia_factor.score == 0.0
    assert eia_factor.weight == 0.0
    assert "indisponible" in eia_factor.raw_value.lower()
    assert "Dynamique récente" in names
    dyn = next(f for f in factors if f.name == "Dynamique récente")
    assert dyn.score > 0  # spot > ma200 ci-dessus
    assert sum(f.weight for f in factors) == pytest.approx(1.0)
    # Le mock de factor_dollar renvoie un poids 0.15 (celui de l'or) ; build_factors
    # doit le forcer à 0.10 (poids gaz) AVANT renormalisation, pas garder le 0.15 du mock.
    # Poids actifs avant renorm : 0.20 (cftc) + 0.25 (saisonnalite) + 0.20 (dynamique)
    # + 0.10 (dollar forcé) = 0.75 -> scale 1/0.75 -> dollar final = 0.10/0.75.
    dollar_out = next(f for f in factors if f.name == "Dollar (DXY)")
    assert dollar_out.weight == pytest.approx(0.10 / 0.75)


def test_run_commodity_writes_json_payload_with_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("Dollar (DXY)", 2.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, 42.0)), \
         patch("commodity_score.get_spot_and_ma200", return_value=(100.0, 95.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]), \
         patch("commodity_score.os.environ.get", return_value="fake-key"):
        payload = run_commodity("argent", api_key="fake-key", daily_snapshot=False)
    assert payload["composite_score"] == pytest.approx(20.0)
    assert payload["factors"][0]["name"] == "Dollar (DXY)"
    assert payload["technical"]["spot"] == 100.0
    assert payload["technical"]["unite"] == "$ / oz"
    assert "interpretation" in payload


def test_run_commodity_passes_spot_ma200_into_build_factors(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)) as mock_build, \
         patch("commodity_score.get_spot_and_ma200", return_value=(70.0, 65.0)), \
         patch("commodity_score.build_macro_calendar", return_value=[]):
        run_commodity("petrole", api_key="fake-key", daily_snapshot=False)
    mock_build.assert_called_once_with("petrole", "fake-key", spot=70.0, ma200=65.0)


def test_run_commodity_passes_none_spot_ma200_when_technical_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("commodity_score.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("commodity_score.DOCS_DIR", str(tmp_path / "docs"))
    fake_factors = [FactorResult("X", 1.0, 1.0, "x")]
    with patch("commodity_score.build_factors", return_value=(fake_factors, None)) as mock_build, \
         patch("commodity_score.get_spot_and_ma200", side_effect=RuntimeError("indisponible")), \
         patch("commodity_score.build_macro_calendar", return_value=[]):
        run_commodity("petrole", api_key="fake-key", daily_snapshot=False)
    mock_build.assert_called_once_with("petrole", "fake-key", spot=None, ma200=None)


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
