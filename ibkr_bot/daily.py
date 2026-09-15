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
import math
import os
import re
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


# Borne de la boucle de confirmation : le CPAPI peut repondre a un ordre
# par une question (marche ferme, ordre au marche hors seance, taille
# inhabituelle...), et la reponse a une question peut elle-meme etre une
# question. Sans borne, une chaine sans fin bloquerait le batch dans la
# fenetre de marche.
MAX_CONFIRMATIONS = 5


_SEPARATEUR_MILLIERS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _nombre(valeur):
    """float(valeur) ou None — le CPAPI renvoie avgPrice et commission
    tantot en nombre, tantot en CHAINE ("415.20" dans les fixtures de
    tests/ibkr_bot/test_gateway.py), et parfois avec un separateur de
    milliers ("1,234.50") sur certains champs numeriques. La virgule
    n'est retiree QUE si la chaine entiere a la forme d'un separateur de
    milliers anglo-saxon (finding de la revue du round de correctifs de
    la tache 4) : un retrait inconditionnel confondrait une virgule
    decimale ("1234,50", un format europeen plausible sur un futur
    fournisseur de donnees) avec un separateur de milliers et
    produirait un prix 100x trop grand, silencieusement pris pour un
    prix d'execution reel plutot que pour le repli estime."""
    if isinstance(valeur, bool) or valeur is None:
        return None
    if isinstance(valeur, str) and _SEPARATEUR_MILLIERS.match(valeur):
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
        detail_confirmations = (
            f" ({confirmations} confirmation(s) auto-validee(s) avant l'abandon : "
            + " | ".join(messages) + ")" if messages else ""
        )
        return reponse, confirmations, (
            f"chaine de confirmation non resolue apres {confirmations} reponses"
            f"{detail_confirmations}"), messages
    return reponse, confirmations, None, messages


