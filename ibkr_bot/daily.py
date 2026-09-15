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
# dry_run) DOIT ETRE RECHARGE DEPUIS LE DISQUE juste avant chaque envoi
# d'ordre, meme si run_batch l'a deja verifie au demarrage — exactement ce
# que fait gold_bot/loop.py:execute_steps. Un batch peut durer 20 minutes
# (preflight) : l'etat lu au debut est potentiellement perime.
# CE RECHARGEMENT EST IMPLEMENTE DANS _place_order (Tache 4), PAS ICI ET
# PAS AILLEURS : c'est la toute premiere instruction de cette fonction
# (state.load_state(state_path), rappele a chaque appel). Ne jamais
# dupliquer cette relecture ailleurs dans le module, et ne jamais la
# remplacer par un booleen passe en parametre depuis run_batch — la seule
# valeur qui compte est celle du disque AU MOMENT de l'envoi.
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


# Borne de la boucle de confirmation : le CPAPI peut repondre a un ordre
# par une question (marche ferme, ordre au marche hors seance, taille
# inhabituelle...), et la reponse a une question peut elle-meme etre une
# question. Sans borne, une chaine sans fin bloquerait le batch dans la
# fenetre de marche.
MAX_CONFIRMATIONS = 5


def _nombre(valeur):
    """float(valeur) ou None — le CPAPI renvoie avgPrice et commission
    tantot en nombre, tantot en CHAINE ("415.20" dans les fixtures de
    tests/ibkr_bot/test_gateway.py), et parfois avec un separateur de
    milliers ("1,234.50") sur certains champs numeriques — la virgule est
    retiree avant conversion plutot que de perdre silencieusement un prix
    d'execution reel au profit du repli estime."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, str):
        valeur = valeur.replace(",", "")
    try:
        nombre = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if nombre != nombre else nombre  # NaN -> None


def _prix_valide(valeur) -> bool:
    """True si `valeur` est un prix strictement positif utilisable — ni
    None, ni bool, ni chaine, ni zero/negatif/NaN."""
    return (isinstance(valeur, (int, float)) and not isinstance(valeur, bool)
            and valeur > 0)


def _premier_order_id(reponse) -> str | None:
    for entree in reponse or []:
        if isinstance(entree, dict) and entree.get("order_id"):
            return str(entree["order_id"])
    return None


def _texte_question(question: dict) -> str:
    """Texte lisible d'une question de confirmation IBKR (`message` est
    documente comme une liste de lignes), pour l'audit du `detail` du
    journal (finding Important #3 de la revue tache 4)."""
    message = question.get("message")
    if isinstance(message, list):
        return " ".join(str(ligne) for ligne in message)
    return "" if message is None else str(message)


def _premiere_question(reponse) -> dict | None:
    """Le dict de la premiere question de confirmation dans `reponse`, ou
    None si `reponse` est deja resolue. Verifie D'ABORD si `reponse`
    contient une confirmation d'ordre (`order_id`) n'importe ou dans le
    lot, AVANT de chercher une question (finding Minor #1 de la revue
    tache 4) : un lot mixte [question, confirmation] doit etre traite
    comme resolu, jamais relance en confirmation supplementaire — une
    question porte `id` + `message`, une confirmation d'ordre porte
    `order_id`."""
    if any(isinstance(entree, dict) and entree.get("order_id")
           for entree in reponse or []):
        return None
    for entree in reponse or []:
        if isinstance(entree, dict) and entree.get("id"):
            return entree
    return None


def _resoudre_confirmations(gw, base_url: str, reponse) -> tuple[list, int, str | None, list[str]]:
    """Repond aux eventuelles questions de confirmation, au plus
    MAX_CONFIRMATIONS fois. Renvoie (derniere reponse, nombre de
    confirmations, erreur, textes des questions posees) — les textes
    alimentent le `detail` du journal sur un ordre reussi : un
    `confirmed=True` automatique sur un avertissement IBKR (marche ferme,
    taille inhabituelle, ecart de prix...) doit laisser une trace
    auditable, jamais disparaitre silencieusement (finding Important #3)."""
    confirmations = 0
    messages: list[str] = []
    while confirmations < MAX_CONFIRMATIONS:
        question = _premiere_question(reponse)
        if question is None:
            return reponse, confirmations, None, messages
        messages.append(_texte_question(question))
        reponse = gw.confirm_reply(base_url, str(question["id"]))
        confirmations += 1
    if _premiere_question(reponse) is not None:
        return reponse, confirmations, (
            f"chaine de confirmation non resolue apres {confirmations} reponses"), messages
    return reponse, confirmations, None, messages


def _place_order(gw, base_url: str, account_id: str, *, ticker: str, conid,
                 side: str, quantity: int, prix_reference_cotation,
                 state_path: str) -> dict:
    """LE SEUL ENDROIT DU DEPOT D'OU UN ORDRE ACTIONS REEL PART.

    DEFENSE EN PROFONDEUR (spec 4.3 point 2, copie conforme de
    gold_bot.loop.execute_steps) : l'etat est RELU SUR LE DISQUE ici, a
    chaque appel, meme si run_batch l'a deja verifie au demarrage. Entre
    les deux, il a pu se passer 20 minutes de preflight et plusieurs
    autres ordres. Ne jamais remplacer cette relecture par un booleen
    passe en parametre. state.load_state ne leve jamais et retombe sur
    dry_run=True si le fichier est absent/corrompu : un etat illisible
    est donc traite comme un dry_run, jamais comme une autorisation.

    `prix_reference_cotation` est le prix de reference du sizing, DANS LA
    DEVISE DE COTATION (pence pour le LSE) : il sert de repli si IBKR ne
    donne pas encore de prix moyen d'execution. Le resultat renvoye est
    lui aussi en devise de cotation — c'est journal.build_order_record
    qui le ramene en devise de compte.
    """
    etat = state.load_state(state_path)
    if etat["kill_switch"] or etat["dry_run"]:
        return {"statut": "simule", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None,
                "detail": "dry_run ou kill_switch actif : aucun ordre envoye"}

    try:
        reponse = gw.place_market_order(base_url, account_id, conid, side, quantity)
    except Exception as e:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : {e}"}

    try:
        reponse, confirmations, erreur, messages = _resoudre_confirmations(gw, base_url, reponse)
    except Exception as e:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : confirmation refusee : {e}"}
    if erreur is not None:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None, "detail": f"{ticker} : {erreur}"}

    order_id = _premier_order_id(reponse)
    if order_id is None:
        return {"statut": "erreur", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None,
                "detail": f"{ticker} : reponse sans order_id : {reponse}"}

    # L'ORDRE EST PARTI. A partir d'ici, plus aucune erreur ne doit faire
    # renvoyer "erreur" : le declarer en echec alors qu'il est execute
    # ferait croire au lendemain que la position n'existe pas.
    prix, estime, commission = prix_reference_cotation, True, None
    try:
        statut = gw.order_status(base_url, order_id)
        prix_moyen = _nombre(statut.get("avgPrice"))
        if prix_moyen is not None and prix_moyen > 0:
            prix, estime = prix_moyen, False
        commission = _nombre(statut.get("commission"))
    except Exception as e:
        print(f"Prix d'execution indisponible pour {ticker} ({order_id}) : {e}")

    # `detail` reste None sur le cas propre (pas de confirmation a
    # auto-valider, prix d'execution fiable) : les deux morceaux
    # ci-dessous ne s'ajoutent que quand il y a effectivement quelque
    # chose a auditer (findings Important #2 et #3).
    details = []
    if confirmations:
        details.append(
            f"{confirmations} confirmation(s) IBKR auto-validee(s) (confirmed=True) : "
            + " | ".join(messages))
    if estime and not _prix_valide(prix):
        details.append(
            f"prix d'execution indisponible (avgPrice IBKR absent ou invalide) ET "
            f"prix de reference du sizing invalide ou manquant ({prix_reference_cotation!r}) "
            f": prix_execution_cotation ({prix!r}) n'est pas fiable et devra etre "
            f"corrige manuellement avant reconciliation, sous peine de position "
            f"non fermable (cf. Ecart #4, Tache 7)")

    return {"statut": "execute", "order_id": order_id,
            "prix_execution_cotation": prix, "prix_execution_estime": estime,
            "commission": commission,
            "detail": "; ".join(details) if details else None}


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
    if not journal.append_run(run, chemins["journal"]):
        # journal.append_run ne leve jamais (voir journal.py) : une panne
        # disque sur le journal ne doit pas interrompre le batch, mais elle
        # ne doit pas non plus disparaitre sans laisser de trace — d'autant
        # plus une fois que les taches 4/5 feront transiter de vrais
        # remplissages par cette meme sortie.
        run["erreurs"].append({
            "etape": "journalisation",
            "detail": f"echec d'ecriture dans {chemins['journal']}",
        })
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
    # gw.DEFAULT_GATEWAY_URL plutot que gateway.DEFAULT_GATEWAY_URL : le
    # module gateway reste le repli, mais si un `gw` injecte (tests, ou un
    # futur client alternatif) porte sa propre URL par defaut, c'est elle
    # qui doit gouverner — pas la constante du module reel qu'on a
    # justement injecte `gw` pour court-circuiter.
    base_url = base_url or os.environ.get(
        "IBKR_GATEWAY_URL",
        getattr(gw, "DEFAULT_GATEWAY_URL", gateway.DEFAULT_GATEWAY_URL))
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
