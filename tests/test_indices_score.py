import pytest
import requests
from datetime import datetime
from indices_score import sector_risk_profile, score_rentabilite, COST_OF_CAPITAL_PROXY


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
    assert result.weight == 0.24
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
    assert result.weight == 0.20
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
    assert result.weight == 0.16
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
    assert result.weight == 0.12
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
    assert result.weight == 0.08
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


from indices_score import score_dynamique_recente


def test_score_dynamique_recente_averages_both_subsignals():
    # écart MM200 de +10% -> sous-score 5.0 (10/20*10) ; accélération de
    # +5pt (croissance trim 11% vs tendance 5 ans 6%) -> sous-score 5.0 (5/10*10)
    result = score_dynamique_recente(
        ecart_pct_ma200=10.0, quarterly_yoy_growth_ca=11.0, cagr_ca=6.0
    )
    assert result.name == "Dynamique récente"
    assert result.weight == 0.10
    assert result.score == 5.0


def test_score_dynamique_recente_uses_only_price_when_quarterly_unavailable():
    result = score_dynamique_recente(
        ecart_pct_ma200=10.0, quarterly_yoy_growth_ca=None, cagr_ca=6.0
    )
    assert result.score == 5.0


def test_score_dynamique_recente_uses_only_quarterly_when_price_unavailable():
    result = score_dynamique_recente(
        ecart_pct_ma200=None, quarterly_yoy_growth_ca=11.0, cagr_ca=6.0
    )
    assert result.score == 5.0


def test_score_dynamique_recente_neutral_when_no_subsignal_available():
    result = score_dynamique_recente(
        ecart_pct_ma200=None, quarterly_yoy_growth_ca=None, cagr_ca=6.0
    )
    assert result.score == 0.0
    assert result.raw_value == "Données insuffisantes"


def test_score_dynamique_recente_clamps_extreme_price_deviation():
    result = score_dynamique_recente(
        ecart_pct_ma200=100.0, quarterly_yoy_growth_ca=None, cagr_ca=0.0
    )
    assert result.score == 10.0


from indices_score import score_actualite_recente
from datetime import timedelta


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def test_score_actualite_recente_averages_recent_sentiments():
    news = [
        {"date": _days_ago(1), "sentiment": 1},
        {"date": _days_ago(2), "sentiment": 1},
        {"date": _days_ago(3), "sentiment": -1},
    ]
    result = score_actualite_recente(news)
    assert result.name == "Actualité récente"
    assert result.weight == 0.10
    # moyenne (1+1-1)/3 = 0.333... -> score 3.33...
    assert 3.0 < result.score < 3.5


def test_score_actualite_recente_ignores_old_news():
    news = [
        {"date": _days_ago(1), "sentiment": 1},
        {"date": _days_ago(30), "sentiment": -1},  # hors fenêtre de 14 jours
    ]
    result = score_actualite_recente(news)
    assert result.score == 10.0  # seule l'actu récente (sentiment 1) compte


def test_score_actualite_recente_neutral_when_no_news():
    result = score_actualite_recente([])
    assert result.score == 0.0
    assert result.raw_value == "Aucune actualité récente exploitable"


def test_score_actualite_recente_neutral_when_all_news_are_old():
    news = [{"date": _days_ago(30), "sentiment": 1}]
    result = score_actualite_recente(news)
    assert result.score == 0.0


def test_score_actualite_recente_handles_missing_sentiment_key_as_neutral():
    news = [{"date": _days_ago(1)}]  # pas de clé "sentiment"
    result = score_actualite_recente(news)
    assert result.score == 0.0


def test_score_actualite_recente_weighs_majeure_news_more_than_mineure():
    """Une actu majeure très négative doit faire basculer le facteur vers
    le négatif même entourée de plusieurs actus mineures neutres — une
    moyenne simple (poids égal) resterait quasi neutre ici."""
    news = [
        {"date": _days_ago(1), "sentiment": -1, "importance": "majeure"},
        {"date": _days_ago(2), "sentiment": 0, "importance": "mineure"},
        {"date": _days_ago(3), "sentiment": 0, "importance": "mineure"},
        {"date": _days_ago(4), "sentiment": 0, "importance": "mineure"},
    ]
    result = score_actualite_recente(news)
    # moyenne pondérée (-1*4 + 0+0+0) / (4+1+1+1) = -4/7 ≈ -0.571 -> score ≈ -5.71
    assert result.score < -4.0
    assert "1 majeure" in result.raw_value


def test_score_actualite_recente_missing_importance_defaults_to_mineure_weight():
    """Sans clé "importance" (actu jamais reclassée, ou ancien format),
    le poids doit rester 1.0 — comportement strictement identique à
    l'ancienne moyenne simple, pas de régression pour les actus déjà en
    cache d'un run précédent."""
    with_explicit_mineure = score_actualite_recente(
        [{"date": _days_ago(1), "sentiment": 1, "importance": "mineure"}]
    )
    without_importance_key = score_actualite_recente(
        [{"date": _days_ago(1), "sentiment": 1}]
    )
    assert with_explicit_mineure.score == without_importance_key.score == 10.0


def test_score_actualite_recente_all_mineure_reproduces_simple_average():
    news = [
        {"date": _days_ago(1), "sentiment": 1, "importance": "mineure"},
        {"date": _days_ago(2), "sentiment": 1, "importance": "mineure"},
        {"date": _days_ago(3), "sentiment": -1, "importance": "mineure"},
    ]
    result = score_actualite_recente(news)
    assert 3.0 < result.score < 3.5  # identique à test_..._averages_recent_sentiments


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
        "current_pe", "avg_pe_5y", "fcf", "net_debt", "equity",
        "tax_rate", "total_debt",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu croît régulièrement de 800 à 1000 sur 5 ans ; CAGR lissé
    # (moyenne des 2 exercices récents vs moyenne des 2 plus anciens,
    # cf. test dédié ci-dessous) ~ 5.7%/an sur cette série linéaire.
    assert 5.0 < ratios["cagr_ca"] < 6.5


import math
from indices_score import _cagr


def test_cagr_returns_neutral_zero_when_oldest_value_is_missing():
    assert _cagr(float("nan"), 1000.0, 4) == 0.0


def test_cagr_returns_neutral_zero_when_latest_value_is_missing():
    assert _cagr(800.0, float("nan"), 4) == 0.0


