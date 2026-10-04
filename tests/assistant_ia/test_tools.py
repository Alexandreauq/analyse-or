# tests/assistant_ia/test_tools.py
import assistant_ia.tools as tools

INDICES_FIXTURE = {
    "companies": [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical",
         "score": 72.0, "interpretation": "Tres solide", "stage_label": "Achat",
         "factors": {"roce": 18.0, "dette_nette_ebitda": 1.2}, "alerts": [],
         "financial_analysis_html": "<p>analyse detaillee tres longue...</p>",
         "news": "Actualite recente tres longue sur LVMH..."},
        {"ticker": "KER.PA", "name": "Kering", "index": "CAC40", "sector": "Consumer Cyclical",
         "score": 45.0, "interpretation": "Correct", "stage_label": "Neutre",
         "factors": {"roce": 9.0, "dette_nette_ebitda": 3.1}, "alerts": []},
        {"ticker": "SAN.PA", "name": "Sanofi", "index": "CAC40", "sector": "Healthcare",
         "score": 30.0, "interpretation": "Moyen", "stage_label": "Neutre",
         "factors": {}, "alerts": []},
    ]
}


def _indices_source(**kwargs):
    return INDICES_FIXTURE


def test_fiche_entreprise_returns_the_matching_company():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "MC.PA"}, indices_source=_indices_source)

    assert resultat["ticker"] == "MC.PA"
    assert resultat["score"] == 72.0
    assert resultat["factors"]["roce"] == 18.0


def test_fiche_entreprise_strips_the_bloated_fields():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "MC.PA"}, indices_source=_indices_source)

    assert "financial_analysis_html" not in resultat
    assert "news" not in resultat


def test_fiche_entreprise_does_not_strip_fields_from_the_suggestions_error_shape():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "LVMHH"}, indices_source=_indices_source)

    assert "erreur" in resultat
    assert "suggestions" in resultat


def test_fiche_entreprise_suggests_close_matches_when_not_found():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "LVMHH"}, indices_source=_indices_source)

    assert "erreur" in resultat
    assert "MC.PA" in resultat["suggestions"]


def test_comparer_entreprises_returns_both_fiches():
    resultat = tools.dispatch_tool(
        "comparer_entreprises", {"ticker1": "MC.PA", "ticker2": "KER.PA"},
        indices_source=_indices_source)

    assert resultat["entreprise_1"]["ticker"] == "MC.PA"
    assert resultat["entreprise_2"]["ticker"] == "KER.PA"


def test_classement_filters_by_index_and_sorts_by_score_desc():
    resultat = tools.dispatch_tool(
        "classement", {"indice": "CAC40", "n": 2}, indices_source=_indices_source)

    tickers = [c["ticker"] for c in resultat["classement"]]
    assert tickers == ["MC.PA", "KER.PA"]  # 72 puis 45, SAN.PA (30) exclu par n=2


def test_classement_filters_by_sector():
    resultat = tools.dispatch_tool(
        "classement", {"secteur": "Healthcare", "n": 10}, indices_source=_indices_source)

    assert [c["ticker"] for c in resultat["classement"]] == ["SAN.PA"]


def test_classement_strips_the_bloated_fields():
    resultat = tools.dispatch_tool(
        "classement", {"indice": "CAC40", "n": 10}, indices_source=_indices_source)

    for entry in resultat["classement"]:
        assert "financial_analysis_html" not in entry
        assert "news" not in entry


def test_classement_clamps_n_to_a_hard_maximum_of_25():
    many_companies = {
        "companies": [
            {"ticker": f"T{i}.PA", "name": f"Societe {i}", "index": "CAC40",
             "sector": "Technology", "score": float(i)}
            for i in range(1000)
        ]
    }

    resultat = tools.dispatch_tool(
        "classement", {"n": 1000}, indices_source=lambda **kw: many_companies)

    assert len(resultat["classement"]) == 25


def test_statut_bot_dispatches_to_the_dashboard_source():
    appels = []

    def fake_dashboard(nom):
        appels.append(nom)
        return {"balance": 981.45, "balance_fetched_at": "2026-10-03T10:00:00Z"}

    resultat = tools.dispatch_tool("statut_bot", {"nom": "or"}, dashboard_source=fake_dashboard)

    assert appels == ["or"]
    assert resultat["balance"] == 981.45


def test_positions_bot_returns_only_the_positions_list():
    def fake_dashboard(nom):
        return {"balance": 20.0, "positions": [{"ticker": "MC.PA", "pnl_eur": -16.57}]}

    resultat = tools.dispatch_tool("positions_bot", {"nom": "actions"}, dashboard_source=fake_dashboard)

    assert resultat["positions"] == [{"ticker": "MC.PA", "pnl_eur": -16.57}]
    assert "balance" not in resultat


