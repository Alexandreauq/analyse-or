import json
import pytest

import analyste_replay


def _signal_exemple():
    return {
        "ticker": "TEST.PA", "name": "Entreprise Test", "index": "CAC40",
        "date_cible": "2026-04-01", "score_raw": 25.0, "score": 25.0,
        "stage_label": "Achat", "current_price": 100.0, "entry_price": 98.0,
        "exit_price": 150.0, "sector": "Technology",
        "actus": [{"date": "2026-03-28", "sentiment": 0.6, "importance": "notable",
                   "titre": "Entreprise Test annonce un contrat majeur", "resume": "...",
                   "url": "https://lemonde.fr/a"}],
    }


def test_construit_prompt_includes_anti_leakage_instruction_and_date_cible():
    prompt = analyste_replay.construit_prompt(_signal_exemple())
    assert "2026-04-01" in prompt
    assert "ne raisonne qu" in prompt.lower() or "n'utilise jamais" in prompt.lower()
    assert "Entreprise Test" in prompt


def test_construit_prompt_surfaces_rendement_and_volume_ratio_when_present():
    """FIX 8 (revue finale) : le prompt demande au modele de raisonner sur
    4 dimensions (actualite, technique, secteur, volume) mais ne fournissait
    aucune donnee pour la dimension "volume" ni pour le momentum court
    terme -- un vide que le modele serait tente de combler par sa
    connaissance generale (exactement ce que l'instruction anti-fuite
    essaie d'empecher). Doit apparaitre seulement quand les champs sont
    presents (voir le test suivant pour l'absence, backward-compat)."""
    signal = _signal_exemple()
    signal["rendement_5j_pct"] = 2.5
    signal["rendement_20j_pct"] = -1.2
    signal["volume_ratio_5j_vs_20j"] = 1.35

    prompt = analyste_replay.construit_prompt(signal)

    assert "+2.5%" in prompt
    assert "-1.2%" in prompt
    assert "1.35" in prompt


def test_construit_prompt_without_rendement_fields_still_works(monkeypatch):
    """FIX 8 : la fixture _signal_exemple() existante n'a pas ces champs --
    doit continuer a fonctionner sans erreur (.get(), jamais un acces par
    crochet direct), avec un repli explicite "n/d"."""
    prompt = analyste_replay.construit_prompt(_signal_exemple())

    assert "n/d" in prompt


def test_construit_prompt_never_mentions_a_date_after_date_cible():
    signal = _signal_exemple()
    signal["actus"].append({
        "date": "2026-05-01", "sentiment": -0.8, "importance": "majeure",
        "titre": "Entreprise Test en difficulte", "resume": "...", "url": "https://lemonde.fr/b"})
    prompt = analyste_replay.construit_prompt(signal)
    assert "2026-05-01" not in prompt


def test_appelle_gemini_retries_on_429(monkeypatch):
    appels = {"n": 0}

    def client_factice(prompt, cle_api, modele):
        appels["n"] += 1
        if appels["n"] < 3:
            raise analyste_replay.ErreurLimiteDebit("quota depasse")
        return {"appel": "neutre", "confiance": "moyenne", "raison": "..."}

    resultat = analyste_replay.appelle_gemini(
        "prompt test", cle_api="fausse-cle", client=client_factice,
        sleep_fn=lambda s: None)

    assert appels["n"] == 3
    assert resultat["appel"] == "neutre"


def test_appelle_gemini_reraises_last_error_after_exhausting_retries():
    appels = {"n": 0}

    def client_toujours_limite(prompt, cle_api, modele):
        appels["n"] += 1
        raise analyste_replay.ErreurLimiteDebit("quota depasse")

    with pytest.raises(analyste_replay.ErreurLimiteDebit):
        analyste_replay.appelle_gemini(
            "prompt test", cle_api="fausse-cle", client=client_toujours_limite,
            sleep_fn=lambda s: None, max_tentatives=3)

    assert appels["n"] == 3