def test_extract_ratios_smooths_cagr_over_two_year_windows():
    """Reproduit le cas TotalEnergies 2022 : un pic isolé sur l'exercice le
    plus ancien disponible ne doit pas, seul, déterminer tout le CAGR — le
    calcul doit moyenner 2 exercices de chaque côté plutôt qu'un point à
    point. Avec seulement 4 exercices dispo (limite yfinance), le résultat
    lissé (-31.9%) doit différer nettement du point-à-point naïf qu'il
    remplace (-21.9%) : ce test échoue si quelqu'un revient à l'ancienne
    formule point à point."""
    years = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31"]
    financials = pd.DataFrame(
        {
            years[0]: [100, 40, 30, 20, 0.25],
            years[1]: [90, 35, 25, 18, 0.25],
            years[2]: [200, 80, 60, 40, 0.25],
            years[3]: [210, 85, 65, 45, 0.25],
        },
        index=["Total Revenue", "EBITDA", "EBIT", "Net Income", "Tax Rate For Calcs"],
    )
    balance_sheet = pd.DataFrame(
        {
            years[0]: [100, 200],
            years[1]: [100, 200],
            years[2]: [100, 200],
            years[3]: [100, 200],
        },
        index=["Total Debt", "Stockholders Equity"],
    )
    balance_sheet.loc["Cash And Cash Equivalents"] = [20, 20, 20, 20]
    cashflow = pd.DataFrame(
        {
            years[0]: [30, -10],
            years[1]: [28, -9],
            years[2]: [60, -20],
            years[3]: [65, -22],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    closes_by_year = {y: 100.0 for y in years}

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    # moyenne(100,90)=95 vs moyenne(200,210)=205, sur 2 ans -> -31.9%
    assert -33.0 < ratios["cagr_ca"] < -30.5
    # le point-à-point naïf (210 -> 100 sur 3 ans) donnerait -21.9% :
    # s'assurer qu'on ne l'a pas retrouvé par erreur.
    assert not (-23.0 < ratios["cagr_ca"] < -20.0)


def test_extract_ratios_uses_net_ppe_purchase_and_sale_as_capex_fallback():
    """Reproduit un cas observé en production (Veolia Environnement) : pas
    de ligne 'Capital Expenditure' isolée chez yfinance pour cette
    entreprise, seulement 'Net PPE Purchase And Sale' — doit être
    utilisée comme repli plutôt que de faire lever KeyError sur toute
    l'entreprise (même convention de signe négatif)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    cashflow = cashflow.rename(index={"Capital Expenditure": "Net PPE Purchase And Sale"})

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["fcf"] == 120.0 + (-30.0)  # OCF + proxy capex, exercice le plus récent


def test_extract_ratios_uses_net_investment_properties_purchase_and_sale_as_capex_fallback():
    """Reproduit le cas Vonovia (VNA.DE) en production : une foncière
    n'a ni 'Capital Expenditure' ni 'Net PPE Purchase And Sale' — elle
    investit en achetant des immeubles de placement ('Net Investment
    Properties Purchase And Sale'), pas des PPE industrielles. Doit être
    utilisée comme 2e repli plutôt que de faire lever KeyError."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    cashflow = cashflow.rename(index={"Capital Expenditure": "Net Investment Properties Purchase And Sale"})

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["fcf"] == 120.0 + (-30.0)  # OCF + proxy capex, exercice le plus récent


def test_extract_ratios_ignores_years_with_missing_ebitda_or_net_income():
    """yfinance ne garantit pas 5 années pleines pour chaque poste : une
    année (souvent la plus ancienne) peut manquer de valeur pour EBITDA ou
    Net Income. Le CAGR étant lissé sur une fenêtre de 2 exercices, un seul
    exercice manquant dans cette fenêtre ne doit pas produire de NaN : la
    moyenne se recalcule sur le seul exercice restant. Ces trous ne
    doivent pas non plus produire de NaN dans avg_ev_ebitda_5y/avg_pe_5y."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    oldest_year = list(financials.columns)[-1]
    financials.loc["Total Revenue", oldest_year] = float("nan")
    financials.loc["EBITDA", oldest_year] = float("nan")
    financials.loc["Net Income", oldest_year] = float("nan")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert not math.isnan(ratios["cagr_ca"])
    assert not math.isnan(ratios["cagr_ebitda"])
    assert not math.isnan(ratios["avg_ev_ebitda_5y"])
    assert not math.isnan(ratios["avg_pe_5y"])


def test_extract_ratios_cagr_is_neutral_zero_when_whole_old_window_is_missing():
    """Si les 2 exercices les plus anciens (toute la fenêtre de lissage)
    manquent de valeur, aucune moyenne n'est calculable -> repli neutre
    0.0, plutôt qu'un NaN ou une valeur fabriquée."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    for y in years[-2:]:
        financials.loc["Total Revenue", y] = float("nan")
        financials.loc["EBITDA", y] = float("nan")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert ratios["cagr_ca"] == 0.0
    assert ratios["cagr_ebitda"] == 0.0


def test_extract_ratios_degrades_gracefully_when_latest_year_has_nan_balance_sheet_values():
    """Un NaN sur l'exercice le plus récent (total_debt/cash/equity) ne
    doit jamais se propager dans roce/roe/icr/net_debt_ebitda — `if X else
    default` seul ne suffit pas (bool(nan) est True en Python), d'où le
    garde-fou _is_missing ajouté en plus du test de vérité déjà présent."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    latest_year = list(financials.columns)[0]
    balance_sheet.loc["Total Debt", latest_year] = float("nan")
    balance_sheet.loc["Stockholders Equity", latest_year] = float("nan")
    balance_sheet.loc["Cash And Cash Equivalents", latest_year] = float("nan")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert ratios["roce"] == 0.0
    assert ratios["roe"] == 0.0
    assert ratios["icr"] == 10.0  # repli documenté quand total_debt est absent/invalide
    assert ratios["net_debt_ebitda"] == 0.0


def test_extract_ratios_handles_balance_sheet_entirely_missing_total_debt_row():
    """Reproduit le cas E.ON (EOAN.DE) en production : le bilan yfinance
    n'a carrément aucune ligne "Total Debt" (ni aucun équivalent) — pas
    une valeur manquante sur un exercice, une ligne absente sur toute la
    période. get_row() lèverait un KeyError non rattrapé ; _get_row_or_nan
    doit dégrader vers les mêmes replis que le cas "valeur NaN" ci-dessus."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    balance_sheet = balance_sheet.drop(index="Total Debt")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert ratios["icr"] == 10.0
    assert ratios["net_debt_ebitda"] == 0.0
    assert ratios["total_debt"] != ratios["total_debt"]  # NaN (total_debt réellement indisponible)


def test_extract_ratios_handles_latest_year_missing_from_balance_sheet_and_cashflow():
    """Reproduit le bug SAN.PA/BN.PA (colonnes désalignées entre relevés
    annuels), mais sur l'exercice le plus récent plutôt qu'un ancien : ne
    doit jamais lever KeyError, doit dégrader vers les replis documentés
    plutôt que de planter tout le calcul."""
    years = ["2025-12-31", "2024-12-31"]
    financials = pd.DataFrame(
        {
            years[0]: [1000, 200, 150, 140, 0.25],
            years[1]: [950, 185, 138, 130, 0.25],
        },
        index=["Total Revenue", "EBITDA", "EBIT", "Net Income", "Tax Rate For Calcs"],
    )
    # balance_sheet/cashflow n'ont QUE l'exercice le plus ancien — le plus
    # récent (years[0]) leur manque entièrement.
    balance_sheet = pd.DataFrame(
        {years[1]: [320, 45]}, index=["Total Debt", "Stockholders Equity"],
    )
    balance_sheet.loc["Cash And Cash Equivalents"] = [45]
    cashflow = pd.DataFrame(
        {years[1]: [110, -28]}, index=["Operating Cash Flow", "Capital Expenditure"],
    )
    closes_by_year = {y: 100.0 for y in years}

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["icr"] == 10.0
    assert ratios["roce"] == 0.0
    assert ratios["roe"] == 0.0
    assert ratios["fcf"] == 0.0


from indices_score import extract_quarterly_growth


def test_extract_quarterly_growth_computes_yoy_growth_with_five_quarters():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [100],  # même trimestre il y a un an (cols[4])
        },
        index=["Total Revenue"],
    )
    result = extract_quarterly_growth(quarterly_financials)
    assert result == pytest.approx(10.0)  # (110/100 - 1) * 100


def test_extract_quarterly_growth_returns_none_with_fewer_than_five_quarters():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None


def test_extract_quarterly_growth_returns_none_when_year_ago_value_missing():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [float("nan")],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None


def test_extract_quarterly_growth_returns_none_when_year_ago_value_is_zero_or_negative():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [0.0],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None


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


SAMPLE_RSS_WITH_SOURCE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>LVMH annonce une hausse de ses ventes</title>
  <link>https://example.com/article1</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://www.lemonde.fr">Le Monde.fr</source>
</item>
</channel></rss>
"""


def test_parse_news_rss_extracts_source():
    items = parse_news_rss(SAMPLE_RSS_WITH_SOURCE)
    assert items[0]["source"] == "Le Monde.fr"


def test_parse_news_rss_defaults_source_to_empty_string_when_absent():
    items = parse_news_rss(SAMPLE_RSS)  # fixture existante, sans tag <source>
    assert items[0]["source"] == ""


import requests
import indices_score
from indices_score import fetch_article_text


def test_fetch_article_text_returns_extracted_text_on_success(monkeypatch):
    class FakeResponse:
        text = "<html><body><p>Contenu de l'article.</p></body></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: "Contenu de l'article.")
    assert fetch_article_text("https://example.com/article") == "Contenu de l'article."


def test_fetch_article_text_truncates_to_4000_chars(monkeypatch):
    class FakeResponse:
        text = "<html></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: "a" * 5000)
    result = fetch_article_text("https://example.com/article")
    assert len(result) == 4000


def test_fetch_article_text_returns_none_on_http_error(monkeypatch):
    def raise_error(*a, **k):
        raise requests.RequestException("boom")
    monkeypatch.setattr(indices_score.requests, "get", raise_error)
    assert fetch_article_text("https://example.com/article") is None


def test_fetch_article_text_returns_none_when_extraction_is_empty(monkeypatch):
    class FakeResponse:
        text = "<html></html>"
        def raise_for_status(self):
            pass
    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(indices_score.trafilatura, "extract", lambda html: None)
    assert fetch_article_text("https://example.com/article") is None


import indices_score
from indices_score import summarize_news_item


class _FakeAnthropicResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_summarize_news_item_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    assert summarize_news_item("Titre", "LVMH", "Texte de l'article") == {
        "summary": "", "sentiment": 0, "importance": "mineure",
    }


def test_summarize_news_item_uses_article_text_when_available(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Résumé généré.", "sentiment": 1, "importance": "notable"}'}]}
        )

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", "Contenu réel de l'article")
    assert result == {"summary": "Résumé généré.", "sentiment": 1, "importance": "notable"}
    assert "Contenu réel de l'article" in captured["json"]["messages"][0]["content"]
    assert captured["json"]["model"] == "claude-haiku-4-5-20251001"


def test_summarize_news_item_uses_headline_only_prompt_when_article_text_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Contexte prudent.", "sentiment": 0}'}]}
        )

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", None)
    assert result == {"summary": "Contexte prudent.", "sentiment": 0, "importance": "mineure"}
    assert "suggère" in captured["json"]["messages"][0]["content"]


def test_summarize_news_item_returns_empty_on_http_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_post(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    assert summarize_news_item("Titre", "LVMH", "texte") == {
        "summary": "", "sentiment": 0, "importance": "mineure",
    }


def test_summarize_news_item_returns_empty_on_malformed_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse({"unexpected": "shape"})
    )
    assert summarize_news_item("Titre", "LVMH", "texte") == {
        "summary": "", "sentiment": 0, "importance": "mineure",
    }


def test_summarize_news_item_strips_markdown_code_fences_before_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse(
            {"content": [{"text": '```json\n{"summary": "Texte.", "sentiment": -1, "importance": "majeure"}\n```'}]}
        )
    )
    result = summarize_news_item("Titre", "LVMH", "texte")
    assert result == {"summary": "Texte.", "sentiment": -1, "importance": "majeure"}


def test_summarize_news_item_returns_empty_when_response_is_not_valid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse({"content": [{"text": "Ceci n'est pas du JSON."}]})
    )
    assert summarize_news_item("Titre", "LVMH", "texte") == {
        "summary": "", "sentiment": 0, "importance": "mineure",
    }


def test_summarize_news_item_defaults_invalid_sentiment_value_to_zero(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Texte.", "sentiment": 5}'}]}
        )
    )
    result = summarize_news_item("Titre", "LVMH", "texte")
    assert result == {"summary": "Texte.", "sentiment": 0, "importance": "mineure"}


def test_summarize_news_item_defaults_invalid_importance_value_to_mineure(monkeypatch):
    """Une valeur d'importance hors des 3 niveaux attendus ne doit jamais
    se propager — une classification ratée ne doit ni gonfler le poids
    de l'actu ni déclencher à tort l'alerte "actu majeure"."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Texte.", "sentiment": 0, "importance": "critique"}'}]}
        )
    )
    result = summarize_news_item("Titre", "LVMH", "texte")
    assert result["importance"] == "mineure"


from indices_score import estimate_dcf_price


def test_estimate_dcf_price_nominal_case():
    result = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result == pytest.approx(43.8363904302955)


def test_estimate_dcf_price_clamps_growth_at_the_cap():
    over_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=50.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    at_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=15.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert over_cap == at_cap


def test_estimate_dcf_price_clamps_growth_at_the_floor():
    under_floor = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=-50.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    at_floor = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=-5.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert under_floor == at_floor


def test_estimate_dcf_price_returns_none_when_fcf_not_positive():
    assert estimate_dcf_price(
        fcf=0.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    ) is None
    assert estimate_dcf_price(
        fcf=-10.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    ) is None


def test_estimate_dcf_price_returns_none_when_shares_outstanding_is_zero():
    assert estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=0.0,
        discount_rate_pct=8.0,
    ) is None


def test_estimate_dcf_price_returns_none_when_fcf_is_nan():
    """Un FCF NaN (yfinance en produit parfois) ne doit pas passer le garde-fou
    `fcf <= 0` (NaN <= 0 vaut False) et doit dégrader vers None, pas NaN."""
    result = estimate_dcf_price(
        fcf=float("nan"), cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result is None


def test_estimate_dcf_price_returns_none_when_net_debt_is_nan():
    result = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=float("nan"), shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result is None


def test_estimate_dcf_price_varies_with_discount_rate():
    """Preuve que `discount_rate_pct` est réellement pris en compte (et pas
    silencieusement ignoré au profit d'une constante interne) : deux taux
    différents doivent produire des prix différents."""
    at_8 = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    at_10 = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=10.0,
    )
    assert at_8 is not None and at_10 is not None
    assert at_8 != at_10


def test_estimate_dcf_price_returns_none_when_discount_rate_too_close_to_terminal_growth():
    """Un WACC calculé peut, dans un régime de taux bas (ex : taux OAT
    français négatif comme en 2020, bêta faible), tomber trop près voire
    en dessous de la croissance terminale (2%) : la valeur terminale de
    Gordon dégénère alors (dénominateur proche de 0 ou négatif). Doit
    dégrader vers None plutôt que produire un prix négatif ou lever
    ZeroDivisionError."""
    assert estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=2.0,  # == DCF_TERMINAL_GROWTH : dénominateur nul
    ) is None
    assert estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=1.65,  # < DCF_TERMINAL_GROWTH : dénominateur négatif
    ) is None
    assert estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=2.5,  # écart < DCF_MIN_DISCOUNT_SPREAD (1.0 pt)
    ) is None


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
        "ecart_pct_ma200": 5.0,
        "quarterly_yoy_growth_ca": 7.0,
        "fcf": 50.0,
        "net_debt": 100.0,
        "equity": 200.0,
        "current_price": 120.0,
        "ma200": 110.0,
        "shares_outstanding": 10.0,
        "tax_rate": 0.25,
        "total_debt": 150.0,
        "beta": 1.1,
        "sector": "Consumer Defensive",
        "financial_context": "Comptes annuels (le plus récent en premier) :\n- ...",
        "latest_quarter_date": "2026-06-30",
        "is_financial": False,
    }


def _carried_forward_analysis():
    return {
        "BN.PA": {
            "financial_analysis_html": "<p>Analyse existante.</p>",
            "financial_analysis_quarter": "2026-06-30",
        }
    }


