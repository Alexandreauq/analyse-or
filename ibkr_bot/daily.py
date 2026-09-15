# ibkr_bot/daily.py
# ORCHESTRATEUR DU BATCH QUOTIDIEN — point d'entree : python -m ibkr_bot.daily
#
# CE MODULE EST LE SEUL DE TOUT LE DEPOT D'OU UN ORDRE ACTIONS REEL PEUT
# PARTIR (via ibkr_bot.gateway). Il n'ajoute AUCUNE regle de trading : le
# plafond de 10, le classement par score, le garde-fou de solde, les
# regles de sortie et la reconciliation sont deja implementes et testes
# dans portfolio.py (Plan A) — ici on les appelle, on execute, on
# journalise. Toute regle metier ajoutee ici serait au mauvais endroit.
#
# DEFENSE EN PROFONDEUR (spec 4.3, point 2) : l'etat (kill_switch,
# dry_run) est RECHARGE DEPUIS LE DISQUE juste avant chaque envoi d'ordre,
# meme si run_batch l'a deja verifie au demarrage — exactement ce que fait
# gold_bot/loop.py:execute_steps. Un batch peut durer 20 minutes
# (preflight) : l'etat lu au debut est potentiellement perime.
import os
import subprocess
import time
from datetime import datetime, timezone

import ibkr_bot.contracts as contracts
import ibkr_bot.gateway as gateway
import ibkr_bot.journal as journal
import ibkr_bot.notify as notify
import ibkr_bot.portfolio as portfolio
import ibkr_bot.signals as signals
import ibkr_bot.sizing as sizing
import ibkr_bot.state as state

# Preflight : 3 tentatives espacees de 10 minutes (spec 5.5). Le batch
# demarre a 14:45 UTC et reste donc au plus tard a 15:05 UTC, encore dans
# la plage d'ouverture commune aux 8 places (14:30-15:30 UTC, spec 4.5).
PREFLIGHT_ATTEMPTS = 3
PREFLIGHT_DELAY_SECONDS = 600

# Racine du clone local : le bot lit docs/indices.json et
# docs/signal_tracking.json de CE clone, apres un git pull (spec 4.5).
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GIT_PULL_TIMEOUT_SECONDS = 120

DEFAULT_PATHS = {
    "state": state.STATE_PATH,
    "positions": portfolio.POSITIONS_PATH,
    "journal": journal.REAL_TRADING_LOG_PATH,
    "conid_cache": contracts.CONID_CACHE_PATH,
    "indices": signals.INDICES_PATH,
    "tracking": signals.SIGNAL_TRACKING_PATH,
    "account_snapshot": journal.LATEST_ACCOUNT_PATH,
}


def resolve_paths(paths: dict | None = None) -> dict:
    """Tous les fichiers touches par le batch, en un seul endroit — les
    tests les redirigent en bloc vers un tmp_path. Les fonctions de Plan A
    figent leur chemin par defaut a l'import (argument par defaut), donc
    monkeypatcher leurs constantes de module serait sans effet : les
    chemins doivent etre passes explicitement."""
    resolus = dict(DEFAULT_PATHS)
    resolus.update(paths or {})
    return resolus


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def pull_repo(repo_dir: str = REPO_DIR, run_fn=subprocess.run) -> dict:
    """git pull sur le clone local (spec 4.5). Non bloquant : un echec est
    journalise, et c'est le garde de fraicheur juste apres qui decidera
    s'il y a lieu d'agir — un pull rate sur des donnees deja a jour n'est
    pas un probleme, un pull rate sur des donnees d'hier sera attrape par
    `donnees_perimees`.

    Note d'exploitation : le code deja importe en memoire n'est pas
    remplace par ce pull ; un changement de code ne prend effet qu'au
    batch suivant. C'est exactement le modele de deploy.sh (git pull +
    redemarrage) et c'est voulu — on ne veut pas qu'un batch change de
    version en plein vol."""
    try:
        resultat = run_fn(["git", "pull", "--ff-only"], cwd=repo_dir,
                          capture_output=True, text=True,
                          timeout=GIT_PULL_TIMEOUT_SECONDS)
    except Exception as e:
        return {"ok": False, "detail": f"git pull impossible : {e}"}
    if getattr(resultat, "returncode", 1) != 0:
        return {"ok": False,
                "detail": (getattr(resultat, "stderr", "") or "").strip()[:500]}
    return {"ok": True, "detail": (getattr(resultat, "stdout", "") or "").strip()[:200]}