def _place_order(gw, base_url: str, account_id: str, *, ticker: str, conid,
                 side: str, quantity: int, prix_reference_cotation,
                 state_path: str, mode_attendu: str | None = None) -> dict:
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

    `mode_attendu` (Critical #2 de la revue finale de branche) est le mode
    ("dry_run"/"reel") sur lequel run_batch s'est arrete APRES sa PROPRE
    relecture (celle qui suit le preflight). Il ne sert JAMAIS a autoriser
    l'envoi d'un ordre — la relecture ci-dessus reste la SEULE source de
    verite pour ca. Il sert uniquement a la COMPARAISON juste en dessous,
    pour distinguer deux situations qui, sans lui, produisent exactement
    le meme statut "simule" alors qu'elles n'ont pas du tout les memes
    consequences sur positions.json :
      - une simulation authentique (le batch entier est dry_run depuis le
        debut) : la position n'a jamais existe reellement, la retirer de
        positions.json est correct ;
      - un batch qui s'etait engage en mode REEL (mode_attendu == "reel")
        mais dont l'operateur a tire l'interrupteur d'urgence ENTRE deux
        ordres : la position, elle, est bien reelle chez IBKR. La
        retirer de positions.json comme un simple "simule" l'orphelinerait
        silencieusement pour toujours (portfolio.reconcile n'adopte
        jamais une position inconnue du journal, spec 9.5).
    """
    etat = state.load_state(state_path)
    if etat["kill_switch"] or etat["dry_run"]:
        if mode_attendu == "reel":
            return {"statut": "annule_interruption", "order_id": None,
                    "prix_execution_cotation": None, "prix_execution_estime": False,
                    "commission": None,
                    "detail": (
                        "execution interrompue : kill_switch active (ou dry_run "
                        "repasse a true) entre le debut du batch, engage en mode "
                        "reel, et cet envoi d'ordre precis — aucun ordre envoye, "
                        "position laissee INTACTE dans positions.json pour verification "
                        "manuelle (ne pas traiter comme une simulation)")}
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


def _prix_reference_absent(valeur) -> bool:
    """True si une valeur numerique est absente ou NaN — meme garde que
    portfolio._is_missing (fonction privee de Plan A), duplique ici plutot
    qu'importee : ce plan ne restructure pas Plan A, et daily.py n'a pas a
    dependre d'un symbole prive d'un autre module."""
    try:
        return math.isnan(valeur)
    except TypeError:
        return valeur is None


def _taux_de_change(gw, base_url: str, devise: str, cache: dict, run: dict) -> float:
    """Taux EUR -> devise de l'indice, mis en cache par devise sur la
    duree du batch. Un echec renvoie 0.0, que sizing.compute_quantity
    traduit en motif `prix_ou_taux_invalide` et select_entries en rejet :
    on echoue bruyamment plutot que d'acheter sur un taux devine."""
    if devise in cache:
        return cache[devise]
    try:
        taux = gw.exchange_rate(base_url, "EUR", devise)
    except Exception as e:
        run["erreurs"].append({"etape": "taux_de_change",
                               "detail": f"EUR->{devise} : {e}"})
        taux = 0.0
    cache[devise] = taux
    return taux


def _executer_sortie(gw, base_url, account_id, sortie: dict, chemins: dict,
                     *, mode_attendu: str | None = None) -> dict:
    """Vend une position dont une condition de sortie est remplie."""
    position = sortie["position"]
    execution = _place_order(
        gw, base_url, account_id, ticker=position["ticker"],
        conid=position["conid"], side="SELL", quantity=int(position["quantite"]),
        prix_reference_cotation=sizing.to_quotation_price(
            sortie["current_price"], position["ticker"]),
        state_path=chemins["state"], mode_attendu=mode_attendu)
    return journal.build_order_record(
        ticker=position["ticker"], conid=position["conid"], sens="SELL",
        quantite=int(position["quantite"]),
        devise_compte=position.get("devise", ""),
        devise_cotation=sizing.quotation_currency(
            position.get("devise", ""), position["ticker"]),
        prix_reference_sizing=sortie["current_price"],
        execution=execution, close_reason=sortie["close_reason"])


def _executer_entree(gw, base_url, account_id, retenu: dict, contrat: dict,
                     chemins: dict, *, mode_attendu: str | None = None) -> dict:
    """Achete un signal retenu par portfolio.select_entries."""
    signal, plan = retenu["signal"], retenu["plan"]
    execution = _place_order(
        gw, base_url, account_id, ticker=signal["ticker"], conid=contrat["conid"],
        side="BUY", quantity=int(plan["quantite"]),
        prix_reference_cotation=plan["prix_unitaire_cotation"],
        state_path=chemins["state"], mode_attendu=mode_attendu)
    return journal.build_order_record(
        ticker=signal["ticker"], conid=contrat["conid"], sens="BUY",
        quantite=int(plan["quantite"]),
        devise_compte=plan["devise_compte"], devise_cotation=plan["devise_cotation"],
        taux_de_change=plan["taux_de_change"], budget_converti=plan["budget_converti"],
        prix_reference_sizing=signal["current_price"],
        prix_paper=signal.get("paper_entry_price"),
        execution=execution, rang=retenu["rang"])


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
      1bis. jour de bourse (samedi/dimanche exclus, Critical #1 de la
         revue finale de branche) -> AVANT le preflight, meme raisonnement
         que la fraicheur ci-dessous : le garde de fraicheur NE DETECTE
         PAS un week-end (le workflow indices.json tourne 7j/7) ;
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

    # 1bis. Jour de bourse ? (Critical #1 de la revue finale de branche) —
    # le timer systemd tourne 7j/7, ET le garde de fraicheur juste en
    # dessous ne detecte PAS un week-end : le workflow GitHub Actions qui
    # produit docs/indices.json tourne lui aussi 7j/7 et tamponne
    # inconditionnellement la date du jour, donc indices_are_fresh renvoie
    # True un samedi ou un dimanche. Or les regles de sortie a date fixe de
    # portfolio.exit_reason (delai_max a 6 mois, stop_loss compare a un
    # cours de cloture de vendredi perime) peuvent legitimement se
    # declencher un jour non ouvre, et la boucle de confirmation IBKR
    # repondrait automatiquement "confirmed=True" a l'avertissement
    # "marche ferme". Seuls les week-ends sont couverts ici : les jours
    # feries sont hors perimetre (voir deploy/README-ibkr.md, section
    # « Jours feries »), l'operateur doit couper manuellement autour d'eux.
    # Place AVANT le preflight, meme raisonnement que le garde de
    # fraicheur juste apres : inutile de bruler 10-20 minutes de tentatives
    # de reauthentification un jour ou le batch n'agira de toute facon pas.
    if datetime.strptime(today, "%Y-%m-%d").weekday() >= 5:
        run["statut"] = "hors_jour_de_bourse"
        run["signaux_rejetes"].append({
            "ticker": None, "raison": "hors_jour_de_bourse",
            "rang": None, "score": None,
        })
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

    # DEFENSE EN PROFONDEUR (spec 4.3 point 2), symetrique a celle de
    # _place_order mais ICI pour la POLITIQUE DE RECONCILIATION : la
    # lecture de `etat` au tout debut de cette fonction date d'avant le
    # preflight, qui peut durer jusqu'a ~20 minutes (spec 5.5). Un
    # operateur qui bascule dry_run: true -> false PENDANT cette fenetre
    # ("ok, on passe en reel maintenant") est un scenario plausible. Sans
    # cette relecture, la politique de reconciliation resterait figee sur
    # "faire confiance au journal local" (positions simulees, jamais
    # detenues chez IBKR) alors que _place_order, lui, relit l'etat a
    # chaque ordre et enverrait de VRAIS ordres — un stop-loss calcule sur
    # une position purement simulee deviendrait alors une VRAIE vente a nu
    # sur un conid dont le compte ne detient rien. Cette relecture ne
    # remplace PAS celle de _place_order (qui reste la seule source de
    # verite pour l'envoi effectif de chaque ordre) ; elle evite seulement
    # que la decision de politique ci-dessous reste bloquee sur une
    # lecture perimee.
    etat = state.load_state(chemins["state"])
    run["mode"] = "dry_run" if etat["dry_run"] else "reel"

    # --- 5. Reconciliation AVANT toute decision (spec 5.4) ------------
    locales = portfolio.load_positions(chemins["positions"])
    try:
        brutes = gw.positions(base_url, account_id)
    except Exception as e:
        # Agir sans savoir ce que le compte detient, c'est risquer de
        # racheter une ligne deja detenue (spec 5.4 / 5.5).
        run["erreurs"].append({"etape": "positions_ibkr", "detail": str(e)})
        run["statut"] = "reconciliation_impossible"
        return _terminer(run, chemins)

    reconciliation = portfolio.reconcile(locales, brutes)
    run["reconciliation"] = {
        "actives": len(reconciliation["actives"]),
        "cloturees_hors_bot": [p.get("id") for p in reconciliation["cloturees_hors_bot"]],
        "anomalies_quantite": reconciliation["anomalies_quantite"],
        "ignorees": len(reconciliation["ignorees"]),
    }

    # En dry_run, les positions simulees n'existent evidemment pas chez
    # IBKR : laisser la reconciliation les fermer viderait positions.json
    # chaque jour et rendrait la validation en simulation (spec 5.3) sans
    # objet — aucune sortie ne serait jamais observee. On journalise donc
    # ce que la reconciliation DIRAIT, mais on garde l'etat local. En mode
    # reel, la reconciliation fait foi, sans exception.
    if etat["dry_run"]:
        positions_ouvertes = list(locales)
        # Important #1 (revue finale de branche) : run["reconciliation"]
        # ci-dessus vient de reconcile(), qui ne voit evidemment aucune
        # position simulee chez IBKR — son verdict brut dirait "0 position
        # active, tout clôture hors bot", chaque jour, pendant TOUTES les
        # semaines de validation en dry_run (spec 7). notify.py restitue
        # ce dict verbatim dans le resume quotidien : sans cette
        # surcharge, l'email mentirait sur le nombre de positions gerees
        # exactement pendant la periode ou ce chiffre compte le plus. On
        # ecrase seulement `actives`/`cloturees_hors_bot` (ce que dry_run
        # gere REELLEMENT), en gardant `anomalies_quantite`/`ignorees` de
        # la vraie reconciliation pour l'audit/debug.
        run["reconciliation"] = {
            **run["reconciliation"],
            "actives": len(positions_ouvertes),
            "cloturees_hors_bot": [],
        }
    else:
        positions_ouvertes = list(reconciliation["actives"])

    # Alerte operateur : portfolio.exit_reason laisse volontairement
    # inclosable une position sans prix de reference (ecart #4 documente
    # dans portfolio.py), et dit explicitement que c'est au Plan B de la
    # signaler. Sans ca, elle occuperait une place pour toujours, en
    # silence.
    for position in positions_ouvertes:
        if _prix_reference_absent(position.get("prix_execution_reference")):
            run["anomalies"].append({
                "type": "prix_reference_absent",
                "ticker": position.get("ticker"),
                "detail": ("position inclosable par toute regle de sortie tant "
                           "que son prix d'execution de reference n'est pas "
                           "restaure dans positions.json"),
            })

    def _sauver_positions():
        if not journal.save_positions_safely(positions_ouvertes, chemins["positions"]):
            run["erreurs"].append({
                "etape": "positions.json",
                "detail": "ecriture impossible — etat local potentiellement perime",
            })

    _sauver_positions()

    # Un ticker qui quitte le portefeuille aujourd'hui n'est JAMAIS
    # rachete dans le meme batch. Pour les clotures hors bot, la spec 5.4
    # est explicite : "Le bot ne la rouvre jamais". La sortie du bot
    # lui-meme est remplie plus bas, au fil des ventes.
    # En dry_run, cloturees_hors_bot contient TOUTES les positions
    # simulees (IBKR n'en connait aucune) : l'appliquer bloquerait tous
    # les tickers du portefeuille simule pour rien.
    tickers_indisponibles = {
        p.get("ticker"): "cloturee_hors_bot"
        for p in reconciliation["cloturees_hors_bot"]
    } if not etat["dry_run"] else {}

    # --- 6. Sorties (spec 3.6), AVANT les entrees ---------------------
    # Meme enchainement que le paper-trading (_close_eligible_positions
    # puis _open_new_signal_positions) : les places liberees aujourd'hui
    # sont disponibles pour les signaux du jour.
    companies = {c["ticker"]: c for c in indices.get("companies", [])
                 if isinstance(c, dict) and c.get("ticker")}
    # portfolio.positions_to_close() est appelee UNE POSITION A LA FOIS
    # (plutot qu'une seule fois sur toute la liste) pour isoler une ligne
    # corrompue de positions.json : c'est une fonction pure, sans etat
    # partage entre positions, donc le resultat agrege est identique tant
    # que toutes les lignes sont bien formees. Une ligne cassee (ex.
    # `date_limite` absent — precisement le champ que l'anomalie
    # `prix_reference_absent` ci-dessus invite un operateur a corriger a
    # la main) fait lever un KeyError QUE portfolio.py ne rattrape pas :
    # sans cet isolement, cette seule ligne ferait perdre TOUT le batch —
    # aucune ligne de journal, aucun email, silence total (spec 5.5 :
    # jamais une donnee corrompue ne doit en bloquer d'autres).
    sorties_a_traiter = []
    for position in positions_ouvertes:
        try:
            sorties_a_traiter.extend(
                portfolio.positions_to_close([position], companies, today))
        except Exception as e:
            run["erreurs"].append({
                "etape": "positions_to_close",
                "detail": f"{position.get('ticker', '?')} : {e}",
            })
    for sortie in sorties_a_traiter:
        position = sortie["position"]
        try:
            record = _executer_sortie(gw, base_url, account_id, sortie, chemins,
                                      mode_attendu=run["mode"])
        except Exception as e:
            # Meme isolement que portfolio.positions_to_close() ci-dessus,
            # mais pour l'EXECUTION de la sortie cette fois : `conid` et
            # `quantite` ne sont lus qu'ICI (dans _executer_sortie), jamais
            # par la decision de sortie elle-meme — une ligne de
            # positions.json corrompue sur CES champs precis passe le
            # premier garde intacte (positions_to_close ne les touche pas)
            # et ne serait attrapee nulle part sans celui-ci. Meme risque
            # que ci-dessus : un typo lors de la correction manuelle
            # recommandee par l'anomalie `prix_reference_absent` peut tout
            # aussi bien toucher `conid` ou `quantite` que `date_limite`.
            run["erreurs"].append({
                "etape": "executer_sortie",
                "detail": f"{position.get('ticker', '?')} : {e}",
            })
            continue
        run["sorties"].append(record)
        if record["statut"] == "annule_interruption":
            # Critical #2 (revue finale de branche) : ne JAMAIS traiter ce
            # cas comme un simple "simule" — la position est reelle chez
            # IBKR et reste dans positions.json intacte (le bloc
            # ci-dessous, qui retire une position de positions_ouvertes,
            # est volontairement SAUTE ici). On le remonte aussi dans
            # run["erreurs"] : sans ca, l'evenement resterait noye au
            # milieu des cartes de vente ordinaires de l'email au lieu
            # d'alerter clairement l'operateur qu'une position reelle
            # attend une verification manuelle.
            run["erreurs"].append({
                "etape": "execution_sortie_interrompue",
                "detail": f"{position.get('ticker', '?')} : {record.get('detail')}",
            })
        if record["statut"] in ("execute", "simule"):
            identifiant = position.get("id")
            positions_ouvertes = [p for p in positions_ouvertes
                                  if p.get("id") != identifiant]
            # Racheter dans la minute un titre qu'on vient de stop-losser
            # serait absurde, et le filtre `deja_en_portefeuille` de
            # select_entries ne peut plus le voir : il vient d'etre retire
            # de positions_ouvertes juste au-dessus.
            tickers_indisponibles[position["ticker"]] = "vendu_aujourd_hui"
            # Reecrit apres CHAQUE ordre, pas en fin de batch : un plantage
            # entre les deux laisserait positions.json en desaccord avec la
            # realite du compte.
            _sauver_positions()

    # --- 7. Entrees ---------------------------------------------------
    paper_positions = signals.load_signal_tracking(chemins["tracking"])
    signaux, rejets = signals.collect_new_signals(indices, paper_positions, today)
    for rejet in rejets:
        run["signaux_rejetes"].append({"ticker": rejet.get("ticker"),
                                       "raison": rejet.get("raison"),
                                       "rang": None, "score": None})

    try:
        base_cash = gw.base_currency_cash(base_url, account_id)
    except Exception as e:
        # Solde inconnu -> 0.0 : le garde-fou de select_entries rejettera
        # tout, ce qui est le sens sur (spec 9.9).
        run["erreurs"].append({"etape": "base_currency_cash", "detail": str(e)})
        base_cash = 0.0
    journal.save_account_snapshot({"base_cash": base_cash},
                                  chemins["account_snapshot"])

    cache_conid = contracts.load_cache(chemins["conid_cache"])
    taux_par_devise: dict[str, float] = {}
    plans: dict[str, dict] = {}
    contrats: dict[str, dict] = {}
    for signal in signaux:
        ticker = signal["ticker"]
        taux = _taux_de_change(gw, base_url, signal["currency"], taux_par_devise, run)
        plans[ticker] = sizing.compute_quantity(
            ticker, signal["currency"], signal["current_price"], taux)
        contrats[ticker] = contracts.resolve_conid(
            ticker,
            lambda symbole: gw.search_contract(base_url, symbole),
            lambda conid: gw.contract_info(base_url, conid),
            cache_conid, today)
    try:
        contracts.save_cache(cache_conid, chemins["conid_cache"])
    except Exception as e:
        print(f"Erreur ecriture du cache de conid : {e}")

    # Garde d'idempotence supplementaire : un conid deja detenu sur le
    # compte mais absent de positions.json (batch plante entre l'ordre et
    # la sauvegarde) serait classe `ignorees` par la reconciliation —
    # c'est-a-dire "position de l'utilisateur" — et rien n'empecherait un
    # rachat. On refuse d'en acheter davantage ; on n'y touche pas pour
    # autant (spec 9.5).
    conids_detenus = set()
    for brute in reconciliation["ignorees"]:
        try:
            conids_detenus.add(int(brute.get("conid")))
        except (TypeError, ValueError):
            continue
    signaux_financables = []
    for signal in signaux:
        ticker = signal["ticker"]
        raison_indisponible = tickers_indisponibles.get(ticker)
        if raison_indisponible is not None:
            run["signaux_rejetes"].append({
                "ticker": ticker, "raison": raison_indisponible,
                "rang": None, "score": signal.get("score"),
            })
            continue
        contrat = contrats.get(ticker) or {}
        if contrat.get("conid") is not None and contrat["conid"] in conids_detenus:
            run["signaux_rejetes"].append({
                "ticker": ticker, "raison": "deja_detenu_hors_journal",
                "rang": None, "score": signal.get("score"),
            })
            continue
        # Important #3 (revue finale de branche) : contracts.py valide la
        # devise du contrat resolu contre EXPECTED_VENUE (suffixe du
        # ticker), sizing.py prend independamment `devise_compte` dans
        # index_currency de docs/indices.json. Les deux sources ne sont
        # aujourd'hui jamais confrontees l'une a l'autre — elles
        # coincident pour les 8 indices actuels, mais rien n'empecherait
        # un futur indice ou elles divergent de dimensionner un budget
        # dans une devise et d'executer dans une autre, exactement l'erreur
        # que le garde-fou pence/livre existe deja pour empecher. On ne
        # compare que lorsque les deux devises sont effectivement connues
        # (un contrat non resolu porte deja une devise vide et sera rejete
        # plus loin par select_entries via `contrat_non_resolu`).
        plan = plans.get(ticker) or {}
        devise_contrat = contrat.get("currency")
        devise_sizing = plan.get("devise_compte")
        # Comparaison insensible a la casse : le LSE annonce sa devise
        # tantot "GBP", tantot "GBp" (contracts.py accepte les deux
        # explicitement, voir EXPECTED_VENUE) — une comparaison stricte
        # rejetterait a tort tout contrat FTSE resolu en "GBp" alors que
        # sizing.py utilise toujours "GBP" (index_currency), un faux
        # positif trouve a la revue finale de branche.
        if (devise_contrat and devise_sizing
                and devise_contrat.upper() != devise_sizing.upper()):
            run["erreurs"].append({
                "etape": "coherence_devise",
                "detail": (f"{ticker} : devise du contrat resolu "
                           f"({devise_contrat}) differente de la devise de "
                           f"sizing ({devise_sizing}) — signal rejete par "
                           f"prudence plutot que d'executer dans une devise "
                           f"differente de celle du budget"),
            })
            run["signaux_rejetes"].append({
                "ticker": ticker, "raison": "devise_incoherente",
                "rang": None, "score": signal.get("score"),
            })
            continue
        signaux_financables.append(signal)

    # NOTE : les signaux ecartes ci-dessus ne sont pas passes a
    # select_entries, donc les `rang` renvoyes se comptent sur les seuls
    # signaux finançables. C'est voulu : un signal inachetable ne doit ni
    # consommer une place sous le plafond, ni du budget (meme raisonnement
    # que l'ordre des filtres de select_entries, spec 3.3). Les rejets
    # ecartes ici portent donc `rang: None`.
    retenus, rejets_selection = portfolio.select_entries(
        signaux_financables, positions_ouvertes, plans, contrats, base_cash)
    for rejet in rejets_selection:
        run["signaux_rejetes"].append({"ticker": rejet["ticker"],
                                       "raison": rejet["raison"],
                                       "rang": rejet.get("rang"),
                                       "score": rejet.get("score")})

    for retenu in retenus:
        signal = retenu["signal"]
        contrat = contrats[signal["ticker"]]
        record = _executer_entree(gw, base_url, account_id, retenu, contrat, chemins,
                                  mode_attendu=run["mode"])
        run["entrees"].append(record)
        if record["statut"] == "annule_interruption":
            # Meme raisonnement que cote sorties : un achat interrompu par
            # une bascule kill_switch/dry_run mid-batch n'a jamais ete
            # envoye — rien a orpheliner ici puisqu'aucune position n'est
            # ajoutee (le bloc ci-dessous est saute par le `continue`
            # suivant) — mais l'evenement doit rester visible dans l'email,
            # pas se fondre parmi les cartes d'achat ordinaires.
            run["erreurs"].append({
                "etape": "execution_entree_interrompue",
                "detail": f"{signal.get('ticker', '?')} : {record.get('detail')}",
            })
        if record["statut"] not in ("execute", "simule"):
            continue   # spec 5.5 : pas de reprise, on passe au suivant
        positions_ouvertes.append(journal.build_position_record(
            signal, retenu["plan"], contrat, int(retenu["plan"]["quantite"]),
            record["prix_execution"] if record["prix_execution"] is not None
            else signal["current_price"],
            today))
        _sauver_positions()

    return _terminer(run, chemins)


def main() -> None:
    run_batch()


if __name__ == "__main__":
    main()
