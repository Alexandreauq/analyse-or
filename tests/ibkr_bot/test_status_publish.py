# tests/ibkr_bot/test_status_publish.py
import json

import ibkr_bot.status_publish as status_publish


class _Resultat:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- build_public_status ------------------------------------------------

def test_build_public_status_extracts_operational_fields_only():
    """Le detail des entrees/sorties/rejets/anomalies/erreurs du run
    lui-meme (ex. un vrai ordre passe) ne doit jamais fuiter dans le
    statut public, quel que soit le mode — seuls des compteurs et des
    booleens en ressortent pour ces champs-la (les positions OUVERTES,
    elles, sont un champ separe, testees plus bas)."""
    run = {
        "timestamp": "2026-09-19T14:45:00Z", "date": "2026-09-19", "mode": "reel",
        "statut": "termine",
        "git_pull": {"ok": True, "detail": "Already up to date."},
        "preflight": {"ok": True, "tentatives": 1, "detail": "authentifie"},
        "entrees": [{"ticker": "AAPL", "quantite": 10, "prix": 200.0}],
        "sorties": [{"ticker": "MC.PA", "montant": 5000.0}],
        "signaux_rejetes": [{"ticker": "BA.L", "raison": "solde_insuffisant"}],
        "anomalies": [{"ticker": "TTE.PA", "detail": "quantite inattendue"}],
        "erreurs": [{"etape": "journalisation", "detail": "echec disque"}],
    }
    status = status_publish.build_public_status(run)

    assert status == {
        "timestamp": "2026-09-19T14:45:00Z", "date": "2026-09-19", "mode": "reel",
        "statut": "termine", "git_pull_ok": True, "preflight_ok": True,
        "entrees_count": 1, "sorties_count": 1, "signaux_rejetes_count": 1,
        "anomalies_count": 1, "erreurs_count": 1,
    }
    dumped = json.dumps(status)
    assert "AAPL" not in dumped
    assert "MC.PA" not in dumped
    assert "200.0" not in dumped
    assert "5000.0" not in dumped


def test_build_public_status_handles_missing_optional_fields():
    """Un run minimal (ex. kill_switch, sorti avant que git_pull/preflight
    ne soient meme tentes) ne doit jamais lever."""
    run = {"timestamp": "t", "date": "2026-09-19", "mode": "dry_run", "statut": "kill_switch"}
    status = status_publish.build_public_status(run)
    assert status["git_pull_ok"] is None
    assert status["preflight_ok"] is None
    assert status["entrees_count"] == 0
    assert status["erreurs_count"] == 0


def test_build_public_status_includes_sanitized_positions_in_dry_run():
    """Decision explicite de l'utilisateur (2026-09-20) : les tickers des
    positions OUVERTES sont publies tant que le bot est en simulation —
    mais seulement un sous-ensemble (jamais conid, l'identifiant de
    contrat IBKR interne)."""
    run = {"timestamp": "t", "date": "2026-09-20", "mode": "dry_run", "statut": "termine"}
    positions = [{
        "id": "sig-1", "ticker": "AAPL", "name": "Apple Inc.", "index": "NASDAQ",
        "conid": 265598, "devise": "USD", "quantite": 5,
        "prix_execution_reference": 200.0, "paper_entry_price": 198.5,
        "date_entree": "2026-09-15", "target_exit_price": 250.0, "date_limite": "2027-03-15",
    }]
    status = status_publish.build_public_status(run, positions)

    assert status["positions"] == [{
        "ticker": "AAPL", "name": "Apple Inc.", "index": "NASDAQ",
        "quantite": 5, "prix_entree": 200.0, "date_entree": "2026-09-15",
    }]
    assert "conid" not in json.dumps(status)
    assert "265598" not in json.dumps(status)


def test_build_public_status_omits_positions_when_mode_is_reel():
    """La bascule vers argent reel doit automatiquement arreter de publier
    les tickers/positions, sans intervention manuelle — meme si une liste
    de positions est passee, elle est ignoree en mode reel."""
    run = {"timestamp": "t", "date": "2026-09-20", "mode": "reel", "statut": "termine"}
    positions = [{"ticker": "AAPL", "name": "Apple Inc.", "index": "NASDAQ", "quantite": 5}]
    status = status_publish.build_public_status(run, positions)

    assert "positions" not in status
    assert "AAPL" not in json.dumps(status)