def test_build_company_entry_degrades_gracefully_when_news_fetch_fails(monkeypatch):
    """Une panne du flux RSS (fetch_news) ne doit pas faire perdre le score
    déjà calculé pour l'entreprise — seule la liste de news doit être vide,
    et le facteur Actualité récente doit rester neutre plutôt que planter."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())

    def _raise_news(name, prev=None):
        raise RuntimeError("flux RSS indisponible")

    monkeypatch.setattr(indices_score, "fetch_news", _raise_news)

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68, previous_analyses=_carried_forward_analysis(),
    )

    assert entry["news"] == []
    assert entry["ticker"] == "BN.PA"
    assert entry["name"] == "Danone"
    assert isinstance(entry["score"], float)
    assert len(entry["factors"]) == 7
    assert entry["factors"][6]["name"] == "Actualité récente"
    assert entry["factors"][6]["score"] == 0.0
    assert entry["current_price"] == 120.0
    assert entry["fair_value"] is not None
    assert entry["entry_price"] is not None
    assert entry["exit_price"] is not None
    assert entry["entry_price"] < entry["exit_price"]
    assert entry["wacc"] is not None
    assert entry["wacc"] != COST_OF_CAPITAL_PROXY  # WACC réel calculable avec _fake_ratios()
    # Le WACC calculé doit réellement atteindre le facteur Rentabilité (pas
    # seulement la clé de sortie `wacc`) : preuve que build_company_entry ne
    # calcule pas cost_of_capital pour rien en le laissant de côté au moment
    # d'appeler score_rentabilite.
    assert f"coût du capital {entry['wacc']:.1f}%" in entry["factors"][0]["raw_value"]
    # Le WACC calculé doit aussi réellement atteindre estimate_valuation_targets
    # (pas seulement score_rentabilite) : si build_company_entry retombait sur
    # COST_OF_CAPITAL_PROXY pour ce seul appel, le fair_value serait celui
    # ci-dessous plutôt que celui obtenu avec le vrai WACC.
    fair_value_with_proxy = indices_score.estimate_valuation_targets(
        _fake_ratios(), indices_score.COST_OF_CAPITAL_PROXY
    )["fair_value"]
    assert entry["fair_value"] != fair_value_with_proxy
    assert entry["financial_analysis_html"] == "<p>Analyse existante.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"


def test_build_company_entry_includes_news_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(
        indices_score, "fetch_news",
        lambda name, prev=None: [
            {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
        ],
    )

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68, previous_analyses=_carried_forward_analysis(),
    )

    assert entry["news"] == [
        {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
    ]
    assert entry["factors"][5]["name"] == "Dynamique récente"


def test_build_company_entry_falls_back_to_proxy_wacc_when_beta_missing(monkeypatch):
    """Si le bêta manque (yfinance ne le fournit pas toujours), le WACC ne
    doit pas être calculé partiellement — repli sur COST_OF_CAPITAL_PROXY
    pour cette entreprise, jamais d'exception."""
    ratios = _fake_ratios()
    ratios["beta"] = None
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68, previous_analyses=_carried_forward_analysis(),
    )

    assert entry["wacc"] == COST_OF_CAPITAL_PROXY


def test_build_company_entry_falls_back_to_proxy_wacc_when_risk_free_rate_missing(monkeypatch):
    """Si le taux sans risque n'a pas pu être récupéré pour tout le run
    (ex : FRED_API_KEY absente, panne réseau), repli sur
    COST_OF_CAPITAL_PROXY pour chaque entreprise."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=None, previous_analyses=_carried_forward_analysis(),
    )

    assert entry["wacc"] == COST_OF_CAPITAL_PROXY


from indices_score import fetch_news


def test_fetch_news_attaches_source_and_summary_and_isolates_per_item_failures(monkeypatch):
    xml_two_items = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Titre A</title>
  <link>https://example.com/a</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://a.example.com">Source A</source>
</item>
<item>
  <title>Titre B</title>
  <link>https://example.com/b</link>
  <pubDate>Wed, 03 Sep 2026 08:00:00 GMT</pubDate>
  <source url="https://b.example.com">Source B</source>
</item>
</channel></rss>
"""

    class FakeRssResponse:
        content = xml_two_items

        def raise_for_status(self):
            pass

    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeRssResponse())
    # Simule un échec d'extraction sur l'article A (fetch_article_text
    # renvoie None, comme sur un vrai paywall) et un succès sur B — sans
    # jamais lever, conformément au contrat de fetch_article_text.
    monkeypatch.setattr(
        indices_score, "fetch_article_text",
        lambda url: None if url == "https://example.com/a" else "Texte B"
    )
    monkeypatch.setattr(
        indices_score, "summarize_news_item",
        lambda title, name, text: {
            "summary": f"Résumé pour {title} (article={text})",
            "sentiment": 1 if text is None else -1,
            "importance": "mineure" if text is None else "majeure",
        }
    )

    items = fetch_news("Test SA")

    assert len(items) == 2
    assert items[0]["source"] == "Source A"
    assert items[0]["summary"] == "Résumé pour Titre A (article=None)"
    assert items[0]["sentiment"] == 1
    assert items[0]["importance"] == "mineure"
    assert items[1]["source"] == "Source B"
    assert items[1]["summary"] == "Résumé pour Titre B (article=Texte B)"
    assert items[1]["sentiment"] == -1
    assert items[1]["importance"] == "majeure"


def test_fetch_news_reuses_previous_classification_instead_of_reclassifying(monkeypatch):
    """Une actu déjà classée lors d'un run précédent (par lien) ne doit
    pas être renvoyée à Claude — sa classification reste stable d'un run
    à l'autre au lieu de risquer de varier (ex : "majeure" un jour,
    "mineure" le lendemain pour le même article)."""
    xml_one_item = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Titre A</title>
  <link>https://example.com/a</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://a.example.com">Source A</source>
</item>
</channel></rss>
"""

    class FakeRssResponse:
        content = xml_one_item

        def raise_for_status(self):
            pass

    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeRssResponse())

    def _fail_if_called(*a, **k):
        raise AssertionError("summarize_news_item ne doit pas être appelée pour une actu déjà classée")

    monkeypatch.setattr(indices_score, "fetch_article_text", _fail_if_called)
    monkeypatch.setattr(indices_score, "summarize_news_item", _fail_if_called)

    previous_classifications = {
        "https://example.com/a": {"summary": "Résumé mis en cache", "sentiment": 1, "importance": "majeure"},
    }
    items = fetch_news("Test SA", previous_classifications)

    assert len(items) == 1
    assert items[0]["summary"] == "Résumé mis en cache"
    assert items[0]["sentiment"] == 1
    assert items[0]["importance"] == "majeure"


def test_fetch_news_classifies_new_items_not_in_previous_classifications(monkeypatch):
    """Une actu absente du cache (nouvelle, ou run précédent inexistant)
    doit bien être classée normalement — le cache ne doit pas empêcher
    la classification des nouvelles actus."""
    xml_one_item = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Titre B</title>
  <link>https://example.com/b</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
  <source url="https://b.example.com">Source B</source>
</item>
</channel></rss>
"""

    class FakeRssResponse:
        content = xml_one_item

        def raise_for_status(self):
            pass

    monkeypatch.setattr(indices_score.requests, "get", lambda *a, **k: FakeRssResponse())
    monkeypatch.setattr(indices_score, "fetch_article_text", lambda url: "Texte B")
    monkeypatch.setattr(
        indices_score, "summarize_news_item",
        lambda title, name, text: {"summary": "Résumé frais", "sentiment": -1, "importance": "notable"},
    )

    # Cache non vide, mais pour un autre lien — l'actu B doit être classée.
    previous_classifications = {
        "https://example.com/a": {"summary": "Autre", "sentiment": 0, "importance": "mineure"},
    }
    items = fetch_news("Test SA", previous_classifications)

    assert items[0]["summary"] == "Résumé frais"
    assert items[0]["importance"] == "notable"


from indices_score import estimate_asset_based_price, estimate_multiple_based_price


def test_estimate_asset_based_price_nominal_case():
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0) == 4.0


def test_estimate_asset_based_price_returns_none_when_equity_not_positive():
    assert estimate_asset_based_price(equity=0.0, shares_outstanding=50.0) is None
    assert estimate_asset_based_price(equity=-10.0, shares_outstanding=50.0) is None


def test_estimate_asset_based_price_returns_none_when_shares_outstanding_is_zero():
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=0.0) is None


def test_estimate_asset_based_price_returns_none_when_equity_is_nan():
    """Des capitaux propres NaN ne doivent pas passer le garde-fou
    `equity <= 0` (NaN <= 0 vaut False) et doivent dégrader vers None."""
    result = estimate_asset_based_price(equity=float("nan"), shares_outstanding=50.0)
    assert result is None


def test_estimate_multiple_based_price_nominal_case():
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=10.0, avg_ev_ebitda_5y=8.0
    )
    assert result == 80.0


def test_estimate_multiple_based_price_returns_none_when_current_multiple_is_zero():
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=0.0, avg_ev_ebitda_5y=8.0
    )
    assert result is None


def test_estimate_multiple_based_price_returns_none_when_current_ev_ebitda_is_nan():
    """Un multiple EV/EBITDA NaN ne doit pas passer le garde-fou `not
    current_ev_ebitda` (NaN est "truthy") et doit dégrader vers None."""
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=float("nan"), avg_ev_ebitda_5y=8.0
    )
    assert result is None


import indices_score
from indices_score import fetch_risk_free_rate


class _FakeFredResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_risk_free_rate_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "get", fail_if_called)
    assert fetch_risk_free_rate() is None


def test_fetch_risk_free_rate_returns_latest_observation(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({
            "observations": [
                {"value": "3.60"},
                {"value": "3.68"},
            ]
        })
    )
    assert fetch_risk_free_rate() == 3.68


def test_fetch_risk_free_rate_ignores_missing_observations(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({
            "observations": [
                {"value": "3.60"},
                {"value": "."},
            ]
        })
    )
    assert fetch_risk_free_rate() == 3.60


def test_fetch_risk_free_rate_returns_none_on_empty_observations(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({"observations": []})
    )
    assert fetch_risk_free_rate() is None


def test_fetch_risk_free_rate_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")

    def raise_error(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "get", raise_error)
    assert fetch_risk_free_rate() is None


from indices_score import _size_premium, estimate_wacc


def test_size_premium_mega_cap():
    assert _size_premium(60_000_000_000) == 0.0


def test_size_premium_large_cap():
    assert _size_premium(30_000_000_000) == 0.5


def test_size_premium_mid_cap():
    assert _size_premium(5_000_000_000) == 1.5


def test_size_premium_small_cap():
    assert _size_premium(1_000_000_000) == 3.0


def test_size_premium_boundary_is_strict():
    """Une capitalisation pile au seuil ne doit PAS obtenir la tranche
    supérieure (comparaison stricte `>`)."""
    assert _size_premium(50_000_000_000) == 0.5
    assert _size_premium(10_000_000_000) == 1.5
    assert _size_premium(2_000_000_000) == 3.0


def test_estimate_wacc_nominal_case():
    # Re = 3.68 + 1.2*5.0 + 0.0 (méga cap) = 9.68
    # Rd_after_tax = 3.0 * (1 - 0.25) = 2.25
    # E=100, D=50 -> poids E=100/150, D=50/150
    # WACC = (100/150)*9.68 + (50/150)*2.25 = 6.4533... + 0.75 = 7.2033...
    result = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    )
    assert result == pytest.approx(7.203333333333333)


def test_estimate_wacc_returns_none_when_risk_free_rate_missing():
    assert estimate_wacc(
        risk_free_rate=None, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_beta_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=None, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_market_cap_not_positive():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=0.0,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=-1.0,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_total_debt_negative():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=-1.0, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_tax_rate_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=None,
    ) is None


