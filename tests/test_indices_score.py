import email
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
    result = score_rentabilite(roce=23.0, roe=15.0, cost_of_capital=8.0)
    assert result.name == "Rentabilité / création de valeur"
    assert result.weight == 0.24
    assert result.score == 10.0  # spread of +15pp caps the score at +10 (audit Minor #6)
    assert "23.0" in result.raw_value
    assert "8.0" in result.raw_value


def test_score_rentabilite_below_cost_of_capital_is_negative():
    result = score_rentabilite(roce=-7.0, roe=2.0, cost_of_capital=8.0)
    assert result.score == -10.0  # spread of -15pp floors the score at -10 (audit Minor #6)


def test_score_rentabilite_equal_to_cost_of_capital_is_neutral():
    result = score_rentabilite(roce=8.0, roe=8.0, cost_of_capital=8.0)
    assert result.score == 0.0


def test_score_rentabilite_partial_spread_scales_linearly():
    result = score_rentabilite(roce=15.5, roe=11.0, cost_of_capital=8.0)
    assert result.score == 5.0  # +7.5pp spread / 15.0pp scale * 10 = 5.0 (audit Minor #6)


def test_score_rentabilite_neutral_when_data_unavailable():
    """Audit 2026-09-13 : sans ce garde, roce=0.0 (repli de données
    manquantes) face à un vrai coût du capital positif donnerait un score
    NÉGATIF (donnée absente lue comme mauvaise performance)."""
    result = score_rentabilite(roce=0.0, roe=0.0, cost_of_capital=8.0, data_available=False)
    assert result.score == 0.0
    assert result.raw_value == "Donnée indisponible (pas de ligne EBIT exploitable chez la source de données)"


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


def test_score_structure_financiere_neutral_when_data_unavailable():
    """Audit 2026-09-13 : sans ce garde, net_debt_ebitda=0.0 (repli de
    données manquantes) donnerait à tort le MEILLEUR score possible
    (+10.0, endettement nul) au lieu d'une absence de donnée."""
    result = score_structure_financiere(
        net_debt_ebitda=0.0, icr=10.0, sector="Industrials", data_available=False,
    )
    assert result.score == 0.0
    assert result.raw_value == (
        "Donnée indisponible (pas de ligne EBITDA/EBIT exploitable chez la source de données)"
    )


from indices_score import score_croissance


def test_score_croissance_strong_aligned_growth():
    result = score_croissance(cagr_ca=12.0, cagr_ebitda=12.0)
    assert result.name == "Croissance"
    assert result.weight == 0.16
    assert result.score == 10.0  # moyenne 12% / échelle 10% -> plafonné à +10


def test_score_croissance_raw_value_does_not_claim_a_fixed_year_count():
    """audit Minor #5, point 2 : le libellé affichait "(5 ans)" alors que
    la fenêtre CAGR effective (lissage sur 2 exercices, cagr_span =
    n_years - smoothing_window) varie selon les données disponibles et
    ne vaut 5 dans aucun cas réel -- "(CAGR lissé)" reste correct sans
    revendiquer un nombre d'années précis."""
    result = score_croissance(cagr_ca=12.0, cagr_ebitda=12.0)
    assert "5 ans" not in result.raw_value
    assert "CAGR lissé" in result.raw_value


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


def test_score_generation_cash_neutral_when_data_unavailable():
    """Audit 2026-09-13 : sans ce garde, fcf_conversion=0.0 (repli de
    données manquantes) donnerait à tort le PIRE score possible (-10.0,
    conversion nulle) au lieu d'une absence de donnée."""
    result = score_generation_cash(fcf_conversion=0.0, data_available=False)
    assert result.score == 0.0
    assert result.raw_value == "Donnée indisponible (pas de ligne EBITDA exploitable chez la source de données)"


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


def test_score_valorisation_negative_pe_is_neutral_not_maximally_favorable():
    """Audit 2026-09-24, constat C4 : un P/E négatif (résultat net
    déficitaire) comparé à une moyenne 5 ans positive n'est pas une
    "décote" — avant ce correctif, `_premium_score` lisait
    current/avg_5y < 0 comme une décote MAXIMALE (+10.0, "très bon
    marché") au lieu d'un signal non comparable. Cas réel : Anglo
    American, P/E -8.8x vs moyenne 15.9x, obtenait +4.1 sur ce facteur
    avant le correctif."""
    result = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,  # EV/EBITDA neutre/légèrement pénalisant
        current_pe=-8.8, avg_pe_5y=15.9,
        cagr_ebitda=1.0,
    )
    # Le facteur combine EV/EBITDA (légèrement pénalisant) et P/E (neutre
    # depuis ce correctif, plus jamais fortement positif) -> score global
    # nettement inférieur à ce qu'un P/E lu comme "très bon marché"
    # aurait produit.
    assert result.score < 2.0


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


def test_score_valorisation_neutral_when_data_unavailable():
    """Audit 2026-09-13 : le score était déjà neutre par coïncidence dans
    ce cas (avg_5y=0.0 déclenche déjà le garde de _premium_score), mais le
    texte affiché ("EV/EBITDA 0.0x...") donnait à tort l'impression d'une
    vraie donnée à zéro plutôt que d'une absence de donnée."""
    result = score_valorisation(
        current_ev_ebitda=0.0, avg_ev_ebitda_5y=0.0,
        current_pe=0.0, avg_pe_5y=0.0,
        cagr_ebitda=0.0, data_available=False,
    )
    assert result.score == 0.0
    assert result.raw_value == (
        "Donnée indisponible (pas de ligne EBITDA/résultat net exploitable chez la source de données)"
    )


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


def test_score_actualite_recente_includes_news_dated_exactly_at_window_boundary():
    """Une actu datée pile à J-14 (NEWS_SENTIMENT_WINDOW_DAYS) doit compter
    comme "récente" — comparaison en dates pures, pas datetime.now() brut
    (qui porte l'heure d'exécution courante et excluait à tort ce cas
    limite un run sur deux selon l'heure du jour). Trouvé en audit le
    2026-09-13 (FME.DE, 2269.T, 2382.HK concernés en production)."""
    news = [{"date": _days_ago(14), "sentiment": 1}]
    result = score_actualite_recente(news)
    assert result.score == 10.0
    assert "1 actualités récentes" in result.raw_value


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
    assert interpret(70.0) == "Profil fondamental très solide"
    assert interpret(30.0) == "Solide"
    assert interpret(0.0) == "Neutre"
    assert interpret(-30.0) == "Fragile"
    assert interpret(-70.0) == "Très fragile"


def test_interpret_boundary_values():
    # Valeurs exactement aux bornes -- doivent tomber dans la bande DU
    # DESSOUS (comparaison stricte ">", pas ">=").
    assert interpret(60.0) == "Solide"
    assert interpret(20.0) == "Neutre"
    assert interpret(-20.0) == "Fragile"
    assert interpret(-60.0) == "Très fragile"


def test_compute_percentile_rank_min_value_is_near_zero():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(10.0, pool)
    assert result == 10.0  # 0 inférieurs, 1 égal (lui-même) -> 100*(0+0.5)/5


def test_compute_percentile_rank_max_value_is_near_hundred():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(50.0, pool)
    assert result == 90.0  # 4 inférieurs, 1 égal -> 100*(4+0.5)/5


def test_compute_percentile_rank_median_value():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(30.0, pool)
    assert result == 50.0  # 2 inférieurs, 1 égal -> 100*(2+0.5)/5


def test_compute_percentile_rank_handles_ties():
    pool = [10.0, 20.0, 20.0, 20.0, 50.0]
    result = indices_score.compute_percentile_rank(20.0, pool)
    # 1 strictement inférieur (10.0), 3 égaux (les trois 20.0) -> 100*(1+1.5)/5
    assert result == 50.0


def test_compute_percentile_rank_single_element_pool():
    result = indices_score.compute_percentile_rank(42.0, [42.0])
    assert result == 50.0  # seul élément du pool -> 100*(0+0.5)/1


def test_score_profile_key_trust():
    assert indices_score._score_profile_key({"is_trust": True, "is_financial": False}) == "trust"


def test_score_profile_key_financial():
    assert indices_score._score_profile_key({"is_trust": False, "is_financial": True}) == "financial"


def test_score_profile_key_standard():
    assert indices_score._score_profile_key({"is_trust": False, "is_financial": False}) == "standard"


def test_score_profile_key_defaults_to_standard_when_keys_absent():
    assert indices_score._score_profile_key({}) == "standard"


def _make_companies_with_scores(scores: list[float], is_financial=False, is_trust=False) -> list[dict]:
    return [
        {
            "ticker": f"T{i}", "score": s, "interpretation": "peu importe",
            "is_financial": is_financial, "is_trust": is_trust,
        }
        for i, s in enumerate(scores)
    ]


def test_recalibrate_scores_by_profile_remaps_large_pool():
    # 25 sociétés standard (>= seuil 20) avec des scores bruts variés.
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    indices_score.recalibrate_scores_by_profile(companies)
    # La société avec le score brut le plus bas (0.0) doit désormais avoir
    # un score recalibré proche de -100 ; la plus haute (24.0), proche de +100.
    assert companies[0]["score"] < -80
    assert companies[-1]["score"] > 80
    # Le score n'est plus la valeur brute d'origine.
    assert companies[0]["score"] != 0.0


def test_recalibrate_scores_by_profile_updates_interpretation():
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    indices_score.recalibrate_scores_by_profile(companies)
    for c in companies:
        assert c["interpretation"] == indices_score.interpret(c["score"])


def test_recalibrate_scores_by_profile_marks_recalibrated_companies_true():
    # audit I3 : score_recalibrated distingue un score percentile (pool
    # >= seuil, échelle uniforme) d'un score brut laissé tel quel.
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    indices_score.recalibrate_scores_by_profile(companies)
    assert all(c["score_recalibrated"] is True for c in companies)


def test_recalibrate_scores_by_profile_leaves_small_pool_score_recalibrated_false():
    # Profil trust (< seuil) : jamais recalibré -- score_recalibrated doit
    # rester à sa valeur par défaut posée par build_company_entry (False),
    # pas devenir True par erreur.
    companies = _make_companies_with_scores([1.0, 2.0, 3.0, 4.0, 5.0], is_trust=True)
    for c in companies:
        c["score_recalibrated"] = False
    indices_score.recalibrate_scores_by_profile(companies)
    assert all(c["score_recalibrated"] is False for c in companies)


def test_recalibrate_scores_by_profile_leaves_score_raw_untouched():
    # score_raw doit rester la trace du score brut d'origine même après
    # recalibrage -- sinon plus aucun moyen d'expliquer un score
    # recalibré négatif pour une société dont tous les facteurs bruts
    # étaient positifs (audit I1).
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    for c in companies:
        c["score_raw"] = c["score"]
    original_raw = [c["score_raw"] for c in companies]
    indices_score.recalibrate_scores_by_profile(companies)
    assert [c["score_raw"] for c in companies] == original_raw
    # Le score affiché, lui, a bien changé (recalibrage appliqué).
    assert companies[0]["score"] != companies[0]["score_raw"]


def test_recalibrate_scores_by_profile_leaves_small_pool_untouched():
    # 5 sociétés trust (< seuil 20) : score et interpretation doivent rester
    # strictement identiques à ce qu'ils étaient avant l'appel.
    companies = _make_companies_with_scores([1.0, 2.0, 3.0, 4.0, 5.0], is_trust=True)
    for c in companies:
        c["interpretation"] = "Neutre"  # valeur arbitraire posée avant l'appel
    original = [dict(c) for c in companies]
    indices_score.recalibrate_scores_by_profile(companies)
    assert companies == original


def test_recalibrate_scores_by_profile_groups_by_profile_independently():
    # 25 standard (scores bruts 0..24) + 25 financier (scores bruts
    # 100..124, plage totalement disjointe) : si les pools étaient
    # incorrectement fusionnés en un seul de 50, TOUTES les sociétés
    # standard se retrouveraient tassées en bas du classement (dominées
    # par les scores financier, bien plus hauts) -- alors qu'avec un
    # regroupement correct, la meilleure société standard doit être proche
    # du sommet de SON PROPRE pool, peu importe les valeurs de l'autre
    # groupe.
    standard = _make_companies_with_scores([float(i) for i in range(25)], is_financial=False)
    financial = _make_companies_with_scores([float(i) + 100.0 for i in range(25)], is_financial=True)
    companies = standard + financial
    indices_score.recalibrate_scores_by_profile(companies)
    # Meilleure société standard (score brut 24.0, la plus haute de son
    # propre pool de 25) : doit être proche de +100, pas écrasée par les
    # scores financier.
    best_standard = companies[24]
    assert best_standard["score"] > 80
    # Pire société financier (score brut 100.0, la plus basse de SON
    # propre pool) : doit être proche de -100, pas portée en haut par sa
    # valeur brute élevée en absolu.
    worst_financial = companies[25]
    assert worst_financial["score"] < -80


import pandas as pd
from datetime import date as _date
from indices_score import get_row, extract_ratios, compute_dividend_streak_years, _compute_no_loss_years


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


def _dividend_series(years_with_dividend: list[int]) -> pd.Series:
    """Une Series indexée par une date arbitraire dans chaque année listée
    (15 juin, sans incidence — seule l'année compte), valeur = 1.0
    (peu importe le montant pour ces tests)."""
    dates = [pd.Timestamp(f"{y}-06-15") for y in years_with_dividend]
    return pd.Series([1.0] * len(dates), index=pd.DatetimeIndex(dates))


def test_compute_dividend_streak_years_counts_consecutive_years_including_current():
    dividends = _dividend_series([2020, 2021, 2022, 2023, 2024, 2025])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    # 2025 payé, mais pas encore 2026 (le dividende de l'année en cours
    # n'est peut-être pas encore tombé) -> démarre à 2025, remonte
    # jusqu'à 2020 incluse = 6 années consécutives.
    assert result == 6


def test_compute_dividend_streak_years_counts_current_year_if_already_paid():
    dividends = _dividend_series([2024, 2025, 2026])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 9, 1))
    assert result == 3


def test_compute_dividend_streak_years_zero_when_broken_last_two_years():
    dividends = _dividend_series([2015, 2016, 2017, 2018, 2019, 2020])  # ancien historique, rompu depuis
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    assert result == 0


def test_compute_dividend_streak_years_ignores_old_gap_before_current_streak():
    # Trou en 2018 (aucun versement), mais streak récent ininterrompu
    # depuis 2019 -> ne doit compter que le streak récent, pas être
    # cassé par le trou ancien.
    dividends = _dividend_series([2010, 2011, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    assert result == 7  # 2019..2025 inclus


def test_compute_dividend_streak_years_empty_series_returns_zero():
    result = compute_dividend_streak_years(pd.Series(dtype=float), today=_date(2026, 3, 1))
    assert result == 0


def test_compute_no_loss_years_true_when_all_positive():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, 90.0, 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is True


def test_compute_no_loss_years_false_when_one_year_negative():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, -10.0, 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is False


def test_compute_no_loss_years_false_when_one_year_missing():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, float("nan"), 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is False


def test_compute_no_loss_years_true_when_trailing_oldest_year_missing():
    # Cas réel (audit C6) : yfinance ne fournit quasiment jamais le 5e
    # exercice de Net Income, systématiquement NaN en bout de série
    # (le plus ancien) — ce n'est pas une vraie perte manquante.
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, 90.0, float("nan")], index=years)
    assert _compute_no_loss_years(net_income, years) is True


def test_compute_no_loss_years_false_when_all_years_missing():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([float("nan"), float("nan"), float("nan")], index=years)
    assert _compute_no_loss_years(net_income, years) is False


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
        "cagr_net_income",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y", "fcf", "net_debt", "equity",
        "tax_rate", "total_debt",
        "current_pb", "avg_pb_5y", "current_ratio", "no_loss_years",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu croît régulièrement de 800 à 1000 sur 5 ans ; CAGR lissé
    # (moyenne des 2 exercices récents vs moyenne des 2 plus anciens,
    # cf. test dédié ci-dessous) ~ 5.7%/an sur cette série linéaire.
    assert 5.0 < ratios["cagr_ca"] < 6.5


def test_extract_ratios_negative_ebitda_marks_structure_and_cash_generation_unavailable():
    """Audit 2026-09-24, constat C4 : `bool(ebitda[latest] and ...)` était
    déjà vrai pour un EBITDA NÉGATIF (un nombre non-nul est "truthy" en
    Python) — net_debt_ebitda_available/fcf_conversion_available
    valaient donc True, et les ratios qui en résultaient (dette nette
    positive / EBITDA négatif -> ratio négatif ; FCF et EBITDA tous deux
    négatifs -> ratio positif) étaient notés comme une vraie mesure au
    lieu d'être traités comme une donnée indisponible. Cas réels : KHC
    (levier -5.3x), Renault (-8.9x), MSTR (conversion cash 415%)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    financials.loc["EBITDA", years[0]] = -50.0  # exercice le plus récent
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["structure_available"] is False
    assert ratios["fcf_conversion_available"] is False
    assert ratios["net_debt_ebitda"] == 0.0
    assert ratios["fcf_conversion"] == 0.0


def test_extract_ratios_missing_shares_outstanding_leaves_valuation_unavailable():
    """Audit 2026-09-24, constat C5 : sharesOutstanding manquant chez
    yfinance devient 0.0 par convention (fetch_company_financials) — sans
    ce garde, market_cap = price * 0 = 0 rendait EV/EBITDA exactement
    égal à dette nette/EBITDA et P/E exactement à 0.0x, publiés comme de
    vraies valeurs. 13 sociétés touchées en production (dont ALV.DE, qui
    avait déclenché une alerte "entrée" sans aucune valorisation réelle
    derrière)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=0.0
    )
    assert ratios["valuation_available"] is False
    assert ratios["current_pe"] == 0.0
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["current_pb"] == 0.0


def test_extract_ratios_financial_missing_shares_outstanding_leaves_pe_pb_zero():
    """Même correctif que le profil standard, côté extract_ratios_financial
    (audit 2026-09-24, constat C5)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=0.0
    )
    assert ratios["current_pe"] == 0.0
    assert ratios["current_pb"] == 0.0
    assert ratios["avg_pe_5y"] == 0.0
    assert ratios["avg_pb_5y"] == 0.0


def test_extract_ratios_computes_cagr_net_income():
    """Régression : extract_ratios (profil standard) n'exposait pas
    cagr_net_income avant ce correctif -> compute_graham_defensive_badge
    retombait silencieusement sur ratios.get("cagr_net_income", 0.0) et le
    critère croissance_benefices était toujours False pour ce profil
    (utilisé par ~85% des sociétés). Net Income du fixture = [140, 130,
    120, 108, 96] (plus récent en premier) -> croissance réelle, CAGR > 0
    attendu."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert "cagr_net_income" in ratios
    assert ratios["cagr_net_income"] > 0


def test_extract_ratios_uses_current_price_for_latest_year_only():
    """audit I6 : current_pb (et current_pe/current_ev_ebitda) doivent
    refléter le cours du JOUR, pas le cours de clôture du dernier
    exercice fiscal -- seule l'année la plus récente utilise
    current_price ; les années antérieures (moyenne 5 ans) restent sur
    closes_by_year, le prix de LEUR propre époque."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # closes_by_year vaut 100.0 pour toutes les années (fixture) ; le
    # cours du jour est nettement différent (150.0) pour prouver que
    # c'est bien lui qui est utilisé pour l'année la plus récente.
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0,
        current_price=150.0,
    )
    # equity la plus récente (2025) = 50.0 -> P/B = (150*10)/50 = 30.0,
    # PAS 20.0 (qu'on aurait obtenu avec le cours de clôture d'exercice).
    assert ratios["current_pb"] == pytest.approx(1500.0 / 50.0)
    # avg_pb_5y : seule l'année 2025 utilise le cours du jour (150), les
    # 4 autres (equity 45/40/35/30) restent sur closes_by_year (100).
    expected_avg_pb_5y = (1500.0 / 50.0 + 1000.0 / 45.0 + 1000.0 / 40.0 + 1000.0 / 35.0 + 1000.0 / 30.0) / 5
    assert ratios["avg_pb_5y"] == pytest.approx(expected_avg_pb_5y)


def test_extract_ratios_falls_back_to_closes_by_year_when_current_price_absent():
    """Rétrocompatibilité : current_price=None (défaut) laisse le
    comportement identique à avant ce correctif -- repli sur
    closes_by_year même pour l'année la plus récente."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0,
        current_price=None,
    )
    assert ratios["current_pb"] == pytest.approx(1000.0 / 50.0)


def test_extract_ratios_financial_uses_current_price_for_latest_year_only():
    """Même correctif que le profil standard, côté extract_ratios_financial
    (audit I6)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0,
        current_price=80.0,
    )
    # closes_by_year vaut 50.0 pour toutes les années (fixture) ; equity
    # la plus récente (2025) = 3000.0 -> P/B = (80*100)/3000 = 2.667,
    # PAS (50*100)/3000 = 1.667 (cours de clôture d'exercice).
    assert ratios["current_pb"] == pytest.approx(80.0 * 100.0 / 3000.0)


def test_extract_ratios_computes_implied_cost_of_debt_from_interest_expense():
    """audit I7, volet 1 : extract_ratios doit exposer implied_cost_of_debt
    à partir de la ligne Interest Expense, absente du fixture de base
    (_make_fixture_statements) -- ajoutée ici explicitement."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    # Total Debt le plus récent (fixture) = 300.0 -> Interest Expense = 9.0
    # donne un coût de la dette implicite de 3.0%.
    financials.loc["Interest Expense"] = [9.0, 8.5, 8.0, 7.5, 7.0]
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["implied_cost_of_debt"] == pytest.approx(3.0)


def test_extract_ratios_implied_cost_of_debt_none_when_interest_expense_row_absent():
    """Rétrocompatibilité : le fixture de base n'a pas de ligne Interest
    Expense -- implied_cost_of_debt doit être None (repli chez
    l'appelant), pas planter."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["implied_cost_of_debt"] is None


def test_extract_ratios_icr_uses_implied_cost_of_debt_when_available():
    """audit Minor #5, point 1 : l'ICR doit utiliser implied_cost_of_debt
    (I7 volet 1) plutôt que le taux proxy fixe DEBT_INTEREST_RATE_PROXY
    quand il est disponible -- un reliquat du fix I7 laissait encore
    l'ICR sur le taux unique de 3% malgré un coût de la dette propre à
    la société déjà calculé juste à côté pour le WACC."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # Total Debt le plus récent (fixture) = 300.0 -> Interest Expense = 18.0
    # donne un coût de la dette implicite de 6%, nettement au-dessus du
    # proxy fixe (3%).
    financials.loc["Interest Expense"] = [18.0, 17.0, 16.0, 15.0, 14.0]
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    # EBIT le plus récent (fixture) = 150.0 -> ICR = 150 / (300 * 0.06)
    assert ratios["icr"] == pytest.approx(150.0 / (300.0 * 0.06))
    # Nettement différent du calcul sur le proxy fixe (150/(300*0.03))
    assert ratios["icr"] != pytest.approx(150.0 / (300.0 * 0.03))


