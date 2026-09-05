from indices_score import sector_risk_profile, score_rentabilite


def test_sector_risk_profile_defensif():
    assert sector_risk_profile("Healthcare") == "defensif"
    assert sector_risk_profile("Utilities") == "defensif"
    assert sector_risk_profile("Consumer Defensive") == "defensif"
    assert sector_risk_profile("Real Estate") == "defensif"


def test_sector_risk_profile_standard():
    assert sector_risk_profile("Industrials") == "standard"
    assert sector_risk_profile("Communication Services") == "standard"


def test_sector_risk_profile_cyclique():
    assert sector_risk_profile("Energy") == "cyclique"
    assert sector_risk_profile("Consumer Cyclical") == "cyclique"


def test_sector_risk_profile_defaults_to_standard_when_unknown():
    assert sector_risk_profile("Some Unmapped Sector") == "standard"
    assert sector_risk_profile(None) == "standard"


def test_score_rentabilite_above_cost_of_capital_is_positive():
    result = score_rentabilite(roce=13.0, roe=15.0, cost_of_capital=8.0)
    assert result.name == "Rentabilité / création de valeur"
    assert result.weight == 0.30
    assert result.score == 10.0  # spread of +5pp caps the score at +10
    assert "13.0" in result.raw_value
    assert "8.0" in result.raw_value


def test_score_rentabilite_below_cost_of_capital_is_negative():
    result = score_rentabilite(roce=3.0, roe=2.0, cost_of_capital=8.0)
    assert result.score == -10.0  # spread of -5pp floors the score at -10


def test_score_rentabilite_equal_to_cost_of_capital_is_neutral():
    result = score_rentabilite(roce=8.0, roe=8.0, cost_of_capital=8.0)
    assert result.score == 0.0


def test_score_rentabilite_partial_spread_scales_linearly():
    result = score_rentabilite(roce=10.5, roe=11.0, cost_of_capital=8.0)
    assert result.score == 5.0  # +2.5pp spread / 5.0pp scale * 10 = 5.0


from indices_score import score_structure_financiere


def test_score_structure_financiere_comfortable_standard_profile():
    # net_debt_ebitda=0 -> +10 sub-score ; icr très élevé -> +10 sub-score
    result = score_structure_financiere(net_debt_ebitda=0.0, icr=10.0, sector="Industrials")
    assert result.name == "Structure financière / solvabilité"
    assert result.weight == 0.25
    assert result.score == 10.0


def test_score_structure_financiere_at_standard_thresholds_is_neutral():
    # pile au seuil confortable (3.0) et pile au seuil critique ICR (3.0) -> 0 des deux côtés
    result = score_structure_financiere(net_debt_ebitda=3.0, icr=3.0, sector="Industrials")
    assert result.score == 0.0


def test_score_structure_financiere_beyond_risky_threshold_floors_at_minus_ten():
    # net_debt_ebitda largement au-delà du seuil risqué (-10) ET icr nul (-10) -> combiné = -10.0
    result = score_structure_financiere(net_debt_ebitda=8.0, icr=0.0, sector="Industrials")
    assert result.score == -10.0


def test_score_structure_financiere_defensif_profile_more_tolerant():
    # même ratio de 4.0x : risqué en Standard (entre 3 et 5.5) mais mieux toléré en Défensif
    # (seuil confortable ajusté = 3.0 * 1.3 = 3.9, donc 4.0 est tout juste au-delà -> proche de 0)
    standard = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Industrials")
    defensif = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Healthcare")
    assert defensif.score > standard.score


def test_score_structure_financiere_cyclique_profile_stricter():
    cyclique = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Energy")
    standard = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Industrials")
    assert cyclique.score < standard.score
    assert "cyclique" in cyclique.raw_value


def test_score_structure_financiere_net_cash_position_leverage_capped():
    # Position de cash net (net_debt_ebitda négatif) : le sous-score de levier
    # ne doit jamais dépasser +10 avant d'être moyenné avec la couverture,
    # sinon le résultat est faussé (5.0 au lieu de ~1.67 dans ce cas précis).
    result = score_structure_financiere(net_debt_ebitda=-2.0, icr=1.0, sector="Industrials")
    assert result.score < 2.0


from indices_score import score_croissance


def test_score_croissance_strong_aligned_growth():
    result = score_croissance(cagr_ca=12.0, cagr_ebitda=12.0)
    assert result.name == "Croissance"
    assert result.weight == 0.20
    assert result.score == 10.0  # moyenne 12% / échelle 10% -> plafonné à +10


