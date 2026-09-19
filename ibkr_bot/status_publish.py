# ibkr_bot/status_publish.py
# Publie un resume operationnel du dernier batch dans docs/ibkr_bot_status.json
# du depot (ecrit puis commite/pousse depuis le VPS) — le bot n'a
# deliberement pas d'API HTTP (decision de securite prise apres
# l'incident du token API du Bot Or, voir deploy/README-ibkr.md), donc
# c'est le seul canal par lequel le site statique peut savoir ce qui
# s'est passe. Champs strictement operationnels — jamais de ticker ni de
# montant : decision explicite de l'utilisateur (ce bot pourrait un jour
# passer en argent reel), voir build_public_status.
import json
import os
import subprocess

STATUS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "ibkr_bot_status.json"
)
GITHUB_REPO_SLUG = "Alexandreauq/analyse-or"
GIT_TIMEOUT_SECONDS = 30


def build_public_status(run: dict) -> dict:
    """Extrait uniquement des champs operationnels d'un run (voir
    _nouveau_run dans daily.py pour sa forme complete) — jamais de
    ticker ni de montant, seulement des compteurs et des booleens."""
    return {
        "timestamp": run.get("timestamp"),
        "date": run.get("date"),
        "mode": run.get("mode"),
        "statut": run.get("statut"),
        "git_pull_ok": (run.get("git_pull") or {}).get("ok"),
        "preflight_ok": (run.get("preflight") or {}).get("ok"),
        "entrees_count": len(run.get("entrees") or []),
        "sorties_count": len(run.get("sorties") or []),
        "signaux_rejetes_count": len(run.get("signaux_rejetes") or []),
        "anomalies_count": len(run.get("anomalies") or []),
        "erreurs_count": len(run.get("erreurs") or []),
    }


def write_status_file(status: dict, path: str = STATUS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2, allow_nan=False)


def _redact(text: str, token: str) -> str:
    """Le token peut apparaitre dans la sortie de git lui-meme (ex. un
    message d'erreur qui reecrit l'URL distante) — jamais laisser passer
    tel quel dans un detail qui pourrait finir journalise/maile."""
    return (text or "").replace(token, "***") if token else (text or "")


def push_status_file(
    repo_dir: str, token: str, path: str = STATUS_PATH, run_fn=subprocess.run,
) -> dict:
    """git add + commit + push du seul fichier de statut. Le token ne
    transite que comme argument de commande (jamais ecrit dans
    .git/config, jamais journalise en clair) — construit une URL
    authentifiee juste pour ce push, sans toucher au remote "origin"
    habituel (celui utilise par git pull, credential-free, reste
    inchange). Degrade toujours vers {"ok": False, "detail": ...} plutot
    que de lever : un echec de publication ne doit jamais faire echouer
    le batch de trading qui l'appelle."""
    push_url = f"https://x-access-token:{token}@github.com/{GITHUB_REPO_SLUG}.git"
    try:
        relative_path = os.path.relpath(path, repo_dir)
        # Rattrape les commits atterris pendant le batch (peut durer
        # 20+ min avec les tentatives de preflight) — meme mecanique que
        # indices.yml (git pull --rebase --autostash avant de pousser).
        run_fn(["git", "pull", "--rebase", "--autostash"], cwd=repo_dir,
               capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        run_fn(["git", "add", relative_path], cwd=repo_dir,
               capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        diff = run_fn(["git", "diff", "--cached", "--quiet"], cwd=repo_dir,
                      capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if diff.returncode == 0:
            return {"ok": True, "detail": "rien a publier (statut inchange)"}
        commit = run_fn(["git", "commit", "-m", "Statut Bot Actions IBKR"], cwd=repo_dir,
                        capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if commit.returncode != 0:
            return {"ok": False, "detail": _redact(commit.stderr, token)[:300]}
        push = run_fn(["git", "push", push_url, "HEAD:main"], cwd=repo_dir,
                      capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if push.returncode != 0:
            return {"ok": False, "detail": _redact(push.stderr, token)[:300]}
        return {"ok": True, "detail": "publie"}
    except Exception as e:
        return {"ok": False, "detail": _redact(str(e), token)[:300]}


def publish_status(run: dict, *, repo_dir: str, path: str = STATUS_PATH, run_fn=subprocess.run) -> dict:
    """Point d'entree unique, appele depuis _terminer (daily.py) apres
    CHAQUE batch, quel que soit son statut (kill_switch,
    hors_jour_de_bourse, gateway_indisponible, termine...). Ne leve
    jamais — un GITHUB_PUSH_TOKEN absent ou une panne reseau degradent
    vers {"ok": False, ...}, jamais une exception qui remonterait
    jusqu'au batch de trading."""
    token = os.environ.get("GITHUB_PUSH_TOKEN")
    if not token:
        return {"ok": False, "detail": "GITHUB_PUSH_TOKEN absent de l'environnement"}
    try:
        status = build_public_status(run)
        write_status_file(status, path)
        return push_status_file(repo_dir, token, path, run_fn=run_fn)
    except Exception as e:
        return {"ok": False, "detail": _redact(str(e), token)[:300]}