def preflight(base_url: str, *, gw=gateway, sleep_fn=time.sleep,
              attempts: int = PREFLIGHT_ATTEMPTS,
              delay_s: float = PREFLIGHT_DELAY_SECONDS) -> dict:
    """Le Gateway est-il utilisable ? (spec 5.5)

    "Utilisable" ne veut pas seulement dire "authentifie" : le CPAPI exige
    que /iserver/accounts (brokerage_accounts) ET /portfolio/accounts
    (portfolio_accounts) aient ete appeles au moins une fois dans la
    session avant que les routes portefeuille et ordres ne renvoient des
    donnees reelles. Sans cet amorcage, /portfolio/... renvoie
    SILENCIEUSEMENT DU VIDE — le sens dangereux : la reconciliation
    conclurait que le bot ne detient rien et justifierait un rachat. Un
    echec d'amorcage compte donc comme une tentative ratee, au meme titre
    qu'une session expiree."""
    detail = ""
    for tentative in range(1, attempts + 1):
        try:
            if gw.is_authenticated(base_url):
                gw.brokerage_accounts(base_url)
                gw.portfolio_accounts(base_url)
                return {"ok": True, "tentatives": tentative,
                        "detail": "authentifie, session amorcee"}
            detail = "session non authentifiee"
        except Exception as e:
            detail = f"amorcage de session impossible : {e}"
        if tentative < attempts:
            sleep_fn(delay_s)
    return {"ok": False, "tentatives": attempts, "detail": detail}


def _nouveau_run(today: str, mode: str) -> dict:
    return {
        "timestamp": journal.now_iso(),
        "date": today,
        "mode": mode,
        "statut": "termine",
        "git_pull": {"ok": None, "detail": "non tente"},
        "preflight": {"ok": None, "tentatives": 0, "detail": "non tente"},
        "reconciliation": {},
        "sorties": [],
        "entrees": [],
        "signaux_rejetes": [],
        "anomalies": [],
        "erreurs": [],
    }


def _terminer(run: dict, chemins: dict, *, alerte_gateway: bool = False) -> dict:
    """Sortie unique du batch : journal puis email. Le journal AVANT
    l'email pour qu'une panne SMTP ne fasse jamais perdre la trace d'un
    batch qui a reellement passe des ordres."""
    journal.append_run(run, chemins["journal"])
    if alerte_gateway:
        # Le corps de l'alerte dit deja qu'aucun ordre n'a ete passe : un
        # resume vide en plus ne ferait que noyer l'alerte.
        notify.send_gateway_alert(run["preflight"]["tentatives"], run["date"])
    else:
        notify.send_daily_summary(run, run["date"])
    return run


def run_batch(today: str | None = None, *, gw=gateway, sleep_fn=time.sleep,
              base_url: str | None = None, account_id: str | None = None,
              repo_dir: str = REPO_DIR, paths: dict | None = None) -> dict:
    """Un batch quotidien complet. Renvoie la ligne de journal produite.

    ORDRE DES GARDES, qui est lui-meme une decision :
      1. kill_switch  -> aucun appel reseau du tout ;
      2. git pull     -> non bloquant ;
      3. fraicheur    -> AVANT le preflight : si les donnees ne sont pas
         du jour, le batch ne fera rien de toute facon (spec 4.5), donc
         bruler 20 minutes de reauthentification serait inutile — et
         l'email d'alerte Gateway donnerait un mauvais diagnostic ;
      4. preflight    -> 3 tentatives / 10 min, abandon + alerte.
    """
    chemins = resolve_paths(paths)
    today = today or _today()
    base_url = base_url or os.environ.get("IBKR_GATEWAY_URL",
                                          gateway.DEFAULT_GATEWAY_URL)
    account_id = account_id or os.environ.get("IBKR_ACCOUNT_ID", "")

    etat = state.load_state(chemins["state"])
    run = _nouveau_run(today, "dry_run" if etat["dry_run"] else "reel")

    # 1. Interrupteur d'urgence : on ne touche meme pas au reseau.
    if etat["kill_switch"]:
        run["statut"] = "kill_switch"
        return _terminer(run, chemins)

    # 2. Donnees du jour : git pull sur le clone local (spec 4.5).
    run["git_pull"] = pull_repo(repo_dir)

    # 3. Garde de fraicheur, non negociable (spec 4.5).
    indices = signals.load_indices(chemins["indices"])
    if not signals.indices_are_fresh(indices, today):
        run["statut"] = "donnees_perimees"
        run["signaux_rejetes"].append({
            "ticker": None, "raison": "donnees_perimees",
            "rang": None, "score": None,
        })
        return _terminer(run, chemins)

    # 4. Preflight du Gateway (spec 5.5).
    run["preflight"] = preflight(base_url, gw=gw, sleep_fn=sleep_fn)
    if not run["preflight"]["ok"]:
        run["statut"] = "gateway_indisponible"
        return _terminer(run, chemins, alerte_gateway=True)

    # Les taches 4 et 5 inserent ici : reconciliation, sorties, entrees.
    return _terminer(run, chemins)


def main() -> None:
    run_batch()


if __name__ == "__main__":
    main()