def test_score_croissance_no_growth_is_neutral():
    result = score_croissance(cagr_ca=0.0, cagr_ebitda=0.0)
    assert result.score == 0.0


def test_score_croissance_decline_is_negative():
    result = score_croissance(cagr_ca=-10.0, cagr_ebitda=-10.0)
    assert result.score == -10.0


def test_score_croissance_penalizes_ebitda_divergence():
    # CA croît bien, mais l'EBITDA décroche largement (dégradation de la rentabilité)
    aligned = score_croissance(cagr_ca=6.0, cagr_ebitda=6.0)
    diverging = score_croissance(cagr_ca=6.0, cagr_ebitda=-2.0)
    assert diverging.score < aligned.score


from indices_score import score_generation_cash


def test_score_generation_cash_full_conversion():
    result = score_generation_cash(fcf_conversion=100.0)
    assert result.name == "Génération de cash"
    assert result.weight == 0.15
    assert result.score == 10.0


def test_score_generation_cash_neutral_at_fifty_percent():
    result = score_generation_cash(fcf_conversion=50.0)
    assert result.score == 0.0


def test_score_generation_cash_negative_conversion_floors_at_minus_ten():
    result = score_generation_cash(fcf_conversion=-20.0)
    assert result.score == -10.0


from indices_score import score_valorisation


def test_score_valorisation_trading_at_discount_is_positive():
    # EV/EBITDA et PER tous deux 30% sous leur moyenne 5 ans -> décote favorable
    result = score_valorisation(
        current_ev_ebitda=7.0, avg_ev_ebitda_5y=10.0,
        current_pe=10.5, avg_pe_5y=15.0,
        cagr_ebitda=5.0,
    )
    assert result.name == "Valorisation relative"
    assert result.weight == 0.10
    assert result.score > 0


def test_score_valorisation_premium_with_weak_growth_is_penalized():
    result = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=1.0,  # croissance faible -> la prime n'est pas justifiée
    )
    assert result.score < 0


def test_score_valorisation_premium_with_strong_growth_is_dampened():
    weak_growth = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=1.0,
    )
    strong_growth = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=12.0,  # même prime, mais croissance forte -> pénalité atténuée
    )
    assert strong_growth.score > weak_growth.score


def test_score_valorisation_at_historical_average_is_neutral():
    result = score_valorisation(
        current_ev_ebitda=10.0, avg_ev_ebitda_5y=10.0,
        current_pe=15.0, avg_pe_5y=15.0,
        cagr_ebitda=5.0,
    )
    assert result.score == 0.0


from indices_score import compute_composite, interpret, FactorResult


def test_compute_composite_all_max_positive():
    factors = [
        FactorResult("A", 10.0, 0.30, ""),
        FactorResult("B", 10.0, 0.25, ""),
        FactorResult("C", 10.0, 0.20, ""),
        FactorResult("D", 10.0, 0.15, ""),
        FactorResult("E", 10.0, 0.10, ""),
    ]
    assert compute_composite(factors) == 100.0


def test_compute_composite_all_zero_is_neutral():
    factors = [FactorResult("A", 0.0, 1.0, "")]
    assert compute_composite(factors) == 0.0


def test_interpret_bands():
    assert interpret(60.0) == "Profil fondamental très solide"
    assert interpret(20.0) == "Solide"
    assert interpret(0.0) == "Neutre"
    assert interpret(-30.0) == "Fragile"


import pandas as pd
from indices_score import get_row, extract_ratios


def test_get_row_returns_first_matching_alias():
    df = pd.DataFrame({"2025-12-31": [100.0]}, index=["Total Debt"])
    row = get_row(df, "Net Debt", "Total Debt")
    assert row.iloc[0] == 100.0


def test_get_row_raises_when_no_alias_matches():
    df = pd.DataFrame({"2025-12-31": [100.0]}, index=["Something Else"])
    try:
        get_row(df, "Net Debt", "Total Debt")
        assert False, "expected KeyError"
    except KeyError:
        pass