def test_extract_ratios_roe_treated_as_missing_when_equity_negative():
    """audit Minor #7 : des capitaux propres négatifs (ex. McDonald's,
    rachats d'actions massifs) inversent le signe du ROE en un chiffre
    économiquement absurde -- traité comme une donnée manquante (repli
    0.0), pas calculé tel quel."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    balance_sheet.loc["Stockholders Equity", years[0]] = -100.0
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["roe"] == 0.0


def test_extract_ratios_computes_current_pb():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    # market_cap = 100.0 (prix) * 10.0 (actions) = 1000.0 ; equity la plus
    # récente (2025, ligne "Stockholders Equity" du fixture) = 50.0 ->
    # P/B = 1000/50 = 20.0. (Note : le commentaire d'origine du brief
    # indiquait equity=300, qui est en fait la valeur "Total Debt" du même
    # exercice dans ce fixture — corrigé ici pour coller aux données réelles
    # de _make_fixture_statements.)
    assert ratios["current_pb"] == pytest.approx(1000.0 / 50.0)
    assert ratios["avg_pb_5y"] > 0


def test_extract_ratios_computes_current_ratio():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    balance_sheet.loc["Current Assets"] = [400, 380, 360, 340, 320]
    balance_sheet.loc["Current Liabilities"] = [150, 145, 140, 135, 130]
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    # Dernier exercice (2025) : 400 / 150 ≈ 2.67
    assert ratios["current_ratio"] == pytest.approx(400.0 / 150.0)


def test_extract_ratios_current_ratio_zero_when_rows_absent():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # Pas de lignes Current Assets/Current Liabilities dans ce fixture.
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["current_ratio"] == 0.0


def test_extract_ratios_no_loss_years_true_for_all_positive_fixture():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # _make_fixture_statements a un Net Income positif sur les 5 exercices
    # (140, 130, 120, 108, 96).
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["no_loss_years"] is True


def test_extract_ratios_no_loss_years_false_with_one_loss_year():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    financials.loc["Net Income", years[2]] = -50.0  # une perte sur l'exercice du milieu
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["no_loss_years"] is False


import math
from indices_score import _cagr


def test_cagr_returns_neutral_zero_when_oldest_value_is_missing():
    assert _cagr(float("nan"), 1000.0, 4) == 0.0


def test_cagr_returns_neutral_zero_when_latest_value_is_missing():
    assert _cagr(800.0, float("nan"), 4) == 0.0


def test_cagr_returns_neutral_zero_when_latest_value_is_negative():
    """EBITDA passé positif devenu négatif (Boeing, Stellantis, Renault,
    Porsche SE... constaté en production 2026-09-13) : (last/first) élevé à
    une puissance fractionnaire n'est pas défini pour un ratio négatif,
    produit un NaN silencieux qui, non intercepté ici, atteignait _clamp et
    se voyait attribuer +10.0 (score maximal) au lieu d'une absence de
    donnée — inversion complète du signe pour une entreprise en réelle
    difficulté."""
    result = _cagr(1000.0, -50.0, 4)
    assert result == 0.0
    assert not math.isnan(result)


def test_cagr_zero_latest_value_is_a_real_minus_100_pct_cagr_not_neutral():
    """Contre-exemple délibéré : contrairement à une valeur négative
    (mathématiquement indéfinie), un EBITDA tombé exactement à zéro a un
    CAGR réel et bien défini (-100%) — ne doit pas être confondu avec le
    cas "donnée manquante/indéfinie" et forcé à 0.0 neutre."""
    result = _cagr(1000.0, 0.0, 4)
    assert result == -100.0


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


def test_extract_ratios_derives_operating_cash_flow_from_free_cash_flow_when_missing():
    """Reproduit le cas ASML Holding (reporting IFRS) en production : pas de
    ligne 'Operating Cash Flow' chez yfinance pour cette entreprise, mais
    'Free Cash Flow' (= OCF + capex) est présente — doit être utilisée pour
    reconstituer OCF plutôt que de faire lever KeyError sur toute
    l'entreprise."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # FCF = OCF + capex (capex déjà négatif) sur chaque exercice de la
    # fixture d'origine (OCF=[120,110,100,90,80], capex=[-30,-28,-26,-24,-22]).
    cashflow = cashflow.rename(index={"Operating Cash Flow": "Free Cash Flow"})
    cashflow.loc["Free Cash Flow"] = [90.0, 82.0, 74.0, 66.0, 58.0]

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    # OCF reconstitué = FCF - capex = 90 - (-30) = 120 (exercice le plus
    # récent) -> fcf recalculé = OCF + capex = 120 + (-30) = 90, identique
    # à la valeur FCF d'origine (cohérence du repli).
    assert ratios["fcf"] == 90.0


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


def test_extract_ratios_degrades_gracefully_when_latest_year_tax_rate_is_nan():
    """Reproduit ENX.PA/FGR.PA/MBG.DE en production (audit 2026-09-13) :
    tax_rate manquant sur l'exercice le plus récent laissait passer
    `ebit[latest] * (1 - nan)` = NaN jusqu'à _clamp (même garde manquant
    que pour economic_assets_latest/ebit[latest], juste à côté) — attribué
    à tort comme score Rentabilité maximal (+10.0) au lieu d'une absence
    de donnée. roce doit dégrader vers 0.0, pas vers NaN."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    latest_year = list(financials.columns)[0]
    financials.loc["Tax Rate For Calcs", latest_year] = float("nan")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )

    assert ratios["roce"] == 0.0
    assert not math.isnan(ratios["roce"])


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


def test_estimate_dcf_price_caps_terminal_growth_at_risk_free_rate_when_lower():
    """audit I7, volet 2 : une croissance perpétuelle de 2% fixe est
    incohérente pour une devise dont le taux sans risque est
    structurellement plus bas (JPY, CHF) -- la croissance terminale
    effective doit être plafonnée au taux sans risque quand il est
    inférieur à DCF_TERMINAL_GROWTH, produisant une valeur terminale (et
    donc un prix implicite) plus basse."""
    default_terminal_growth = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    capped_at_risk_free_rate = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0, risk_free_rate=0.5,
    )
    assert capped_at_risk_free_rate < default_terminal_growth


def test_estimate_dcf_price_terminal_growth_unaffected_when_risk_free_rate_above_default():
    """Le taux sans risque de la plupart des devises (EUR/USD/GBP) est
    généralement au-dessus de DCF_TERMINAL_GROWTH (2%) -- dans ce cas, le
    plafond ne change rien (min(2.0, risk_free_rate) reste 2.0)."""
    default_terminal_growth = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    with_high_risk_free_rate = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0, risk_free_rate=5.0,
    )
    assert with_high_risk_free_rate == default_terminal_growth


def test_estimate_dcf_price_guard_clause_uses_effective_terminal_growth():
    """Le garde-fou DCF_MIN_DISCOUNT_SPREAD doit comparer le taux
    d'actualisation à la croissance terminale EFFECTIVE (après plafond
    risk_free_rate), pas à la constante fixe -- sinon un cas valide
    (écart largement suffisant une fois le plafond appliqué) serait
    rejeté à tort."""
    # discount_rate_pct=2.5, DCF_TERMINAL_GROWTH=2.0 -> écart 0.5, sous
    # DCF_MIN_DISCOUNT_SPREAD=1.0 -> None sans le plafond.
    blocked_without_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=2.5,
    )
    assert blocked_without_cap is None
    # Avec risk_free_rate=0.5 -> croissance terminale effective = 0.5,
    # écart = 2.5-0.5 = 2.0, largement au-dessus du seuil -> doit passer.
    allowed_with_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=2.5, risk_free_rate=0.5,
    )
    assert allowed_with_cap is not None


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


def test_estimate_dcf_price_returns_none_when_equity_value_is_negative():
    """Audit 2026-09-24, constat C4 : un FCF de départ positif ne garantit
    pas une valeur des capitaux propres positive quand la dette nette est
    très élevée — avant ce correctif, `equity_value / shares_outstanding`
    pouvait ressortir négatif et s'afficher tel quel comme prix par
    action (cas réels : Boeing -154.96, Meituan -17.09)."""
    result = estimate_dcf_price(
        fcf=50.0, cagr_ebitda=2.0, net_debt=1_000_000.0, shares_outstanding=50.0,
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


from indices_score import compute_graham_defensive_badge


def _graham_eligible_ratios(**overrides):
    """Ratios standard qui satisfont TOUS les critères Graham par
    défaut — chaque test override le(s) champ(s) qui doit échouer."""
    base = {
        "current_ratio": 2.5,
        "no_loss_years": True,
        "dividend_streak_years": 12,
        "cagr_net_income": 4.0,
        "current_pe": 12.0,
        "current_pb": 1.5,
    }
    base.update(overrides)
    return base


def test_compute_graham_defensive_badge_eligible_when_all_criteria_met():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["eligible"] is True
    assert len(result["criteria"]) == 6
    assert result["criteria"]["structure_financiere"] is True


def test_compute_graham_defensive_badge_not_eligible_when_one_criterion_fails():
    ratios = _graham_eligible_ratios(current_pe=25.0)  # dépasse GRAHAM_PE_MAX
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["eligible"] is False
    assert result["criteria"]["valorisation_pe"] is False
    # Les autres critères restent corrects individuellement -> un seul
    # échec suffit à invalider le badge global, pas les autres clés.
    assert result["criteria"]["stabilite_benefices"] is True


def test_compute_graham_defensive_badge_excludes_structure_financiere_for_financial_profile():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=True, is_trust=False)
    assert "structure_financiere" not in result["criteria"]
    assert len(result["criteria"]) == 5


def test_compute_graham_defensive_badge_excludes_structure_financiere_for_trust_profile():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=True)
    assert "structure_financiere" not in result["criteria"]
    assert len(result["criteria"]) == 5


def test_compute_graham_defensive_badge_graham_number_criterion():
    # P/E 14 x P/B 1.5 = 21 <= 22.5 -> vrai
    ratios = _graham_eligible_ratios(current_pe=14.0, current_pb=1.5)
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["criteria"]["valorisation_graham_number"] is True

    # P/E 14 x P/B 2.0 = 28 > 22.5 -> faux
    ratios2 = _graham_eligible_ratios(current_pe=14.0, current_pb=2.0)
    result2 = compute_graham_defensive_badge(ratios2, is_financial=False, is_trust=False)
    assert result2["criteria"]["valorisation_graham_number"] is False


def test_compute_graham_defensive_badge_missing_fields_degrade_to_false():
    result = compute_graham_defensive_badge({}, is_financial=False, is_trust=False)
    assert result["eligible"] is False
    assert all(v is False for v in result["criteria"].values())


def test_compute_graham_defensive_badge_output_is_json_serializable_with_real_ratios():
    """Régression : `ratios` en production vient de extract_ratios/
    extract_ratios_financial, où current_pe/current_pb/current_ratio sont
    des numpy.float64 (indexation de pandas Series), pas des float Python
    natifs comme dans _graham_eligible_ratios() (dict construit à la main
    par les autres tests de ce fichier). Une comparaison Python/
    numpy.float64 produit un numpy.bool_ -- qui n'est PAS une sous-classe
    de bool et fait planter json.dump(..., allow_nan=False) dans main()
    avec TypeError: Object of type bool_ is not JSON serializable. Ce test
    utilise donc la VRAIE sortie de extract_ratios (pas le dict à la main)
    pour reproduire fidèlement le bug tel qu'il se produit en production."""
    import json

    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    ratios["dividend_streak_years"] = 12  # pas dans extract_ratios, ajouté séparément dans main()
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)

    # Ne doit jamais lever, contrairement au comportement avant le correctif.
    json.dumps({"graham_defensive": result}, allow_nan=False)

    for key, value in result["criteria"].items():
        assert isinstance(value, bool), (
            f"criteria[{key!r}] = {value!r} ({type(value)}) n'est pas un bool "
            f"Python natif (probablement un numpy.bool_, non sérialisable en JSON)"
        )
    assert isinstance(result["eligible"], bool)


def _fake_ratios():
    return {
        "roce": 15.0,
        "roce_available": True,
        "roe": 18.0,
        "net_debt_ebitda": 1.5,
        "icr": 8.0,
        "structure_available": True,
        "cagr_ca": 6.0,
        "cagr_ebitda": 6.5,
        "fcf_conversion": 70.0,
        "fcf_conversion_available": True,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "current_pe": 20.0,
        "avg_pe_5y": 20.0,
        "valuation_available": True,
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
        "is_trust": False,
        "_price_history_daily": [],
        "_dividend_history": [],
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


def test_estimate_asset_based_price_no_discount_when_roe_meets_cost_of_capital():
    """ROE >= coût du capital : facteur qualité plafonné à 1.0, valeur
    comptable brute inchangée — pas de prime au-dessus du book value."""
    result = estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=10.0, cost_of_capital=8.0)
    assert result == 4.0


def test_estimate_asset_based_price_still_capped_at_1_by_default_with_high_roe():
    """allow_premium=False (défaut) : même avec un ROE très supérieur au
    coût du capital, le facteur qualité reste plafonné à 1.0 -- pour les
    sociétés standard/cycliques, le DCF/multiples portent déjà cet upside
    dans la moyenne pondérée (audit I5, comportement par défaut
    inchangé)."""
    # ratio ROE/coût du capital = 40/8 = 5.0, très au-dessus de 1.0
    result = estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=40.0, cost_of_capital=8.0)
    assert result == 4.0


def test_estimate_asset_based_price_allows_premium_when_flag_set():
    """allow_premium=True (profils financier/trust, audit I5) : un ROE
    au-dessus du coût du capital donne désormais une prime au-dessus de
    la valeur comptable, pas seulement l'absence de décote."""
    # ratio = 10/8 = 1.25
    result = estimate_asset_based_price(
        equity=200.0, shares_outstanding=50.0, roe=10.0, cost_of_capital=8.0, allow_premium=True,
    )
    assert result == pytest.approx(4.0 * 1.25)


def test_estimate_asset_based_price_premium_capped_at_asset_quality_ceiling():
    """allow_premium=True : la prime reste plafonnée à ASSET_QUALITY_CEILING
    même pour un ratio ROE/coût du capital extrême -- pas de prime
    débridée pour un Ke très faible face à un ROE exceptionnel."""
    # ratio = 40/8 = 5.0, bien au-dessus d'ASSET_QUALITY_CEILING (3.0)
    result = estimate_asset_based_price(
        equity=200.0, shares_outstanding=50.0, roe=40.0, cost_of_capital=8.0, allow_premium=True,
    )
    assert result == pytest.approx(4.0 * indices_score.ASSET_QUALITY_CEILING)


def test_estimate_asset_based_price_discounted_when_roe_below_cost_of_capital():
    """ROE sous le coût du capital : la valeur comptable est décotée
    proportionnellement (modèle du résultat résiduel / justified P/B) —
    repéré sur Volkswagen (ROE faible, book value très au-dessus du
    marché)."""
    # facteur = 4.0/8.0 = 0.5
    result = estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=4.0, cost_of_capital=8.0)
    assert result == 2.0


def test_estimate_asset_based_price_floors_discount_at_asset_quality_floor():
    """Un ROE très négatif ne doit pas faire tendre le prix vers 0/négatif
    — décote plafonnée à ASSET_QUALITY_FLOOR (approximation simplifiée,
    jamais une décote totale)."""
    result = estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=-50.0, cost_of_capital=8.0)
    assert result == pytest.approx(4.0 * indices_score.ASSET_QUALITY_FLOOR)


def test_estimate_asset_based_price_ignores_quality_factor_when_not_provided():
    """Comportement inchangé pour les appelants qui ne fournissent pas
    roe/cost_of_capital (rétrocompatibilité)."""
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0) == 4.0
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=4.0) == 4.0
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, cost_of_capital=8.0) == 4.0


def test_estimate_asset_based_price_ignores_quality_factor_when_cost_of_capital_not_positive():
    """cost_of_capital nul/négatif (donnée dégradée) : pas de division par
    zéro, repli sur la valeur comptable brute."""
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0, roe=4.0, cost_of_capital=0.0) == 4.0


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


def test_estimate_multiple_based_price_returns_none_when_current_ev_ebitda_is_negative():
    """Audit 2026-09-24, constat C4 : `not current_ev_ebitda` ne filtrait
    que le zéro exact, pas le négatif — un EV/EBITDA négatif produisait
    un ratio de retour au multiple négatif, donc un "prix implicite"
    négatif affiché à l'écran."""
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=-5.0, avg_ev_ebitda_5y=8.0
    )
    assert result is None


def test_estimate_multiple_based_price_returns_none_when_avg_ev_ebitda_is_negative():
    """Même correctif que ci-dessus, côté moyenne 5 ans cette fois — une
    société dont l'EBITDA a été négatif sur toute la fenêtre n'a pas de
    multiple moyen interprétable sur cette méthode de retour à la
    moyenne."""
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=13.0, avg_ev_ebitda_5y=-3.0
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


def test_fetch_risk_free_rate_uses_series_id_argument(monkeypatch):
    """Ajouté avec le Nasdaq-100 : fetch_risk_free_rate doit interroger la
    série FRED demandée (US pour les entreprises en dollars), pas rester
    câblée en dur sur la série France par défaut."""
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    captured = {}

    def _fake_get(url, params, timeout):
        captured["series_id"] = params["series_id"]
        return _FakeFredResponse({"observations": [{"value": "4.20"}]})

    monkeypatch.setattr(indices_score.requests, "get", _fake_get)
    result = fetch_risk_free_rate(indices_score.FRED_RISK_FREE_SERIES_US)
    assert result == 4.20
    assert captured["series_id"] == "DGS10"


from indices_score import fetch_fx_rate_to_usd


def _fake_ticker_returning(price):
    class FakeHistory:
        def __getitem__(self, key):
            assert key == "Close"
            import pandas as pd
            return pd.Series([price - 0.01, price])

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            assert period == "5d"
            return FakeHistory()

    return FakeTicker


def test_fetch_fx_rate_to_usd_returns_1_for_usd_without_network_call(monkeypatch):
    def _should_not_be_called(*a, **k):
        raise AssertionError("USD ne doit déclencher aucun appel réseau")
    monkeypatch.setattr(indices_score.yf, "Ticker", _should_not_be_called)
    assert fetch_fx_rate_to_usd("USD") == 1.0


def test_fetch_fx_rate_to_usd_multiplies_for_direct_quote_currencies(monkeypatch):
    """EUR/GBP/CHF cotent XXXUSD=X en direct (USD par unité de devise) —
    vérifié empiriquement sur l'API Yahoo Finance : EURUSD=X≈1.15 doit se
    traduire par une multiplication, pas une division."""
    monkeypatch.setattr(indices_score.yf, "Ticker", _fake_ticker_returning(1.15))
    assert fetch_fx_rate_to_usd("EUR") == pytest.approx(1.15)


def test_fetch_fx_rate_to_usd_divides_for_indirect_quote_currencies(monkeypatch):
    """JPY/HKD cotent XXX=X en indirect (unités de devise par USD) —
    vérifié empiriquement (JPY=X et USDJPY=X renvoient la même valeur) :
    157 JPY pour 1 USD -> il faut DIVISER par 157, pas multiplier."""
    monkeypatch.setattr(indices_score.yf, "Ticker", _fake_ticker_returning(157.0))
    assert fetch_fx_rate_to_usd("JPY") == pytest.approx(1 / 157.0)


def test_fetch_fx_rate_to_usd_hkd_divides_too(monkeypatch):
    monkeypatch.setattr(indices_score.yf, "Ticker", _fake_ticker_returning(7.84))
    assert fetch_fx_rate_to_usd("HKD") == pytest.approx(1 / 7.84)


def test_fetch_fx_rate_to_usd_returns_none_for_unhandled_currency(monkeypatch):
    monkeypatch.setattr(indices_score.yf, "Ticker", _fake_ticker_returning(1.0))
    assert fetch_fx_rate_to_usd("XYZ") is None


def test_fetch_fx_rate_to_usd_returns_none_on_fetch_failure(monkeypatch):
    class FailingTicker:
        def __init__(self, symbol):
            pass

        def history(self, period):
            raise RuntimeError("panne réseau")

    monkeypatch.setattr(indices_score.yf, "Ticker", FailingTicker)
    assert fetch_fx_rate_to_usd("JPY") is None


def test_fetch_fx_rate_to_usd_returns_none_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(indices_score, "yf", None)
    assert fetch_fx_rate_to_usd("EUR") is None


def test_fetch_fx_rate_same_currency_returns_1_without_network_call(monkeypatch):
    def _should_not_be_called(*a, **k):
        raise AssertionError("devises identiques : aucun appel réseau attendu")
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", _should_not_be_called)
    assert indices_score.fetch_fx_rate("USD", "USD") == 1.0


def test_fetch_fx_rate_composes_via_usd_pivot(monkeypatch):
    """USD/HKD proche du cas réel AIA : financialCurrency=USD,
    quote_currency=HKD, taux HKD/USD ≈ 7.8 -> convertir un montant USD
    vers HKD doit multiplier par ≈7.8."""
    rates = {"USD": 1.0, "HKD": 1 / 7.8}  # fetch_fx_rate_to_usd("HKD") : HKD=X coté indirect, déjà divisé

    def _fake_fetch_fx_rate_to_usd(currency):
        return rates.get(currency)

    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", _fake_fetch_fx_rate_to_usd)
    result = indices_score.fetch_fx_rate("USD", "HKD")
    assert result == pytest.approx(7.8, rel=0.01)


def test_fetch_fx_rate_returns_none_when_from_currency_unhandled(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda c: None if c == "XYZ" else 1.0)
    assert indices_score.fetch_fx_rate("XYZ", "USD") is None


def test_fetch_fx_rate_returns_none_when_to_currency_unhandled(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda c: None if c == "XYZ" else 1.0)
    assert indices_score.fetch_fx_rate("USD", "XYZ") is None


from indices_score import _size_premium, estimate_wacc, estimate_cost_of_equity


def test_estimate_cost_of_equity_nominal_case():
    # 3.68 + 1.2*5.0 + 0.0 (méga cap) = 9.68
    result = estimate_cost_of_equity(risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000, fx_rate_to_usd=1.0)
    assert result == pytest.approx(9.68)