def test_estimate_wacc_returns_none_when_any_input_is_nan():
    assert estimate_wacc(
        risk_free_rate=float("nan"), beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_beta_is_non_numeric():
    """yfinance renvoie `info` comme un dict non typé — un bêta remonté sous
    une forme inattendue (ex : chaîne de caractères) ne doit pas faire
    lever d'exception (`_is_missing` ne détecte que None/NaN, pas les
    types incompatibles avec l'arithmétique)."""
    assert estimate_wacc(
        risk_free_rate=3.68, beta="1.2", market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


from indices_score import estimate_fair_value


def test_estimate_fair_value_averages_all_three_methods():
    result = estimate_fair_value(dcf_price=40.0, asset_price=50.0, multiple_price=60.0)
    assert result == 50.0


def test_estimate_fair_value_averages_available_methods_when_one_is_missing():
    result = estimate_fair_value(dcf_price=40.0, asset_price=None, multiple_price=60.0)
    assert result == 50.0


def test_estimate_fair_value_returns_none_when_no_method_is_available():
    assert estimate_fair_value(dcf_price=None, asset_price=None, multiple_price=None) is None


from indices_score import estimate_entry_exit_prices


def test_estimate_entry_exit_prices_combines_valuation_and_technical():
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0)
    assert result == {"entry": 80.0, "exit": 119.0}


def test_estimate_entry_exit_prices_uses_only_valuation_when_ma200_missing():
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=1.0, ecart_pct_ma200=0.0)
    assert result == {"entry": 70.0, "exit": 130.0}


def test_estimate_entry_exit_prices_uses_only_technical_when_fair_value_missing():
    result = estimate_entry_exit_prices(fair_value=None, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0)
    assert result == {"entry": 90.0, "exit": 108.0}


def test_estimate_entry_exit_prices_returns_none_for_both_when_nothing_available():
    result = estimate_entry_exit_prices(fair_value=None, ma200=None, beta=1.0, ecart_pct_ma200=0.0)
    assert result == {"entry": None, "exit": None}


def test_estimate_entry_exit_prices_ignores_ma200_when_it_is_nan():
    """Une MM200 NaN (calculable seulement avec un historique de cours
    insuffisant) ne doit pas être traitée comme un candidat valide — le
    garde-fou `ma200 is not None` ne suffit pas à l'exclure."""
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=float("nan"), beta=1.0, ecart_pct_ma200=0.0)
    assert result == {"entry": 70.0, "exit": 130.0}


def test_estimate_entry_exit_prices_widens_margin_for_high_beta():
    """Une action volatile (bêta > 1) doit avoir une marge de sécurité
    plus large qu'une action neutre — entrée plus basse, sortie plus haute."""
    neutral = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=1.0, ecart_pct_ma200=0.0)
    volatile = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=1.5, ecart_pct_ma200=0.0)
    assert volatile["entry"] < neutral["entry"]
    assert volatile["exit"] > neutral["exit"]


def test_estimate_entry_exit_prices_narrows_margin_for_low_beta():
    """Une action stable (bêta < 1) doit avoir une marge plus resserrée."""
    neutral = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=1.0, ecart_pct_ma200=0.0)
    stable = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=0.6, ecart_pct_ma200=0.0)
    assert stable["entry"] > neutral["entry"]
    assert stable["exit"] < neutral["exit"]


def test_estimate_entry_exit_prices_falls_back_to_base_margin_when_beta_missing():
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=None, beta=None, ecart_pct_ma200=0.0)
    assert result == {"entry": 70.0, "exit": 130.0}  # marge de base (30%), comportement inchangé


def test_estimate_entry_exit_prices_shifts_technical_entry_down_in_downtrend():
    """Une tendance baissière prononcée (écart MM200 très négatif) doit
    décaler le repère technique sous la MM200 elle-même — évite de
    recommander une entrée juste parce que le prix est sous sa moyenne
    (le piège classique du "couteau qui tombe")."""
    flat = estimate_entry_exit_prices(fair_value=None, ma200=100.0, beta=1.0, ecart_pct_ma200=0.0)
    downtrend = estimate_entry_exit_prices(fair_value=None, ma200=100.0, beta=1.0, ecart_pct_ma200=-20.0)
    assert downtrend["entry"] < flat["entry"]
    assert downtrend["exit"] < flat["exit"]


def test_estimate_entry_exit_prices_shifts_technical_entry_up_in_uptrend():
    """Une tendance haussière confirmée doit décaler le repère technique
    au-dessus de la MM200 plutôt que d'attendre un retour qui peut ne
    jamais venir."""
    flat = estimate_entry_exit_prices(fair_value=None, ma200=100.0, beta=1.0, ecart_pct_ma200=0.0)
    uptrend = estimate_entry_exit_prices(fair_value=None, ma200=100.0, beta=1.0, ecart_pct_ma200=20.0)
    assert uptrend["entry"] > flat["entry"]
    assert uptrend["exit"] > flat["exit"]


from indices_score import estimate_valuation_targets


def test_estimate_valuation_targets_computes_all_three_output_keys():
    data = {
        "fcf": 50.0,
        "cagr_ebitda": 6.5,
        "net_debt": 100.0,
        "shares_outstanding": 10.0,
        "equity": 200.0,
        "current_price": 120.0,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "ma200": 110.0,
        "beta": 1.0,
        "ecart_pct_ma200": 0.0,
        "fcf_normalized": 50.0,
        "sector": "Unknown",
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0)
    assert set(result.keys()) == {"fair_value", "entry_price", "exit_price"}
    assert result["fair_value"] is not None
    assert result["entry_price"] is not None
    assert result["exit_price"] is not None
    assert result["entry_price"] < result["exit_price"]


def test_estimate_valuation_targets_varies_with_cost_of_capital():
    """Preuve que `cost_of_capital` est réellement transmis à la composante
    DCF (et pas silencieusement remplacé par une constante interne) : deux
    taux différents doivent produire des juste valeurs différentes."""
    data = {
        "fcf": 50.0,
        "cagr_ebitda": 6.5,
        "net_debt": 100.0,
        "shares_outstanding": 10.0,
        "equity": 200.0,
        "current_price": 120.0,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "ma200": 110.0,
        "beta": 1.0,
        "ecart_pct_ma200": 0.0,
        "fcf_normalized": 50.0,
        "sector": "Unknown",
    }
    at_8 = estimate_valuation_targets(data, cost_of_capital=8.0)
    at_10 = estimate_valuation_targets(data, cost_of_capital=10.0)
    assert at_8["fair_value"] != at_10["fair_value"]


def test_estimate_valuation_targets_degrades_to_none_with_nan_inputs():
    """Une valeur manquante (NaN, comme yfinance en produit parfois) ne doit
    jamais se propager jusqu'en sortie — toujours None, jamais NaN, pour
    rester sérialisable en JSON valide."""
    import math
    data = {
        "fcf": float("nan"),
        "cagr_ebitda": 6.5,
        "net_debt": 100.0,
        "shares_outstanding": 10.0,
        "equity": float("nan"),
        "current_price": 120.0,
        "current_ev_ebitda": float("nan"),
        "avg_ev_ebitda_5y": 10.0,
        "ma200": float("nan"),
        "beta": 1.0,
        "ecart_pct_ma200": 0.0,
        "fcf_normalized": float("nan"),
        "sector": "Unknown",
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0)
    assert result["fair_value"] is None
    assert result["entry_price"] is None
    assert result["exit_price"] is None
    for value in result.values():
        assert value is None or not (isinstance(value, float) and math.isnan(value))


def test_estimate_fair_value_weighs_dcf_more_for_defensive_sector():
    """Le DCF est plus fiable pour une entreprise défensive (flux
    prévisibles) — doit peser plus dans la moyenne qu'une entreprise
    standard, quand DCF diverge des deux autres méthodes."""
    standard = estimate_fair_value(100.0, 50.0, 50.0, sector_profile="standard")
    defensif = estimate_fair_value(100.0, 50.0, 50.0, sector_profile="defensif")
    assert defensif > standard  # DCF (le plus haut des 3) pèse plus lourd


def test_estimate_fair_value_weighs_dcf_less_for_cyclical_sector():
    """Le DCF est moins fiable pour une cyclique (point de départ possible
    en haut/bas de cycle) — doit peser moins qu'une entreprise standard."""
    standard = estimate_fair_value(100.0, 50.0, 50.0, sector_profile="standard")
    cyclique = estimate_fair_value(100.0, 50.0, 50.0, sector_profile="cyclique")
    assert cyclique < standard  # DCF (le plus haut des 3) pèse moins lourd


def test_estimate_fair_value_unknown_sector_falls_back_to_equal_weights():
    """Un profil sectoriel inconnu ne doit pas planter — replie sur une
    pondération égale, identique à l'ancien comportement (moyenne simple)."""
    result = estimate_fair_value(100.0, 50.0, 50.0, sector_profile="inconnu")
    assert result == pytest.approx((100.0 + 50.0 + 50.0) / 3)


def test_estimate_fair_value_reweights_when_a_method_is_unavailable():
    """Quand une méthode manque (None), les poids restants doivent se
    renormaliser plutôt que de traiter la méthode absente comme un zéro."""
    result = estimate_fair_value(None, 50.0, 100.0, sector_profile="cyclique")
    weights = indices_score.VALUATION_METHOD_WEIGHTS["cyclique"]
    expected = (50.0 * weights["asset"] + 100.0 * weights["multiple"]) / (weights["asset"] + weights["multiple"])
    assert result == pytest.approx(expected)


def test_extract_ratios_computes_fcf_normalized_over_recent_window():
    """fcf_normalized doit moyenner OCF+capex sur la même fenêtre récente
    que le lissage du CAGR (2 exercices ici), pas seulement le dernier —
    utilisé comme point de départ du DCF pour les cycliques plutôt que le
    seul dernier exercice, qui peut être en haut ou en bas de cycle."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    expected_recent_fcf = [
        (120.0, -30.0),  # exercice le plus récent (OCF, capex) — voir _make_fixture_statements
        (110.0, -28.0),  # exercice précédent
    ]
    expected = sum(ocf + capex for ocf, capex in expected_recent_fcf) / len(expected_recent_fcf)
    assert ratios["fcf_normalized"] == pytest.approx(expected)
    assert ratios["fcf_normalized"] != ratios["fcf"]  # le lissage change bien la valeur


def test_estimate_valuation_targets_uses_normalized_fcf_for_cyclical_companies():
    """Preuve bout en bout que le profil cyclique bascule bien sur
    fcf_normalized (pas fcf) pour le DCF — deux valeurs différentes
    doivent produire des juste valeurs différentes."""
    base_data = {
        "cagr_ebitda": 6.5, "net_debt": 100.0, "shares_outstanding": 10.0,
        "equity": 200.0, "current_price": 120.0, "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0, "ma200": 110.0, "beta": 1.0, "ecart_pct_ma200": 0.0,
        "sector": "Consumer Cyclical",  # -> profil "cyclique" (SECTOR_PROFILES)
    }
    low_fcf = {**base_data, "fcf": 999.0, "fcf_normalized": 20.0}
    high_fcf = {**base_data, "fcf": 999.0, "fcf_normalized": 80.0}

    result_low = estimate_valuation_targets(low_fcf, cost_of_capital=8.0)
    result_high = estimate_valuation_targets(high_fcf, cost_of_capital=8.0)

    assert result_low["fair_value"] != result_high["fair_value"]  # fcf_normalized utilisé, pas fcf (identique aux 2)


def test_load_indices_history_returns_empty_list_when_file_absent(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    assert indices_score.load_indices_history(path=str(missing_path)) == []


def test_load_indices_history_returns_empty_list_on_corrupted_json(tmp_path):
    corrupted_path = tmp_path / "corrupted.json"
    corrupted_path.write_text("{not valid json", encoding="utf-8")
    assert indices_score.load_indices_history(path=str(corrupted_path)) == []


def test_append_indices_history_adds_new_entries(tmp_path):
    path = tmp_path / "history.json"
    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}],
        path=str(path),
    )
    assert result == [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}]
    assert indices_score.load_indices_history(path=str(path)) == result


def test_append_indices_history_trims_independently_per_ticker(tmp_path):
    """Ajouter une entrée au ticker A ne doit jamais tronquer l'historique
    du ticker B — chaque ticker garde sa propre fenêtre de rétention."""
    import json
    path = tmp_path / "history.json"
    existing = (
        [{"date": f"2020-01-{i:02d}", "ticker": "MC.PA", "composite": float(i)} for i in range(1, 10)]
        + [{"date": f"2020-01-{i:02d}", "ticker": "TTE.PA", "composite": float(i)} for i in range(1, 5)]
    )
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}],
        path=str(path),
    )
    tte_entries = [e for e in result if e["ticker"] == "TTE.PA"]
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(tte_entries) == 4  # inchangé
    assert len(mc_entries) == 10  # 9 existantes + 1 nouvelle
    assert mc_entries[-1] == {"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}


def test_append_indices_history_retains_only_last_730_entries_per_ticker(tmp_path):
    import json
    path = tmp_path / "history.json"
    existing = [
        {"date": f"2020-{(i % 12) + 1:02d}-01", "ticker": "MC.PA", "composite": float(i)}
        for i in range(735)
    ]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}],
        path=str(path),
    )
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(mc_entries) == 730
    assert mc_entries[-1]["composite"] == 42.0


def test_compute_company_alerts_returns_info_when_nothing_triggers():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[],
    )
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "info"


def test_compute_company_alerts_watch_when_score_crosses_15_upward():
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": 10.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" in kinds


def test_compute_company_alerts_no_watch_when_already_above_15():
    """Ne doit se déclencher qu'au franchissement, pas rester actif en continu."""
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": 20.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=22.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" not in kinds