def test_resume_portefeuille_merges_manual_and_bot_positions():
    def fake_dashboard(nom):
        return {"positions": [{"ticker": f"BOT_{nom.upper()}"}]}

    resultat = tools.dispatch_tool(
        "resume_portefeuille",
        {"positions_manuelles": [{"ticker": "MANUELLE.PA", "quantity": 5}]},
        dashboard_source=fake_dashboard)

    tickers = {p["ticker"] for p in resultat["positions"]}
    assert tickers == {"MANUELLE.PA", "BOT_OR", "BOT_ACTIONS"}
    assert resultat["nombre_total"] == 3


def test_proposer_lien_echoes_the_suggestion():
    resultat = tools.dispatch_tool(
        "proposer_lien", {"cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH"})

    assert resultat == {
        "cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH",
    }


def test_proposer_lien_rejects_an_invalid_cible_type():
    resultat = tools.dispatch_tool(
        "proposer_lien",
        {"cible_type": "quelquechose_de_bizarre", "cible_valeur": "x", "libelle": "y"})

    assert "erreur" in resultat


def test_dispatch_tool_returns_an_error_dict_for_an_unknown_tool():
    resultat = tools.dispatch_tool("outil_inexistant", {})

    assert "erreur" in resultat


def test_tool_definitions_lists_all_nine_tools():
    noms = {t["name"] for t in tools.TOOL_DEFINITIONS}

    assert noms == {
        "fiche_entreprise", "comparer_entreprises", "classement",
        "statut_bot", "positions_bot", "resume_portefeuille", "proposer_lien",
        "actualites_entreprise", "actualites_marche",
    }


def _schema_for(nom):
    return next(t for t in tools.TOOL_DEFINITIONS if t["name"] == nom)["input_schema"]


def test_resume_portefeuille_schema_no_longer_lists_positions_manuelles():
    schema = _schema_for("resume_portefeuille")

    assert "positions_manuelles" not in schema["properties"]


def test_classement_schema_lists_the_exact_index_values_as_an_enum():
    schema = _schema_for("classement")

    assert schema["properties"]["indice"]["enum"] == [
        "CAC40", "DAX", "DOW", "FTSE", "FTSEMIB",
        "HANGSENG", "IBEX35", "NASDAQ", "NIKKEI225", "SMI",
    ]


def _indices_avec_safran():
    return {"companies": [{"ticker": "MC.PA", "name": "LVMH"}]}


def test_dispatch_actualites_entreprise_passes_the_company_name_to_both_sources():
    appels_gdelt = []

    def gdelt(requete, debut, ttl, cle):
        appels_gdelt.append((requete, cle))
        return {"articles": []}

    resultat = tools.dispatch_tool(
        "actualites_entreprise", {"ticker": "MC.PA"},
        indices_source=_indices_avec_safran, finnhub_source=lambda *a: [],
        gdelt_source=gdelt)

    assert appels_gdelt == [('"LVMH"', "entreprise:MC.PA")]
    assert resultat == {"ticker": "MC.PA", "articles": []}


def test_dispatch_actualites_entreprise_with_an_unknown_ticker_skips_gdelt():
    appels_gdelt = []

    def gdelt(*args):
        appels_gdelt.append(args)
        return {"articles": []}

    tools.dispatch_tool(
        "actualites_entreprise", {"ticker": "INCONNU.PA"},
        indices_source=_indices_avec_safran, finnhub_source=lambda *a: [],
        gdelt_source=gdelt)

    assert appels_gdelt == []


def test_dispatch_actualites_marche_needs_no_input():
    resultat = tools.dispatch_tool(
        "actualites_marche", {}, gdelt_source=lambda *a: {"articles": []})

    assert resultat == {"articles": []}


def test_dispatch_actualites_entreprise_returns_an_error_dict_when_all_sources_fail():
    erreur = {"erreur": "actualites indisponibles (RuntimeError)"}
    resultat = tools.dispatch_tool(
        "actualites_entreprise", {"ticker": "MC.PA"},
        indices_source=_indices_avec_safran, finnhub_source=lambda *a: erreur,
        gdelt_source=lambda *a: erreur)

    assert "erreur" in resultat


def test_actualites_tool_schemas_have_the_expected_inputs():
    par_nom = {t["name"]: t for t in tools.TOOL_DEFINITIONS}

    assert par_nom["actualites_entreprise"]["input_schema"]["required"] == ["ticker"]
    assert "properties" in par_nom["actualites_marche"]["input_schema"]
