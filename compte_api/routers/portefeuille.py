# compte_api/routers/portefeuille.py
# Portefeuille Actions du membre (table `positions`) et import unique des
# données du navigateur (spec §7).
#
# Une position clôturée est la même ligne que la position ouverte, avec
# sell_price et sell_date remplis (même forme que docs/portfolio.js).
# Validations alignées sur validatePositionInput (docs/portfolio.js) :
# quantité et prix strictement positifs ; en plus, côté serveur, ticker au
# format attendu et dates AAAA-MM-JJ valides, jamais après aujourd'hui (UTC).
# Une position d'un autre membre répond 404, jamais 403 : on ne révèle pas
# qu'elle existe.
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.deps import current_user, get_db
from compte_api.routers.reglages import (
    TAILLE_MAX, ReglageInvalide, ecrire_reglages, lire_reglages, taille_serialisee, valider_reglage,
)

router = APIRouter()

POSITIONS_MAX = 500
HISTORIQUE_IA_MAX = 100
QUESTION_MAX = 4000
REPONSE_MAX = 100_000
# Même règle que les clés de favoris : lettres, chiffres, '.', '-', '^', '='.
_TICKER = re.compile(r"^[A-Za-z0-9.\-^=]{1,20}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_MIN = date(1900, 1, 1)
STATUTS = ("ouvertes", "cloturees", "toutes")


class EntreeInvalide(ValueError):
    """Donnée refusée ; le message est prêt à afficher."""


def _aujourdhui() -> date:
    return datetime.now(timezone.utc).date()


# --- Validation (partagée par les routes et l'import) ---------------------

def _ticker(valeur: Any) -> str:
    if not isinstance(valeur, str) or not _TICKER.match(valeur.strip()):
        raise EntreeInvalide("Ticker invalide.")
    return valeur.strip()


def _positif(valeur: Any, message: str) -> float:
    # Les booléens sont des entiers en Python : on les refuse explicitement.
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise EntreeInvalide(message)
    valeur = float(valeur)
    if not math.isfinite(valeur) or valeur <= 0:
        raise EntreeInvalide(message)
    return valeur


def _date(valeur: Any, nom: str) -> date:
    if not isinstance(valeur, str) or not _DATE.match(valeur):
        raise EntreeInvalide(f"La date {nom} doit être au format AAAA-MM-JJ.")
    try:
        jour = date.fromisoformat(valeur)
    except ValueError:
        raise EntreeInvalide(f"La date {nom} n'existe pas.")
    if jour < _DATE_MIN:
        raise EntreeInvalide(f"La date {nom} est trop ancienne.")
    if jour > _aujourdhui():
        raise EntreeInvalide(f"La date {nom} ne peut pas être dans le futur.")
    return jour


def _quantite(v):
    return _positif(v, "La quantité doit être un nombre positif.")


def _prix_achat(v):
    return _positif(v, "Le prix d'achat doit être un nombre positif.")


def _prix_vente(v):
    return _positif(v, "Le prix de vente doit être un nombre positif.")


def valider_achat(donnees: Any) -> dict:
    """{ticker, quantity, buy_price, buy_date} validés ; lève EntreeInvalide."""
    if not isinstance(donnees, dict):
        raise EntreeInvalide("Position invalide.")
    return {
        "ticker": _ticker(donnees.get("ticker")),
        "quantity": _quantite(donnees.get("quantity")),
        "buy_price": _prix_achat(donnees.get("buy_price")),
        "buy_date": _date(donnees.get("buy_date"), "d'achat"),
    }


def valider_vente(donnees: Any, buy_date: date) -> dict:
    if not isinstance(donnees, dict):
        raise EntreeInvalide("Vente invalide.")
    vente = {
        "sell_price": _prix_vente(donnees.get("sell_price")),
        "sell_date": _date(donnees.get("sell_date"), "de vente"),
    }
    if vente["sell_date"] < buy_date:
        raise EntreeInvalide("La date de vente ne peut pas précéder la date d'achat.")
    return vente


def _corps_objet(corps: Any) -> dict:
    if not isinstance(corps, dict):
        raise HTTPException(status_code=422, detail="Données invalides.")
    return corps


def _422(e: EntreeInvalide) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


# --- Accès aux données ----------------------------------------------------

def serialiser(p: models.Position) -> dict:
    created = p.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "id": p.id,
        "ticker": p.ticker,
        "quantity": p.quantity,
        "buy_price": p.buy_price,
        "buy_date": p.buy_date.isoformat(),
        "sell_price": p.sell_price,
        "sell_date": p.sell_date.isoformat() if p.sell_date else None,
        "created_at": created.isoformat() if created else None,
    }


def _position_du_membre(db: DbSession, user: models.User, position_id: int) -> models.Position:
    position = db.scalar(select(models.Position).where(
        models.Position.id == position_id, models.Position.user_id == user.id))
    if position is None:
        raise HTTPException(status_code=404, detail="Position introuvable.")
    return position