def _make_fixture_statements():
    years = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]
    financials = pd.DataFrame(
        {
            years[0]: [1000, 200, 150, 140, 0.25],
            years[1]: [950, 185, 138, 130, 0.25],
            years[2]: [900, 170, 128, 120, 0.25],
            years[3]: [850, 155, 116, 108, 0.25],
            years[4]: [800, 140, 104, 96, 0.25],
        },
        index=["Total Revenue", "EBITDA", "EBIT", "Net Income", "Tax Rate For Calcs"],
    )
    balance_sheet = pd.DataFrame(
        {
            years[0]: [300, 50],
            years[1]: [320, 45],
            years[2]: [340, 40],
            years[3]: [360, 35],
            years[4]: [380, 30],
        },
        index=["Total Debt", "Stockholders Equity"],
    )
    balance_sheet.loc["Cash And Cash Equivalents"] = [50, 45, 40, 35, 30]
    cashflow = pd.DataFrame(
        {
            years[0]: [120, -30],
            years[1]: [110, -28],
            years[2]: [100, -26],
            years[3]: [90, -24],
            years[4]: [80, -22],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    closes_by_year = {y: 100.0 for y in years}
    return financials, balance_sheet, cashflow, closes_by_year


def test_extract_ratios_computes_expected_keys():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    for key in [
        "roce", "roe", "net_debt_ebitda", "icr", "cagr_ca", "cagr_ebitda",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu passe de 800 à 1000 sur 4 intervalles annuels -> CAGR ~ 5.7%
    assert 5.0 < ratios["cagr_ca"] < 6.5


import math
from indices_score import _cagr


def test_cagr_returns_neutral_zero_when_oldest_value_is_missing():
    assert _cagr(float("nan"), 1000.0, 4) == 0.0


def test_cagr_returns_neutral_zero_when_latest_value_is_missing():
    assert _cagr(800.0, float("nan"), 4) == 0.0


def test_extract_ratios_ignores_years_with_missing_ebitda_or_net_income():
    """yfinance ne garantit pas 5 années pleines pour chaque poste : une
    année (souvent la plus ancienne) peut manquer de valeur pour EBITDA ou
    Net Income. Ces trous ne doivent pas produire de NaN dans cagr_ca,
    cagr_ebitda, avg_ev_ebitda_5y ou avg_pe_5y."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    oldest_year = list(financials.columns)[-1]
    financials.loc["Total Revenue", oldest_year] = float("nan")
    financials.loc["EBITDA", oldest_year] = float("nan")
    financials.loc["Net Income", oldest_year] = float("nan")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert ratios["cagr_ca"] == 0.0
    assert ratios["cagr_ebitda"] == 0.0
    assert not math.isnan(ratios["avg_ev_ebitda_5y"])
    assert not math.isnan(ratios["avg_pe_5y"])


from indices_score import parse_news_rss

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Google News</title>
<item>
  <title>LVMH annonce une hausse de ses ventes</title>
  <link>https://example.com/article1</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>LVMH ouvre un nouveau magasin</title>
  <link>https://example.com/article2</link>
  <pubDate>Wed, 03 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


def test_parse_news_rss_extracts_title_date_link():
    items = parse_news_rss(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0]["title"] == "LVMH annonce une hausse de ses ventes"
    assert items[0]["link"] == "https://example.com/article1"
    assert items[0]["date"] == "2026-09-04"


def test_parse_news_rss_limits_to_five_items():
    many_items = b"<rss><channel>" + b"".join(
        f"<item><title>Titre {i}</title><link>https://example.com/{i}</link>"
        f"<pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate></item>".encode()
        for i in range(10)
    ) + b"</channel></rss>"
    items = parse_news_rss(many_items)
    assert len(items) == 5


import indices_score


def _fake_ratios():
    return {
        "roce": 15.0,
        "roe": 18.0,
        "net_debt_ebitda": 1.5,
        "icr": 8.0,
        "cagr_ca": 6.0,
        "cagr_ebitda": 6.5,
        "fcf_conversion": 70.0,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "current_pe": 20.0,
        "avg_pe_5y": 20.0,
        "sector": "Consumer Defensive",
    }


def test_build_company_entry_degrades_gracefully_when_news_fetch_fails(monkeypatch):
    """Une panne du flux RSS (fetch_news) ne doit pas faire perdre le score
    déjà calculé pour l'entreprise — seule la liste de news doit être vide."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())

    def _raise_news(name):
        raise RuntimeError("flux RSS indisponible")

    monkeypatch.setattr(indices_score, "fetch_news", _raise_news)

    entry = indices_score.build_company_entry("BN.PA", "Danone")

    assert entry["news"] == []
    assert entry["ticker"] == "BN.PA"
    assert entry["name"] == "Danone"
    assert isinstance(entry["score"], float)
    assert len(entry["factors"]) == 5


def test_build_company_entry_includes_news_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(
        indices_score, "fetch_news",
        lambda name: [{"title": "Titre", "date": "2026-09-04", "link": "https://example.com"}],
    )

    entry = indices_score.build_company_entry("BN.PA", "Danone")

    assert entry["news"] == [{"title": "Titre", "date": "2026-09-04", "link": "https://example.com"}]
