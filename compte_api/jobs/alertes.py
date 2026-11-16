# compte_api/jobs/alertes.py
# Tâche planifiée des alertes e-mail (minuteur systemd, voir
# deploy/compte-alertes.timer) :
#   python -m compte_api.jobs.alertes detecter   # franchissements -> alert_events
#   python -m compte_api.jobs.alertes resume     # un e-mail par membre
#
# Les fonctions pures (lecture d'une fiche, calcul d'un franchissement,
# rédaction de l'e-mail) sont séparées des entrées-sorties (téléchargement,
# base, envoi) pour être testées sans réseau.
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.db import make_engine, make_sessionmaker
from compte_api.mailer import Mailer, build_mailer
from compte_api.routers.alertes import lien_desinscription

journal = logging.getLogger("compte_api.alertes")

URL_INDICES_DEFAUT = "https://alexandreauq.github.io/analyse-or/indices.json"
# Un événement plus vieux que ça n'est plus envoyé (membre qui confirme son
# adresse ou réactive les e-mails des semaines plus tard : pas d'arriéré).
FENETRE_RESUME = timedelta(days=7)


# --- Fonctions pures ------------------------------------------------------

@dataclass(frozen=True)
class Etat:
    """Ce que la détection retient d'une entreprise à un instant donné."""
    score: float | None
    entry_signal: bool


def signal_entree_actif(entreprise: dict) -> bool:
    # Même définition que le site : findOpportunities (docs/portfolio.js),
    # qui alimente l'onglet Opportunités du portefeuille dans index.html :
    # une alerte de type "entree" présente dans company.alerts.
    return any(isinstance(a, dict) and a.get("kind") == "entree" for a in (entreprise.get("alerts") or []))


def lire_etats(indices: dict) -> tuple[dict[str, Etat], dict[str, str]]:
    """(état par ticker, nom par ticker) tirés de indices.json."""
    etats, noms = {}, {}
    for entreprise in indices.get("companies") or []:
        if not isinstance(entreprise, dict) or not entreprise.get("ticker"):
            continue
        ticker = str(entreprise["ticker"])
        score = entreprise.get("score")
        score = float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None
        etats[ticker] = Etat(score=score, entry_signal=signal_entree_actif(entreprise))
        if entreprise.get("name"):
            noms[ticker] = str(entreprise["name"])
    return etats, noms


def franchit(kind: str, seuil: float | None, avant: Etat, apres: Etat) -> bool:
    """Vrai seulement au passage d'un côté à l'autre : tant que la valeur
    reste au-delà du seuil, plus rien n'est émis.
    score_above : avant <= seuil < après ; score_below : avant >= seuil > après ;
    entry_signal : pas de signal -> signal actif."""
    if kind == "entry_signal":
        return not avant.entry_signal and apres.entry_signal
    if seuil is None or avant.score is None or apres.score is None:
        return False
    if kind == "score_above":
        return avant.score <= seuil < apres.score
    if kind == "score_below":
        return avant.score >= seuil > apres.score
    return False


def _nombre(valeur: float) -> str:
    texte = f"{valeur:.1f}".replace(".", ",")
    return "0,0" if texte == "-0,0" else texte


def decrire_regle(kind: str, seuil: float | None) -> str:
    if kind == "score_above":
        return f"score passé au-dessus de {_nombre(seuil)}"
    if kind == "score_below":
        return f"score passé en dessous de {_nombre(seuil)}"
    return "signal d'entrée activé"


@dataclass(frozen=True)
class LigneResume:
    ticker: str
    nom: str | None
    kind: str
    seuil: float | None
    valeur: float | None


def rediger_resume(lignes: list[LigneResume], prenom: str | None, site_url: str,
                   lien_desinscr: str) -> tuple[str, str]:
    """(sujet, texte) de l'e-mail quotidien d'un membre."""
    n = len(lignes)
    sujet = "Ton alerte du jour" if n == 1 else f"Tes {n} alertes du jour"
    corps = [f"Bonjour {prenom}," if prenom else "Bonjour,", ""]
    corps.append("Voici l'alerte déclenchée depuis le dernier résumé :" if n == 1
                 else "Voici les alertes déclenchées depuis le dernier résumé :")
    corps.append("")
    for ligne in lignes:
        titre = f"{ligne.nom} ({ligne.ticker})" if ligne.nom else ligne.ticker
        detail = decrire_regle(ligne.kind, ligne.seuil)
        if ligne.valeur is not None:
            detail += f" (score : {_nombre(ligne.valeur)})"
        corps.append(f"- {titre} : {detail}")
        corps.append(f"  {site_url}/#indices/{quote(ligne.ticker, safe='')}")
    corps += [
        "",
        "Ces alertes sont des repères automatiques, pas des conseils d'investissement.",
        f"Gérer tes alertes : {site_url}/",
        f"Ne plus recevoir ces e-mails : {lien_desinscr}",
    ]
    return sujet, "\n".join(corps) + "\n"


# --- Entrées-sorties --------------------------------------------------------

def charger_indices(source: str) -> dict:
    """indices.json depuis une URL (site publié) ou un chemin de fichier local."""
    if source.startswith(("http://", "https://")):
        import httpx
        reponse = httpx.get(source, params={"t": int(datetime.now().timestamp())},
                            timeout=60, follow_redirects=True)
        reponse.raise_for_status()
        return reponse.json()
    with open(source, encoding="utf-8") as fichier:
        return json.load(fichier)