def _nombre_positions(db: DbSession, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(models.Position)
                     .where(models.Position.user_id == user_id)) or 0


# --- Routes du portefeuille -----------------------------------------------

@router.get("/positions")
def lister_positions(statut: str = Query("toutes"), user: models.User = Depends(current_user),
                     db: DbSession = Depends(get_db)) -> list[dict]:
    if statut not in STATUTS:
        raise HTTPException(status_code=422, detail="Statut inconnu (ouvertes, cloturees ou toutes).")
    requete = select(models.Position).where(models.Position.user_id == user.id)
    if statut == "ouvertes":
        requete = requete.where(models.Position.sell_date.is_(None))
    elif statut == "cloturees":
        requete = requete.where(models.Position.sell_date.is_not(None))
    # Ordre d'ajout, comme la liste locale de portfolio.js.
    return [serialiser(p) for p in db.scalars(requete.order_by(models.Position.id))]


@router.post("/positions", status_code=201)
def ajouter_position(corps: Any = Body(...), user: models.User = Depends(current_user),
                     db: DbSession = Depends(get_db)) -> dict:
    try:
        achat = valider_achat(_corps_objet(corps))
    except EntreeInvalide as e:
        raise _422(e)
    if _nombre_positions(db, user.id) >= POSITIONS_MAX:
        raise HTTPException(status_code=409, detail=f"Limite de {POSITIONS_MAX} positions atteinte.")
    position = models.Position(user_id=user.id, **achat)
    db.add(position)
    db.commit()
    db.refresh(position)
    return serialiser(position)


@router.patch("/positions/{position_id}")
def modifier_position(position_id: int, corps: Any = Body(...), user: models.User = Depends(current_user),
                      db: DbSession = Depends(get_db)) -> dict:
    corps = _corps_objet(corps)
    position = _position_du_membre(db, user, position_id)
    champs_achat = {"ticker": _ticker, "quantity": _quantite, "buy_price": _prix_achat,
                    "buy_date": lambda v: _date(v, "d'achat")}
    champs_vente = {"sell_price": _prix_vente, "sell_date": lambda v: _date(v, "de vente")}
    inconnus = set(corps) - set(champs_achat) - set(champs_vente)
    if inconnus:
        raise HTTPException(status_code=422, detail="Champ non modifiable : " + sorted(inconnus)[0][:40] + ".")
    if not corps:
        raise HTTPException(status_code=422, detail="Aucun champ à modifier.")
    if position.sell_date is None and set(corps) & set(champs_vente):
        # Une vente passe par /close, qui renseigne les deux champs ensemble.
        raise HTTPException(status_code=422, detail="Pour vendre une position, utilise la clôture.")
    try:
        nouveaux = {cle: {**champs_achat, **champs_vente}[cle](valeur) for cle, valeur in corps.items()}
    except EntreeInvalide as e:
        raise _422(e)
    buy_date = nouveaux.get("buy_date", position.buy_date)
    sell_date = nouveaux.get("sell_date", position.sell_date)
    if sell_date is not None and sell_date < buy_date:
        raise HTTPException(status_code=422, detail="La date de vente ne peut pas précéder la date d'achat.")
    for cle, valeur in nouveaux.items():
        setattr(position, cle, valeur)
    db.commit()
    return serialiser(position)


@router.patch("/positions/{position_id}/close")
def cloturer_position(position_id: int, corps: Any = Body(...), user: models.User = Depends(current_user),
                      db: DbSession = Depends(get_db)) -> dict:
    corps = _corps_objet(corps)
    position = _position_du_membre(db, user, position_id)
    if position.sell_date is not None:
        raise HTTPException(status_code=409, detail="Cette position est déjà clôturée.")
    try:
        vente = valider_vente(corps, position.buy_date)
    except EntreeInvalide as e:
        raise _422(e)
    position.sell_price = vente["sell_price"]
    position.sell_date = vente["sell_date"]
    db.commit()
    return serialiser(position)


@router.delete("/positions/{position_id}", status_code=204)
def supprimer_position(position_id: int, user: models.User = Depends(current_user),
                       db: DbSession = Depends(get_db)) -> Response:
    position = _position_du_membre(db, user, position_id)
    db.delete(position)
    db.commit()
    return Response(status_code=204)


# --- Import des données du navigateur ---------------------------------------

def _cle_position(ticker: str, buy_date: date, quantity: float) -> tuple:
    # Doublon = même ticker, même date d'achat, même quantité (spec §7).
    return (ticker, buy_date, round(quantity, 8))


def _instant_utc(valeur: Any) -> datetime:
    """Date ISO de l'historique local (toISOString) -> datetime UTC sans fuseau."""
    if not isinstance(valeur, str) or not 10 <= len(valeur) <= 40:
        raise EntreeInvalide("Date invalide.")
    try:
        instant = datetime.fromisoformat(valeur)
    except ValueError:
        raise EntreeInvalide("Date invalide.")
    if instant.tzinfo is not None:
        instant = instant.astimezone(timezone.utc).replace(tzinfo=None)
    # Une marge d'un jour couvre une horloge d'appareil un peu en avance.
    if instant > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1):
        raise EntreeInvalide("Date dans le futur.")
    if instant.year < 2000:
        raise EntreeInvalide("Date trop ancienne.")
    return instant