def test_resout_pari_computes_return_and_alpha_vs_index():
    signal = _signal_exemple()
    pari = {"appel": "haussier", "confiance": "moyenne", "dimension_dominante": "technique", "raison": "..."}

    def prix_source_factice(ticker_ou_indice, date):
        valeurs = {
            ("TEST.PA", "2026-05-01"): 110.0,
            ("^FCHI", "2026-05-01"): 8080.0,
        }
        return valeurs[(ticker_ou_indice, date)]

    def prix_source_avant_factice(ticker_ou_indice, date):
        # FIX 4 (revue finale) : seule l'entree de l'INDICE passe par cette
        # fonction (convention "dernier close avant date_cible", alignee
        # sur signal["current_price"]) -- jamais l'entree de l'action
        # (prise directement sur signal["current_price"], deja calculee).
        valeurs = {("^FCHI", "2026-04-01"): 8000.0}
        return valeurs[(ticker_ou_indice, date)]

    resultat = analyste_replay.resout_pari(
        signal, pari, index_yfinance_ticker="^FCHI",
        prix_source=prix_source_factice, prix_source_avant=prix_source_avant_factice)

    assert resultat["return_pct"] == pytest.approx(10.0)
    assert resultat["index_return_pct"] == pytest.approx(1.0)
    assert resultat["methode"] == "backtest_retroactif"


def test_resout_pari_uses_prix_source_avant_only_for_index_entree(monkeypatch):
    """FIX 4 : prix_entree (action) vient de signal["current_price"] (deja
    "dernier close avant date_cible"), jamais d'un nouvel appel reseau --
    index_entree doit utiliser la MEME convention ("avant", pas "a ou
    apres") via prix_source_avant, pour que les deux entrees (action et
    indice) soient alignees sur le meme jour calendaire quand date_cible
    tombe un week-end."""
    signal = _signal_exemple()
    pari = {"appel": "neutre", "confiance": "faible", "dimension_dominante": "secteur", "raison": "..."}

    appels_avant = []

    def prix_source_factice(ticker_ou_indice, date):
        return {("TEST.PA", "2026-05-01"): 110.0, ("^FCHI", "2026-05-01"): 8080.0}[(ticker_ou_indice, date)]

    def prix_source_avant_factice(ticker_ou_indice, date):
        appels_avant.append((ticker_ou_indice, date))
        return 8000.0

    analyste_replay.resout_pari(
        signal, pari, index_yfinance_ticker="^FCHI",
        prix_source=prix_source_factice, prix_source_avant=prix_source_avant_factice)

    assert appels_avant == [("^FCHI", "2026-04-01")]


def _fake_ticker_with_close(prices_by_symbol):
    """Reproduit la forme minimale de yf.Ticker(...).history(...)["Close"]
    necessaire a _prix_a_date/_prix_avant_date : .iloc[0] et
    .dropna().iloc[-1] doivent fonctionner sur le resultat."""
    import pandas as pd

    class FakeHistory:
        def __init__(self, symbol):
            self._symbol = symbol

        def __getitem__(self, key):
            assert key == "Close"
            return pd.Series(prices_by_symbol[self._symbol])

    class FakeTicker:
        def __init__(self, symbol):
            self._symbol = symbol

        def history(self, **kwargs):
            return FakeHistory(self._symbol)

    return FakeTicker


def test_prix_a_date_converts_pence_to_pounds_for_lse_tickers(monkeypatch):
    """BUG REEL (backtest du 2026-10-26) : yfinance cote les actions LSE
    (suffixe .L) en PENCE (GBp), jamais en livres -- indices_score.py
    divise deja par 100 a la source (signal["current_price"] est donc en
    livres), mais _prix_a_date appelait yfinance brut sans la meme
    conversion. Resultat observe : des return_pct a +11000% sur des
    tickers .L (prix_entree en livres compare a prix_sortie en pence,
    soit un facteur ~100 parasite). Verifie empiriquement contre le vrai
    yfinance (STAN.L ~1780 pence un jour donne) que ce facteur 100 est
    bien le mecanisme reel, pas une supposition."""
    monkeypatch.setattr(
        "yfinance.Ticker", _fake_ticker_with_close({"STAN.L": [1780.0, 1790.0]}))

    assert analyste_replay._prix_a_date("STAN.L", "2026-04-29") == pytest.approx(17.8)


def test_prix_avant_date_converts_pence_to_pounds_for_lse_tickers(monkeypatch):
    monkeypatch.setattr(
        "yfinance.Ticker", _fake_ticker_with_close({"BARC.L": [390.0, 394.0]}))

    assert analyste_replay._prix_avant_date("BARC.L", "2026-03-28") == pytest.approx(3.94)


def test_prix_a_date_does_not_convert_non_lse_tickers(monkeypatch):
    monkeypatch.setattr(
        "yfinance.Ticker", _fake_ticker_with_close({"TEST.PA": [100.0, 110.0]}))

    assert analyste_replay._prix_a_date("TEST.PA", "2026-04-01") == pytest.approx(100.0)