def test_compute_company_alerts_risque_on_rapid_drop():
    from datetime import datetime, timedelta
    recent_date = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    previous_history = [{"date": recent_date, "ticker": "BN.PA", "composite": 40.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=15.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" in kinds


def test_compute_company_alerts_no_risque_when_drop_outside_window():
    from datetime import datetime, timedelta
    old_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    previous_history = [{"date": old_date, "ticker": "BN.PA", "composite": 40.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=15.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" not in kinds


def test_compute_company_alerts_entree_when_score_favorable_and_price_near_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=102.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" in kinds


def test_compute_company_alerts_no_entree_when_price_far_from_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=130.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_no_entree_when_score_not_favorable():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=101.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_handles_missing_current_or_entry_price():
    """Ne doit jamais lever, même si le cours ou le repère d'entrée est
    manquant (yfinance en panne, valorisation non calculable ce jour-là)."""
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=None, entry_price=None,
        previous_history=[],
    )
    assert isinstance(alerts, list)
    assert len(alerts) >= 1


def test_compute_company_alerts_ignores_malformed_dates():
    """Ne doit jamais lever sur une date malformée dans previous_history.
    Doit ignorer silencieusement l'entrée malformée et continuer le calcul."""
    from datetime import datetime, timedelta
    recent_date = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    previous_history = [
        {"date": "pas-une-date", "ticker": "BN.PA", "composite": 40.0},  # malformed
        {"date": recent_date, "ticker": "BN.PA", "composite": 40.0},      # valid
    ]
    # Ne doit pas lever, et doit détecter la chute de 40->15 en ignorant l'entrée malformée
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=15.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    # Doit encore détecter "risque" car l'entrée valide est récente et trigger le seuil
    assert "risque" in kinds


def test_compute_company_alerts_actu_majeure_when_recent():
    news_items = [{
        "title": "Rachat surprise annoncé", "link": "https://example.com/a",
        "date": _days_ago(1), "summary": "Résumé.", "importance": "majeure",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    kinds = [a["kind"] for a in alerts]
    assert "actu_majeure" in kinds
    assert "info" not in kinds  # pas "pas de signal actif" en même temps qu'une vraie actu majeure


def test_compute_company_alerts_no_actu_majeure_for_non_majeure_news():
    news_items = [{
        "title": "Petite mention", "link": "https://example.com/a",
        "date": _days_ago(1), "summary": "Résumé.", "importance": "notable",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" not in [a["kind"] for a in alerts]


def test_compute_company_alerts_actu_majeure_persists_even_if_already_emailed():
    """compute_company_alerts ne dédoublonne plus par lien — l'alerte doit
    rester affichée (panneau Alertes + badge du site) tant que l'actu est
    dans sa fenêtre de pertinence, même si un email a déjà été envoyé pour
    elle. Le dédoublonnage "ne pas ré-envoyer un email" est de la
    responsabilité de _attach_alerts_and_update_history, pas de cette
    fonction — voir test_attach_alerts_and_update_history_does_not_reemail_persisting_actu_majeure."""
    news_items = [{
        "title": "Rachat surprise annoncé", "link": "https://example.com/a",
        "date": _days_ago(1), "summary": "Résumé.", "importance": "majeure",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" in [a["kind"] for a in alerts]


def test_compute_company_alerts_no_actu_majeure_outside_news_window():
    news_items = [{
        "title": "Vieille actu majeure", "link": "https://example.com/a",
        "date": _days_ago(30), "summary": "Résumé.", "importance": "majeure",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" not in [a["kind"] for a in alerts]


def test_compute_company_alerts_no_actu_majeure_without_link():
    """Une actu majeure sans lien ne peut pas être suivie de façon fiable
    (impossible de savoir si elle a déjà été signalée) — ignorée plutôt
    que de risquer un spam quotidien."""
    news_items = [{
        "title": "Rachat surprise annoncé", "link": "",
        "date": _days_ago(1), "summary": "Résumé.", "importance": "majeure",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" not in [a["kind"] for a in alerts]


def test_attach_alerts_and_update_history_sets_alerts_key(monkeypatch):
    companies = [
        {"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0},
        {"ticker": "MC.PA", "score": 5.0, "current_price": 200.0, "entry_price": 150.0},
    ]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    recorded = {}
    monkeypatch.setattr(
        indices_score, "append_indices_history",
        lambda entries: recorded.setdefault("entries", entries),
    )

    indices_score._attach_alerts_and_update_history(companies)

    assert isinstance(companies[0]["alerts"], list)
    assert len(companies[0]["alerts"]) >= 1
    assert isinstance(companies[1]["alerts"], list)
    assert recorded["entries"] == [
        {"date": recorded["entries"][0]["date"], "ticker": "BN.PA", "composite": 20.0},
        {"date": recorded["entries"][1]["date"], "ticker": "MC.PA", "composite": 5.0},
    ]


def test_attach_alerts_and_update_history_filters_history_per_ticker(monkeypatch):
    """L'historique passé à compute_company_alerts pour une entreprise ne
    doit contenir que les entrées de son propre ticker."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0}]
    mixed_history = [
        {"date": "2026-09-01", "ticker": "MC.PA", "composite": 99.0},
        {"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0},
    ]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: mixed_history)
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)

    captured = {}
    original = indices_score.compute_company_alerts

    def _spy(ticker, composite, current_price, entry_price, previous_history, **kwargs):
        captured["previous_history"] = previous_history
        return original(ticker, composite, current_price, entry_price, previous_history, **kwargs)

    monkeypatch.setattr(indices_score, "compute_company_alerts", _spy)

    indices_score._attach_alerts_and_update_history(companies)

    assert captured["previous_history"] == [{"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0}]


def test_attach_alerts_and_update_history_degrades_gracefully_on_failure(monkeypatch):
    """Une panne de lecture/écriture de l'historique (disque plein,
    permissions...) ne doit jamais faire lever d'exception ni empêcher la
    publication du score déjà calculé pour chaque entreprise."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0}]

    def _raise():
        raise OSError("disque plein")

    monkeypatch.setattr(indices_score, "load_indices_history", _raise)

    indices_score._attach_alerts_and_update_history(companies)  # ne doit pas lever

    assert companies[0]["alerts"] == []


def test_load_previous_alert_kinds_returns_empty_dict_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_previous_alert_kinds() == {}


def test_load_previous_alert_kinds_extracts_kinds_per_ticker(tmp_path, monkeypatch):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps({
        "companies": [
            {"ticker": "BN.PA", "alerts": [{"kind": "entree"}, {"kind": "watch"}]},
            {"ticker": "MC.PA", "alerts": [{"kind": "info"}]},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    result = indices_score.load_previous_alert_kinds()
    assert result["BN.PA"] == {"entree", "watch"}
    assert result["MC.PA"] == {"info"}


def test_load_previous_alerted_news_links_returns_empty_dict_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_previous_alerted_news_links() == {}


def test_load_previous_alerted_news_links_extracts_links_per_ticker(tmp_path, monkeypatch):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps({
        "companies": [
            {"ticker": "BN.PA", "alerts": [
                {"kind": "actu_majeure", "link": "https://example.com/a"},
                {"kind": "entree"},  # pas de champ "link" pertinent, ignoré
            ]},
            {"ticker": "MC.PA", "alerts": [{"kind": "info"}]},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    result = indices_score.load_previous_alerted_news_links()
    assert result["BN.PA"] == {"https://example.com/a"}
    assert result["MC.PA"] == set()


def test_attach_alerts_and_update_history_flags_newly_triggered_entree_signal(monkeypatch):
    """Une entreprise dont le signal "entree" apparaît aujourd'hui, sans
    être actif hier, doit être renvoyée par _attach_alerts_and_update_history
    — c'est ce que main() utilise pour déclencher l'email d'alerte."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 100.0, "entry_price": 100.0}]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "load_previous_alert_kinds", lambda: {})

    newly_triggered_entree, newly_triggered_major_news = indices_score._attach_alerts_and_update_history(companies)

    assert [c["ticker"] for c in newly_triggered_entree] == ["BN.PA"]
    assert newly_triggered_major_news == []


def test_attach_alerts_and_update_history_does_not_reflag_persisting_entree_signal(monkeypatch):
    """Une entreprise dont le signal "entree" était déjà actif hier ne
    doit pas être renvoyée à nouveau aujourd'hui — évite un email par
    jour tant que le cours reste proche du repère d'entrée."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 100.0, "entry_price": 100.0}]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "load_previous_alert_kinds", lambda: {"BN.PA": {"entree"}})

    newly_triggered_entree, newly_triggered_major_news = indices_score._attach_alerts_and_update_history(companies)

    assert newly_triggered_entree == []
    assert newly_triggered_major_news == []


def test_attach_alerts_and_update_history_flags_new_actu_majeure_link(monkeypatch):
    """Une actu majeure dont le lien n'a jamais été signalé doit déclencher
    un email — c'est ce que main() utilise pour l'alerte actu majeure."""
    companies = [{
        "ticker": "BN.PA", "name": "Danone", "score": 5.0, "current_price": 100.0, "entry_price": 50.0,
        "news": [{"title": "Rachat surprise", "link": "https://example.com/a",
                  "date": datetime.today().strftime("%Y-%m-%d"), "summary": "Résumé.", "importance": "majeure"}],
    }]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "load_previous_alert_kinds", lambda: {})
    monkeypatch.setattr(indices_score, "load_previous_alerted_news_links", lambda: {})

    newly_triggered_entree, newly_triggered_major_news = indices_score._attach_alerts_and_update_history(companies)

    assert len(newly_triggered_major_news) == 1
    assert newly_triggered_major_news[0][0]["ticker"] == "BN.PA"
    assert newly_triggered_major_news[0][1]["link"] == "https://example.com/a"


def test_attach_alerts_and_update_history_persists_actu_majeure_alert_but_does_not_reemail(monkeypatch):
    """Une actu majeure déjà signalée (lien connu de la veille) doit rester
    dans company["alerts"] — panneau Alertes + badge du site restent
    corrects — mais ne doit PAS redéclencher un email chaque jour."""
    companies = [{
        "ticker": "BN.PA", "name": "Danone", "score": 5.0, "current_price": 100.0, "entry_price": 50.0,
        "news": [{"title": "Rachat surprise", "link": "https://example.com/a",
                  "date": datetime.today().strftime("%Y-%m-%d"), "summary": "Résumé.", "importance": "majeure"}],
    }]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "load_previous_alert_kinds", lambda: {})
    monkeypatch.setattr(
        indices_score, "load_previous_alerted_news_links", lambda: {"BN.PA": {"https://example.com/a"}},
    )

    newly_triggered_entree, newly_triggered_major_news = indices_score._attach_alerts_and_update_history(companies)

    assert newly_triggered_major_news == []
    assert "actu_majeure" in [a["kind"] for a in companies[0]["alerts"]]


def test_send_entry_alert_email_returns_false_when_companies_empty():
    assert indices_score.send_entry_alert_email([]) is False


def test_send_entry_alert_email_returns_false_when_smtp_credentials_missing(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    companies = [{"ticker": "BN.PA", "name": "Danone", "score": 20.0, "current_price": 100.0, "entry_price": 100.0}]
    assert indices_score.send_entry_alert_email(companies) is False


def _fake_entry_alert_company(**overrides):
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "score": 20.0, "interpretation": "Solide",
        "current_price": 100.0, "entry_price": 100.0, "exit_price": 130.0,
        "alerts": [{"kind": "entree", "detail": "Score favorable, cours à moins de 5% du repère d'entrée."}],
    }
    company.update(overrides)
    return company


def test_send_entry_alert_email_sends_via_smtp_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.delenv("MAIL_TO", raising=False)
    companies = [_fake_entry_alert_company()]

    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["from_addr"] = from_addr
            sent["to_addrs"] = to_addrs
            sent["message"] = message

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _FakeSMTP)

    result = indices_score.send_entry_alert_email(companies)

    assert result is True
    assert sent["host"] == indices_score.SMTP_HOST
    assert sent["login"] == ("bot@example.com", "secret")
    assert sent["to_addrs"] == ["bot@example.com"]  # repli sur SMTP_USER si MAIL_TO absent
    assert sent["from_addr"] == "bot@example.com"
    assert sent["message"]  # le message MIME a bien été construit et envoyé


def test_send_entry_alert_email_returns_false_on_smtp_error(monkeypatch):
    """Une panne SMTP (identifiants invalides, réseau...) ne doit jamais
    faire lever d'exception ni faire échouer le run."""
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    companies = [{"ticker": "BN.PA", "name": "Danone", "score": 20.0, "current_price": 100.0, "entry_price": 100.0}]

    def _raise(host, port):
        raise OSError("connexion refusée")

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _raise)

    assert indices_score.send_entry_alert_email(companies) is False


def test_build_entry_alert_email_html_includes_company_details():
    company = _fake_entry_alert_company(current_price=63.5, entry_price=60.0)
    html = indices_score.build_entry_alert_email_html(company)
    assert "Danone" in html
    assert "BN.PA" in html
    assert "CAC 40" in html  # nom affiché de l'indice, pas la clé brute
    assert "#indices/BN.PA" in html
    assert "Score favorable" in html  # détail de l'alerte "entree" elle-même


def test_entry_alert_context_includes_dynamique_recente_and_news():
    company = _fake_entry_alert_company(
        factors=[
            {"name": "Croissance", "score": 5.0, "weight": 0.16, "raw_value": "CAGR CA +8%"},
            {"name": "Dynamique récente", "score": 7.0, "weight": 0.10, "raw_value": "Cours +12.0% vs MM200"},
        ],
        news=[
            {"title": "Danone relève ses objectifs", "source": "Les Echos", "date": "2026-09-06",
             "summary": "Résultats trimestriels supérieurs aux attentes.", "sentiment": 1},
            {"title": "Sans résumé (échec)", "source": "Reuters", "date": "2026-09-05", "summary": "", "sentiment": 0},
        ],
    )
    context = indices_score._entry_alert_context(company)
    assert "Cours +12.0% vs MM200" in context
    assert "Danone relève ses objectifs" in context
    assert "Résultats trimestriels supérieurs aux attentes." in context
    assert "Sans résumé (échec)" not in context  # actu sans résumé exploitable, exclue


def test_entry_alert_context_empty_when_no_data():
    company = _fake_entry_alert_company(factors=[], news=[])
    assert indices_score._entry_alert_context(company) == ""


def test_build_entry_alert_email_html_omits_context_heading_when_no_context():
    company = _fake_entry_alert_company(factors=[], news=[])
    html = indices_score.build_entry_alert_email_html(company)
    assert "Pourquoi ce signal" not in html


def _fake_major_news_alert(**overrides):
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "news": [
            {"title": "Danone annonce une OPA sur un concurrent", "source": "Les Echos",
             "date": "2026-09-08", "summary": "Danone lance une offre publique d'achat.", "sentiment": 1,
             "link": "https://example.com/danone-opa"},
        ],
    }
    company.update(overrides.pop("company_overrides", {}))
    alert = {
        "kind": "actu_majeure",
        "title": "Danone annonce une OPA sur un concurrent",
        "detail": "Danone lance une offre publique d'achat.",
        "date": "2026-09-08",
        "link": "https://example.com/danone-opa",
    }
    alert.update(overrides)
    return company, alert


def test_build_major_news_alert_email_html_includes_article_details():
    company, alert = _fake_major_news_alert()
    html = indices_score.build_major_news_alert_email_html(company, alert)
    assert "Danone" in html
    assert "CAC 40" in html  # nom affiché de l'indice, pas la clé brute
    assert "Danone annonce une OPA sur un concurrent" in html
    assert "Danone lance une offre publique d'achat." in html
    assert "Les Echos" in html  # source retrouvée via le lien dans company["news"]
    assert "Favorable" in html  # sentiment de l'actu retrouvée
    assert "#indices/BN.PA" in html


def test_build_major_news_alert_email_html_defaults_when_news_item_not_found():
    """Si le lien de l'alerte ne correspond à aucune actu de company["news"]
    (ne devrait pas arriver en pratique, mais ne doit jamais planter), le
    mail reste construit avec un sentiment neutre par défaut."""
    company, alert = _fake_major_news_alert(link="https://example.com/inconnu")
    html = indices_score.build_major_news_alert_email_html(company, alert)
    assert "Neutre" in html


def test_send_major_news_alert_email_returns_false_when_triggered_empty():
    assert indices_score.send_major_news_alert_email([]) is False


def test_send_major_news_alert_email_returns_false_when_smtp_credentials_missing(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    company, alert = _fake_major_news_alert()
    assert indices_score.send_major_news_alert_email([(company, alert)]) is False


def test_send_major_news_alert_email_sends_via_smtp_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.delenv("MAIL_TO", raising=False)
    company, alert = _fake_major_news_alert()

    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["from_addr"] = from_addr
            sent["to_addrs"] = to_addrs
            sent["message"] = message
            sent["subject"] = "OPA" in message and "Danone" in message

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _FakeSMTP)

    result = indices_score.send_major_news_alert_email([(company, alert)])

    assert result is True
    assert sent["host"] == indices_score.SMTP_HOST
    assert sent["login"] == ("bot@example.com", "secret")
    assert sent["to_addrs"] == ["bot@example.com"]  # repli sur SMTP_USER si MAIL_TO absent
    assert sent["from_addr"] == "bot@example.com"
    assert sent["message"]  # le message MIME a bien été construit et envoyé


def test_send_major_news_alert_email_returns_false_on_smtp_error(monkeypatch):
    """Une panne SMTP ne doit jamais faire lever d'exception ni faire
    échouer le run."""
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    company, alert = _fake_major_news_alert()

    def _raise(host, port):
        raise OSError("connexion refusée")

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _raise)

    assert indices_score.send_major_news_alert_email([(company, alert)]) is False


def test_main_writes_alerts_key_for_every_company(monkeypatch, tmp_path):
    """Preuve que main() câble réellement _attach_alerts_and_update_history
    et écrit le résultat dans le JSON — pas seulement que la fonction
    existe en isolation. Si l'appel à _attach_alerts_and_update_history
    était supprimé de main(), ce test doit échouer."""
    import json

    sentinel_previous_analyses = {"__sentinel__": True}
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda: 3.68)
    monkeypatch.setattr(
        indices_score, "load_previous_company_analyses", lambda: sentinel_previous_analyses,
    )

    def _fake_build_company_entry(ticker, name, risk_free_rate, previous_analyses, index_key="CAC40"):
        assert previous_analyses is sentinel_previous_analyses, (
            "main() doit transmettre le previous_analyses réellement chargé "
            "par load_previous_company_analyses(), pas un dict vide/différent "
            "— sans ça, le mécanisme de carry-forward (contrôle des coûts) "
            "est silencieusement désactivé en production."
        )
        return {
            "ticker": ticker, "name": name, "index": index_key, "score": 20.0,
            "interpretation": "Solide",
            "current_price": 100.0, "entry_price": 100.0,
        }

    monkeypatch.setattr(indices_score, "build_company_entry", _fake_build_company_entry)
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(written["companies"]) == len(indices_score.COMPANIES)
    for company in written["companies"]:
        assert isinstance(company["alerts"], list)
        assert len(company["alerts"]) >= 1


def test_main_payload_includes_index_metadata(monkeypatch, tmp_path):
    """Le payload exporté doit dire quels indices il couvre (index_names),
    et chaque entreprise doit porter le bon "index" — pas une seule valeur
    globale, puisque COMPANIES mélange déjà CAC40 et DAX."""
    import json

    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda: 3.68)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40": {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["index_names"] == {"CAC40": "CAC 40", "DAX": "DAX"}
    written_by_ticker = {c["ticker"]: c["index"] for c in written["companies"]}
    for company in indices_score.COMPANIES:
        assert written_by_ticker[company["ticker"]] == company["index"]
    assert {c["index"] for c in written["companies"]} == {"CAC40", "DAX"}


import pandas as pd


def _fake_annual_df(rows: dict, cols: list) -> pd.DataFrame:
    return pd.DataFrame(rows, index=cols).T


def test_build_financial_narrative_context_formats_years_and_quarters():
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0, 900.0], "EBITDA": [200.0, 180.0],
         "EBIT": [150.0, 130.0], "Net Income": [90.0, 80.0]}, cols,
    )
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0, 450.0], "Total Debt": [300.0, 280.0],
         "Cash And Cash Equivalents": [50.0, 40.0]}, cols,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0, 160.0], "Capital Expenditure": [-60.0, -55.0]}, cols,
    )
    q_cols = [pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")]
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0, 250.0]}, q_cols)

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )

    assert "Comptes annuels" in result
    assert "2025-12-31" in result
    assert "CA 1,000" in result
    assert "dette nette 250" in result  # 300 - 50
    assert "FCF 120" in result  # 180 + (-60)
    assert "Derniers trimestres publiés" in result
    assert "2025-09-30" in result