def detecter(db: DbSession, indices: dict, maintenant: datetime | None = None) -> int:
    """Compare l'état actuel de chaque ticker visé par une règle active à
    l'instantané précédent, enregistre les franchissements, puis met
    l'instantané à jour. Renvoie le nombre d'événements créés."""
    maintenant = maintenant or models.maintenant()
    etats, _noms = lire_etats(indices)
    regles = db.scalars(select(models.AlertRule).where(models.AlertRule.active.is_(True))).all()
    par_ticker: dict[str, list[models.AlertRule]] = defaultdict(list)
    for regle in regles:
        par_ticker[regle.target].append(regle)

    instantanes = {s.target: s for s in db.scalars(
        select(models.AlertSnapshot).where(models.AlertSnapshot.target.in_(list(par_ticker))))}
    crees = 0
    for ticker, regles_ticker in par_ticker.items():
        apres = etats.get(ticker)
        if apres is None:
            continue  # ticker absent du fichier : on garde l'ancien instantané
        instantane = instantanes.get(ticker)
        if instantane is None:
            # Premier passage : on enregistre l'état sans rien émettre.
            db.add(models.AlertSnapshot(target=ticker, score=apres.score,
                                        entry_signal=apres.entry_signal, updated_at=maintenant))
            continue
        avant = Etat(score=instantane.score, entry_signal=bool(instantane.entry_signal))
        for regle in regles_ticker:
            if franchit(regle.kind, regle.threshold, avant, apres):
                db.add(models.AlertEvent(rule_id=regle.id, triggered_at=maintenant, value=apres.score))
                crees += 1
        # Un score momentanément absent ne remplace pas le dernier connu.
        if apres.score is not None:
            instantane.score = apres.score
        instantane.entry_signal = apres.entry_signal
        instantane.updated_at = maintenant
    db.commit()
    return crees


def envoyer_resumes(db: DbSession, mailer: Mailer, settings: Settings, noms: dict[str, str] | None = None,
                    maintenant: datetime | None = None) -> tuple[int, int]:
    """Un e-mail par membre confirmé, abonné, ayant des événements non
    envoyés. Renvoie (e-mails envoyés, échecs). Un échec laisse les
    événements du membre non envoyés, pour le prochain passage."""
    maintenant = maintenant or models.maintenant()
    noms = noms or {}
    lignes = db.execute(
        select(models.AlertEvent, models.AlertRule, models.User)
        .join(models.AlertRule, models.AlertEvent.rule_id == models.AlertRule.id)
        .join(models.User, models.AlertRule.user_id == models.User.id)
        .where(models.AlertEvent.emailed_at.is_(None),
               models.AlertEvent.triggered_at >= maintenant - FENETRE_RESUME,
               models.AlertRule.active.is_(True),
               models.User.email_verified_at.is_not(None),
               models.User.alerts_email_enabled.is_(True),
               models.User.deleted_at.is_(None))
        .order_by(models.User.id, models.AlertEvent.triggered_at, models.AlertEvent.id)
    ).all()
    par_membre: dict[int, tuple[models.User, list]] = {}
    for evenement, regle, user in lignes:
        par_membre.setdefault(user.id, (user, []))[1].append((evenement, regle))

    envoyes = echecs = 0
    for user, evenements in par_membre.values():
        contenu = [LigneResume(ticker=r.target, nom=noms.get(r.target), kind=r.kind,
                               seuil=r.threshold, valeur=e.value) for e, r in evenements]
        sujet, texte = rediger_resume(contenu, user.display_name, settings.site_url,
                                      lien_desinscription(user.id, settings))
        try:
            mailer.envoyer(user.email, sujet, texte)
        except Exception:  # noqa: BLE001 : un membre en échec ne bloque pas les autres
            # Jamais l'adresse complète dans les journaux : l'id suffit.
            journal.exception("Échec d'envoi du résumé d'alertes au membre %s", user.id)
            echecs += 1
            continue
        for evenement, _regle in evenements:
            evenement.emailed_at = maintenant
        db.commit()
        envoyes += 1
    return envoyes, echecs


def _source_indices() -> str:
    return os.environ.get("COMPTE_INDICES_URL") or URL_INDICES_DEFAUT


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("detecter", "resume"):
        print("Usage : python -m compte_api.jobs.alertes detecter|resume", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    db = make_sessionmaker(make_engine(settings.database_url))()
    try:
        if argv[0] == "detecter":
            crees = detecter(db, charger_indices(_source_indices()))
            journal.info("Détection terminée : %d franchissement(s) enregistré(s).", crees)
            return 0
        # Les noms d'entreprise ne sont qu'un confort : sans eux, le ticker suffit.
        try:
            _etats, noms = lire_etats(charger_indices(_source_indices()))
        except Exception:  # noqa: BLE001
            journal.warning("indices.json illisible : résumé envoyé sans les noms d'entreprise.")
            noms = {}
        envoyes, echecs = envoyer_resumes(db, build_mailer(settings), settings, noms)
        journal.info("Résumés envoyés : %d, échecs : %d.", envoyes, echecs)
        return 1 if echecs and not envoyes else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