def test_build_public_status_dry_run_with_no_open_positions_is_empty_list():
    run = {"timestamp": "t", "date": "2026-09-20", "mode": "dry_run", "statut": "termine"}
    status = status_publish.build_public_status(run, None)
    assert status["positions"] == []


# --- write_status_file ----------------------------------------------------

def test_write_status_file_writes_valid_json(tmp_path):
    path = tmp_path / "sub" / "ibkr_bot_status.json"
    status_publish.write_status_file({"statut": "termine"}, path=str(path))
    assert json.loads(path.read_text(encoding="utf-8")) == {"statut": "termine"}


# --- push_status_file -----------------------------------------------------

def test_push_status_file_commits_and_pushes_when_status_changed(tmp_path):
    appels = []

    def fake_run(cmd, **kwargs):
        appels.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)  # des changements en attente
        return _Resultat(returncode=0)

    path = tmp_path / "docs" / "ibkr_bot_status.json"
    resultat = status_publish.push_status_file(
        str(tmp_path), "fake-token-123", path=str(path), run_fn=fake_run,
    )

    assert resultat == {"ok": True, "detail": "publie"}
    commands = [c[:2] for c in appels]
    assert ["git", "pull"] in commands
    assert ["git", "add"] in commands
    assert ["git", "diff"] in commands
    assert ["git", "commit"] in commands
    push_cmd = next(c for c in appels if c[:2] == ["git", "push"])
    assert "fake-token-123" in push_cmd[2]
    assert push_cmd[2].startswith("https://x-access-token:fake-token-123@github.com/")
    assert push_cmd[3] == "HEAD:main"
    # Le remote "origin" habituel (credential-free, utilise par git pull
    # ailleurs dans daily.py) ne doit jamais etre touche par ce mecanisme.
    assert not any(c[:3] == ["git", "remote", "set-url"] for c in appels)


def test_push_status_file_skips_commit_when_nothing_changed(tmp_path):
    appels = []

    def fake_run(cmd, **kwargs):
        appels.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=0)  # rien en attente
        return _Resultat(returncode=0)

    resultat = status_publish.push_status_file(
        str(tmp_path), "fake-token", path=str(tmp_path / "docs" / "s.json"), run_fn=fake_run,
    )

    assert resultat == {"ok": True, "detail": "rien a publier (statut inchange)"}
    assert not any(c[:2] == ["git", "commit"] for c in appels)
    assert not any(c[:2] == ["git", "push"] for c in appels)


def test_push_status_file_redacts_token_from_commit_failure_detail(tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)
        if cmd[:2] == ["git", "commit"]:
            return _Resultat(returncode=1, stderr="fatal: identity unknown, token=secret-token-xyz")
        return _Resultat(returncode=0)

    resultat = status_publish.push_status_file(
        str(tmp_path), "secret-token-xyz", path=str(tmp_path / "docs" / "s.json"), run_fn=fake_run,
    )

    assert resultat["ok"] is False
    assert "secret-token-xyz" not in resultat["detail"]
    assert "***" in resultat["detail"]


def test_push_status_file_redacts_token_from_push_failure_detail(tmp_path):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)
        if cmd[:2] == ["git", "push"]:
            return _Resultat(
                returncode=1,
                stderr="fatal: unable to access 'https://x-access-token:secret-token-xyz@github.com/...'",
            )
        return _Resultat(returncode=0)

    resultat = status_publish.push_status_file(
        str(tmp_path), "secret-token-xyz", path=str(tmp_path / "docs" / "s.json"), run_fn=fake_run,
    )

    assert resultat["ok"] is False
    assert "secret-token-xyz" not in resultat["detail"]


def test_push_status_file_degrades_gracefully_on_exception(tmp_path):
    def boom(*args, **kwargs):
        raise OSError("git introuvable, token=secret-token-xyz")

    resultat = status_publish.push_status_file(
        str(tmp_path), "secret-token-xyz", path=str(tmp_path / "docs" / "s.json"), run_fn=boom,
    )

    assert resultat["ok"] is False
    assert "secret-token-xyz" not in resultat["detail"]
    assert "git introuvable" in resultat["detail"]