def test_build_financial_narrative_context_handles_missing_values():
    cols = [pd.Timestamp("2025-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0], "EBITDA": [float("nan")],
         "EBIT": [150.0], "Net Income": [90.0]}, cols,
    )
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0], "Total Debt": [300.0],
         "Cash And Cash Equivalents": [50.0]}, cols,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0], "Capital Expenditure": [-60.0]}, cols,
    )
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0]}, cols)

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )

    assert "EBITDA non disponible" in result


def test_build_financial_narrative_context_handles_empty_quarterly_frame():
    cols = [pd.Timestamp("2025-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0], "EBITDA": [200.0],
         "EBIT": [150.0], "Net Income": [90.0]}, cols,
    )
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0], "Total Debt": [300.0],
         "Cash And Cash Equivalents": [50.0]}, cols,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0], "Capital Expenditure": [-60.0]}, cols,
    )
    empty_quarterly = pd.DataFrame()

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, empty_quarterly
    )

    assert "Derniers trimestres publiés" in result
    assert "Comptes annuels" in result


def test_build_financial_narrative_context_handles_column_mismatch_across_statements():
    """Reproduit un bug observé en production sur SAN.PA/BN.PA : `financials`
    remonte à une date que `balance_sheet`/`cashflow` n'ont pas (les 3
    relevés annuels yfinance n'ont pas toujours exactement les mêmes
    colonnes) — ne doit jamais lever KeyError, doit dégrader vers 'non
    disponible' pour l'année sans données de bilan/trésorerie."""
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2021-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0, 800.0], "EBITDA": [200.0, 150.0],
         "EBIT": [150.0, 110.0], "Net Income": [90.0, 70.0]}, cols,
    )
    # balance_sheet/cashflow n'ont QUE la colonne récente — 2021-12-31 absent
    recent_only = [cols[0]]
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0], "Total Debt": [300.0],
         "Cash And Cash Equivalents": [50.0]}, recent_only,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0], "Capital Expenditure": [-60.0]}, recent_only,
    )
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0]}, recent_only)

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )  # ne doit pas lever KeyError

    assert "2021-12-31" in result
    assert "dette nette non disponible" in result
    assert "FCF non disponible" in result
    assert "capitaux propres non disponible" in result


