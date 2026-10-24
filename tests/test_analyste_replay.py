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
            ("TEST.PA", "2026-04-01"): 100.0, ("TEST.PA", "2026-05-01"): 110.0,
            ("^FCHI", "2026-04-01"): 8000.0, ("^FCHI", "2026-05-01"): 8080.0,
        }
        return valeurs[(ticker_ou_indice, date)]

    resultat = analyste_replay.resout_pari(
        signal, pari, index_yfinance_ticker="^FCHI", prix_source=prix_source_factice)

    assert resultat["return_pct"] == pytest.approx(10.0)
    assert resultat["index_return_pct"] == pytest.approx(1.0)
    assert resultat["methode"] == "backtest_retroactif"


def test_ecrit_resultats_writes_valid_json(tmp_path):
    chemin = tmp_path / "resultats.json"
    paris = [{"ticker": "TEST.PA", "methode": "backtest_retroactif", "return_pct": 10.0}]

    analyste_replay.ecrit_resultats(paris, str(chemin))

    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    assert contenu["paris"][0]["ticker"] == "TEST.PA"


def test_main_wires_sampling_replay_and_resolution_together(monkeypatch):
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