# --- publish_status (point d'entree) --------------------------------------

def test_publish_status_returns_early_without_token(tmp_path, monkeypatch):
    """Aucun GITHUB_PUSH_TOKEN dans l'environnement -> ne doit RIEN
    ecrire ni tenter le moindre appel git (voir la fuite decouverte plus
    tot avec update_nikkei_hangseng_price_history : mieux vaut un court-
    circuit avant toute E/S)."""
    monkeypatch.delenv("GITHUB_PUSH_TOKEN", raising=False)
    calls = []
    monkeypatch.setattr(status_publish, "write_status_file", lambda *a, **k: calls.append("write"))
    monkeypatch.setattr(status_publish, "push_status_file", lambda *a, **k: calls.append("push"))

    resultat = status_publish.publish_status(
        {"date": "2026-09-19", "statut": "termine"}, repo_dir=str(tmp_path),
    )

    assert resultat["ok"] is False
    assert "GITHUB_PUSH_TOKEN" in resultat["detail"]
    assert calls == []


def test_publish_status_writes_and_pushes_when_token_present(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "fake-token")
    appels = []

    def fake_run(cmd, **kwargs):
        appels.append(cmd)
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)
        return _Resultat(returncode=0)

    path = tmp_path / "docs" / "ibkr_bot_status.json"
    run = {"timestamp": "t", "date": "2026-09-19", "mode": "dry_run", "statut": "termine"}

    resultat = status_publish.publish_status(
        run, repo_dir=str(tmp_path), path=str(path), run_fn=fake_run,
        load_positions_fn=lambda: [],
    )

    assert resultat["ok"] is True
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["statut"] == "termine"
    assert any(c[:2] == ["git", "push"] for c in appels)


def test_publish_status_loads_and_includes_positions_in_dry_run(tmp_path, monkeypatch):
    """Preuve que publish_status cable bien load_positions_fn jusque dans
    le fichier ecrit — si l'appel etait supprime, ce test doit echouer."""
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "fake-token")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)
        return _Resultat(returncode=0)

    path = tmp_path / "docs" / "ibkr_bot_status.json"
    run = {"timestamp": "t", "date": "2026-09-20", "mode": "dry_run", "statut": "termine"}
    fake_positions = [{"ticker": "AAPL", "name": "Apple Inc.", "index": "NASDAQ",
                       "conid": 123, "quantite": 5, "prix_execution_reference": 200.0,
                       "date_entree": "2026-09-15"}]

    status_publish.publish_status(
        run, repo_dir=str(tmp_path), path=str(path), run_fn=fake_run,
        load_positions_fn=lambda: fake_positions,
    )

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["positions"] == [{
        "ticker": "AAPL", "name": "Apple Inc.", "index": "NASDAQ",
        "quantite": 5, "prix_entree": 200.0, "date_entree": "2026-09-15",
    }]


def test_publish_status_does_not_load_positions_when_mode_is_reel(tmp_path, monkeypatch):
    """En mode reel, load_positions_fn ne doit meme pas etre appelee —
    defense en profondeur en plus du filtrage dans build_public_status."""
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "fake-token")

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "diff"]:
            return _Resultat(returncode=1)
        return _Resultat(returncode=0)

    called = []

    def boom_if_called():
        called.append(True)
        return [{"ticker": "AAPL"}]

    run = {"timestamp": "t", "date": "2026-09-20", "mode": "reel", "statut": "termine"}
    status_publish.publish_status(
        run, repo_dir=str(tmp_path), path=str(tmp_path / "docs" / "s.json"), run_fn=fake_run,
        load_positions_fn=boom_if_called,
    )

    assert called == []


def test_publish_status_degrades_gracefully_when_write_fails(tmp_path, monkeypatch):
    """Un disque plein/repertoire inexistant lors de l'ecriture du fichier
    ne doit jamais faire remonter d'exception jusqu'a _terminer."""
    monkeypatch.setenv("GITHUB_PUSH_TOKEN", "fake-token")

    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(status_publish, "write_status_file", boom)

    resultat = status_publish.publish_status(
        {"date": "2026-09-19", "statut": "termine"}, repo_dir=str(tmp_path),
    )

    assert resultat["ok"] is False
    assert "no space left" in resultat["detail"]