def test_estimate_cost_of_equity_matches_wacc_equity_component():
    """estimate_wacc doit utiliser exactement ce même coût des fonds
    propres en interne (pas une formule dupliquée qui pourrait diverger)
    — vérifié en isolant le cas 100% fonds propres (dette nulle), où le
    WACC doit être strictement égal au coût des fonds propres seul."""
    cost_of_equity = estimate_cost_of_equity(risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000, fx_rate_to_usd=1.0)
    wacc = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=0.0, tax_rate=0.25, fx_rate_to_usd=1.0,
    )
    assert wacc == pytest.approx(cost_of_equity)


def test_estimate_cost_of_equity_returns_none_when_market_cap_not_positive():
    assert estimate_cost_of_equity(risk_free_rate=3.68, beta=1.2, market_cap=0.0, fx_rate_to_usd=1.0) is None


def test_estimate_cost_of_equity_returns_none_when_beta_missing():
    assert estimate_cost_of_equity(risk_free_rate=3.68, beta=None, market_cap=100_000_000_000, fx_rate_to_usd=1.0) is None


def test_estimate_cost_of_equity_returns_none_when_fx_rate_missing():
    assert estimate_cost_of_equity(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000, fx_rate_to_usd=None) is None


def test_estimate_cost_of_equity_converts_market_cap_before_size_premium():
    """Le bug corrigé : une capitalisation en devise locale (ex. yens)
    doit être convertie en USD avant d'être comparée aux seuils de
    SIZE_PREMIUM_BANDS (conçus en USD) — sans conversion, une petite
    capitalisation japonaise passerait pour une méga-cap. 8 000 milliards
    de yens à un taux de change de 1/157 -> ~50,96 Md$ (juste au-dessus du
    seuil méga-cap 50Md$, prime 0.0) ; sans conversion, 8 000 milliards
    comparés directement aux mêmes seuils donnerait aussi 0.0 par
    coïncidence de valeur brute — donc on teste plutôt un cas où la
    différence de résultat est sans ambiguïté (mid-cap réelle traitée à
    tort comme méga-cap sans conversion)."""
    # 300 milliards de yens ~= 1.91 Md$ à 1/157 -> petite capitalisation
    # (prime 3.0), alors que 300 milliards bruts (sans conversion)
    # dépasserait même le seuil méga-cap (50 milliards) -> prime 0.0.
    fx_rate = 1 / 157.0
    with_conversion = estimate_cost_of_equity(
        risk_free_rate=1.0, beta=1.0, market_cap=300_000_000_000, fx_rate_to_usd=fx_rate)
    without_conversion_equivalent = estimate_cost_of_equity(
        risk_free_rate=1.0, beta=1.0, market_cap=300_000_000_000, fx_rate_to_usd=1.0)
    assert with_conversion == pytest.approx(1.0 + 1.0 * 5.0 + 3.0)  # petite cap -> prime 3.0
    assert without_conversion_equivalent == pytest.approx(1.0 + 1.0 * 5.0 + 0.0)  # méga cap -> prime 0.0
    assert with_conversion != without_conversion_equivalent


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


def test_compute_implied_cost_of_debt_nominal_case():
    # |interest_expense|/total_debt = 30/1000 = 3.0%, dans la fourchette
    # [DEBT_INTEREST_RATE_FLOOR, DEBT_INTEREST_RATE_CEILING] -- pas de clamp.
    result = indices_score._compute_implied_cost_of_debt(30.0, 1000.0)
    assert result == pytest.approx(3.0)


def test_compute_implied_cost_of_debt_uses_absolute_value():
    """interest_expense peut être remonté négatif par yfinance selon la
    convention de signe de la ligne -- le ratio doit rester positif."""
    result = indices_score._compute_implied_cost_of_debt(-30.0, 1000.0)
    assert result == pytest.approx(3.0)


def test_compute_implied_cost_of_debt_floors_at_rate_floor():
    """Cas réel Toyota (7203.T) : dette de financement captif (crédit
    auto) à très faible marge, ~0.14% -- doit être remonté à
    DEBT_INTEREST_RATE_FLOOR, pas laissé tel quel (audit I7, volet 1)."""
    result = indices_score._compute_implied_cost_of_debt(60.0, 43000.0)  # ~0.14%
    assert result == pytest.approx(indices_score.DEBT_INTEREST_RATE_FLOOR)


def test_compute_implied_cost_of_debt_caps_at_rate_ceiling():
    """Cas réel Rocket Lab (RKLB) : ~10.4%, sous le plafond -- mais un
    ratio plus extrême doit être plafonné à DEBT_INTEREST_RATE_CEILING."""
    result = indices_score._compute_implied_cost_of_debt(300.0, 1000.0)  # 30%
    assert result == pytest.approx(indices_score.DEBT_INTEREST_RATE_CEILING)


def test_compute_implied_cost_of_debt_none_when_interest_expense_missing():
    """Cas réel Apple (AAPL) : la ligne Interest Expense existe mais peut
    être NaN pour l'exercice le plus récent -- repli chez l'appelant
    (estimate_wacc), pas ici."""
    assert indices_score._compute_implied_cost_of_debt(float("nan"), 1000.0) is None


def test_compute_implied_cost_of_debt_none_when_total_debt_missing_zero_or_negative():
    assert indices_score._compute_implied_cost_of_debt(30.0, float("nan")) is None
    assert indices_score._compute_implied_cost_of_debt(30.0, 0.0) is None
    assert indices_score._compute_implied_cost_of_debt(30.0, -100.0) is None


def test_estimate_wacc_uses_implied_cost_of_debt_when_provided():
    """audit I7, volet 1 : un coût de la dette propre à l'entreprise doit
    remplacer DEBT_INTEREST_RATE_PROXY (3.0%) quand fourni."""
    # Rd_after_tax = 8.0 * (1 - 0.25) = 6.0 (au lieu de 3.0*(1-0.25)=2.25)
    # WACC = (100/150)*9.68 + (50/150)*6.0 = 6.4533... + 2.0 = 8.4533...
    result = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
        implied_cost_of_debt=8.0,
    )
    assert result == pytest.approx(8.453333333333333)


def test_estimate_wacc_falls_back_to_proxy_when_implied_cost_of_debt_absent():
    """Rétrocompatibilité : sans implied_cost_of_debt fourni (défaut
    None), le résultat doit rester identique à avant ce correctif."""
    with_none = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
        implied_cost_of_debt=None,
    )
    without_arg = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    )
    assert with_none == without_arg == pytest.approx(7.203333333333333)


def test_estimate_wacc_nominal_case():
    # Re = 3.68 + 1.2*5.0 + 0.0 (méga cap) = 9.68
    # Rd_after_tax = 3.0 * (1 - 0.25) = 2.25
    # E=100, D=50 -> poids E=100/150, D=50/150
    # WACC = (100/150)*9.68 + (50/150)*2.25 = 6.4533... + 0.75 = 7.2033...
    result = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    )
    assert result == pytest.approx(7.203333333333333)


def test_estimate_wacc_caps_debt_weight_for_captive_finance_heavy_balance_sheet():
    """Un constructeur auto dont la dette de financement captif (crédit
    clients/concessionnaires) écrase largement une capitalisation
    boursière déprimée ne doit pas voir son WACC retomber quasiment au
    seul coût de la dette — la pondération dette est plafonnée à
    DEBT_WEIGHT_CAP (voir sa docstring, cas Volkswagen)."""
    # Re = 3.68 + 1.2*5.0 + 0.0 (méga cap) = 9.68
    # Rd_after_tax = 3.0 * (1 - 0.25) = 2.25
    # E=60, D=200 -> poids marché D = 200/260 ≈ 0.769, plafonné à 0.75
    # WACC = 0.25*9.68 + 0.75*2.25 = 2.42 + 1.6875 = 4.1075
    result = indices_score.estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=60_000_000_000,
        total_debt=200_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    )
    assert result == pytest.approx(4.1075)
    # Sans plafond, le WACC serait plus bas (dominé par le coût de la
    # dette) : preuve que le plafond a un effet réel, pas seulement présent
    # dans le code sans changer le résultat.
    unc_debt_weight = 200_000_000_000 / 260_000_000_000
    unc_equity_weight = 1 - unc_debt_weight
    uncapped = unc_equity_weight * 9.68 + unc_debt_weight * 2.25
    assert result > uncapped


def test_estimate_wacc_debt_weight_ratio_uses_local_currency_not_usd():
    """Le correctif de conversion FX (pour _size_premium) ne doit PAS
    toucher au ratio dette/capital (total_capital = market_cap +
    total_debt) : total_debt reste en devise locale, donc si market_cap
    était converti en USD à cet endroit, le ratio comparerait des choux
    et des carottes. Vérifié en comparant un taux de change très
    éloigné de 1.0 (1/157, JPY) au cas nominal déjà testé
    (fx_rate_to_usd=1.0) : la pondération dette/capital doit être
    identique dans les deux cas puisque market_cap et total_debt sont
    dans la même devise (locale) dans les deux scénarios — seule la
    prime de taille (donc le coût des fonds propres) doit différer."""
    # Même E=100Md, D=50Md dans les deux cas (devise locale, JPY ici) ;
    # seul fx_rate_to_usd change -> seule la prime de taille change.
    wacc_usd_scale = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    )
    wacc_jpy_scale = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1 / 157.0,
    )
    # 100 Md JPY / 157 ~= 637M$ -> petite capitalisation -> prime 3.0
    # (au lieu de 0.0 pour 100 Md$ directement) -> Re plus élevé de 3.0.
    cost_of_equity_usd_scale = estimate_cost_of_equity(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000, fx_rate_to_usd=1.0)
    cost_of_equity_jpy_scale = estimate_cost_of_equity(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000, fx_rate_to_usd=1 / 157.0)
    assert cost_of_equity_jpy_scale - cost_of_equity_usd_scale == pytest.approx(3.0)
    # La pondération dette/capital (visible dans l'écart WACC vs coût des
    # fonds propres seul) doit être identique dans les deux cas -> l'écart
    # entre les deux WACC doit être EXACTEMENT la même pondération fonds
    # propres (100/150) appliquée au même delta de coût des fonds propres.
    equity_weight = 100_000_000_000 / 150_000_000_000
    expected_delta = equity_weight * (cost_of_equity_jpy_scale - cost_of_equity_usd_scale)
    assert wacc_jpy_scale - wacc_usd_scale == pytest.approx(expected_delta)