def _naif_utc(instant: datetime) -> datetime:
    # SQLite rend des dates sans fuseau ; PostgreSQL les rend avec.
    if instant.tzinfo is not None:
        return instant.astimezone(timezone.utc).replace(tzinfo=None)
    return instant


def _texte(valeur: Any, longueur_max: int) -> str:
    if not isinstance(valeur, str) or not valeur.strip() or len(valeur) > longueur_max:
        raise EntreeInvalide("Texte invalide.")
    return valeur


def _importer_positions(db, user, ouvertes: list, cloturees: list, importees: dict, ignorees: dict) -> None:
    existantes = db.scalars(select(models.Position).where(models.Position.user_id == user.id)).all()
    vues = {_cle_position(p.ticker, p.buy_date, p.quantity) for p in existantes}
    place = POSITIONS_MAX - len(existantes)
    for nom, lot, cloturee in (("positions", ouvertes, False), ("positions_cloturees", cloturees, True)):
        for entree in lot:
            try:
                achat = valider_achat(entree)
                vente = valider_vente(entree, achat["buy_date"]) if cloturee else {}
            except EntreeInvalide:
                ignorees[nom] += 1
                continue
            cle = _cle_position(achat["ticker"], achat["buy_date"], achat["quantity"])
            if cle in vues or place <= 0:
                ignorees[nom] += 1
                continue
            vues.add(cle)
            place -= 1
            db.add(models.Position(user_id=user.id, **achat, **vente))
            importees[nom] += 1


def _importer_historique(db, user, lot: list, importees: dict, ignorees: dict) -> None:
    existants = db.scalars(select(models.AiExchange).where(models.AiExchange.user_id == user.id)).all()
    vus = {(e.question, _naif_utc(e.created_at)) for e in existants}
    nouveaux = []
    for entree in lot:
        try:
            if not isinstance(entree, dict):
                raise EntreeInvalide("Échange invalide.")
            question = _texte(entree.get("question"), QUESTION_MAX)
            reponse = _texte(entree.get("reponse"), REPONSE_MAX)
            instant = _instant_utc(entree.get("date"))
        except EntreeInvalide:
            ignorees["historique_ia"] += 1
            continue
        if (question, instant) in vus:
            ignorees["historique_ia"] += 1
            continue
        vus.add((question, instant))
        nouveaux.append(models.AiExchange(user_id=user.id, question=question, answer=reponse, created_at=instant))
    # On ne garde que les 100 échanges les plus récents, anciens et importés
    # confondus : un échange importé trop ancien n'entre pas (compté ignoré),
    # un ancien échange en base qui sort du lot est supprimé (même règle que
    # l'historique de l'assistant).
    tous = [(_naif_utc(e.created_at), e.id or 0, e, False) for e in existants]
    tous += [(e.created_at, 0, e, True) for e in nouveaux]
    tous.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for rang, (_, _, echange, nouveau) in enumerate(tous):
        garde = rang < HISTORIQUE_IA_MAX
        if nouveau and garde:
            db.add(echange)
            importees["historique_ia"] += 1
        elif nouveau:
            ignorees["historique_ia"] += 1
        elif not garde:
            db.delete(echange)


def _importer_reglages(db, user, recus: dict, importees: dict, ignorees: dict) -> None:
    # Fusion : les clés envoyées écrasent les existantes ; une clé inconnue,
    # invalide ou qui ferait dépasser 16 Ko est ignorée.
    reglages = lire_reglages(db, user.id)
    for cle, valeur in recus.items():
        try:
            valeur = valider_reglage(cle, valeur)
        except ReglageInvalide:
            ignorees["reglages"] += 1
            continue
        essai = {**reglages, cle: valeur}
        if taille_serialisee(essai) > TAILLE_MAX:
            ignorees["reglages"] += 1
            continue
        reglages = essai
        importees["reglages"] += 1
    if importees["reglages"]:
        ecrire_reglages(db, user.id, reglages)


@router.post("/import")
def importer(corps: Any = Body(...), user: models.User = Depends(current_user),
             db: DbSession = Depends(get_db)) -> dict:
    corps = _corps_objet(corps)

    def liste(nom):
        valeur = corps.get(nom)
        return valeur if isinstance(valeur, list) else []

    reglages = corps.get("reglages")
    importees = {"positions": 0, "positions_cloturees": 0, "historique_ia": 0, "reglages": 0}
    ignorees = dict(importees)
    _importer_positions(db, user, liste("positions"), liste("positions_cloturees"), importees, ignorees)
    _importer_historique(db, user, liste("historique_ia"), importees, ignorees)
    _importer_reglages(db, user, reglages if isinstance(reglages, dict) else {}, importees, ignorees)
    db.commit()
    return {"importees": importees, "ignorees": ignorees}