def test_prix_a_date_does_not_convert_index_tickers(monkeypatch):
    """^FTSE/^FCHI etc. sont des cotations d'indice (points), jamais en
    pence, meme pour un indice britannique -- confirme empiriquement
    contre le vrai yfinance (^FTSE ~10300 points, pas ~103 livres)."""
    monkeypatch.setattr(
        "yfinance.Ticker", _fake_ticker_with_close({"^FTSE": [10332.0, 10213.0]}))

    assert analyste_replay._prix_a_date("^FTSE", "2026-04-29") == pytest.approx(10332.0)


def test_ecrit_resultats_writes_valid_json(tmp_path):
    chemin = tmp_path / "resultats.json"
    paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": 10.0}]

    analyste_replay.ecrit_resultats(paris, str(chemin))

    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    assert contenu["paris"][0]["ticker"] == "TEST.PA"


def test_ecrit_resultats_raises_on_nan_instead_of_writing_invalid_json(tmp_path):
    """FIX 6 (revue finale) : json.dump ecrit NaN tel quel par defaut (JSON
    invalide au sens strict) -- un seul NaN avait deja casse le chargement
    d'un fichier entier pour tous les utilisateurs par le passe (incident
    documente). allow_nan=False fait echouer tot et bruyamment plutot que
    d'ecrire un fichier silencieusement corrompu."""
    chemin = tmp_path / "resultats.json"
    paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": float("nan")}]

    with pytest.raises(ValueError):
        analyste_replay.ecrit_resultats(paris, str(chemin))


def test_ecrit_resultats_is_atomic_original_file_survives_a_failed_rewrite(tmp_path):
    """Correctif anti-corruption (revue 2) : ecrit_resultats ecrivait jusqu'ici
    directement dans le fichier final avec "w" -- un json.dump qui leve en
    cours de route (NaN, allow_nan=False) laissait alors un fichier
    TRONQUE et invalide, pas l'ancien contenu valide de la derniere
    ecriture reussie. L'ecriture doit passer par un fichier temporaire
    puis os.replace(), pour que l'ancien fichier reste intact si la
    nouvelle ecriture echoue."""
    chemin = tmp_path / "resultats.json"
    premier_paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": 10.0}]
    analyste_replay.ecrit_resultats(premier_paris, str(chemin))
    contenu_initial = chemin.read_text(encoding="utf-8")

    deuxieme_paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": float("nan")}]
    with pytest.raises(ValueError):
        analyste_replay.ecrit_resultats(deuxieme_paris, str(chemin))

    assert chemin.read_text(encoding="utf-8") == contenu_initial
    contenu = json.loads(contenu_initial)
    assert contenu["paris"][0]["return_pct"] == 10.0


def test_main_continues_past_a_failing_write_and_still_writes_succeeding_signals(monkeypatch):
    """ecrit_resultats elle-meme peut lever (ex. NaN dans CE pari precis,
    allow_nan=False) -- cet echec d'ECRITURE pour un seul signal ne doit
    pas non plus faire perdre les signaux resolus avant/apres lui dans la
    meme execution (meme garde-fou que pour une exception Gemini/
    resolution du FIX 5, mais a l'etage de l'ecriture, FIX introduit par
    la revue 2). Le signal en echec ne doit pas non plus rester coince
    dans paris_resolus pour toujours (sinon TOUTES les ecritures
    suivantes echoueraient aussi a cause de lui)."""
    monkeypatch.setenv("GEMINI_API_KEY", "fausse-cle")
    signaux = [
        {**_signal_exemple(), "ticker": "A.PA"},
        {**_signal_exemple(), "ticker": "B.PA"},
        {**_signal_exemple(), "ticker": "C.PA"},
    ]
    monkeypatch.setattr(
        analyste_replay.analyste_backtest, "genere_signaux_historiques",
        lambda *a, **k: signaux)
    monkeypatch.setattr(
        analyste_replay, "appelle_gemini",
        lambda prompt, **k: {"appel": "haussier", "confiance": "moyenne",
                             "dimension_dominante": "technique", "raison": "..."})
    monkeypatch.setattr(
        analyste_replay, "resout_pari",
        lambda signal, pari, **k: {**signal, **pari, "return_pct": 5.0})

    appels_ecriture = []

    def ecrit_resultats_qui_echoue_pour_b(paris, chemin):
        if paris and paris[-1]["ticker"] == "B.PA":
            raise ValueError("Out of range float values are not JSON compliant")
        appels_ecriture.append([p["ticker"] for p in paris])

    monkeypatch.setattr(analyste_replay, "ecrit_resultats", ecrit_resultats_qui_echoue_pour_b)

    analyste_replay.main(chemin_sortie="sortie-test.json")

    assert appels_ecriture[-1] == ["A.PA", "C.PA"]