def test_estimate_wacc_returns_none_when_risk_free_rate_missing():
    assert estimate_wacc(
        risk_free_rate=None, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_beta_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=None, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_market_cap_not_positive():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=0.0,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=-1.0,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_total_debt_negative():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=-1.0, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_tax_rate_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=None, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_any_input_is_nan():
    assert estimate_wacc(
        risk_free_rate=float("nan"), beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
    ) is None


def test_estimate_wacc_returns_none_when_beta_is_non_numeric():
    """yfinance renvoie `info` comme un dict non typé — un bêta remonté sous
    une forme inattendue (ex : chaîne de caractères) ne doit pas faire
    lever d'exception (`_is_missing` ne détecte que None/NaN, pas les
    types incompatibles avec l'arithmétique)."""
    assert estimate_wacc(
        risk_free_rate=3.68, beta="1.2", market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25, fx_rate_to_usd=1.0,
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


from indices_score import classify_weinstein_stage


def _weekly_series(values, start="2020-01-05"):
    """Série pandas hebdomadaire (une valeur par dimanche, comme le
    ferait .resample('W')) à partir d'une liste de valeurs, la plus
    ancienne en premier."""
    index = pd.date_range(start=start, periods=len(values), freq="W")
    return pd.Series(values, index=index, dtype=float)


def test_classify_weinstein_stage_returns_none_when_history_too_short():
    closes = _weekly_series([100.0] * 33)  # 33 < WEINSTEIN_MIN_WEEKS (34)
    volumes = _weekly_series([1000.0] * 33)
    result = classify_weinstein_stage(closes, volumes)
    assert result == {"stage": None, "stage_label": "Neutre", "volume_confirme": False}


def test_classify_weinstein_stage_detects_achat_phase():
    # MM30s clairement montante (prix croissant sur toute la fenêtre) et
    # prix courant au-dessus de la MM30s -> Phase 2 (Achat).
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 2
    assert result["stage_label"] == "Achat"


def test_classify_weinstein_stage_detects_declin_phase():
    # MM30s clairement descendante et prix courant en dessous -> Phase 4 (Déclin).
    closes = _weekly_series([300.0 - i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 4
    assert result["stage_label"] == "Déclin"


def test_classify_weinstein_stage_flat_ma_is_neutre():
    # Prix constant sur toute la fenêtre -> MM30s parfaitement plate -> Neutre.
    closes = _weekly_series([100.0] * 40)
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage_label"] == "Neutre"
    assert result["stage"] in (1, 3)  # best-effort interne, jamais exposé comme label


def test_classify_weinstein_stage_price_ma_disagreement_is_neutre():
    # Prix au-dessus d'une MM30s qui descend encore (transition typique,
    # ni Achat ni Déclin au sens strict de la méthode) -> Neutre.
    closes = _weekly_series([300.0 - i * 2.0 for i in range(36)] + [250.0, 260.0, 270.0, 280.0])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage_label"] == "Neutre"


def test_classify_weinstein_stage_volume_confirmed_when_spike_above_prior_average():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 39 + [2000.0])  # dernière semaine : 2x la moyenne des 30 précédentes
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is True


def test_classify_weinstein_stage_volume_not_confirmed_below_multiple():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 39 + [1200.0])  # 1.2x, sous le multiple de 1.5x
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is False


def test_classify_weinstein_stage_current_week_volume_excluded_from_its_own_baseline():
    """Point de correction identifié à la relecture de la spec : un
    volume extrême sur la semaine courante ne doit pas gonfler sa propre
    moyenne de référence."""
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    # Moyenne des 30 semaines précédentes = 1000 ; dernière semaine = 10000
    # (10x) -> doit rester confirmé, pas dilué par sa propre valeur extrême
    # dans le calcul de la moyenne.
    volumes = _weekly_series([1000.0] * 39 + [10000.0])
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is True


def test_classify_weinstein_stage_missing_volume_degrades_gracefully():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([float("nan")] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 2  # le calcul de phase ne dépend pas du volume
    assert result["volume_confirme"] is False


def test_classify_weinstein_stage_never_raises_on_empty_series():
    closes = pd.Series([], dtype=float)
    volumes = pd.Series([], dtype=float)
    result = classify_weinstein_stage(closes, volumes)
    assert result == {"stage": None, "stage_label": "Neutre", "volume_confirme": False}


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


def test_estimate_entry_exit_prices_excludes_technical_candidate_in_declin_phase():
    """Phase 4 (Déclin) : le repère technique (MM200) est exclu, seule
    la valorisation reste — ne jamais proposer un point d'entrée sur un
    titre en déclin confirmé même si son prix paraît bon marché."""
    with_declin = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Déclin",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    # Sans la phase Déclin, la MM200 pèse dans la moyenne -> résultat différent.
    assert with_declin != without_stage
    # Avec Déclin, seule la valorisation compte -> même résultat que ma200=None.
    valuation_only = estimate_entry_exit_prices(
        fair_value=100.0, ma200=None, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_declin == valuation_only


def test_estimate_entry_exit_prices_returns_none_in_declin_phase_without_valuation():
    result = estimate_entry_exit_prices(
        fair_value=None, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Déclin",
    )
    assert result == {"entry": None, "exit": None}


def test_estimate_entry_exit_prices_keeps_technical_candidate_in_achat_phase():
    """Phase 2 (Achat) : comportement inchangé par rapport à aujourd'hui."""
    with_achat = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Achat",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_achat == without_stage


def test_estimate_entry_exit_prices_keeps_technical_candidate_when_neutre():
    with_neutre = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Neutre",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_neutre == without_stage


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
        "roe": 12.0,
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
        "roe": 12.0,
    }
    at_8 = estimate_valuation_targets(data, cost_of_capital=8.0)
    at_10 = estimate_valuation_targets(data, cost_of_capital=10.0)
    assert at_8["fair_value"] != at_10["fair_value"]


def test_estimate_valuation_targets_uses_cost_of_equity_for_asset_quality_discount():
    """La décote qualité de la valeur comptable doit se baser sur
    `cost_of_equity` (coût des seuls fonds propres) quand il est fourni,
    pas sur `cost_of_capital` (WACC, dilué par une dette bon marché) —
    reproduit le cas Volkswagen : WACC bas (dette de financement captif)
    mais coût des fonds propres nettement plus élevé, seul pertinent pour
    juger si le ROE couvre ce que les actionnaires exigent réellement."""
    # DCF exclu (fcf négatif, comme VW) : seuls asset/multiple pèsent.
    data = {
        "fcf": -10.0, "cagr_ebitda": 6.5, "net_debt": 100.0, "shares_outstanding": 10.0,
        "equity": 800.0,  # book value/action = 80.0
        "current_price": 100.0, "current_ev_ebitda": 10.0, "avg_ev_ebitda_5y": 10.0,
        "ma200": 100.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": -10.0,
        "sector": "Unknown", "roe": 4.0,
    }
    # cost_of_capital bas (4.0, proche du ROE) -> quasi pas de décote ;
    # cost_of_equity nettement plus haut (12.0) -> décote marquée.
    low_benchmark = estimate_valuation_targets(data, cost_of_capital=4.0)
    high_benchmark = estimate_valuation_targets(data, cost_of_capital=4.0, cost_of_equity=12.0)
    assert high_benchmark["fair_value"] < low_benchmark["fair_value"]


def test_estimate_valuation_targets_allows_premium_above_book_value_for_financial_profile():
    """audit I5 : pour un profil financier, fcf=0.0 et current_ev_ebitda=0.0
    désactivent structurellement DCF et multiples (voir
    extract_ratios_financial) -- la méthode patrimoniale est alors la
    SEULE source de fair_value, et un ROE nettement supérieur au coût des
    capitaux propres doit pouvoir se traduire par une prime au-dessus de
    la valeur comptable, pas rester plafonné à elle."""
    data = {
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0, "shares_outstanding": 10.0,
        "equity": 800.0,  # book value/action = 80.0
        "current_price": 100.0, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": 100.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": 0.0,
        "sector": "Financial Services", "roe": 20.0,
        "is_financial": True, "is_trust": False,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=8.0)
    # ratio ROE/Ke = 20/8 = 2.5 -> fair_value = 80 * 2.5 = 200, > book value
    assert result["fair_value"] == pytest.approx(200.0)


def test_estimate_valuation_targets_keeps_cap_at_book_value_for_standard_profile():
    """Même ROE/coût des capitaux propres que le test ci-dessus, mais sans
    is_financial/is_trust : le plafond à 1.0 doit rester en place (profil
    standard, où DCF/multiples portent déjà l'upside dans la moyenne
    pondérée -- ici absents seulement parce que la fixture les désactive
    pour isoler la méthode patrimoniale, pas parce que c'est structurel
    pour ce profil)."""
    data = {
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0, "shares_outstanding": 10.0,
        "equity": 800.0,
        "current_price": 100.0, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": 100.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": 0.0,
        "sector": "Unknown", "roe": 20.0,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=8.0)
    assert result["fair_value"] == pytest.approx(80.0)


def test_estimate_valuation_targets_clamps_fair_value_to_sanity_ceiling():
    """audit I7 : cas réel Rakuten (4755.T, fair_value à 46x le cours) --
    une valeur patrimoniale extrême ne doit pas produire une fair_value
    déconnectée du cours réel, bornée à FAIR_VALUE_SANITY_CEILING fois
    current_price."""
    data = {
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0, "shares_outstanding": 10.0,
        "equity": 100000.0,  # book value/action = 10000.0, très au-dessus du cours
        "current_price": 50.0, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": 50.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": 0.0,
        "sector": "Unknown", "roe": 20.0,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=8.0)
    assert result["fair_value"] == pytest.approx(50.0 * indices_score.FAIR_VALUE_SANITY_CEILING)


def test_estimate_valuation_targets_clamps_fair_value_to_sanity_floor():
    """audit I7 : symétrique du test précédent -- une valeur patrimoniale
    quasi nulle ne doit pas non plus produire une fair_value démesurément
    basse par rapport au cours réel."""
    data = {
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0, "shares_outstanding": 10.0,
        "equity": 1.0,  # book value/action = 0.1, très en dessous du cours
        "current_price": 100.0, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": 100.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": 0.0,
        "sector": "Unknown", "roe": -50.0,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=8.0)
    assert result["fair_value"] == pytest.approx(100.0 * indices_score.FAIR_VALUE_SANITY_FLOOR)


def test_estimate_valuation_targets_skips_sanity_clamp_when_current_price_missing():
    """Rien à quoi comparer sans current_price -- ne doit ni lever ni
    tronquer arbitrairement la fair_value (repli sur DCF/multiples seuls,
    déjà couvert ailleurs)."""
    data = {
        "fcf": 0.0, "cagr_ebitda": 0.0, "net_debt": 0.0, "shares_outstanding": 10.0,
        "equity": 100000.0, "current_price": None, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": None, "beta": 1.0, "ecart_pct_ma200": None, "fcf_normalized": 0.0,
        "sector": "Unknown", "roe": 20.0,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=8.0)
    # equity/shares_outstanding = 10000.0, quality_factor plafonné à 1.0 (profil standard)
    assert result["fair_value"] == pytest.approx(10000.0)


def test_estimate_valuation_targets_passes_risk_free_rate_to_dcf():
    """audit I7, volet 2 : risk_free_rate doit transiter jusqu'à
    estimate_dcf_price -- un taux sans risque bas (JPY/CHF) doit réduire
    la juste valeur via la croissance terminale plafonnée."""
    data = {
        "fcf": 100.0, "cagr_ebitda": 10.0, "net_debt": 200.0, "shares_outstanding": 50.0,
        "equity": 0.0,  # asset_price désactivé (equity <= 0)
        "current_price": None, "current_ev_ebitda": 0.0, "avg_ev_ebitda_5y": 0.0,
        "ma200": None, "beta": 1.0, "ecart_pct_ma200": None, "fcf_normalized": 100.0,
        "sector": "Unknown", "roe": 10.0,
    }
    without_risk_free_rate = estimate_valuation_targets(data, cost_of_capital=8.0)
    with_low_risk_free_rate = estimate_valuation_targets(data, cost_of_capital=8.0, risk_free_rate=0.5)
    assert with_low_risk_free_rate["fair_value"] < without_risk_free_rate["fair_value"]


def test_estimate_valuation_targets_falls_back_to_cost_of_capital_when_cost_of_equity_absent():
    """Rétrocompatibilité : sans `cost_of_equity` fourni, le comportement
    doit rester identique à avant (repli sur `cost_of_capital`)."""
    data = {
        "fcf": -10.0, "cagr_ebitda": 6.5, "net_debt": 100.0, "shares_outstanding": 10.0,
        "equity": 800.0, "current_price": 100.0, "current_ev_ebitda": 10.0, "avg_ev_ebitda_5y": 10.0,
        "ma200": 100.0, "beta": 1.0, "ecart_pct_ma200": 0.0, "fcf_normalized": -10.0,
        "sector": "Unknown", "roe": 4.0,
    }
    without_arg = estimate_valuation_targets(data, cost_of_capital=8.0)
    with_none = estimate_valuation_targets(data, cost_of_capital=8.0, cost_of_equity=None)
    assert without_arg["fair_value"] == with_none["fair_value"]


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
        "roe": float("nan"),
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
        "roe": 12.0,
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


def test_append_indices_history_keeps_recent_entries_for_other_tickers(tmp_path):
    """Ajouter une entrée au ticker A ne doit jamais faire disparaître
    l'historique récent du ticker B."""
    import json
    path = tmp_path / "history.json"
    recent_date = (datetime.today().date() - timedelta(days=100)).strftime("%Y-%m-%d")
    existing = [{"date": recent_date, "ticker": "TTE.PA", "composite": 5.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}],
        path=str(path),
    )
    tte_entries = [e for e in result if e["ticker"] == "TTE.PA"]
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(tte_entries) == 1  # inchangé
    assert mc_entries[-1] == {"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}


def test_append_indices_history_drops_entries_older_than_retention_window(tmp_path):
    """audit Minor #4 : rétention par date calendaire (HISTORY_RETENTION_DAYS),
    pas par nombre d'écritures -- une entrée vieille de plus de 730 jours
    doit disparaître, même s'il n'y en a qu'une seule au total pour ce
    ticker (avant ce correctif, "garder les 730 dernières écritures"
    aurait laissé passer une entrée bien plus vieille que 2 ans si le
    workflow tournait rarement, ou au contraire couvert bien moins de 2
    ans s'il tournait plusieurs fois par jour)."""
    import json
    path = tmp_path / "history.json"
    old_date = (datetime.today().date() - timedelta(days=800)).strftime("%Y-%m-%d")
    existing = [{"date": old_date, "ticker": "MC.PA", "composite": 1.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}],
        path=str(path),
    )
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(mc_entries) == 1
    assert mc_entries[0]["composite"] == 42.0


def test_append_indices_history_drops_tickers_no_longer_in_companies(tmp_path):
    """audit Minor #4 : un ticker retiré d'un indice (révision) ne doit
    plus jamais réapparaître après retrim, même avec une date récente --
    sinon son historique reste figé indéfiniment (plus aucune écriture
    ne vient jamais le retrimer, contrairement à un ticker toujours
    actif qui se retrimme à chaque run)."""
    import json
    path = tmp_path / "history.json"
    recent_date = (datetime.today().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    existing = [{"date": recent_date, "ticker": "TICKER_RETIRE_FICTIF", "composite": 5.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}],
        path=str(path),
    )
    assert all(e["ticker"] != "TICKER_RETIRE_FICTIF" for e in result)


def test_load_nikkei_hangseng_price_history_returns_empty_list_when_file_absent(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    assert indices_score.load_nikkei_hangseng_price_history(path=str(missing_path)) == []


def test_load_nikkei_hangseng_price_history_returns_empty_list_on_corrupted_json(tmp_path):
    corrupted_path = tmp_path / "corrupted.json"
    corrupted_path.write_text("{not valid json", encoding="utf-8")
    assert indices_score.load_nikkei_hangseng_price_history(path=str(corrupted_path)) == []


def test_update_nikkei_hangseng_price_history_adds_entries_for_nikkei_and_hangseng_only(tmp_path):
    """Point de départ pour un futur graphique par entreprise sur ces deux
    places (TradingView n'a pas de données fiables dessus) — un ticker
    d'un autre indice (CAC40) ne doit jamais y apparaître."""
    path = tmp_path / "history.json"
    companies = [
        {"ticker": "9984.T", "index": "NIKKEI225", "current_price": 6540.0},
        {"ticker": "1299.HK", "index": "HANGSENG", "current_price": 55.2},
        {"ticker": "MC.PA", "index": "CAC40", "current_price": 660.0},
    ]
    result = indices_score.update_nikkei_hangseng_price_history(companies, path=str(path))
    today_str = datetime.today().strftime("%Y-%m-%d")
    tickers = {e["ticker"] for e in result}
    assert tickers == {"9984.T", "1299.HK"}
    entry = next(e for e in result if e["ticker"] == "9984.T")
    assert entry == {"date": today_str, "ticker": "9984.T", "price": 6540.0}
    assert indices_score.load_nikkei_hangseng_price_history(path=str(path)) == result


def test_update_nikkei_hangseng_price_history_skips_missing_price(tmp_path):
    """Un current_price manquant (None/NaN, _is_missing) ne doit jamais
    écrire une valeur invalide dans ce fichier jamais régénéré à zéro —
    même leçon que signal_tracking.json."""
    path = tmp_path / "history.json"
    companies = [
        {"ticker": "9984.T", "index": "NIKKEI225", "current_price": None},
        {"ticker": "1299.HK", "index": "HANGSENG", "current_price": float("nan")},
    ]
    result = indices_score.update_nikkei_hangseng_price_history(companies, path=str(path))
    assert result == []


def test_update_nikkei_hangseng_price_history_keeps_recent_entries_for_other_tickers(tmp_path):
    path = tmp_path / "history.json"
    recent_date = (datetime.today().date() - timedelta(days=100)).strftime("%Y-%m-%d")
    existing = [{"date": recent_date, "ticker": "1299.HK", "price": 5.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.update_nikkei_hangseng_price_history(
        [{"ticker": "9984.T", "index": "NIKKEI225", "current_price": 99.0}], path=str(path)
    )
    hk_entries = [e for e in result if e["ticker"] == "1299.HK"]
    nikkei_entries = [e for e in result if e["ticker"] == "9984.T"]
    assert len(hk_entries) == 1  # inchangé
    assert nikkei_entries[-1]["price"] == 99.0


def test_update_nikkei_hangseng_price_history_drops_entries_older_than_retention_window(tmp_path):
    """audit Minor #4 : rétention par date calendaire
    (NIKKEI_HANGSENG_HISTORY_RETENTION_DAYS), pas par nombre d'écritures --
    même correctif qu'append_indices_history."""
    path = tmp_path / "history.json"
    old_date = (datetime.today().date() - timedelta(days=800)).strftime("%Y-%m-%d")
    existing = [{"date": old_date, "ticker": "9984.T", "price": 1.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.update_nikkei_hangseng_price_history(
        [{"ticker": "9984.T", "index": "NIKKEI225", "current_price": 42.0}], path=str(path)
    )
    entries = [e for e in result if e["ticker"] == "9984.T"]
    assert len(entries) == 1
    assert entries[0]["price"] == 42.0


def test_update_nikkei_hangseng_price_history_drops_tickers_no_longer_in_roster(tmp_path):
    """audit Minor #4 : un ticker retiré du Nikkei 225/Hang Seng ne doit
    plus jamais réapparaître après retrim, même avec une date récente."""
    path = tmp_path / "history.json"
    recent_date = (datetime.today().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    existing = [{"date": recent_date, "ticker": "TICKER_RETIRE_FICTIF", "price": 5.0}]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.update_nikkei_hangseng_price_history(
        [{"ticker": "9984.T", "index": "NIKKEI225", "current_price": 99.0}], path=str(path)
    )
    assert all(e["ticker"] != "TICKER_RETIRE_FICTIF" for e in result)


def test_update_nikkei_hangseng_price_history_degrades_gracefully_on_failure(tmp_path, monkeypatch):
    """Une panne (ex: répertoire illisible) ne doit jamais faire échouer
    main() — renvoie [] plutôt que de propager l'exception."""
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(indices_score, "load_nikkei_hangseng_price_history", _boom)
    result = indices_score.update_nikkei_hangseng_price_history(
        [{"ticker": "9984.T", "index": "NIKKEI225", "current_price": 42.0}],
        path=str(tmp_path / "history.json"),
    )
    assert result == []


from datetime import date as _date


def test_downsample_keeps_daily_entries_within_the_recent_window():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-01", "ticker": "MC.PA", "price": 100.0},
        {"date": "2026-09-02", "ticker": "MC.PA", "price": 101.0},
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = sorted(e["date"] for e in result)
    assert "2026-09-01" in dates
    assert "2026-09-02" in dates
    assert "2026-09-21" in dates


def test_downsample_reduces_entries_older_than_30_days_to_one_per_iso_week():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-01-05", "ticker": "MC.PA", "price": 90.0},  # lundi semaine 2
        {"date": "2026-01-06", "ticker": "MC.PA", "price": 91.0},  # mardi meme semaine
        {"date": "2026-01-09", "ticker": "MC.PA", "price": 93.0},  # vendredi meme semaine (le plus recent)
    ]
    result = indices_score._downsample_price_entries(entries, today)
    assert len(result) == 1
    assert result[0]["date"] == "2026-01-09"
    assert result[0]["price"] == 93.0


def test_downsample_drops_entries_older_than_six_years_retention():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2019-01-01", "ticker": "MC.PA", "price": 50.0},  # > 2190 jours avant today
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = [e["date"] for e in result]
    assert "2019-01-01" not in dates
    assert "2026-09-21" in dates


def test_downsample_deduplicates_same_day_duplicate_entries_in_the_recent_window():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-20", "ticker": "MC.PA", "price": 118.0},
        {"date": "2026-09-20", "ticker": "MC.PA", "price": 119.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    assert len(result) == 1
    assert result[0]["price"] == 119.0


def test_downsample_returns_entries_sorted_by_date():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-15", "ticker": "MC.PA", "price": 110.0},
        {"date": "2019-06-01", "ticker": "MC.PA", "price": 40.0},
        {"date": "2026-01-09", "ticker": "MC.PA", "price": 93.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = [e["date"] for e in result]
    assert dates == sorted(dates)


def test_load_price_history_returns_empty_list_when_file_is_absent(tmp_path):
    assert indices_score.load_price_history(str(tmp_path / "absent.json")) == []


def test_load_price_history_degrades_to_empty_list_on_corrupt_json(tmp_path):
    path = tmp_path / "corrompu.json"
    path.write_text("pas du json", encoding="utf-8")
    assert indices_score.load_price_history(str(path)) == []


def test_update_price_history_writes_new_entries_to_a_fresh_file(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [{"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4}]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    assert result == entries
    assert indices_score.load_price_history(path) == entries


def test_update_price_history_accumulates_across_calls_and_downsamples(tmp_path):
    path = str(tmp_path / "price_history.json")
    indices_score.update_price_history(
        [{"date": "2026-01-05", "ticker": "MC.PA", "price": 90.0}], path=path, today=_date(2026, 1, 5))
    indices_score.update_price_history(
        [{"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0}], path=path, today=_date(2026, 9, 21))
    result = indices_score.load_price_history(path)
    dates = [e["date"] for e in result]
    assert "2026-01-05" in dates  # seule entree de cette semaine ISO, conservee
    assert "2026-09-21" in dates


def test_update_price_history_trims_independently_per_ticker(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4},
        {"date": "2026-09-21", "ticker": "SAP.DE", "price": 210.0},
    ]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    tickers = {e["ticker"] for e in result}
    assert tickers == {"MC.PA", "SAP.DE"}


def test_update_price_history_never_writes_nan(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [{"date": "2026-09-21", "ticker": "MC.PA", "price": float("nan")}]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    assert result == []
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "NaN" not in content


def test_update_price_history_degrades_to_empty_list_on_unexpected_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "sous_dossier_impossible" / "price_history.json")
    fichier_bloquant = tmp_path / "sous_dossier_impossible"
    fichier_bloquant.write_text("x", encoding="utf-8")
    result = indices_score.update_price_history(
        [{"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4}], path=path, today=_date(2026, 9, 21))
    assert result == []


def test_update_price_history_rounds_prices_and_writes_compact_json(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [{"date": "2026-09-21", "ticker": "MC.PA", "price": 620.123456789}]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    assert result[0]["price"] == 620.1235
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "\n  " not in content  # pas d'indentation
    assert "620.1235" in content


def test_load_dividend_history_returns_empty_list_when_file_is_absent(tmp_path):
    assert indices_score.load_dividend_history(str(tmp_path / "absent.json")) == []


def test_load_dividend_history_degrades_to_empty_list_on_corrupt_json(tmp_path):
    path = tmp_path / "corrompu.json"
    path.write_text("pas du json", encoding="utf-8")
    assert indices_score.load_dividend_history(str(path)) == []


def test_update_dividend_history_writes_new_entries_to_a_fresh_file(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result == entries
    assert indices_score.load_dividend_history(path) == entries


def test_update_dividend_history_accumulates_across_calls(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    indices_score.update_dividend_history(
        [{"date": "2023-06-15", "ticker": "MC.PA", "amount": 3.0}], path=path)
    indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    result = indices_score.load_dividend_history(path)
    dates = [e["date"] for e in result]
    assert "2023-06-15" in dates
    assert "2026-06-15" in dates
    assert len(result) == 2


def test_update_dividend_history_deduplicates_by_ticker_and_date_keeping_the_latest_call(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.40}], path=path)
    result = indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    assert len(result) == 1
    assert result[0]["amount"] == 3.55


def test_update_dividend_history_keeps_tickers_independent(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [
        {"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55},
        {"date": "2026-05-10", "ticker": "SAP.DE", "amount": 2.10},
    ]
    result = indices_score.update_dividend_history(entries, path=path)
    tickers = {e["ticker"] for e in result}
    assert tickers == {"MC.PA", "SAP.DE"}


def test_update_dividend_history_never_writes_nan(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": float("nan")}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result == []
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "NaN" not in content


def test_update_dividend_history_degrades_to_empty_list_on_unexpected_failure(tmp_path):
    path = str(tmp_path / "sous_dossier_impossible" / "dividend_history.json")
    fichier_bloquant = tmp_path / "sous_dossier_impossible"
    fichier_bloquant.write_text("x", encoding="utf-8")
    result = indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    assert result == []


def test_update_dividend_history_rounds_amounts_and_writes_compact_json(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.554321987}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result[0]["amount"] == 3.5543
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "\n  " not in content  # pas d'indentation
    assert "3.5543" in content


def test_last_confirmed_regime_returns_none_for_empty_history():
    assert indices_score._last_confirmed_regime([], 5.0) is None


def test_last_confirmed_regime_returns_none_when_all_values_in_dead_zone():
    history = [
        {"date": "2026-09-01", "composite_raw": -3.0},
        {"date": "2026-09-02", "composite_raw": 4.0},
    ]
    assert indices_score._last_confirmed_regime(history, 5.0) is None


def test_last_confirmed_regime_returns_most_recent_value_outside_band_by_date():
    # Volontairement pas dans l'ordre chronologique de la liste, pour
    # vérifier que le tri se fait bien par date et pas par ordre de liste.
    history = [
        {"date": "2026-09-10", "composite_raw": 30.0},
        {"date": "2026-09-01", "composite_raw": -20.0},
        {"date": "2026-09-05", "composite_raw": 2.0},  # dans la bande morte, ignoré
    ]
    assert indices_score._last_confirmed_regime(history, 5.0) == 30.0


def test_last_confirmed_regime_ignores_entries_missing_composite_raw():
    """Entrées d'historique antérieures à l'ajout de composite_raw (audit
    I2) -- dégradation gracieuse plutôt qu'une KeyError."""
    history = [
        {"date": "2026-09-01", "composite": -20.0},  # ancien format, pas de composite_raw
    ]
    assert indices_score._last_confirmed_regime(history, 5.0) is None


def test_compute_company_alerts_returns_info_when_nothing_triggers():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[],
    )
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "info"


def test_compute_company_alerts_watch_when_score_crosses_0_upward():
    # -10.0 puis +10.0 : franchissement confirmé, chaque valeur est hors de
    # la bande morte HYSTERESIS_BAND=5.0 (audit I2).
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite_raw": -10.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=10.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" in kinds
    watch_alert = next(a for a in alerts if a["kind"] == "watch")
    assert "médiane du profil" in watch_alert["title"]


def test_compute_company_alerts_watch_title_avoids_median_wording_when_not_recalibrated():
    """audit Minor #3 : "franchi la médiane du profil" est inexact pour un
    profil resté sur le score brut (trust, jamais recalibré en
    percentile) -- 0 y est un seuil neutre absolu, pas une médiane
    relative au pool."""
    previous_history = [{"date": "2026-09-05", "ticker": "III.L", "composite_raw": -10.0}]
    alerts = indices_score.compute_company_alerts(
        "III.L", composite_raw=10.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history, score_recalibrated=False,
    )
    watch_alert = next(a for a in alerts if a["kind"] == "watch")
    assert "médiane du profil" not in watch_alert["title"]
    assert "seuil neutre" in watch_alert["title"]


def test_compute_company_alerts_no_watch_when_already_above_0():
    """Ne doit se déclencher qu'au franchissement, pas rester actif en continu."""
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite_raw": 20.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=22.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" not in kinds


def test_compute_company_alerts_no_watch_when_oscillating_in_dead_zone():
    """Sans hystérésis, un score qui oscille autour de 0 dans la bande
    morte [-5, +5] redéclencherait watch à chaque repassage au-dessus de 0
    (l'ancienne logique ne comparait que la veille au jour même, sans
    mémoire du régime) : ici la veille était à -1.0 et aujourd'hui à 2.0,
    un franchissement de 0 au sens strict -- mais aucune valeur de
    l'historique n'est jamais sortie de la bande morte, donc aucun régime
    confirmé n'existe, et watch ne doit PAS se déclencher (audit I2)."""
    previous_history = [
        {"date": "2026-09-01", "ticker": "BN.PA", "composite_raw": -3.0},
        {"date": "2026-09-03", "ticker": "BN.PA", "composite_raw": 2.0},
        {"date": "2026-09-05", "ticker": "BN.PA", "composite_raw": -1.0},
    ]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=2.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" not in kinds


def test_compute_company_alerts_risque_on_rapid_drop():
    from datetime import datetime, timedelta
    recent_date = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    # drop de -45 : bien au-dessus du seuil RAPID_DROP_POINTS=20 (valeur
    # d'origine, échelle du score BRUT -- jamais retunée, audit I2).
    previous_history = [{"date": recent_date, "ticker": "BN.PA", "composite_raw": 80.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=35.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" in kinds


def test_compute_company_alerts_no_risque_when_drop_outside_window():
    from datetime import datetime, timedelta
    old_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    # Même magnitude de chute (-45) que test_..._risque_on_rapid_drop, mais
    # hors fenêtre : doit rester silencieux malgré une chute qui franchirait
    # le seuil RAPID_DROP_POINTS si elle était récente.
    previous_history = [{"date": old_date, "ticker": "BN.PA", "composite_raw": 80.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=35.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" not in kinds


def test_compute_company_alerts_entree_when_score_favorable_and_price_near_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=98.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" in kinds


def test_compute_company_alerts_no_entree_when_price_above_entry():
    """audit Minor #2 : un cours déjà AU-DESSUS du repère d'entrée (même
    de peu) ne doit plus déclencher "entree" -- l'ancienne bande
    symétrique (abs()) le laissait passer jusqu'à +5%, ce qui n'a pas de
    sens pour un signal d'achat ("encore atteignable", pas "déjà
    dépassé")."""
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=102.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_no_entree_during_declin_stage():
    """audit I8 : même score favorable et cours proche du repère d'entrée,
    l'alerte "entree" ne doit jamais se déclencher en phase Weinstein
    "Déclin" -- cohérent avec estimate_entry_exit_prices, qui exclut déjà
    le repère technique (MM200) dans ce cas."""
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=102.0, entry_price=100.0,
        previous_history=[], stage_label="Déclin",
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_entree_active_when_stage_label_none_or_other():
    """Rétrocompatibilité : stage_label=None (défaut, appelants existants)
    ou toute autre valeur ("Achat", "Neutre"...) ne change rien au
    déclenchement de l'alerte "entree"."""
    baseline = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=98.0, entry_price=100.0,
        previous_history=[],
    )
    with_neutral_stage = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=98.0, entry_price=100.0,
        previous_history=[], stage_label="Neutre",
    )
    assert "entree" in [a["kind"] for a in baseline]
    assert "entree" in [a["kind"] for a in with_neutral_stage]


def test_compute_company_alerts_no_entree_when_price_far_from_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=130.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_no_entree_when_score_not_favorable():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=-10.0, current_price=101.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_entree_stays_active_when_score_dips_into_dead_zone():
    """Un régime confirmé favorable (dernière valeur hors bande morte > +5)
    doit garder "entree" actif même si le score du jour retombe dans la
    bande morte -- évite qu'un signal disparaisse (et son email associé se
    redéclenche le lendemain) à cause d'un mouvement mineur (audit I2)."""
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite_raw": 15.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=2.0, current_price=98.0, entry_price=100.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" in kinds


def test_compute_company_alerts_handles_missing_current_or_entry_price():
    """Ne doit jamais lever, même si le cours ou le repère d'entrée est
    manquant (yfinance en panne, valorisation non calculable ce jour-là)."""
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=20.0, current_price=None, entry_price=None,
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
        {"date": "pas-une-date", "ticker": "BN.PA", "composite_raw": 80.0},  # malformed
        {"date": recent_date, "ticker": "BN.PA", "composite_raw": 80.0},      # valid
    ]
    # Ne doit pas lever, et doit détecter la chute de 80->35 en ignorant l'entrée malformée
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=35.0, current_price=100.0, entry_price=50.0,
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
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
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
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
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
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" in [a["kind"] for a in alerts]


def test_compute_company_alerts_no_actu_majeure_outside_news_window():
    news_items = [{
        "title": "Vieille actu majeure", "link": "https://example.com/a",
        "date": _days_ago(30), "summary": "Résumé.", "importance": "majeure",
    }]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
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
        "BN.PA", composite_raw=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[], news_items=news_items,
    )
    assert "actu_majeure" not in [a["kind"] for a in alerts]


def test_attach_alerts_and_update_history_sets_alerts_key(monkeypatch):
    companies = [
        {"ticker": "BN.PA", "score": 20.0, "score_raw": 20.0, "current_price": 102.0, "entry_price": 100.0},
        {"ticker": "MC.PA", "score": 5.0, "score_raw": 5.0, "current_price": 200.0, "entry_price": 150.0},
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
        {"date": recorded["entries"][0]["date"], "ticker": "BN.PA", "composite": 20.0, "composite_raw": 20.0},
        {"date": recorded["entries"][1]["date"], "ticker": "MC.PA", "composite": 5.0, "composite_raw": 5.0},
    ]


def test_attach_alerts_and_update_history_filters_history_per_ticker(monkeypatch):
    """L'historique passé à compute_company_alerts pour une entreprise ne
    doit contenir que les entrées de son propre ticker."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "score_raw": 20.0, "current_price": 102.0, "entry_price": 100.0}]
    mixed_history = [
        {"date": "2026-09-01", "ticker": "MC.PA", "composite": 99.0, "composite_raw": 99.0},
        {"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0, "composite_raw": 10.0},
    ]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: mixed_history)
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)

    captured = {}
    original = indices_score.compute_company_alerts

    def _spy(ticker, composite_raw, current_price, entry_price, previous_history, **kwargs):
        captured["previous_history"] = previous_history
        return original(ticker, composite_raw, current_price, entry_price, previous_history, **kwargs)

    monkeypatch.setattr(indices_score, "compute_company_alerts", _spy)

    indices_score._attach_alerts_and_update_history(companies)

    assert captured["previous_history"] == [
        {"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0, "composite_raw": 10.0},
    ]


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
    companies = [{"ticker": "BN.PA", "score": 20.0, "score_raw": 20.0, "current_price": 100.0, "entry_price": 100.0}]
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
    companies = [{"ticker": "BN.PA", "score": 20.0, "score_raw": 20.0, "current_price": 100.0, "entry_price": 100.0}]
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
        "ticker": "BN.PA", "name": "Danone", "score": 5.0, "score_raw": 5.0, "current_price": 100.0, "entry_price": 50.0,
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
        "ticker": "BN.PA", "name": "Danone", "score": 5.0, "score_raw": 5.0, "current_price": 100.0, "entry_price": 50.0,
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


def _fake_entry_alert_company(**overrides):
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "score": 20.0, "interpretation": "Solide",
        "current_price": 100.0, "entry_price": 100.0, "exit_price": 130.0,
        "alerts": [{"kind": "entree", "detail": "Score favorable, cours à moins de 5% du repère d'entrée."}],
    }
    company.update(overrides)
    return company


def test_entry_alert_item_html_includes_company_details():
    company = _fake_entry_alert_company(current_price=63.5, entry_price=60.0)
    html = indices_score._entry_alert_item_html(company)
    assert "Danone" in html
    assert "BN.PA" in html
    assert "CAC 40" in html  # nom affiché de l'indice, pas la clé brute
    assert "#indices/BN.PA" in html
    assert "Score favorable" in html  # détail de l'alerte "entree" elle-même
    assert "63.50 €" in html  # devise CAC40 = EUR


def test_entry_alert_item_html_uses_correct_currency_symbol_for_non_eur_index():
    """audit Minor #1 : l'email affichait "€" en dur pour toutes les
    devises -- une société Nasdaq (USD) doit afficher "$", pas "€"."""
    company = _fake_entry_alert_company(
        ticker="AAPL", name="Apple", index="NASDAQ",
        current_price=190.0, entry_price=185.0, exit_price=220.0,
    )
    html = indices_score._entry_alert_item_html(company)
    assert "190.00 $" in html
    assert "185.00 $ / 220.00 $" in html
    assert "€" not in html


def test_entry_alert_item_html_defaults_to_eur_for_unknown_index():
    """Repli sur € pour un index_key inconnu (ne devrait jamais arriver
    en pratique, mais ne doit pas lever) -- comportement inchangé."""
    company = _fake_entry_alert_company(index="UNKNOWN_INDEX")
    html = indices_score._entry_alert_item_html(company)
    assert "€" in html


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


def test_entry_alert_context_mentions_weinstein_phase():
    company = _fake_entry_alert_company(stage_label="Achat", volume_confirme=True)
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein : Achat" in context
    assert "volume confirmé" in context


def test_entry_alert_context_mentions_neutre_phase_without_volume_suffix():
    company = _fake_entry_alert_company(stage_label="Neutre", volume_confirme=False)
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein : Neutre" in context
    assert "volume confirmé" not in context


def test_entry_alert_context_omits_weinstein_line_when_stage_label_absent():
    """Ne casse pas le contrat existant "contexte vide si pas de
    donnée" (voir test_entry_alert_context_empty_when_no_data,
    juste au-dessus) : company sans stage_label -> pas de ligne."""
    company = _fake_entry_alert_company(factors=[], news=[])
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein" not in context
    assert context == ""


def test_entry_alert_item_html_omits_context_heading_when_no_context():
    company = _fake_entry_alert_company(factors=[], news=[])
    html = indices_score._entry_alert_item_html(company)
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


def test_major_news_alert_item_html_includes_article_details():
    company, alert = _fake_major_news_alert()
    html = indices_score._major_news_alert_item_html(company, alert)
    assert "Danone" in html
    assert "CAC 40" in html  # nom affiché de l'indice, pas la clé brute
    assert "Danone annonce une OPA sur un concurrent" in html
    assert "Danone lance une offre publique d'achat." in html
    assert "Les Echos" in html  # source retrouvée via le lien dans company["news"]
    assert "Favorable" in html  # sentiment de l'actu retrouvée
    assert "#indices/BN.PA" in html


def test_major_news_alert_item_html_defaults_when_news_item_not_found():
    """Si le lien de l'alerte ne correspond à aucune actu de company["news"]
    (ne devrait pas arriver en pratique, mais ne doit jamais planter), le
    mail reste construit avec un sentiment neutre par défaut."""
    company, alert = _fake_major_news_alert(link="https://example.com/inconnu")
    html = indices_score._major_news_alert_item_html(company, alert)
    assert "Neutre" in html


def test_build_daily_digest_email_html_includes_both_kinds():
    entry_company = _fake_entry_alert_company()
    news_company, news_alert = _fake_major_news_alert()
    html = indices_score.build_daily_digest_email_html([entry_company], [(news_company, news_alert)])
    assert "1 signal d'entrée" in html
    assert "1 actu majeure" in html
    assert "Danone" in html
    assert "Score favorable" in html  # carte du signal d'entrée
    assert "Danone annonce une OPA sur un concurrent" in html  # carte de l'actu majeure


def test_build_daily_digest_email_html_omits_empty_section():
    entry_company = _fake_entry_alert_company()
    html = indices_score.build_daily_digest_email_html([entry_company], [])
    assert "Actus majeures" not in html


def test_send_daily_digest_email_returns_false_when_both_empty():
    assert indices_score.send_daily_digest_email([], []) is False


def test_send_daily_digest_email_returns_false_when_smtp_credentials_missing(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    company = _fake_entry_alert_company()
    assert indices_score.send_daily_digest_email([company], []) is False


def test_send_daily_digest_email_sends_a_single_email_via_smtp_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.delenv("MAIL_TO", raising=False)
    entry_company = _fake_entry_alert_company()
    news_company, news_alert = _fake_major_news_alert()

    sent_messages = []

    class _FakeSMTP:
        def __init__(self, host, port):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, from_addr, to_addrs, message):
            sent_messages.append({"from_addr": from_addr, "to_addrs": to_addrs, "message": message})

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _FakeSMTP)

    result = indices_score.send_daily_digest_email([entry_company], [(news_company, news_alert)])

    assert result is True
    assert len(sent_messages) == 1  # un seul email, pas un par alerte
    message = sent_messages[0]
    assert message["to_addrs"] == ["bot@example.com"]  # repli sur SMTP_USER si MAIL_TO absent
    assert message["from_addr"] == "bot@example.com"

    # Sujet/corps encodés MIME (accents) — on décode avant de comparer,
    # une comparaison sur la chaîne brute échouerait sur le base64.
    parsed = email.message_from_string(message["message"])
    subject = str(email.header.make_header(email.header.decode_header(parsed["Subject"])))
    body = parsed.get_payload()[0].get_payload(decode=True).decode("utf-8")

    assert "Résumé Indices" in subject
    assert "signal d'entrée" in subject  # garde le filtre Gmail existant fonctionnel
    assert "actu majeure" in subject
    assert "Danone" in body


def test_send_daily_digest_email_returns_false_on_smtp_error(monkeypatch):
    """Une panne SMTP ne doit jamais faire lever d'exception ni faire
    échouer le run."""
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    company = _fake_entry_alert_company()

    def _raise(host, port):
        raise OSError("connexion refusée")

    monkeypatch.setattr(indices_score.smtplib, "SMTP", _raise)

    assert indices_score.send_daily_digest_email([company], []) is False


def test_main_writes_alerts_key_for_every_company(monkeypatch, tmp_path):
    """Preuve que main() câble réellement _attach_alerts_and_update_history
    et écrit le résultat dans le JSON — pas seulement que la fonction
    existe en isolation. Si l'appel à _attach_alerts_and_update_history
    était supprimé de main(), ce test doit échouer."""
    import json

    sentinel_previous_analyses = {"__sentinel__": True}
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(
        indices_score, "load_previous_company_analyses", lambda: sentinel_previous_analyses,
    )

    def _fake_build_company_entry(ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0):
        assert previous_analyses is sentinel_previous_analyses, (
            "main() doit transmettre le previous_analyses réellement chargé "
            "par load_previous_company_analyses(), pas un dict vide/différent "
            "— sans ça, le mécanisme de carry-forward (contrôle des coûts) "
            "est silencieusement désactivé en production."
        )
        return {
            "ticker": ticker, "name": name, "index": index_key, "score": 20.0, "score_raw": 20.0,
            "interpretation": "Solide",
            "current_price": 100.0, "entry_price": 100.0,
        }

    monkeypatch.setattr(indices_score, "build_company_entry", _fake_build_company_entry)
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
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

    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    fake_index_prices = {"CAC40": 7600.5, "DAX": 19000.2, "NASDAQ": 20123.4, "DOW": 41234.5}
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: fake_index_prices)
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["index_names"] == {
        "CAC40": "CAC 40", "DAX": "DAX", "NASDAQ": "Nasdaq 100", "DOW": "Dow Jones",
        "FTSE": "FTSE 100", "SMI": "SMI", "IBEX35": "IBEX 35", "FTSEMIB": "FTSE MIB",
        "NIKKEI225": "Nikkei 225", "HANGSENG": "Hang Seng",
    }
    assert written["index_currency"] == {
        "CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD", "DOW": "USD", "FTSE": "GBP",
        "SMI": "CHF", "IBEX35": "EUR", "FTSEMIB": "EUR", "NIKKEI225": "JPY", "HANGSENG": "HKD",
    }
    assert written["index_prices"] == fake_index_prices
    written_by_ticker = {c["ticker"]: c["index"] for c in written["companies"]}
    for company in indices_score.COMPANIES:
        assert written_by_ticker[company["ticker"]] == company["index"]
    assert {c["index"] for c in written["companies"]} == {
        "CAC40", "DAX", "NASDAQ", "DOW", "FTSE", "SMI", "IBEX35", "FTSEMIB",
        "NIKKEI225", "HANGSENG",
    }


def test_main_routes_risk_free_rate_by_currency(monkeypatch, tmp_path):
    """Ajouté avec le Nasdaq-100 : chaque entreprise doit recevoir le taux
    sans risque de SA devise (France pour CAC40/DAX, US pour NASDAQ), pas
    un taux France unique appliqué à tout le monde — sans ça, le WACC (et
    donc le score de valorisation) des entreprises Nasdaq serait
    silencieusement calculé avec le mauvais taux."""
    import json

    def _fake_fetch_risk_free_rate(series_id):
        return {
            indices_score.FRED_RISK_FREE_SERIES: 3.68,
            indices_score.FRED_RISK_FREE_SERIES_US: 4.20,
            indices_score.FRED_RISK_FREE_SERIES_UK: 4.55,
            indices_score.FRED_RISK_FREE_SERIES_CH: 0.31,
            indices_score.FRED_RISK_FREE_SERIES_JP: 2.67,
        }[series_id]

    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", _fake_fetch_risk_free_rate)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    received_rates = {}

    def _fake_build_company_entry(ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0):
        received_rates[ticker] = risk_free_rate
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        }

    monkeypatch.setattr(indices_score, "build_company_entry", _fake_build_company_entry)
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(
        indices_score, "fetch_index_prices",
        lambda: {
            "CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None,
            "FTSE": None, "SMI": None, "IBEX35": None, "FTSEMIB": None,
            "NIKKEI225": None, "HANGSENG": None,
        },
    )
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    cac40_ticker = indices_score.CAC40_COMPANIES[0]["ticker"]
    nasdaq_ticker = indices_score.NASDAQ_COMPANIES[0]["ticker"]
    ftse_ticker = indices_score.FTSE_COMPANIES[0]["ticker"]
    smi_ticker = indices_score.SMI_COMPANIES[0]["ticker"]
    ibex_ticker = indices_score.IBEX35_COMPANIES[0]["ticker"]
    ftsemib_ticker = indices_score.FTSEMIB_COMPANIES[0]["ticker"]
    nikkei_ticker = indices_score.NIKKEI225_COMPANIES[0]["ticker"]
    hangseng_ticker = indices_score.HANGSENG_COMPANIES[0]["ticker"]
    assert received_rates[cac40_ticker] == 3.68
    assert received_rates[nasdaq_ticker] == 4.20
    assert received_rates[ftse_ticker] == 4.55
    assert received_rates[smi_ticker] == 0.31
    assert received_rates[ibex_ticker] == 3.68
    assert received_rates[nikkei_ticker] == 2.67
    assert received_rates[ftsemib_ticker] == 3.68
    # HKD n'a pas sa propre série FRED (Hong Kong hors OCDE) mais utilise
    # le Treasury US (DGS10) vu le peg HKD/USD (audit I7, volet 4) --
    # reçoit donc le même taux que le Nasdaq, pas None/proxy générique.
    assert received_rates[hangseng_ticker] == 4.20


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


def test_extract_ratios_degrades_but_stays_meaningless_on_financial_sector_statements():
    """EBIT est devenu optionnel comme EBITDA (voir
    test_extract_ratios_degrades_gracefully_when_ebit_missing_but_ebitda_absent_too,
    trouvé en échec de production sur des gestionnaires d'actifs/trusts
    fermés/REIT du FTSE 100/FTSE MIB) : extract_ratios ne plante plus du
    tout sur de vrais comptes bancaires (ni EBITDA ni EBIT, comme BNP/
    SocGen/Crédit Agricole/AXA) — roce/icr/net_debt_ebitda/cagr_ebitda/
    fcf_conversion/EV-EBITDA dégradent tous vers leurs valeurs neutres.
    Documente pourquoi extract_ratios_financial reste malgré tout le bon
    choix pour un établissement financier : elle calcule de VRAIS ratios
    (ROE, levier, P/B) là où extract_ratios ne renverrait que des
    placeholders neutres sans aucun signal — une dégradation propre n'est
    pas la même chose qu'une évaluation correcte."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )  # ne doit plus lever KeyError

    assert ratios["roce"] == 0.0
    assert ratios["icr"] == 10.0
    assert ratios["net_debt_ebitda"] == 0.0
    assert ratios["cagr_ebitda"] == 0.0
    assert ratios["fcf_conversion"] == 0.0
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["avg_ev_ebitda_5y"] == 0.0


def test_extract_ratios_degrades_gracefully_when_ebitda_missing_but_ebit_present():
    """Reproduit Kyowa Hakko Kirin (4151.T, Nikkei 225) en production :
    aucune ligne EBITDA/Normalized EBITDA chez yfinance, mais EBIT bien
    présent, et ce n'est pas un établissement financier. Ne doit pas faire
    lever KeyError sur toute l'entreprise — les facteurs dépendant
    d'EBITDA (dette nette/EBITDA, CAGR EBITDA, conversion FCF/EBITDA,
    EV/EBITDA) dégradent vers leurs valeurs neutres (0.0) au lieu."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    financials = financials.drop(index="EBITDA")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["net_debt_ebitda"] == 0.0
    assert ratios["cagr_ebitda"] == 0.0
    assert ratios["fcf_conversion"] == 0.0
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["avg_ev_ebitda_5y"] == 0.0
    # Audit 2026-09-13 : ces valeurs neutres (0.0) doivent aussi porter un
    # signal explicite "donnée indisponible" — sans lui, score_structure_
    # financiere/score_generation_cash liraient ce 0.0 comme une vraie
    # donnée (endettement nul -> +10.0, conversion cash nulle -> -10.0)
    # au lieu d'une absence d'opinion. roce reste disponible ici (EBIT
    # présent) ; valuation_available est aussi False bien que net_income
    # soit disponible pour le P/E — la boucle qui construit ev_ebitda_by_year
    # ET pe_by_year saute l'année dès qu'EBITDA OU net_income manque (les
    # deux jambes sont couplées), donc PE devient indisponible avec
    # EV/EBITDA même si lui seul aurait pu se calculer. Comportement
    # préexistant, pas modifié par ce fix.
    assert ratios["structure_available"] is False
    assert ratios["fcf_conversion_available"] is False
    assert ratios["roce_available"] is True
    assert ratios["valuation_available"] is False


def test_extract_ratios_degrades_gracefully_when_ebit_missing_but_ebitda_absent_too():
    """Reproduit 3i Group/Aberdeen Group/Alliance Witan/F&C Investment
    Trust/ICG/Pershing Square Holdings/Polar Capital Technology Trust/
    Scottish Mortgage/Tritax Big Box REIT (FTSE 100) en production :
    gestionnaires d'actifs, trusts fermés et REIT, aucune ligne EBIT/
    Operating Income/Total Operating Income As Reported chez yfinance (ni
    EBITDA), sans être des établissements financiers au sens de
    FINANCIAL_SECTOR_TICKERS (exclusion volontaire). Banca Mediolanum/
    FinecoBank (FTSE MIB) étaient listées ici jusqu'au 2026-09-13 mais ont
    depuis été reclassées dans FINANCIAL_SECTOR_TICKERS (profil bancaire
    classique confirmé sur données réelles), donc plus dans ce cas. Ne
    doit pas faire lever KeyError — roce/icr dégradent vers leurs valeurs
    neutres (0.0 / 10.0) en plus des facteurs déjà couverts par le test
    EBITDA ci-dessus, l'entreprise reste notée sur ses autres facteurs
    (croissance, valorisation, momentum, actualité)."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    financials = financials.drop(index=["EBITDA", "EBIT"])

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["roce"] == 0.0
    assert ratios["icr"] == 10.0
    assert ratios["net_debt_ebitda"] == 0.0
    assert ratios["cagr_ebitda"] == 0.0
    assert ratios["fcf_conversion"] == 0.0
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["avg_ev_ebitda_5y"] == 0.0
    # Audit 2026-09-13 : les 4 facteurs standard dépendent tous d'EBITDA
    # et/ou EBIT ici — les 4 doivent donc être marqués indisponibles,
    # pour que score_rentabilite/score_structure_financiere/
    # score_generation_cash/score_valorisation retombent sur un vrai 0.0
    # neutre avec un message honnête plutôt que de lire ces replis comme
    # une vraie performance (le pire ou le meilleur score selon le
    # facteur, jamais neutre, avant ce fix).
    assert ratios["roce_available"] is False
    assert ratios["structure_available"] is False
    assert ratios["fcf_conversion_available"] is False
    assert ratios["valuation_available"] is False


def test_extract_ratios_degrades_gracefully_when_capex_entirely_missing():
    """Reproduit une quinzaine d'entreprises du Nikkei 225 en production
    (Aeon, Chubu Electric Power, Japan Airlines, Tokyu...) : aucune des 3
    lignes de repli capex (Capital Expenditure/Net PPE Purchase And
    Sale/Net Investment Properties Purchase And Sale) chez yfinance, sans
    point commun sectoriel avec les établissements financiers. Ne doit pas
    faire lever KeyError — fcf/fcf_normalized dégradent vers 0.0."""
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    cashflow = cashflow.drop(index="Capital Expenditure")

    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )  # ne doit pas lever KeyError

    assert ratios["fcf"] == 0.0
    assert ratios["fcf_normalized"] == 0.0


def test_extract_ratios_financial_derives_operating_cash_flow_from_free_cash_flow_when_missing():
    """Reproduit Swiss Life Holding (SLHN.SW) et Mapfre (MAP.MC) en
    production : pas de ligne 'Operating Cash Flow' chez yfinance pour ces
    deux entreprises (méthodologie financière), mais 'Free Cash Flow' et
    'Capital Expenditure' sont bien présentes — doit être utilisée pour
    reconstituer OCF plutôt que de faire lever KeyError, même repli que
    extract_ratios."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    # FCF = OCF + capex (capex déjà négatif) sur chaque exercice de la
    # fixture d'origine (OCF=[320,300,280,260], capex=[-10,-9,-8,-7]).
    cashflow = cashflow.rename(index={"Operating Cash Flow": "Free Cash Flow"})
    cashflow.loc["Free Cash Flow"] = [310.0, 291.0, 272.0, 253.0]

    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )  # ne doit pas lever KeyError

    # OCF reconstitué = FCF - capex = 310 - (-10) = 320 (exercice le plus
    # récent) -> cash_conversion = OCF / net_income = 320 / 300 * 100.
    assert ratios["cash_conversion"] == pytest.approx(320.0 / 300.0 * 100)


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
    assert ratios["current_ev_ebitda"] == 0.0
    assert ratios["avg_ev_ebitda_5y"] == 0.0
    # net_debt N'EST PAS une valeur neutre (depuis le 2026-09-13, ajout du
    # profil trust) : vrai calcul Total Debt - Cash sur l'exercice le plus
    # récent (500.0 - 200.0), sûr pour le DCF (déjà désactivé par fcf=0.0
    # avant que net_debt ne soit utilisé).
    assert ratios["net_debt"] == pytest.approx(300.0)


def test_extract_ratios_financial_roe_treated_as_missing_when_equity_negative():
    """Même correctif que le profil standard, côté extract_ratios_financial
    (audit Minor #7) -- ici, roe pilote directement score_rentabilite_financiere
    (pas seulement un affichage), donc un signe inversé aurait un impact
    réel sur le score, pas seulement sur le texte."""
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    years = list(financials.columns)
    balance_sheet.loc["Stockholders Equity", years[0]] = -500.0
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )
    assert ratios["roe"] == 0.0


def test_extract_ratios_financial_exposes_no_loss_years():
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )
    assert "no_loss_years" in ratios
    # _make_financial_fixture_statements a un Net Income positif sur les
    # 4 exercices (300.0, 280.0, 260.0, 240.0).
    assert ratios["no_loss_years"] is True


def test_extract_ratios_financial_no_loss_years_false_with_one_loss_year():
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    years = list(financials.columns)
    financials.loc["Net Income", years[1]] = -50.0
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )
    assert ratios["no_loss_years"] is False


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


def test_build_financial_narrative_context_degrades_gracefully_when_capex_entirely_absent():
    """Reproduit le cas JPMorgan Chase (JPM) en production : aucune des 3
    lignes de repli capex ('Capital Expenditure', 'Net PPE Purchase And
    Sale', 'Net Investment Properties Purchase And Sale') n'existe pour
    une banque — pas une panne, la notion de capex industriel n'existe pas
    pour un établissement financier. 'Operating Cash Flow' reste
    directement disponible (donc pas besoin du repli Free Cash Flow) :
    ne doit PAS lever KeyError sur toute l'entreprise, juste afficher
    capex/FCF comme non disponibles."""
    financials, balance_sheet, cashflow, _ = _make_financial_fixture_statements()
    cashflow = cashflow.drop(index="Capital Expenditure")  # aucune ligne capex du tout
    quarterly_financials = _fake_annual_df(
        {"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-06-30")],
    )

    context = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials,
    )  # ne doit pas lever KeyError

    assert "FCF non disponible" in context


def test_score_rentabilite_financiere_rewards_roe_above_cost_of_equity():
    good = indices_score.score_rentabilite_financiere(roe=15.0, cost_of_equity=8.0)
    bad = indices_score.score_rentabilite_financiere(roe=2.0, cost_of_equity=8.0)
    assert good.score > 0
    assert bad.score < 0
    assert "profil financier" in good.raw_value.lower()
    assert "capitaux propres" in good.raw_value.lower()


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
    assert "5 ans" not in result.raw_value  # audit Minor #5, point 2


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


def test_score_structure_financiere_trust_low_gearing_is_strong():
    # 3i Group réel (audit 2026-09-13) : dette nette ~626 M£ / capitaux
    # propres ~30 887 M£ ~= 2% de gearing, bien sous le seuil confort (10%).
    result = indices_score.score_structure_financiere_trust(net_debt=626.0, equity=30887.0)
    assert result.name == "Structure financière / solvabilité"
    assert result.weight == 0.20
    assert result.score > 7.5  # gearing ~2.0% -> proche du plafond +10
    assert "profil trust" in result.raw_value.lower()


def test_score_structure_financiere_trust_high_gearing_is_weak():
    # Gearing 45% (au-delà du seuil de vigilance 40%, courant pour une
    # foncière comme Tritax Big Box) -> score faible/négatif.
    result = indices_score.score_structure_financiere_trust(net_debt=450.0, equity=1000.0)
    assert result.score <= -8.0


def test_score_structure_financiere_trust_neutral_when_equity_missing():
    result = indices_score.score_structure_financiere_trust(net_debt=100.0, equity=0.0)
    assert result.score == 0.0
    assert result.raw_value == (
        "Donnée indisponible (capitaux propres non exploitables chez la source de données)"
    )


def test_score_generation_cash_trust_always_neutral():
    """Pas une donnée manquante à combler : la génération de cash ne
    s'applique structurellement pas à ce modèle d'affaires (cessions de
    portefeuille, pas de cycle d'exploitation)."""
    result = indices_score.score_generation_cash_trust()
    assert result.name == "Génération de cash"
    assert result.weight == 0.12
    assert result.score == 0.0
    assert "non applicable" in result.raw_value.lower()


def test_score_valorisation_trust_discount_to_nav_is_positive():
    # P/B < 1.0 -> décote sur la NAV -> favorable. current_pb arrive déjà
    # dans la bonne unité depuis le fix pence/livre du 2026-09-13
    # (normalisé à la source dans fetch_company_financials, plus besoin
    # d'ajuster ici).
    result = indices_score.score_valorisation_trust(current_pb=0.85)
    assert result.name == "Valorisation relative"
    assert result.weight == 0.08
    assert result.score > 0
    assert "décote" in result.raw_value.lower()
    assert "0.85x" in result.raw_value


def test_score_valorisation_trust_premium_to_nav_is_negative():
    # P/B > 1.0 -> prime sur la NAV -> défavorable
    result = indices_score.score_valorisation_trust(current_pb=1.15)
    assert result.score < 0
    assert "prime" in result.raw_value.lower()


def test_score_valorisation_trust_at_nav_is_neutral():
    result = indices_score.score_valorisation_trust(current_pb=1.0)
    assert result.score == 0.0


def test_score_valorisation_trust_neutral_when_pb_unavailable():
    # Alliance Witan (ALW.L) : aucun P/B exploitable chez yfinance, vérifié
    # via un diagnostic dédié le 2026-09-13.
    result = indices_score.score_valorisation_trust(current_pb=0.0)
    assert result.score == 0.0
    assert result.raw_value == "Donnée indisponible (pas de P/B exploitable chez la source de données)"


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
        "is_trust": False,
        "_price_history_daily": [],
        "_dividend_history": [],
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


def test_build_company_entry_uses_cost_of_equity_not_wacc_for_financial_roe(monkeypatch):
    # audit I4 : le facteur "Rentabilité" du profil financier doit
    # comparer le ROE au coût des CAPITAUX PROPRES (Ke), pas au WACC --
    # avec une dette élevée (plausible pour une banque), WACC et Ke
    # divergent nettement, ce qui permet de vérifier laquelle des deux
    # valeurs est réellement utilisée dans le texte du facteur.
    ratios = _fake_financial_ratios()
    ratios["total_debt"] = 3000.0
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    market_cap = ratios["current_price"] * ratios["shares_outstanding"]
    expected_cost_of_equity = indices_score.estimate_cost_of_equity(3.0, ratios["beta"], market_cap, 1.0)
    expected_wacc = indices_score.estimate_wacc(
        3.0, ratios["beta"], market_cap, ratios["total_debt"], ratios["tax_rate"], 1.0,
    )
    # Vérifie d'abord que la fixture crée bien un écart mesurable -- sinon
    # le test ne prouverait rien.
    assert abs(expected_cost_of_equity - expected_wacc) > 1.0

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    raw_value = entry["factors"][0]["raw_value"]
    assert f"{expected_cost_of_equity:.1f}%" in raw_value
    assert f"{expected_wacc:.1f}%" not in raw_value


def test_build_company_entry_exposes_score_raw_equal_to_score_before_recalibration(monkeypatch):
    # build_company_entry ne recalibre jamais lui-même (recalibrate_scores_by_profile
    # s'applique après, sur l'ensemble du pool) -- à ce stade, score_raw doit donc
    # toujours être strictement identique à score (audit I1).
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    assert entry["score_raw"] == entry["score"]


def test_build_company_entry_defaults_score_recalibrated_to_false(monkeypatch):
    # build_company_entry ne recalibre jamais (voir test ci-dessus) -- le
    # défaut doit être False, changé en True uniquement par
    # recalibrate_scores_by_profile pour les profils au pool suffisant
    # (audit I3).
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    assert entry["score_recalibrated"] is False


def test_build_company_entry_exposes_weinstein_stage_fields(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    # _fake_financial_ratios() ne fournit pas ces clés -> repli attendu.
    assert entry["stage"] is None
    assert entry["stage_label"] == "Neutre"
    assert entry["volume_confirme"] is False


def _fake_trust_ratios():
    ratios = _fake_financial_ratios()
    ratios["is_financial"] = False
    ratios["is_trust"] = True
    ratios["current_pb"] = 0.85  # décote de 15% sur la NAV
    return ratios


def test_build_company_entry_uses_trust_factors_for_trust_tickers(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_trust_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("III.L", "3i Group", 3.0, {}, index_key="FTSE")

    assert entry["is_financial"] is False
    # Régression : is_trust doit être recopié depuis data["is_trust"] jusque
    # dans l'entrée finale, sinon les trusts ne sont jamais isolés dans leur
    # propre pool de recalibration par _score_profile_key.
    assert entry["is_trust"] is True
    assert [f["name"] for f in entry["factors"]] == [
        "Rentabilité / création de valeur", "Structure financière / solvabilité",
        "Croissance", "Génération de cash", "Valorisation relative",
        "Dynamique récente", "Actualité récente",
    ]
    cash_factor = entry["factors"][3]
    assert cash_factor["score"] == 0.0
    assert "non applicable" in cash_factor["raw_value"].lower()
    valorisation_factor = entry["factors"][4]
    assert valorisation_factor["score"] > 0  # décote sur la NAV -> favorable
    assert "nav" in valorisation_factor["raw_value"].lower()


def test_build_company_entry_includes_also_indices(monkeypatch):
    """Reproduit le cas Apple (AAPL) : suivie sous NASDAQ_COMPANIES mais
    aussi membre réel du Dow Jones — pas dupliquée dans DOW_COMPANIES,
    mais le champ also_indices doit porter cette appartenance
    supplémentaire jusqu'au JSON exporté, pour que le frontend l'affiche."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    with_also = indices_score.build_company_entry(
        "AAPL", "Apple Inc.", 3.0, {}, index_key="NASDAQ", also_indices=["DOW"],
    )
    without_also = indices_score.build_company_entry(
        "BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40",
    )

    assert with_also["also_indices"] == ["DOW"]
    assert without_also["also_indices"] == []  # défaut None -> [] plutôt qu'absent du dict


def test_build_company_entry_carries_the_price_history_through(monkeypatch):
    """fetch_company_financials expose desormais _price_history_daily
    (voir test_fetch_company_financials_exposes_the_full_price_history_for_persistence)
    — build_company_entry doit la faire remonter jusqu'a son dict de
    sortie sans toucher aux champs publics existants, pour que main()
    puisse ensuite la retirer (.pop) avant d'ecrire docs/indices.json et
    la rediriger vers update_price_history (docs/price_history.json)."""
    fake_ratios = _fake_financial_ratios()
    fake_ratios["_price_history_daily"] = [
        {"date": "2024-01-01", "ticker": "BNP.PA", "price": 60.0},
    ]
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: fake_ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry(
        "BNP.PA", "BNP Paribas", risk_free_rate=0.03, previous_analyses={}, index_key="CAC40")

    assert "_price_history_daily" in entry
    assert entry["_price_history_daily"]
    assert entry["_price_history_daily"] == fake_ratios["_price_history_daily"]
    # Les champs publics existants restent inchanges par ce cablage.
    assert entry["ticker"] == "BNP.PA"
    assert entry["index"] == "CAC40"


def test_build_company_entry_carries_the_dividend_history_through(monkeypatch):
    """Meme cablage que _price_history_daily (voir
    test_build_company_entry_carries_the_price_history_through) applique a
    _dividend_history : build_company_entry doit la faire remonter jusqu'a
    son dict de sortie, pour que main() puisse ensuite la retirer (.pop)
    avant d'ecrire docs/indices.json et la rediriger vers
    update_dividend_history (docs/dividend_history.json)."""
    fake_ratios = _fake_financial_ratios()
    fake_ratios["_dividend_history"] = [
        {"date": "2026-06-15", "ticker": "BNP.PA", "amount": 2.10},
    ]
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: fake_ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry(
        "BNP.PA", "BNP Paribas", risk_free_rate=0.03, previous_analyses={}, index_key="CAC40")

    assert "_dividend_history" in entry
    assert entry["_dividend_history"] == fake_ratios["_dividend_history"]
    assert entry["ticker"] == "BNP.PA"
    assert entry["index"] == "CAC40"


def test_financial_sector_tickers_are_in_companies():
    company_tickers = {c["ticker"] for c in indices_score.COMPANIES}
    assert indices_score.FINANCIAL_SECTOR_TICKERS <= company_tickers


def test_companies_combines_all_indices_with_correct_index_tag():
    """COMPANIES doit être l'union de CAC40_COMPANIES, DAX_COMPANIES,
    NASDAQ_COMPANIES, DOW_COMPANIES, FTSE_COMPANIES, SMI_COMPANIES,
    IBEX35_COMPANIES, FTSEMIB_COMPANIES, NIKKEI225_COMPANIES et
    HANGSENG_COMPANIES, chaque entreprise gardant son propre indice — pas
    une seule valeur globale (l'ancien bug qu'INDEX_KEY représentait)."""
    assert len(indices_score.COMPANIES) == (
        len(indices_score.CAC40_COMPANIES)
        + len(indices_score.DAX_COMPANIES)
        + len(indices_score.NASDAQ_COMPANIES)
        + len(indices_score.DOW_COMPANIES)
        + len(indices_score.FTSE_COMPANIES)
        + len(indices_score.SMI_COMPANIES)
        + len(indices_score.IBEX35_COMPANIES)
        + len(indices_score.FTSEMIB_COMPANIES)
        + len(indices_score.NIKKEI225_COMPANIES)
        + len(indices_score.HANGSENG_COMPANIES)
    )
    by_ticker = {c["ticker"]: c["index"] for c in indices_score.COMPANIES}
    for c in indices_score.CAC40_COMPANIES:
        assert by_ticker[c["ticker"]] == "CAC40"
    for c in indices_score.DAX_COMPANIES:
        assert by_ticker[c["ticker"]] == "DAX"
    for c in indices_score.NASDAQ_COMPANIES:
        assert by_ticker[c["ticker"]] == "NASDAQ"
    for c in indices_score.DOW_COMPANIES:
        assert by_ticker[c["ticker"]] == "DOW"
    for c in indices_score.FTSE_COMPANIES:
        assert by_ticker[c["ticker"]] == "FTSE"
    for c in indices_score.SMI_COMPANIES:
        assert by_ticker[c["ticker"]] == "SMI"
    for c in indices_score.IBEX35_COMPANIES:
        assert by_ticker[c["ticker"]] == "IBEX35"
    for c in indices_score.FTSEMIB_COMPANIES:
        assert by_ticker[c["ticker"]] == "FTSEMIB"
    for c in indices_score.NIKKEI225_COMPANIES:
        assert by_ticker[c["ticker"]] == "NIKKEI225"
    for c in indices_score.HANGSENG_COMPANIES:
        assert by_ticker[c["ticker"]] == "HANGSENG"
    assert set(indices_score.INDEX_NAMES) >= {
        "CAC40", "DAX", "NASDAQ", "DOW", "FTSE", "SMI", "IBEX35", "FTSEMIB",
        "NIKKEI225", "HANGSENG",
    }


def test_nikkei225_has_no_overlap_with_other_indices():
    nikkei_tickers = {c["ticker"] for c in indices_score.NIKKEI225_COMPANIES}
    other_tickers = {
        c["ticker"] for c in (
            indices_score.CAC40_COMPANIES + indices_score.DAX_COMPANIES
            + indices_score.NASDAQ_COMPANIES + indices_score.DOW_COMPANIES
            + indices_score.FTSE_COMPANIES + indices_score.SMI_COMPANIES
            + indices_score.IBEX35_COMPANIES + indices_score.FTSEMIB_COMPANIES
        )
    }
    assert nikkei_tickers & other_tickers == set()


def test_nikkei225_financial_sector_tickers_are_in_nikkei225_companies():
    """19, pas 15 : Nomura Holdings/Daiwa Securities Group/Orix/Japan Post
    Holdings ont été reclassées après le premier run réel (2026-09-12) —
    aucune ligne EBITDA ni EBIT chez yfinance, même trou de données que
    les banques de dépôt classiques."""
    nikkei_tickers = {c["ticker"] for c in indices_score.NIKKEI225_COMPANIES}
    nikkei_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".T")}
    assert nikkei_financial_tickers <= nikkei_tickers
    assert len(nikkei_financial_tickers) == 19


def test_hangseng_does_not_duplicate_ticker_already_in_ftse():
    """HSBC Holdings (HSBA.L) est un constituant Hang Seng réel mais reste
    suivie uniquement côté FTSE_COMPANIES, avec also_indices=["HANGSENG"]
    — pas dupliquée dans HANGSENG_COMPANIES."""
    ftse_tickers = {c["ticker"] for c in indices_score.FTSE_COMPANIES}
    hangseng_tickers = {c["ticker"] for c in indices_score.HANGSENG_COMPANIES}
    assert ftse_tickers & hangseng_tickers == set()
    hsbc = next(c for c in indices_score.FTSE_COMPANIES if c["ticker"] == "HSBA.L")
    assert hsbc.get("also_indices") == ["HANGSENG"]


def test_hangseng_financial_sector_tickers_are_in_hangseng_companies():
    hangseng_tickers = {c["ticker"] for c in indices_score.HANGSENG_COMPANIES}
    hangseng_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".HK")}
    assert hangseng_financial_tickers <= hangseng_tickers
    assert len(hangseng_financial_tickers) == 8


def test_hkd_uses_us_treasury_series_given_the_hkd_usd_peg():
    """Hong Kong n'est pas membre de l'OCDE — aucune série FRED de taux
    long terme n'existe pour le HKD (IRLTLT01HKM156N confirmé absent) --
    ce fait ne change pas. Mais RISK_FREE_SERIES_BY_CURRENCY utilise
    désormais le Treasury US (DGS10) pour le HKD plutôt que de retomber
    sur le proxy générique COST_OF_CAPITAL_PROXY (audit I7, volet 4) : le
    peg HKD/USD (currency board depuis 1983) rend le Treasury US
    nettement plus pertinent qu'un taux déconnecté de tout marché réel."""
    assert indices_score.RISK_FREE_SERIES_BY_CURRENCY["HKD"] == indices_score.FRED_RISK_FREE_SERIES_US


def test_dow_companies_does_not_duplicate_tickers_already_in_nasdaq():
    """9 composants du Dow (Alphabet, Amazon, Amgen, Apple, Cisco,
    Honeywell Technologies, Microsoft, Nvidia, Walmart) sont déjà suivis
    côté NASDAQ_COMPANIES — même choix explicite que le chevauchement
    CAC40/DAX (pas de doublon de calcul/carte entreprise)."""
    nasdaq_tickers = {c["ticker"] for c in indices_score.NASDAQ_COMPANIES}
    dow_tickers = {c["ticker"] for c in indices_score.DOW_COMPANIES}
    assert nasdaq_tickers & dow_tickers == set()


def test_ftse_companies_does_not_duplicate_ticker_already_in_nasdaq():
    """Coca-Cola Europacific Partners (CCEP) est un constituant FTSE 100
    réel mais reste suivie uniquement côté NASDAQ_COMPANIES, avec
    also_indices=["FTSE"] — pas dupliquée dans FTSE_COMPANIES (même
    convention que les chevauchements CAC40/DAX et NASDAQ/DOW)."""
    nasdaq_tickers = {c["ticker"] for c in indices_score.NASDAQ_COMPANIES}
    ftse_tickers = {c["ticker"] for c in indices_score.FTSE_COMPANIES}
    assert nasdaq_tickers & ftse_tickers == set()
    ccep = next(c for c in indices_score.NASDAQ_COMPANIES if c["ticker"] == "CCEP")
    assert ccep.get("also_indices") == ["FTSE"]


def test_ftse_financial_sector_tickers_are_in_ftse_companies():
    ftse_tickers = {c["ticker"] for c in indices_score.FTSE_COMPANIES}
    ftse_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".L")}
    assert ftse_financial_tickers <= ftse_tickers
    assert len(ftse_financial_tickers) == 15


def test_smi_companies_has_no_overlap_with_other_indices():
    """Le SMI cœur (20 valeurs) n'a aucun chevauchement avec les autres
    indices déjà suivis — vérifié par la recherche dédiée y compris pour
    les doubles cotations US (Alcon, Amrize, Logitech)."""
    smi_tickers = {c["ticker"] for c in indices_score.SMI_COMPANIES}
    other_tickers = {
        c["ticker"] for c in (
            indices_score.CAC40_COMPANIES + indices_score.DAX_COMPANIES
            + indices_score.NASDAQ_COMPANIES + indices_score.DOW_COMPANIES
            + indices_score.FTSE_COMPANIES
        )
    }
    assert smi_tickers & other_tickers == set()


def test_smi_financial_sector_tickers_are_in_smi_companies():
    smi_tickers = {c["ticker"] for c in indices_score.SMI_COMPANIES}
    smi_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".SW")}
    assert smi_financial_tickers <= smi_tickers
    assert len(smi_financial_tickers) == 4


def test_ibex35_does_not_duplicate_tickers_already_tracked_elsewhere():
    """ArcelorMittal (CAC40, MT.PA), Ferrovial (NASDAQ, FER) et
    International Airlines Group (FTSE, IAG.L) sont des constituants IBEX
    35 réels mais restent suivis uniquement sous leur indice d'origine,
    avec also_indices=["IBEX35"] — pas dupliqués dans IBEX35_COMPANIES."""
    other_tickers = {
        c["ticker"] for c in (
            indices_score.CAC40_COMPANIES + indices_score.DAX_COMPANIES
            + indices_score.NASDAQ_COMPANIES + indices_score.DOW_COMPANIES
            + indices_score.FTSE_COMPANIES + indices_score.SMI_COMPANIES
        )
    }
    ibex_tickers = {c["ticker"] for c in indices_score.IBEX35_COMPANIES}
    assert other_tickers & ibex_tickers == set()

    mt = next(c for c in indices_score.CAC40_COMPANIES if c["ticker"] == "MT.PA")
    fer = next(c for c in indices_score.NASDAQ_COMPANIES if c["ticker"] == "FER")
    iag = next(c for c in indices_score.FTSE_COMPANIES if c["ticker"] == "IAG.L")
    assert mt.get("also_indices") == ["IBEX35"]
    assert fer.get("also_indices") == ["IBEX35"]
    assert iag.get("also_indices") == ["IBEX35"]


def test_ibex35_financial_sector_tickers_are_in_ibex35_companies():
    ibex_tickers = {c["ticker"] for c in indices_score.IBEX35_COMPANIES}
    ibex_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".MC")}
    assert ibex_financial_tickers <= ibex_tickers
    assert len(ibex_financial_tickers) == 7


def test_ftsemib_does_not_duplicate_tickers_already_tracked_in_cac40():
    """STMicroelectronics (STMPA.PA) et Stellantis (STLAP.PA) sont des
    constituants FTSE MIB réels mais restent suivis uniquement côté
    CAC40_COMPANIES, avec also_indices=["FTSEMIB"] — pas dupliqués dans
    FTSEMIB_COMPANIES."""
    cac40_tickers = {c["ticker"] for c in indices_score.CAC40_COMPANIES}
    ftsemib_tickers = {c["ticker"] for c in indices_score.FTSEMIB_COMPANIES}
    assert cac40_tickers & ftsemib_tickers == set()

    stm = next(c for c in indices_score.CAC40_COMPANIES if c["ticker"] == "STMPA.PA")
    stl = next(c for c in indices_score.CAC40_COMPANIES if c["ticker"] == "STLAP.PA")
    assert stm.get("also_indices") == ["FTSEMIB"]
    assert stl.get("also_indices") == ["FTSEMIB"]


def test_ftsemib_financial_sector_tickers_are_in_ftsemib_companies():
    ftsemib_tickers = {c["ticker"] for c in indices_score.FTSEMIB_COMPANIES}
    ftsemib_financial_tickers = {t for t in indices_score.FINANCIAL_SECTOR_TICKERS if t.endswith(".MI")}
    assert ftsemib_financial_tickers <= ftsemib_tickers
    # 8 -> 10 le 2026-09-13 : FinecoBank (FBK.MI) et Banca Mediolanum
    # (BMED.MI) reclassées après vérification directe de leurs comptes
    # réels (profil bancaire classique, pas des cas limites courtage/
    # distribution comme initialement supposé) — voir le commentaire sur
    # FINANCIAL_SECTOR_TICKERS.
    assert len(ftsemib_financial_tickers) == 10


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


def test_fetch_company_financials_exposes_weinstein_stage_from_weekly_volume_history(monkeypatch):
    """Historique construit pour donner une MM30 semaines nettement
    montante (Phase 2 Achat), avec Volume disponible dans la réponse
    yfinance — vérifie le branchement complet Volume -> hebdomadaire ->
    classify_weinstein_stage, pas seulement la fonction pure du Task 1."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])

    # 300 jours ouvrés (~43 semaines), prix croissant -> MM30s montante.
    history_index = pd.bdate_range("2025-01-01", periods=300)
    history_close = pd.Series([100.0 + i * 0.5 for i in range(300)], index=history_index)
    history_volume = pd.Series([1000.0] * 300, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close, "Volume": history_volume})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST.PA")

    assert ratios["stage"] == 2
    assert ratios["stage_label"] == "Achat"
    assert ratios["volume_confirme"] is False  # volume constant, pas de pic de cassure


def test_fetch_company_financials_degrades_gracefully_when_volume_column_absent(monkeypatch):
    """Les fixtures existantes de ce fichier ne renvoient qu'une colonne
    "Close" (pas de "Volume") — le code doit s'en accommoder sans
    exception, stage_label retombant sur "Neutre" au pire (jamais un
    crash), conformément aux Global Constraints du plan."""
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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})  # pas de colonne "Volume"

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST2.PA")  # ne doit pas lever

    assert ratios["volume_confirme"] is False
    assert ratios["stage_label"] in ("Achat", "Déclin", "Neutre")


def test_fetch_dividend_history_returns_dividends_property(monkeypatch):
    class _FakeTickerWithDividends:
        @property
        def dividends(self):
            return pd.Series([1.0, 1.1], index=pd.DatetimeIndex(["2025-06-15", "2026-06-15"]))

    result = indices_score.fetch_dividend_history(_FakeTickerWithDividends())
    assert len(result) == 2


def test_fetch_dividend_history_returns_empty_series_on_exception():
    class _FailingTicker:
        @property
        def dividends(self):
            raise RuntimeError("panne réseau")

    result = indices_score.fetch_dividend_history(_FailingTicker())
    assert len(result) == 0


class _FrozenDate(_date):
    """Sous-classe de datetime.date dont .today() renvoie une date figée —
    permet de monkeypatcher `indices_score.date` (la classe entière, pas
    une instance) pour un test déterministe, indépendant du jour réel
    d'exécution."""
    @classmethod
    def today(cls):
        return _date(2026, 9, 23)


def test_fetch_company_financials_exposes_dividend_streak_years(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([4.80] * 250, index=history_index)
    fake_dividends = pd.Series(
        [1.0, 1.0, 1.0],
        index=pd.DatetimeIndex(["2023-06-15", "2024-06-15", "2025-06-15"]),
    )

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        @property
        def dividends(self):
            return fake_dividends

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "date", _FrozenDate)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    assert ratios["dividend_streak_years"] == 3  # 2023, 2024, 2025 consécutifs, 2026 pas encore tombé


def test_fetch_company_financials_excludes_dropped_close_date_volume_from_weekly_aggregate(monkeypatch):
    """Reproduit précisément le risque d'alignement que le
    `daily_volumes.reindex(history.index)` (placé APRÈS `history.dropna()`)
    est censé neutraliser : une date dont le Close est NaN (donc purgée par
    dropna()) porte ici un volume extrême et distinctif (999999.0, toutes
    les autres dates valant 1000.0) — si le Volume de cette date n'est pas
    aligné sur l'index Close déjà nettoyé, il fuit dans l'agrégat
    hebdomadaire et fausse `volume_confirme`.

    Cas construit à la main (vérifié par calcul manuel hors test, voir
    commentaires) : l'avant-dernière date de l'historique (2026-02-23,
    un lundi) a un Close NaN et un Volume de 999999.0 ; la toute dernière
    date (2026-02-24, mardi) reste valide avec un Volume normal de
    1000.0 — les deux dates tombent dans la même semaine calendaire.

    - Alignement correct (ce que ce commit implémente) : la date NaN est
      absente de `history.index` après dropna(), donc son Volume est
      exclu de `daily_volumes` par le reindex -> la semaine courante ne
      contient que le volume normal (1000.0), bien en-dessous du seuil
      de confirmation (1.5x la moyenne des 30 semaines précédentes,
      ~5000 chacune) -> `volume_confirme` doit être False.
    - Bug qu'on veut détecter (reindex absent, déplacé avant le dropna(),
      ou remplacé par un dropna() indépendant sur daily_volumes) : le
      Volume de la date NaN resterait dans `daily_volumes` telle quelle
      (le Volume lui-même n'est jamais NaN) et fuiterait dans la semaine
      courante -> somme hebdomadaire ~1 000 999, très au-dessus du seuil
      -> `volume_confirme` basculerait à tort à True. C'est exactement
      cette bascule que ce test doit détecter si le reindex est cassé."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])

    history_index = pd.bdate_range("2025-01-01", periods=300)
    history_close = pd.Series([100.0 + i * 0.5 for i in range(300)], index=history_index)
    history_volume = pd.Series([1000.0] * 300, index=history_index)
    nan_date = history_index[-2]  # 2026-02-23, lundi — même semaine que la dernière date
    history_close.loc[nan_date] = float("nan")
    history_volume.loc[nan_date] = 999999.0  # volume extrême sur la date dont le Close est NaN

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close, "Volume": history_volume})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    # Si le volume de la date purgée avait fui dans l'agrégat hebdomadaire,
    # la semaine courante afficherait ~1 000 999 (>> le seuil de
    # confirmation) et volume_confirme serait True — la valeur correcte,
    # avec l'exclusion, est bien en-dessous du seuil.
    assert ratios["volume_confirme"] is False


def test_fetch_company_financials_detects_volume_spike_despite_incomplete_current_week(monkeypatch):
    """`indices.yml` tourne quotidiennement — la dernière semaine du
    resample("W") est donc presque toujours EN COURS (incomplète) au
    moment du run, pas seulement dans de rares cas limites. Avec
    l'ancien `.sum()`, une semaine en cours ne contenant qu'UN jour de
    bourse est comparée à une moyenne de 30 semaines PLEINES — même un
    vrai pic de volume (3x le volume quotidien normal) serait dilué et
    NE serait PAS détecté. `.mean()` compare des volumes quotidiens
    moyens, donc reste sensible au pic quel que soit le nombre de jours
    déjà écoulés dans la semaine courante.

    Historique construit pour que la toute dernière date (2026-02-23)
    soit un LUNDI, donc seule dans le bin de la semaine en cours (aucun
    autre jour ouvré ne précède dans ce même bin ISO) — volume normal
    1000.0 partout sauf ce dernier jour, à 3000.0 (pic réel x3).

    Calcul vérifié à la main hors test :
    - Avec .sum() (ancien comportement) : semaine courante = 3000.0
      (un seul jour), baseline 30 semaines pleines = 5000.0/semaine ->
      seuil 7500.0 -> 3000.0 < 7500.0 -> volume_confirme resterait
      FAUSSEMENT False malgré le vrai pic x3.
    - Avec .mean() (comportement attendu ici) : semaine courante =
      3000.0 (moyenne d'un seul jour = lui-même), baseline = 1000.0/jour
      -> seuil 1500.0 -> 3000.0 > 1500.0 -> volume_confirme = True,
      détection correcte du pic."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])

    history_index = pd.bdate_range("2025-01-01", periods=299)  # se termine un lundi
    history_close = pd.Series([100.0 + i * 0.5 for i in range(299)], index=history_index)
    history_volume = pd.Series([1000.0] * 299, index=history_index)
    history_volume.loc[history_index[-1]] = 3000.0  # pic réel x3 sur l'unique jour de la semaine en cours

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close, "Volume": history_volume})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST5.PA")

    assert ratios["volume_confirme"] is True


def test_fetch_company_financials_falls_back_to_safe_weinstein_defaults_on_exception(monkeypatch):
    """Global Constraints du plan : aucune exception liée à Weinstein ne
    doit jamais remonter hors de fetch_company_financials — sinon
    l'entreprise entière disparaîtrait de docs/indices.json (bloc
    except par entreprise de main()), pas seulement ses champs
    Weinstein. Simule une panne du calcul (classify_weinstein_stage lève)
    et vérifie le repli gracieux vers les valeurs par défaut sûres."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.bdate_range("2025-01-01", periods=300)
    history_close = pd.Series([100.0 + i * 0.5 for i in range(300)], index=history_index)
    history_volume = pd.Series([1000.0] * 300, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close, "Volume": history_volume})

    def _boom(weekly_closes, weekly_volumes):
        raise RuntimeError("simulated classify_weinstein_stage failure")

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "classify_weinstein_stage", _boom)

    ratios = indices_score.fetch_company_financials("TEST6.PA")  # ne doit pas lever

    assert ratios["stage"] is None
    assert ratios["stage_label"] == "Neutre"
    assert ratios["volume_confirme"] is False


def test_fetch_company_financials_converts_lse_pence_prices_to_pounds(monkeypatch):
    """Bug racine trouvé le 2026-09-13 via le profil trust (score_valorisation_trust,
    qui compare current_pb à une valeur ABSOLUE et n'annule donc pas
    l'erreur d'échelle comme le fait chaque autre facteur en se comparant
    à sa propre moyenne 5 ans) : yfinance renvoie les prix de CERTAINS
    tickers londoniens (.L) en PENCE, alors que les comptes annuels sont en
    LIVRES — sans conversion, market_cap = price * shares_outstanding
    mélange les unités d'un facteur ~100 pour P/E, P/B, EV/EBITDA, DCF, et
    fair_value/entry_price/exit_price (qui mélangeaient carrément une
    méthode en pence avec deux méthodes en livres). Reproduit ici avec un
    cours constant de 1478.0 (pence, cas réel Scottish Mortgage) et
    info["currency"]="GBp" (le signal réel qui déclenche la conversion
    depuis le 2026-09-24, pas juste le suffixe .L — voir le test
    ..._leaves_lse_non_gbp_prices_unconverted juste après) : doit
    ressortir à 14.78 (livres) partout en aval."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([1478.0] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Energy", "currency": "GBp"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    # Un ticker .L hors TRUST_TICKERS/FINANCIAL_SECTOR_TICKERS (méthodologie
    # standard) : la conversion pence/livre se fait dans
    # fetch_company_financials avant tout branchement extract_ratios vs
    # extract_ratios_financier, donc le choix du chemin d'extraction n'a
    # pas d'importance ici — seul compte info["currency"].
    ratios = indices_score.fetch_company_financials("SHEL.L")

    assert ratios["current_price"] == pytest.approx(14.78)
    assert ratios["ma200"] == pytest.approx(14.78)


def test_fetch_company_financials_leaves_non_lse_prices_unconverted(monkeypatch):
    """Contre-exemple délibéré : la conversion ne doit s'appliquer que si
    info["currency"] == "GBp" — un ticker Euronext/Xetra/NASDAQ (pas .L,
    et sans ce champ dans son info) à un prix de 1478.0 (déjà dans la
    bonne unité, en euros/dollars) ne doit pas être divisé par 100."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([1478.0] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("MC.PA")

    assert ratios["current_price"] == pytest.approx(1478.0)


def test_fetch_company_financials_leaves_lse_non_gbp_prices_unconverted(monkeypatch):
    """Le bug réel corrigé le 2026-09-24 : contrairement à ce que supposait
    le premier correctif du 2026-09-13, TOUS les tickers .L ne cotent pas
    en pence — certains (IHG.L, CPG.L cotent en USD ; MTLN.L en EUR,
    confirmé via l'API Yahoo Finance : meta.currency) sont des doubles
    cotations dont la devise de référence n'est pas la livre. Diviser
    leur prix par 100 les faisait apparaître ~100x moins chers qu'en
    réalité (IHG.L : juste valeur affichée à 70x le cours). Un ticker .L
    avec info["currency"]="USD" ne doit PAS être divisé par 100."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([156.45] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Consumer Cyclical", "currency": "USD"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("IHG.L")

    assert ratios["current_price"] == pytest.approx(156.45)


def test_fetch_company_financials_converts_statements_when_financial_currency_differs(monkeypatch):
    """Inspiré du cas réel AIA (1299.HK) : comptes en USD
    (financialCurrency), cotation en HKD (currency) — sans conversion,
    market_cap (en HKD) combiné à des capitaux propres en USD faussait
    P/B d'un facteur ~7.8. Un taux de 2.0 (valeur simple pour le test)
    doit multiplier Stockholders Equity par 2.0.

    NOTE : utilise un ticker fictif hors FINANCIAL_SECTOR_TICKERS/
    TRUST_TICKERS ("TEST3.PA", pas "1299.HK") pour router vers
    extract_ratios (profil standard) plutôt qu'extract_ratios_financial
    — AIA elle-même est dans FINANCIAL_SECTOR_TICKERS, mais son profil
    financier a des exigences de fixture différentes (ex: ligne "Total
    Assets" absente de _make_fixture_statements()) sans rapport avec ce
    qu'on teste ici ; le mécanisme de conversion devise est identique
    pour les deux profils (câblé en amont, dans fetch_company_financials,
    avant le branchement extract_ratios vs extract_ratios_financial).
    "equity" est utilisé pour l'assertion (pas "net_income", qui n'est
    exposé dans AUCUN des deux dicts de retour) — "equity" l'est dans
    les deux, donc le choix reste valable quel que soit le profil.

    Couvre aussi quarterly_financials (revue finale 2026-09-24, constat
    I1) : sans conversion, le texte injecté dans le prompt d'analyse IA
    (build_financial_narrative_context) mélangerait un CA annuel converti
    avec un CA trimestriel resté dans financial_currency. La ligne "Total
    Revenue" est ajoutée à la fixture trimestrielle (absente jusqu'ici,
    qui ne portait qu'un comptage d'actions) pour pouvoir l'observer ;
    quarterly_yoy_growth_ca ne convient pas pour cette assertion (c'est
    un ratio trimestre/trimestre qui annule le taux de change des deux
    côtés), donc on lit directement le DataFrame `quarterly` capturé par
    la fake ticker après l'appel -- fetch_company_financials le convertit
    en place (`.loc[...] = ...`), donc `quarterly` reflète l'état
    post-conversion."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df(
        {"Diluted Average Shares": [100.0], "Total Revenue": [50.0]},
        [pd.Timestamp("2025-09-30")],
    )
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)
    equity_before_conversion = balance_sheet.loc["Stockholders Equity"].copy()
    tax_rate_before_conversion = financials.loc["Tax Rate For Calcs"].copy()
    quarterly_revenue_before_conversion = quarterly.loc["Total Revenue"].copy()
    quarterly_shares_before_conversion = quarterly.loc["Diluted Average Shares"].copy()

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
                "sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Industrials",
                "currency": "HKD", "financialCurrency": "USD",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", lambda f, t: 2.0)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    # equity (le plus récent exercice) doit refléter la conversion x2.0
    # appliquée aux DataFrames de comptes.
    assert ratios["equity"] == pytest.approx(equity_before_conversion.iloc[0] * 2.0)
    # tax_rate est un RATIO (0.25 = 25%), pas un montant monétaire -- ne
    # doit surtout PAS être multiplié par le taux de change (sinon
    # 0.25*2.0=0.5, un taux d'imposition de 50% inventé de toutes pièces).
    assert ratios["tax_rate"] == pytest.approx(tax_rate_before_conversion.iloc[0])
    # quarterly_financials doit être converti par le même taux que les
    # comptes annuels (constat I1), sinon le texte du prompt IA mélange
    # des devises.
    assert quarterly.loc["Total Revenue"].iloc[0] == pytest.approx(
        quarterly_revenue_before_conversion.iloc[0] * 2.0
    )
    # "Diluted Average Shares" (comptage d'actions, pas un montant) ne
    # doit surtout PAS être multiplié par le taux de change (constat M1).
    assert quarterly.loc["Diluted Average Shares"].iloc[0] == pytest.approx(
        quarterly_shares_before_conversion.iloc[0]
    )


def test_fetch_company_financials_degrades_shares_outstanding_when_fx_rate_unavailable(monkeypatch):
    """financialCurrency diffère de currency, mais fetch_fx_rate ne peut
    pas obtenir de taux (devise non gérée ou panne réseau) : repli sur
    shares_outstanding=0.0 (mécanisme du constat C5, déjà en place) —
    les ratios prix/comptes tombent proprement à "indisponible" sans
    inventer un taux. Ticker fictif hors FINANCIAL_SECTOR_TICKERS/
    TRUST_TICKERS (même raison que le test précédent — évite un profil
    dont la fixture standard ne couvre pas toutes les lignes requises)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)

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
                "sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Industrials",
                "currency": "HKD", "financialCurrency": "USD",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", lambda f, t: None)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    assert ratios["shares_outstanding"] == 0.0


def test_fetch_company_financials_no_conversion_when_financial_currency_absent(monkeypatch):
    """financialCurrency absent (None) : aucune conversion tentée,
    comportement actuel inchangé — ne doit pas dégrader shares_outstanding
    ni appeler fetch_fx_rate (cas majoritaire, pas de régression)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Technology", "currency": "USD"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    def _should_not_be_called(*a, **k):
        raise AssertionError("financialCurrency absent : fetch_fx_rate ne doit pas être appelé")

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", _should_not_be_called)

    ratios = indices_score.fetch_company_financials("MSFT")

    assert ratios["shares_outstanding"] == 1000.0


def test_fetch_company_financials_drops_trailing_nan_rows_from_current_price(monkeypatch):
    """Incident reel du 2026-09-19 : 131 entreprises europeennes d'un
    coup avec current_price=NaN (yfinance renvoyant une derniere ligne
    de cloture NaN pour ce fetch precis) — un JSON invalide au sens
    strict (json.dump ecrit NaN tel quel, JSON.parse() cote navigateur
    le refuse), qui avait casse le chargement d'indices.json pour TOUT
    le site. current_price doit retomber sur la derniere cloture
    REELLEMENT valide plutot que sur NaN."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    # Les 2 dernieres lignes sont NaN (cas reel constate) : le dernier
    # cours valide, 3 jours avant la fin de la serie, est 88.5.
    closes = [42.0] * 247 + [88.5, float("nan"), float("nan")]
    history_close = pd.Series(closes, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("MC.PA")

    assert ratios["current_price"] == pytest.approx(88.5)
    assert not math.isnan(ratios["current_price"])
    assert ratios["ma200"] is not None and not math.isnan(ratios["ma200"])


def test_fetch_company_financials_passes_current_price_to_extract_ratios(monkeypatch):
    """audit I6 : fetch_company_financials doit transmettre son propre
    current_price (cours du jour) à extract_ratios -- sinon le paramètre
    optionnel ajouté pour ce correctif ne sert qu'aux tests unitaires
    directs de extract_ratios, jamais en production."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([42.0] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    captured = {}
    original = indices_score.extract_ratios

    def _spy(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price=None):
        captured["current_price"] = current_price
        return original(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding, current_price)

    monkeypatch.setattr(indices_score, "extract_ratios", _spy)

    ratios = indices_score.fetch_company_financials("MC.PA")

    assert captured["current_price"] == pytest.approx(42.0)
    assert captured["current_price"] == ratios["current_price"]


def test_fetch_company_financials_uses_price_history_override_for_mtpa(monkeypatch):
    """Constaté en production les 2026-09-18 et 2026-09-19 : yfinance
    renvoie "possibly delisted; no price data found" pour MT.PA
    (ArcelorMittal, cotation Paris) alors que financials/info/quarterly
    restent résolubles dessus — d'où un score quand même calculé mais
    current_price=None. MT.AS (cotation Amsterdam, même émetteur) renvoie
    un historique valide, vérifié en direct via yfinance avant ce test.
    PRICE_HISTORY_TICKER_OVERRIDE doit rediriger UNIQUEMENT l'appel
    history() vers MT.AS, sans changer le ticker utilisé pour
    financials/balance_sheet/cashflow/quarterly/info (qui restent MT.PA,
    la clé d'identité canonique utilisée partout ailleurs)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    mt_as_history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    mt_as_history_close = pd.Series([26.4] * 250, index=mt_as_history_index)

    constructed_tickers = []

    class _FakeTicker:
        def __init__(self, ticker):
            constructed_tickers.append(ticker)
            self._ticker = ticker

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        def history(self, period=None):
            if self._ticker == "MT.PA":
                return pd.DataFrame({"Close": pd.Series(dtype=float)})
            if self._ticker == "MT.AS":
                return pd.DataFrame({"Close": mt_as_history_close})
            raise AssertionError(f"unexpected ticker passed to yf.Ticker: {self._ticker}")

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("MT.PA")

    assert ratios["current_price"] == pytest.approx(26.4)
    assert "MT.AS" in constructed_tickers
    # Le ticker d'identité (financials/balance_sheet/cashflow/quarterly/info)
    # reste MT.PA — _fetch_statement_with_retry et le premier yf.Ticker(t)
    # de fetch_company_financials sont tous deux construits sur ce ticker.
    assert constructed_tickers[0] == "MT.PA"


def test_fetch_company_financials_does_not_override_other_tickers(monkeypatch):
    """Contre-exemple : un ticker absent de PRICE_HISTORY_TICKER_OVERRIDE
    ne doit déclencher aucun yf.Ticker(...) supplémentaire — un seul
    objet Ticker construit, réutilisé pour history() comme avant ce
    correctif."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)

    constructed_tickers = []

    class _FakeTicker:
        def __init__(self, ticker):
            constructed_tickers.append(ticker)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Technology"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("MC.PA")

    assert ratios["current_price"] == pytest.approx(100.0)
    # Aucun ticker autre que MC.PA construit — pas de yf.Ticker("MT.AS")
    # ni d'aucun autre symbole substitué pour un ticker hors override.
    assert set(constructed_tickers) == {"MC.PA"}


def test_fetch_company_financials_exposes_the_full_price_history_for_persistence(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=3, freq="D")
    history_close = pd.Series([100.0, 101.0, 102.0], index=history_index)

    class _FakeTicker:
        def __init__(self, ticker):
            self._ticker = ticker

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    result = indices_score.fetch_company_financials("MC.PA")

    assert "_price_history_daily" in result
    entries = result["_price_history_daily"]
    assert len(entries) == 3
    assert all(e["ticker"] == "MC.PA" for e in entries)
    assert all(set(e.keys()) == {"date", "ticker", "price"} for e in entries)
    assert entries == sorted(entries, key=lambda e: e["date"])
    assert entries[-1]["price"] == pytest.approx(102.0)


def test_fetch_company_financials_exposes_the_full_dividend_history_for_persistence(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=3, freq="D")
    history_close = pd.Series([100.0, 101.0, 102.0], index=history_index)
    fake_dividends = pd.Series(
        [3.40, 3.55],
        index=pd.DatetimeIndex(["2025-06-15", "2026-06-15"]),
    )

    class _FakeTicker:
        def __init__(self, ticker):
            self._ticker = ticker

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        @property
        def dividends(self):
            return fake_dividends

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    result = indices_score.fetch_company_financials("MC.PA")

    assert "_dividend_history" in result
    entries = result["_dividend_history"]
    assert len(entries) == 2
    assert all(e["ticker"] == "MC.PA" for e in entries)
    assert all(set(e.keys()) == {"date", "ticker", "amount"} for e in entries)
    assert entries == sorted(entries, key=lambda e: e["date"])
    assert entries[0] == {"date": "2025-06-15", "ticker": "MC.PA", "amount": 3.40}
    assert entries[1] == {"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}


def test_load_signal_tracking_returns_empty_list_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_signal_tracking() == []


def test_load_signal_tracking_returns_empty_list_on_corrupted_json(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    assert indices_score.load_signal_tracking() == []


def test_load_signal_tracking_returns_positions_list(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text(json.dumps({"positions": [{"id": "BN.PA-2026-09-08"}]}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    assert indices_score.load_signal_tracking() == [{"id": "BN.PA-2026-09-08"}]


def test_save_signal_tracking_writes_positions_wrapped_in_object(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    indices_score.save_signal_tracking([{"id": "BN.PA-2026-09-08"}])
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"positions": [{"id": "BN.PA-2026-09-08"}]}


def test_save_signal_tracking_creates_parent_directory(monkeypatch, tmp_path):
    """docs/ peut ne pas exister sur une éventuelle exécution locale
    from scratch — même garde-fou que le payload principal
    (os.makedirs(..., exist_ok=True) dans main())."""
    path = tmp_path / "nested" / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    indices_score.save_signal_tracking([])
    assert path.exists()


def test_fetch_index_prices_returns_latest_close_per_index(monkeypatch):
    class FakeHistory:
        def __getitem__(self, key):
            assert key == "Close"
            import pandas as pd
            return pd.Series([7800.0, 7850.0])

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            assert period == "5d"
            return FakeHistory()

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_prices()
    assert result == {
        "CAC40": 7850.0, "DAX": 7850.0, "NASDAQ": 7850.0, "DOW": 7850.0,
        "FTSE": 7850.0, "SMI": 7850.0, "IBEX35": 7850.0, "FTSEMIB": 7850.0,
        "NIKKEI225": 7850.0, "HANGSENG": 7850.0,
    }


def test_fetch_index_prices_degrades_to_none_per_index_on_failure(monkeypatch):
    """Une panne sur un seul indice ne doit pas empêcher de récupérer
    l'autre, ni lever d'exception."""
    class FailingTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol == "^FCHI":
                raise RuntimeError("panne réseau")
            import pandas as pd
            return {"Close": pd.Series([19230.0])}

    monkeypatch.setattr(indices_score.yf, "Ticker", FailingTicker)
    result = indices_score.fetch_index_prices()
    assert result["CAC40"] is None
    assert result["DAX"] == 19230.0


def test_fetch_index_prices_returns_all_none_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(indices_score, "yf", None)
    assert indices_score.fetch_index_prices() == {
        "CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None, "FTSE": None,
        "SMI": None, "IBEX35": None, "FTSEMIB": None, "NIKKEI225": None, "HANGSENG": None,
    }


def test_fetch_index_price_history_returns_entries_for_each_tracked_index(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            assert period == "6y"
            return {"Close": pd.Series([7800.0, 7850.0], index=pd.to_datetime(["2026-09-20", "2026-09-21"]))}

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_price_history()
    tickers = {e["ticker"] for e in result}
    assert tickers == set(indices_score.INDEX_YFINANCE_TICKERS.values())
    fchi_entries = [e for e in result if e["ticker"] == "^FCHI"]
    assert len(fchi_entries) == 2


def test_fetch_index_price_history_skips_an_index_whose_fetch_fails(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol == "^FCHI":
                raise RuntimeError("panne réseau")
            return {"Close": pd.Series([19230.0], index=pd.to_datetime(["2026-09-21"]))}

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_price_history()
    tickers = {e["ticker"] for e in result}
    assert "^FCHI" not in tickers
    assert "^GDAXI" in tickers


def test_fetch_index_price_history_returns_empty_list_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(indices_score, "yf", None)
    assert indices_score.fetch_index_price_history() == []


def test_open_new_signal_positions_creates_position_for_newly_triggered_company():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0, "DAX": 19230.0}, today="2026-09-08",
    )
    assert len(positions) == 1
    p = positions[0]
    assert p["id"] == "BN.PA-2026-09-08"
    assert p["ticker"] == "BN.PA"
    assert p["name"] == "Danone"
    assert p["index"] == "CAC40"
    assert p["status"] == "open"
    assert p["entry_date"] == "2026-09-08"
    assert p["entry_price"] == 100.0
    assert p["target_exit_price"] == 130.0
    assert p["index_price_at_entry"] == 7850.0
    assert p["close_date"] is None
    assert p["close_reason"] is None
    assert p["shadow_close_date"] == "2027-03-08"
    assert p["shadow_resolved"] is False
    assert p["shadow_price"] is None


def test_open_new_signal_positions_stores_methodology_version_from_github_sha(monkeypatch):
    """audit I9, volet 1 : une position doit garder trace du commit actif
    à son ouverture (GITHUB_SHA, variable standard GitHub Actions), pour
    distinguer a posteriori les positions ouvertes avant/après un
    correctif de méthodologie donné."""
    monkeypatch.setenv("GITHUB_SHA", "abc123def456")
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert positions[0]["methodology_version"] == "abc123def456"


def test_open_new_signal_positions_methodology_version_none_outside_ci(monkeypatch):
    """En dehors de GitHub Actions (tests locaux), GITHUB_SHA n'existe
    pas -- dégradation attendue vers None, pas une erreur."""
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert positions[0]["methodology_version"] is None


def test_open_new_signal_positions_skips_ticker_with_already_open_position():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    existing = [{"ticker": "BN.PA", "status": "open"}]
    positions = indices_score._open_new_signal_positions(
        existing, [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert len(positions) == 1  # pas de doublon, la position existante reste seule
    assert positions[0] is existing[0]


def test_open_new_signal_positions_allows_new_position_after_previous_closed():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    existing = [{"ticker": "BN.PA", "status": "closed"}]
    positions = indices_score._open_new_signal_positions(
        existing, [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert len(positions) == 2
    assert positions[1]["status"] == "open"


def test_open_new_signal_positions_skips_company_with_missing_price_data():
    """Donnée incomplète (ex: current_price/exit_price manquants) : ne
    doit jamais lever, la société est simplement ignorée pour aujourd'hui."""
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": None, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert positions == []


def test_open_new_signal_positions_uses_none_index_price_when_index_fetch_failed():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": None, "DAX": None}, today="2026-09-08",
    )
    assert positions[0]["index_price_at_entry"] is None


def _fake_open_position(**overrides):
    position = {
        "id": "BN.PA-2026-06-08", "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "status": "open", "entry_date": "2026-06-08", "entry_price": 100.0,
        "target_exit_price": 130.0, "index_price_at_entry": 7500.0,
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "index_price_at_close": None, "index_return_pct": None,
        "shadow_close_date": "2026-12-08", "shadow_resolved": False,
        "shadow_price": None, "shadow_return_pct": None,
    }
    position.update(overrides)
    return position


def test_close_eligible_positions_closes_on_stop_loss():
    position = _fake_open_position(entry_price=100.0)
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # -21%, sous le seuil -20%
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    p = result[0]
    assert p["status"] == "closed"
    assert p["close_reason"] == "stop_loss"
    assert p["close_price"] == 79.0
    assert p["return_pct"] == pytest.approx(-21.0)
    assert p["index_price_at_close"] == 7600.0
    assert p["index_return_pct"] == pytest.approx((7600.0 - 7500.0) / 7500.0 * 100)


def test_close_eligible_positions_closes_on_target_reached():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 131.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_reason"] == "objectif_atteint"
    assert result[0]["return_pct"] == pytest.approx(31.0)


def test_close_eligible_positions_closes_on_delai_max_and_resolves_shadow_immediately():
    position = _fake_open_position(
        entry_price=100.0, target_exit_price=130.0, shadow_close_date="2026-09-08",
    )
    companies_by_ticker = {"BN.PA": {"current_price": 110.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    p = result[0]
    assert p["close_reason"] == "delai_max"
    assert p["return_pct"] == pytest.approx(10.0)
    # Clôture par délai max == date fantôme atteinte le même jour : résolu tout de suite.
    assert p["shadow_resolved"] is True
    assert p["shadow_price"] == 110.0
    assert p["shadow_return_pct"] == pytest.approx(10.0)


def test_close_eligible_positions_stop_loss_takes_priority_over_target():
    """Cas limite improbable mais à couvrir explicitement : si les deux
    conditions sont vraies le même jour (n'arrive normalement jamais vu
    les seuils -20%/objectif > entrée), stop-loss est vérifié en premier
    dans cet ordre de priorité de la spec."""
    position = _fake_open_position(entry_price=100.0, target_exit_price=70.0)  # objectif sous l'entrée
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # <= objectif ET <= stop-loss
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_reason"] == "stop_loss"


def test_close_eligible_positions_leaves_open_when_no_condition_met():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 105.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["status"] == "open"


def test_close_eligible_positions_leaves_open_when_ticker_not_in_companies():
    """Panne de fetch transitoire (ticker toujours dans le roster, raté
    ce run-là seulement) : sans roster_tickers fourni (comportement
    d'avant l'audit I9, volet 2), pas de cours disponible aujourd'hui,
    position laissée intacte plutôt que clôturée sur une donnée périmée
    ou une exception."""
    position = _fake_open_position()
    result = indices_score._close_eligible_positions(
        [position], {}, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["status"] == "open"


def test_close_eligible_positions_leaves_open_when_ticker_missing_but_still_in_roster():
    """audit I9, volet 2 : ticker absent de companies_by_ticker (panne de
    fetch transitoire) mais toujours présent dans roster_tickers -- reste
    "open", pas clôturé à tort."""
    position = _fake_open_position()
    result = indices_score._close_eligible_positions(
        [position], {}, {"CAC40": 7600.0}, today="2026-09-08",
        roster_tickers={"BN.PA", "MC.PA"},
    )
    assert result[0]["status"] == "open"


def test_close_eligible_positions_closes_when_ticker_removed_from_roster():
    """audit I9, volet 2 : ticker sorti DÉFINITIVEMENT du roster (révision
    d'indice, ex. Hang Seng 2026-09) -- ne reviendra jamais dans
    companies_by_ticker, doit être clôturé plutôt que rester "open" pour
    toujours et polluer silencieusement les statistiques de performance.
    close_price/return_pct restent None : pas de donnée finale mesurable,
    pas de chiffre inventé."""
    position = _fake_open_position()
    result = indices_score._close_eligible_positions(
        [position], {}, {"CAC40": 7600.0}, today="2026-09-08",
        roster_tickers={"MC.PA"},  # "BN.PA" n'y figure plus
    )
    assert result[0]["status"] == "closed"
    assert result[0]["close_reason"] == "ticker_retire_indice"
    assert result[0]["close_date"] == "2026-09-08"
    assert result[0]["close_price"] is None
    assert result[0]["return_pct"] is None
    assert result[0]["shadow_resolved"] is True


def test_close_eligible_positions_ignores_already_closed_positions():
    position = _fake_open_position(status="closed", close_price=140.0)
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # aurait déclenché stop-loss si "open"
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_price"] == 140.0  # inchangé


def test_close_eligible_positions_leaves_index_return_none_when_index_fetch_failed():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 131.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": None}, today="2026-09-08",
    )
    assert result[0]["index_price_at_close"] is None
    assert result[0]["index_return_pct"] is None


def test_resolve_pending_shadow_benchmarks_resolves_when_date_reached():
    position = _fake_open_position(
        status="closed", entry_price=100.0, shadow_close_date="2026-09-08", shadow_resolved=False,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 115.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    p = result[0]
    assert p["shadow_resolved"] is True
    assert p["shadow_price"] == 115.0
    assert p["shadow_return_pct"] == pytest.approx(15.0)


def test_resolve_pending_shadow_benchmarks_resolves_for_still_open_position():
    """Une position encore "open" (pas encore clôturée par le repère de
    sortie/stop-loss) mais dont la date fantôme est déjà atteinte doit
    aussi être résolue — les deux cycles de vie sont indépendants."""
    position = _fake_open_position(
        status="open", entry_price=100.0, shadow_close_date="2026-09-08", shadow_resolved=False,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 90.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is True
    assert result[0]["shadow_return_pct"] == pytest.approx(-10.0)


def test_resolve_pending_shadow_benchmarks_leaves_unresolved_before_date():
    position = _fake_open_position(shadow_close_date="2026-12-08", shadow_resolved=False)
    companies_by_ticker = {"BN.PA": {"current_price": 115.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is False
    assert result[0]["shadow_price"] is None


def test_resolve_pending_shadow_benchmarks_skips_already_resolved():
    position = _fake_open_position(
        shadow_close_date="2026-09-08", shadow_resolved=True, shadow_price=999.0,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 42.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_price"] == 999.0  # inchangé


def test_resolve_pending_shadow_benchmarks_leaves_pending_when_no_price_available():
    """Ticker sorti de l'indice ou sans cours ce jour : retenté le jour
    suivant, jamais d'exception."""
    position = _fake_open_position(shadow_close_date="2026-09-08", shadow_resolved=False)
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], {}, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is False


def test_update_signal_tracking_opens_closes_and_saves(monkeypatch, tmp_path):
    """Test bout en bout : preuve que update_signal_tracking cable bien
    les 3 sous-fonctions et écrit le fichier — pas seulement qu'elles
    existent en isolation."""
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": 7600.0, "DAX": 19000.0})

    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    result = indices_score.update_signal_tracking([company], [company])

    assert len(result) == 1
    assert result[0]["ticker"] == "BN.PA"
    assert result[0]["status"] == "open"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["positions"][0]["ticker"] == "BN.PA"


def test_update_signal_tracking_degrades_gracefully_on_failure(monkeypatch, tmp_path):
    """Une panne (ex: fichier illisible, fetch_index_prices qui lève)
    ne doit jamais faire échouer main() — renvoie [] plutôt que de
    propager l'exception."""
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(tmp_path / "signal_tracking.json"))
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = indices_score.update_signal_tracking([], [])
    assert result == []


def test_update_signal_tracking_reuses_newly_triggered_entree_from_alerts(monkeypatch, tmp_path):
    """Ne doit PAS re-détecter lui-même les signaux "entree" nouveaux —
    doit utiliser tel quel ce que _attach_alerts_and_update_history a
    déjà calculé, transmis en paramètre."""
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": 7600.0, "DAX": 19000.0})

    all_companies = [
        {"ticker": "BN.PA", "name": "Danone", "index": "CAC40", "current_price": 100.0, "exit_price": 130.0},
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "current_price": 500.0, "exit_price": 600.0},
    ]
    # Seul BN.PA est dans newly_triggered_entree -> seul BN.PA doit avoir une position.
    result = indices_score.update_signal_tracking(all_companies, [all_companies[0]])
    tickers_with_position = {p["ticker"] for p in result}
    assert tickers_with_position == {"BN.PA"}


def test_main_calls_update_signal_tracking(monkeypatch, tmp_path):
    """Preuve que main() appelle réellement update_signal_tracking —
    si l'appel était supprimé de main(), ce test doit échouer."""
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    called_with = {}

    def _fake_update_signal_tracking(companies, newly_triggered_entree):
        called_with["companies"] = companies
        called_with["newly_triggered_entree"] = newly_triggered_entree
        return []

    monkeypatch.setattr(indices_score, "update_signal_tracking", _fake_update_signal_tracking)
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])

    indices_score.main()

    assert "companies" in called_with
    assert len(called_with["companies"]) == len(indices_score.COMPANIES)


def test_main_writes_indices_json_before_email_and_signal_tracking(monkeypatch, tmp_path):
    """audit I10 : docs/indices.json doit être écrit AVANT l'envoi de
    l'email et l'ouverture des positions de suivi -- sinon un échec entre
    les deux laisserait le fichier qui fait foi pour le dédoublonnage
    (load_previous_alert_kinds/load_previous_alerted_news_links) périmé,
    et la même alerte redéclencherait un email en double au run suivant."""
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(
        indices_score, "fetch_index_prices",
        lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None},
    )
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])

    file_existed_at = {}

    def _fake_send_daily_digest_email(newly_triggered_entree, newly_triggered_major_news):
        file_existed_at["email"] = output_path.exists()
        return False

    def _fake_update_signal_tracking(companies, newly_triggered_entree):
        file_existed_at["signal_tracking"] = output_path.exists()
        return []

    monkeypatch.setattr(indices_score, "send_daily_digest_email", _fake_send_daily_digest_email)
    monkeypatch.setattr(indices_score, "update_signal_tracking", _fake_update_signal_tracking)

    indices_score.main()

    assert file_existed_at["email"] is True
    assert file_existed_at["signal_tracking"] is True


def test_main_recalibrates_scores_before_alerts_and_signal_tracking(monkeypatch, tmp_path):
    """Preuve que main() appelle recalibrate_scores_by_profile AVANT
    _attach_alerts_and_update_history/update_signal_tracking — si l'appel
    était supprimé ou mal placé, ce test doit échouer."""
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})

    # Scores bruts variés (pas tous identiques) pour que le percentile ait
    # un sens à vérifier -- indexé par position d'appel, car
    # build_company_entry est appelé une fois par ticker de COMPANIES (plus
    # de 20, donc le pool "standard" dépasse largement le seuil de
    # recalibration).
    call_counter = {"n": 0}

    def _fake_build_company_entry(ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0):
        call_counter["n"] += 1
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "score": float(call_counter["n"]), "interpretation": "peu importe",
            "current_price": 50.0, "entry_price": 50.0,
            "is_financial": False, "is_trust": False,
        }

    monkeypatch.setattr(indices_score, "build_company_entry", _fake_build_company_entry)
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    called_with = {}

    def _fake_update_signal_tracking(companies, newly_triggered_entree):
        # Snapshot des scores AU MOMENT DE L'APPEL (copie de floats, pas une
        # référence aux dicts) -- companies est le même objet liste tout au
        # long de main() et recalibrate_scores_by_profile mute les dicts en
        # place, donc si on lisait companies après le retour de main(), les
        # mutations seraient visibles quel que soit l'ordre réel des appels.
        # Seul un instantané pris ici, pendant l'appel, prouve l'ordre.
        called_with["scores_at_call_time"] = [c["score"] for c in companies]
        called_with["n_at_call_time"] = len(companies)
        return []

    monkeypatch.setattr(indices_score, "update_signal_tracking", _fake_update_signal_tracking)
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])

    indices_score.main()

    scores = called_with["scores_at_call_time"]
    n = called_with["n_at_call_time"]
    assert n >= indices_score.SCORE_RECALIBRATION_MIN_POOL_SIZE
    # Les scores bruts posés par le mock étaient 1.0, 2.0, ..., n (jamais
    # négatifs) -- si recalibrate_scores_by_profile n'avait pas encore tourné
    # au moment où update_signal_tracking est appelé (parce que supprimé ou
    # déplacé après cet appel), le snapshot ci-dessus montrerait encore les
    # scores bruts non recalibrés (tous positifs). Après recalibration (rang
    # percentile remis sur -100/+100), les sociétés du bas du classement
    # doivent avoir un score négatif au moment de l'appel.
    assert min(scores) < 0
    assert max(scores) > 0
    # Le score n'est plus la valeur brute posée par le mock (1.0..n).
    assert scores != [float(i + 1) for i in range(n)]


def test_main_persists_price_history_from_companies_and_indices(monkeypatch, tmp_path):
    """Preuve que main() combine l'historique de prix des entreprises
    (_price_history_daily, propage par build_company_entry) et celui des
    indices benchmark (fetch_index_price_history), puis les transmet a
    update_price_history — sans ca, docs/price_history.json ne serait
    jamais alimente en production."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [
                {"date": "2026-09-21", "ticker": ticker, "price": 50.0},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    captured = {}
    def _fake_update_price_history(entries, **kwargs):
        captured["entries"] = entries
        return entries
    monkeypatch.setattr(indices_score, "update_price_history", _fake_update_price_history)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [
        {"date": "2026-09-21", "ticker": "^FCHI", "price": 7850.2}])

    indices_score.main()

    tickers = {e["ticker"] for e in captured["entries"]}
    assert "^FCHI" in tickers
    assert "MC.PA" in tickers


def test_main_never_writes_the_internal_price_history_key_to_indices_json(monkeypatch, tmp_path):
    """La cle interne _price_history_daily (voir build_company_entry) ne
    doit jamais atteindre docs/indices.json publie — elle est retiree
    (.pop) dans main() avant construction du payload public, redirigee
    exclusivement vers update_price_history (docs/price_history.json)."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [
                {"date": "2026-09-21", "ticker": ticker, "price": 50.0},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])

    indices_score.main()

    with open(indices_score.OUTPUT_JSON_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    for company in payload["companies"]:
        assert "_price_history_daily" not in company


def test_main_persists_dividend_history_from_companies(monkeypatch, tmp_path):
    """Preuve que main() recupere _dividend_history (propage par
    build_company_entry) et le transmet a update_dividend_history — sans
    ca, docs/dividend_history.json ne serait jamais alimente en
    production."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [],
            "_dividend_history": [
                {"date": "2026-06-15", "ticker": ticker, "amount": 1.5},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    captured = {}
    def _fake_update_dividend_history(entries, **kwargs):
        captured["entries"] = entries
        return entries
    monkeypatch.setattr(indices_score, "update_dividend_history", _fake_update_dividend_history)

    indices_score.main()

    assert captured["entries"] == [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 1.5}]


def test_main_never_writes_the_internal_dividend_history_key_to_indices_json(monkeypatch, tmp_path):
    """La cle interne _dividend_history (voir build_company_entry) ne doit
    jamais atteindre docs/indices.json publie — elle est retiree (.pop)
    dans main() avant construction du payload public, redirigee
    exclusivement vers update_dividend_history (docs/dividend_history.json)."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [],
            "_dividend_history": [
                {"date": "2026-06-15", "ticker": ticker, "amount": 1.5},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    with open(indices_score.OUTPUT_JSON_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    for company in payload["companies"]:
        assert "_dividend_history" not in company