def test_extract_ratios_handles_column_mismatch_across_statements():
    """Même bug de production que ci-dessus, mais pour la boucle EV/EBITDA
    d'extract_ratios (`total_debt[col]`/`cash[col]` indexés par une date
    qui vient de `financials.columns`, pas forcément présente dans
    `balance_sheet`) — ne doit jamais lever KeyError."""
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2021-12-31")]
    financials = _fake_annual_df(
        {
            "Total Revenue": [1000.0, 800.0], "EBITDA": [200.0, 150.0],
            "EBIT": [150.0, 110.0], "Net Income": [90.0, 70.0],
            "Tax Rate For Calcs": [0.25, 0.25],
        }, cols,
    )
    recent_only = [cols[0]]
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0], "Total Debt": [300.0],
         "Cash And Cash Equivalents": [50.0]}, recent_only,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0], "Capital Expenditure": [-60.0]}, recent_only,
    )
    closes_by_year = {cols[0]: 100.0, cols[1]: 90.0}

    result = indices_score.extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert isinstance(result["current_ev_ebitda"], float)


def test_latest_quarter_date_returns_iso_string():
    cols = [pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")]
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0, 250.0]}, cols)
    assert indices_score.latest_quarter_date(quarterly_financials) == "2025-09-30"


def test_latest_quarter_date_returns_none_when_no_columns():
    quarterly_financials = pd.DataFrame()
    assert indices_score.latest_quarter_date(quarterly_financials) is None


def test_resolve_financial_analysis_date_prefers_quarterly_when_available():
    quarterly_financials = _fake_annual_df(
        {"Total Revenue": [260.0]}, [pd.Timestamp("2025-09-30")]
    )
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0]}, [pd.Timestamp("2025-12-31")]
    )
    result = indices_score._resolve_financial_analysis_date(quarterly_financials, financials)
    assert result == "2025-09-30"


def test_resolve_financial_analysis_date_falls_back_to_annual_when_quarterly_empty():
    """Reproduit un cas observé en production (LVMH, Schneider Electric,
    Danone) : quarterly_financials vide chez yfinance pour ces entreprises
    — sans ce repli, latest_quarter_date resterait None indéfiniment et
    leur analyse financière ne se régénérerait plus jamais."""
    quarterly_financials = pd.DataFrame()
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0]}, [pd.Timestamp("2025-12-31")]
    )
    result = indices_score._resolve_financial_analysis_date(quarterly_financials, financials)
    assert result == "2025-12-31"


def test_resolve_financial_analysis_date_returns_none_when_both_empty():
    quarterly_financials = pd.DataFrame()
    financials = pd.DataFrame()
    result = indices_score._resolve_financial_analysis_date(quarterly_financials, financials)
    assert result is None


def test_generate_financial_analysis_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None


def test_generate_financial_analysis_extracts_text_block_after_thinking_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": '{"analysis_html": "<h3>Diagnostic</h3><p>Solide.</p>"}'},
                ]
            }

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    result = indices_score.generate_financial_analysis("Danone", "contexte", "ratios")
    assert result == "<h3>Diagnostic</h3><p>Solide.</p>"


def test_generate_financial_analysis_strips_code_fences(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [
                    {"type": "text", "text": '```json\n{"analysis_html": "<p>OK</p>"}\n```'},
                ]
            }

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") == "<p>OK</p>"


def test_generate_financial_analysis_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": "pas du json valide"}]}

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None


def test_generate_financial_analysis_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def raise_error(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "post", raise_error)
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None


def test_generate_financial_analysis_logs_failure_instead_of_swallowing_silently(monkeypatch, capsys):
    """Une vraie panne (ex : crédit API Anthropic épuisé, rencontré en
    production) ne doit plus disparaître sans laisser de trace — sinon
    une entreprise sans analyse à reprendre par carry-forward reste
    silencieusement vide indéfiniment, sans que rien dans les logs ne le
    signale."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def raise_error(*a, **k):
        raise requests.RequestException("crédit insuffisant")

    monkeypatch.setattr(indices_score.requests, "post", raise_error)
    indices_score.generate_financial_analysis("Danone", "contexte", "ratios")

    captured = capsys.readouterr()
    assert "Danone" in captured.out
    assert "crédit insuffisant" in captured.out


import json


def test_load_previous_company_analyses_returns_empty_dict_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_previous_company_analyses() == {}


def test_load_previous_company_analyses_returns_empty_dict_on_corrupted_json(monkeypatch, tmp_path):
    path = tmp_path / "corrupted.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    assert indices_score.load_previous_company_analyses() == {}


def test_load_previous_company_analyses_indexes_by_ticker(monkeypatch, tmp_path):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps({
        "companies": [
            {"ticker": "BN.PA", "financial_analysis_html": "<p>A</p>", "financial_analysis_quarter": "2026-06-30"},
            {"ticker": "MC.PA", "financial_analysis_html": "<p>B</p>", "financial_analysis_quarter": "2026-03-31"},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    result = indices_score.load_previous_company_analyses()
    assert result["BN.PA"] == {
        "financial_analysis_html": "<p>A</p>", "financial_analysis_quarter": "2026-06-30",
        "news_classifications": {},
    }
    assert result["MC.PA"] == {
        "financial_analysis_html": "<p>B</p>", "financial_analysis_quarter": "2026-03-31",
        "news_classifications": {},
    }


def test_load_previous_company_analyses_indexes_news_classifications_by_link(monkeypatch, tmp_path):
    """Sert à fetch_news pour réutiliser la classification (résumé/
    sentiment/importance) déjà attribuée à une actu déjà vue, plutôt que
    de rappeler Claude et risquer un résultat différent d'un run à
    l'autre (voir fetch_news)."""
    path = tmp_path / "indices.json"
    path.write_text(json.dumps({
        "companies": [
            {"ticker": "BN.PA", "news": [
                {"link": "https://example.com/a", "summary": "Résumé A", "sentiment": 1, "importance": "majeure"},
                {"title": "Sans lien, ignorée"},
            ]},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    result = indices_score.load_previous_company_analyses()
    assert result["BN.PA"]["news_classifications"] == {
        "https://example.com/a": {"summary": "Résumé A", "sentiment": 1, "importance": "majeure"},
    }


def test_build_company_entry_carries_forward_analysis_when_quarter_unchanged(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])

    def fail_if_called(*a, **k):
        raise AssertionError("generate_financial_analysis ne doit pas être appelée si le trimestre est inchangé")

    monkeypatch.setattr(indices_score, "generate_financial_analysis", fail_if_called)

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Analyse existante.</p>",
            "financial_analysis_quarter": "2026-06-30",  # identique à _fake_ratios()
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Analyse existante.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"


def test_build_company_entry_regenerates_analysis_when_quarter_changed(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(
        indices_score, "generate_financial_analysis",
        lambda company_name, financial_context, ratios_summary: "<p>Nouvelle analyse.</p>",
    )

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Ancienne analyse.</p>",
            "financial_analysis_quarter": "2026-03-31",  # différent de _fake_ratios() (2026-06-30)
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Nouvelle analyse.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"


def test_build_company_entry_carries_forward_when_latest_quarter_date_is_none(monkeypatch):
    """Si latest_quarter_date est None (panne yfinance sur le trimestriel),
    ne doit pas régénérer à chaque run — coût illimité sinon."""
    ratios = _fake_ratios()
    ratios["latest_quarter_date"] = None
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])

    def fail_if_called(*a, **k):
        raise AssertionError("generate_financial_analysis ne doit pas être appelée si latest_quarter_date est None et qu'une analyse précédente existe")

    monkeypatch.setattr(indices_score, "generate_financial_analysis", fail_if_called)

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Analyse existante.</p>",
            "financial_analysis_quarter": "2026-06-30",
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Analyse existante.</p>"


def test_build_company_entry_keeps_previous_analysis_when_generation_fails(monkeypatch):
    """Si le trimestre a changé mais que generate_financial_analysis échoue
    (None), garder l'ancienne analyse valide plutôt que la remplacer par None."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(
        indices_score, "generate_financial_analysis",
        lambda company_name, financial_context, ratios_summary: None,
    )

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Ancienne analyse valide.</p>",
            "financial_analysis_quarter": "2026-03-31",  # différent de _fake_ratios() (2026-06-30)
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Ancienne analyse valide.</p>"


def test_compute_health_summary_all_present():
    companies = [{"ticker": c["ticker"]} for c in indices_score.COMPANIES]
    result = indices_score._compute_health_summary(companies)
    assert result == {
        "expected": len(indices_score.COMPANIES),
        "returned": len(indices_score.COMPANIES),
        "missing_tickers": [],
    }


def test_compute_health_summary_detects_missing_ticker():
    """Une entreprise qui a levé une exception dans main() (et n'apparaît
    donc pas dans `companies`) doit être signalée par son ticker, pas
    juste par un décompte silencieux."""
    all_tickers = [c["ticker"] for c in indices_score.COMPANIES]
    companies = [{"ticker": t} for t in all_tickers[1:]]  # le premier manque

    result = indices_score._compute_health_summary(companies)

    assert result["expected"] == len(all_tickers)
    assert result["returned"] == len(all_tickers) - 1
    assert result["missing_tickers"] == [all_tickers[0]]


def _fake_statement_with_row_count(n_rows: int) -> pd.DataFrame:
    cols = [pd.Timestamp("2025-12-31")]
    rows = {f"Row{i}": [float(i)] for i in range(n_rows)}
    return _fake_annual_df(rows, cols)


def test_fetch_statement_with_retry_returns_immediately_when_healthy(monkeypatch):
    healthy = _fake_statement_with_row_count(20)
    call_count = {"n": 0}

    class _FakeTicker:
        def __init__(self, ticker):
            call_count["n"] += 1

        @property
        def financials(self):
            return healthy

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    sleeps = []
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: sleeps.append(s))

    result = indices_score._fetch_statement_with_retry("XX.PA", "financials")

    assert result is healthy
    assert call_count["n"] == 1
    assert sleeps == []


def test_fetch_statement_with_retry_retries_on_degraded_result(monkeypatch):
    """Reproduit le motif observé en production (Air Liquide, Michelin,
    Accor) : un relevé dégradé (quasi vide) sans exception sur les
    premières tentatives, données complètes ensuite — un diagnostic
    isolé sur ces mêmes tickers avait confirmé que les données existent
    bien, juste pas toujours au premier appel dans une longue boucle."""
    degraded = _fake_statement_with_row_count(2)
    healthy = _fake_statement_with_row_count(20)
    results = [degraded, degraded, healthy]

    class _FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def financials(self):
            return results.pop(0)

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    sleeps = []
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: sleeps.append(s))

    result = indices_score._fetch_statement_with_retry("XX.PA", "financials")

    assert result is healthy
    assert len(sleeps) == 2  # 2 tentatives dégradées avant la bonne


def test_fetch_statement_with_retry_gives_up_after_max_attempts(monkeypatch):
    """Ne doit jamais boucler indéfiniment : après FETCH_RETRY_ATTEMPTS,
    renvoie le dernier résultat obtenu (même dégradé) plutôt que de
    bloquer le run pour toujours."""
    degraded = _fake_statement_with_row_count(2)

    class _FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def financials(self):
            return degraded

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    sleep_calls = {"n": 0}
    monkeypatch.setattr(
        indices_score.time, "sleep",
        lambda s: sleep_calls.__setitem__("n", sleep_calls["n"] + 1),
    )

    result = indices_score._fetch_statement_with_retry("XX.PA", "financials")

    assert result is degraded
    assert sleep_calls["n"] == indices_score.FETCH_RETRY_ATTEMPTS - 1


def test_build_financial_narrative_context_handles_quarterly_without_revenue_row():
    """Reproduit exactement le cas Air Liquide/Michelin/Accor : yfinance
    renvoie un quarterly_financials avec 1 colonne (donc pas "vide" au sens
    de len(...columns)), mais dont les seules lignes sont des compteurs
    d'actions (Diluted/Basic Average Shares) — jamais de "Total Revenue" ni
    "Operating Revenue". Avant le correctif, get_row() levait un KeyError
    non rattrapé qui faisait échouer toute l'entreprise dans main()."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    quarterly_financials = _fake_annual_df(
        {"Diluted Average Shares": [100.0], "Basic Average Shares": [98.0]},
        [pd.Timestamp("2025-06-30")],
    )

    context = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials,
    )

    assert "Derniers trimestres publiés" in context
    assert "2025-06-30" not in context  # aucune ligne trimestrielle rendue, faute de CA exploitable


