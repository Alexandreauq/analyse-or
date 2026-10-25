import json

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


def test_commodity_configs_cover_argent_petrole_gaz():
    assert set(COMMODITY_CONFIGS.keys()) == {"argent", "petrole", "gaz"}
    for key, cfg in COMMODITY_CONFIGS.items():
        assert "ticker" in cfg and "unite" in cfg and "label" in cfg


def test_get_spot_and_ma200_uses_given_ticker():
    hist_mock = MagicMock()
    hist_mock.tail.return_value.mean.return_value = 50.0
    hist_mock.iloc = [None] * 199 + [55.0]
    with patch("commodity_score.yf") as mock_yf:
        mock_yf.Ticker.return_value.history.return_value = {"Close": hist_mock}
        spot, ma200 = get_spot_and_ma200("SI=F")
        mock_yf.Ticker.assert_called_with("SI=F")


def test_build_factors_argent_includes_ratio_or_argent_and_no_eia_placeholder():
    with patch("commodity_score.factor_taux_reels", return_value=FactorResult("Taux réels US 10 ans", 1.0, 0.30, "x")), \
         patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.20, "x")), \
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


def test_build_factors_petrole_includes_eia_placeholder_renormalized():
    with patch("commodity_score.factor_dollar", return_value=FactorResult("Dollar (DXY)", 1.0, 0.20, "x")), \
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
