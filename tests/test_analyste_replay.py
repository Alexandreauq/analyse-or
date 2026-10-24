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
    def client_toujours_limite(prompt, cle_api, modele):
        raise analyste_replay.ErreurLimiteDebit("quota depasse")

    with pytest.raises(analyste_replay.ErreurLimiteDebit):
        analyste_replay.appelle_gemini(
            "prompt test", cle_api="fausse-cle", client=client_toujours_limite,
            sleep_fn=lambda s: None, max_tentatives=3)