# --- Profil financier (banques, assurances) --------------------------------

def _make_financial_fixture_statements():
    """Reproduit la forme réelle des comptes yfinance pour BNP.PA/GLE.PA/
    ACA.PA/CS.PA : ni EBITDA ni EBIT, mais Total Revenue/Net Income/Tax
    Rate For Calcs, Total Assets/Stockholders Equity/Total Debt/Cash, et
    Operating Cash Flow/Capital Expenditure sont bien présents (confirmé
    via un diagnostic dédié — voir FINANCIAL_SECTOR_TICKERS)."""
    years = [
        pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"),
        pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31"),
    ]
    financials = _fake_annual_df(
        {
            "Total Revenue": [1000.0, 950.0, 900.0, 850.0],
            "Net Income": [300.0, 280.0, 260.0, 240.0],
            "Tax Rate For Calcs": [0.25, 0.25, 0.25, 0.25],
        },
        years,
    )
    balance_sheet = _fake_annual_df(
        {
            "Total Assets": [50000.0, 48000.0, 46000.0, 44000.0],
            "Stockholders Equity": [3000.0, 2900.0, 2800.0, 2700.0],
            "Total Debt": [500.0, 480.0, 460.0, 440.0],
            "Cash And Cash Equivalents": [200.0, 190.0, 180.0, 170.0],
        },
        years,
    )
    cashflow = _fake_annual_df(
        {
            "Operating Cash Flow": [320.0, 300.0, 280.0, 260.0],
            "Capital Expenditure": [-10.0, -9.0, -8.0, -7.0],
        },
        years,
    )
    closes_by_year = {y: 50.0 for y in years}
    return financials, balance_sheet, cashflow, closes_by_year


def test_extract_ratios_raises_on_financial_sector_statements_without_ebitda():
    """Documente la raison d'être d'extract_ratios_financial : la fonction
    standard plante sur des comptes sans EBITDA, comme observé en
    diagnostic pour BNP/SocGen/Crédit Agricole/AXA."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    try:
        extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0)
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_extract_ratios_financial_computes_expected_keys():
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()

    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )

    for key in [
        "roe", "leverage_ratio", "cash_conversion", "cagr_ca", "cagr_net_income",
        "current_pe", "avg_pe_5y", "current_pb", "avg_pb_5y", "equity", "tax_rate",
        "total_debt", "fcf", "cagr_ebitda", "net_debt", "current_ev_ebitda", "avg_ev_ebitda_5y",
    ]:
        assert key in ratios, f"clé manquante : {key}"

    assert ratios["roe"] == pytest.approx(300.0 / 3000.0 * 100)
    assert ratios["leverage_ratio"] == pytest.approx(3000.0 / 50000.0 * 100)
    assert ratios["cash_conversion"] == pytest.approx(320.0 / 300.0 * 100)
    # Valeurs neutres : désactivent proprement le DCF et le multiple EV/EBITDA
    # dans estimate_valuation_targets (fcf <= 0 -> None, current_ev_ebitda == 0 -> None).
    assert ratios["fcf"] == 0.0
    assert ratios["net_debt"] == 0.0
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["avg_ev_ebitda_5y"] == 0.0


def test_build_financial_narrative_context_omits_ebitda_ebit_for_financial_profile():
    financials, balance_sheet, cashflow, _ = _make_financial_fixture_statements()
    quarterly_financials = _fake_annual_df(
        {"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-06-30")],
    )

    context = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials,
    )

    assert "EBITDA non disponible" in context
    assert "EBIT non disponible" in context
    assert "CA 1,000" in context or "CA 1 000" in context or "1,000" in context


def test_score_rentabilite_financiere_rewards_roe_above_cost_of_capital():
    good = indices_score.score_rentabilite_financiere(roe=15.0, cost_of_capital=8.0)
    bad = indices_score.score_rentabilite_financiere(roe=2.0, cost_of_capital=8.0)
    assert good.score > 0
    assert bad.score < 0
    assert "profil financier" in good.raw_value.lower()


def test_score_structure_financiere_bancaire_bands():
    solid = indices_score.score_structure_financiere_bancaire(leverage_ratio=8.0)
    risky = indices_score.score_structure_financiere_bancaire(leverage_ratio=2.0)
    assert solid.score == 10.0
    assert risky.score == -10.0


def test_score_croissance_financiere_reuses_score_croissance_math():
    result = indices_score.score_croissance_financiere(cagr_ca=6.0, cagr_net_income=6.0)
    expected = indices_score.score_croissance(6.0, 6.0)
    assert result.score == expected.score
    assert "résultat net" in result.raw_value
    assert "EBITDA" not in result.raw_value


def test_score_generation_cash_financiere_wider_scale_than_standard():
    """La même valeur de conversion doit produire un score moins extrême
    côté financier (échelle 40 contre 5) — signal jugé plus volatil."""
    standard = indices_score.score_generation_cash(70.0)
    financial = indices_score.score_generation_cash_financiere(70.0)
    assert abs(financial.score) < abs(standard.score)


def test_score_valorisation_financiere_uses_pb_instead_of_ev_ebitda():
    result = indices_score.score_valorisation_financiere(
        current_pe=8.0, avg_pe_5y=10.0, current_pb=0.7, avg_pb_5y=1.0, cagr_net_income=3.0,
    )
    assert "P/B" in result.raw_value
    assert "EV/EBITDA" not in result.raw_value
    assert result.score > 0  # décote sur les deux multiples -> favorable


def _fake_financial_ratios():
    return {
        "roe": 10.0, "leverage_ratio": 5.5, "cash_conversion": 90.0,
        "cagr_ca": 4.0, "cagr_net_income": 5.0,
        "current_pe": 9.0, "avg_pe_5y": 9.0, "current_pb": 0.8, "avg_pb_5y": 0.8,
        "ecart_pct_ma200": 2.0, "quarterly_yoy_growth_ca": 3.0,
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0,
        "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "equity": 500.0, "current_price": 60.0, "ma200": 58.0,
        "shares_outstanding": 20.0, "tax_rate": 0.25, "total_debt": 30.0,
        "beta": 1.0, "sector": "Financial Services",
        "financial_context": "Comptes annuels (le plus récent en premier) :\n- ...",
        "latest_quarter_date": "2026-06-30",
        "is_financial": True,
    }


def test_build_company_entry_uses_financial_factors_for_financial_sector_tickers(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    assert entry["index"] == "CAC40"
    assert entry["is_financial"] is True
    assert [f["name"] for f in entry["factors"]] == [
        "Rentabilité / création de valeur", "Structure financière / solvabilité",
        "Croissance", "Génération de cash", "Valorisation relative",
        "Dynamique récente", "Actualité récente",
    ]
    assert "profil financier" in entry["factors"][0]["raw_value"].lower()
    # fcf neutre (0.0) désactive le DCF : la juste valeur ne peut reposer
    # que sur l'approche patrimoniale (equity/shares_outstanding = 25.0),
    # qui doit rester calculable malgré l'absence de FCF/EBITDA.
    assert entry["fair_value"] is not None


def test_financial_sector_tickers_are_in_companies():
    company_tickers = {c["ticker"] for c in indices_score.COMPANIES}
    assert indices_score.FINANCIAL_SECTOR_TICKERS <= company_tickers


def test_companies_combines_cac40_and_dax_with_correct_index_tag():
    """COMPANIES doit être l'union de CAC40_COMPANIES et DAX_COMPANIES,
    chaque entreprise gardant son propre indice — pas une seule valeur
    globale (l'ancien bug qu'INDEX_KEY représentait)."""
    assert len(indices_score.COMPANIES) == (
        len(indices_score.CAC40_COMPANIES) + len(indices_score.DAX_COMPANIES)
    )
    by_ticker = {c["ticker"]: c["index"] for c in indices_score.COMPANIES}
    for c in indices_score.CAC40_COMPANIES:
        assert by_ticker[c["ticker"]] == "CAC40"
    for c in indices_score.DAX_COMPANIES:
        assert by_ticker[c["ticker"]] == "DAX"
    assert set(indices_score.INDEX_NAMES) >= {"CAC40", "DAX"}


def test_shares_outstanding_override_tickers_are_in_companies():
    company_tickers = {c["ticker"] for c in indices_score.COMPANIES}
    assert indices_score.SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS <= company_tickers


def test_fetch_company_financials_uses_market_cap_for_dual_class_share_tickers(monkeypatch):
    """Reproduit le cas Volkswagen (VOW3.DE), trouvé via un diagnostic dédié
    comparant cours × sharesOutstanding à marketCap sur les 79 entreprises :
    sharesOutstanding (206M) ne compte que les actions de préférence, alors
    que marketCap/cours reflète l'entreprise entière (~501M actions) — sans
    ce correctif, toute valorisation par action calculée à la main
    (juste valeur DCF notamment) serait surestimée d'environ 2,4x."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([81.0] * 250, index=history_index)

    class _FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def financials(self):
            return financials

        @property
        def balance_sheet(self):
            return balance_sheet

        @property
        def cashflow(self):
            return cashflow

        @property
        def quarterly_financials(self):
            return quarterly

        @property
        def info(self):
            return {
                "sharesOutstanding": 206205445, "marketCap": 40614940672,
                "beta": 1.2, "sector": "Consumer Cyclical",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("VOW3.DE")

    expected_shares = 40614940672 / 81.0
    assert ratios["shares_outstanding"] == pytest.approx(expected_shares)
    assert ratios["shares_outstanding"] > 206205445 * 2  # nettement plus que sharesOutstanding brut


def test_fetch_company_financials_ignores_market_cap_override_for_other_tickers(monkeypatch):
    """Le correctif ne doit s'appliquer qu'aux tickers de
    SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS — pour tout autre ticker,
    sharesOutstanding reste la source (ex : Stellantis, où c'est marketCap
    qui est l'outlier au même diagnostic, pas sharesOutstanding)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([4.80] * 250, index=history_index)

    class _FakeTicker:
        def __init__(self, ticker):
            pass

        @property
        def financials(self):
            return financials

        @property
        def balance_sheet(self):
            return balance_sheet

        @property
        def cashflow(self):
            return cashflow

        @property
        def quarterly_financials(self):
            return quarterly

        @property
        def info(self):
            return {
                "sharesOutstanding": 2900941252, "marketCap": 18094456832,
                "beta": 1.4, "sector": "Consumer Cyclical",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("STLAP.PA")

    assert ratios["shares_outstanding"] == 2900941252