def test_main_wires_sampling_replay_and_resolution_together(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fausse-cle")
    signal_exemple = _signal_exemple()
    monkeypatch.setattr(
        analyste_replay.analyste_backtest, "genere_signaux_historiques",
        lambda *a, **k: [signal_exemple])
    monkeypatch.setattr(
        analyste_replay, "appelle_gemini",
        lambda prompt, **k: {"appel": "haussier", "confiance": "moyenne",
                             "dimension_dominante": "technique", "raison": "..."})
    monkeypatch.setattr(
        analyste_replay, "resout_pari",
        lambda signal, pari, **k: {**signal, **pari, "return_pct": 5.0})
    chemins_ecrits = []
    monkeypatch.setattr(
        analyste_replay, "ecrit_resultats",
        lambda paris, chemin: chemins_ecrits.append((paris, chemin)))

    analyste_replay.main(chemin_sortie="sortie-test.json")

    assert len(chemins_ecrits) == 1
    assert chemins_ecrits[0][0][0]["return_pct"] == 5.0


def test_main_raises_if_gemini_api_key_missing(monkeypatch):
    """FIX 5 (revue finale) : la cle manquante doit etre detectee TOUT DE
    SUITE au debut de main(), pas decouverte au milieu de la boucle apres
    que l'echantillonnage (couteux en appels yfinance/FRED) a deja
    tourne -- genere_signaux_historiques ne doit jamais etre appelee."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def genere_signaux_historiques_qui_ne_doit_jamais_tourner(*a, **k):
        raise AssertionError("l'echantillonnage ne doit jamais demarrer sans cle Gemini")

    monkeypatch.setattr(
        analyste_replay.analyste_backtest, "genere_signaux_historiques",
        genere_signaux_historiques_qui_ne_doit_jamais_tourner)

    with pytest.raises(RuntimeError):
        analyste_replay.main(chemin_sortie="sortie-test.json")


def test_main_continues_past_a_resolution_exception_and_still_writes_succeeded_signals(monkeypatch):
    """FIX 5 : une exception sur UN signal (Gemini/resolution) ne doit
    jamais faire perdre les autres signaux deja resolus avec succes dans
    la meme execution -- et ecrit_resultats doit etre appele apres CHAQUE
    signal reussi, pas seulement a la toute fin, pour qu'un crash en
    cours de route laisse quand meme un fichier avec tout ce qui a
    reussi jusque-la."""
    monkeypatch.setenv("GEMINI_API_KEY", "fausse-cle")
    signaux = [
        {**_signal_exemple(), "ticker": "A.PA"},
        {**_signal_exemple(), "ticker": "B.PA"},
        {**_signal_exemple(), "ticker": "C.PA"},
    ]
    monkeypatch.setattr(
        analyste_replay.analyste_backtest, "genere_signaux_historiques",
        lambda *a, **k: signaux)
    monkeypatch.setattr(
        analyste_replay, "appelle_gemini",
        lambda prompt, **k: {"appel": "haussier", "confiance": "moyenne",
                             "dimension_dominante": "technique", "raison": "..."})

    def resout_pari_qui_echoue_sur_b(signal, pari, **k):
        if signal["ticker"] == "B.PA":
            raise RuntimeError("erreur reseau transitoire")
        return {**signal, **pari, "return_pct": 5.0}

    monkeypatch.setattr(analyste_replay, "resout_pari", resout_pari_qui_echoue_sur_b)

    appels_ecriture = []
    monkeypatch.setattr(
        analyste_replay, "ecrit_resultats",
        lambda paris, chemin: appels_ecriture.append(list(paris)))

    analyste_replay.main(chemin_sortie="sortie-test.json")

    assert len(appels_ecriture) >= 2  # ecrit apres A ET apres C, pas qu'une fois a la fin
    dernier = appels_ecriture[-1]
    tickers_ecrits = [p["ticker"] for p in dernier]
    assert tickers_ecrits == ["A.PA", "C.PA"]
