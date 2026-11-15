# compte_api/routers/reglages.py
# Préférences du membre (table `settings`, colonne `json`) : thème, niveau
# de réponse de l'assistant IA, filtres sauvegardés de la liste des
# entreprises. GET renvoie l'objet, PUT le remplace en entier.
#
# Les fonctions de validation et de lecture/écriture sont aussi utilisées
# par l'import des données du navigateur (routers/portefeuille.py).
import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.deps import current_user, get_db

router = APIRouter()

# Taille maximale de l'objet sérialisé (octets UTF-8).
TAILLE_MAX = 16 * 1024
THEMES = ("dark", "light")
NIVEAUX_REPONSE = ("technique", "grand_public")
FILTRES_MAX = 50
NOM_FILTRE_MAX = 80


class ReglageInvalide(ValueError):
    """Valeur refusée ; le message est prêt à afficher."""


def _valider_filtres(valeur: Any) -> list:
    # Même forme que 'analyse-or-saved-screeners' côté site :
    # [{name, state}] avec state = objet des critères de la liste.
    if not isinstance(valeur, list):
        raise ReglageInvalide("Les filtres sauvegardés doivent être une liste.")
    if len(valeur) > FILTRES_MAX:
        raise ReglageInvalide(f"{FILTRES_MAX} filtres sauvegardés au maximum.")
    for filtre in valeur:
        if not isinstance(filtre, dict) or not isinstance(filtre.get("name"), str):
            raise ReglageInvalide("Chaque filtre sauvegardé doit avoir un nom.")
        nom = filtre["name"].strip()
        if not nom or len(nom) > NOM_FILTRE_MAX:
            raise ReglageInvalide(f"Le nom d'un filtre doit faire entre 1 et {NOM_FILTRE_MAX} caractères.")
        if "state" in filtre and not isinstance(filtre["state"], dict):
            raise ReglageInvalide("Les critères d'un filtre sauvegardé sont invalides.")
    return valeur


def _valider_theme(valeur: Any) -> str:
    if valeur not in THEMES:
        raise ReglageInvalide("Thème inconnu (attendu : dark ou light).")
    return valeur


def _valider_niveau(valeur: Any) -> str:
    if valeur not in NIVEAUX_REPONSE:
        raise ReglageInvalide("Niveau de réponse inconnu (attendu : technique ou grand_public).")
    return valeur


# Clés autorisées et leur validation.
VALIDATEURS = {
    "theme": _valider_theme,
    "niveau_reponse": _valider_niveau,
    "filtres_sauvegardes": _valider_filtres,
}


def valider_reglage(cle: str, valeur: Any) -> Any:
    """Renvoie la valeur validée ; lève ReglageInvalide sinon."""
    validateur = VALIDATEURS.get(cle)
    if validateur is None:
        raise ReglageInvalide(f"Réglage inconnu : {cle[:40]}.")
    return validateur(valeur)


def taille_serialisee(reglages: dict) -> int:
    return len(json.dumps(reglages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def lire_reglages(db: DbSession, user_id: int) -> dict:
    ligne = db.get(models.UserSettings, user_id)
    if ligne is None or not ligne.json:
        return {}
    try:
        donnees = json.loads(ligne.json)
    except ValueError:
        return {}
    return donnees if isinstance(donnees, dict) else {}


def ecrire_reglages(db: DbSession, user_id: int, reglages: dict) -> None:
    """Écrit l'objet (sans commit : l'appelant valide la transaction)."""
    texte = json.dumps(reglages, ensure_ascii=False, separators=(",", ":"))
    ligne = db.get(models.UserSettings, user_id)
    if ligne is None:
        db.add(models.UserSettings(user_id=user_id, json=texte))
    else:
        ligne.json = texte


@router.get("/settings")
def obtenir_reglages(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> dict:
    return lire_reglages(db, user.id)


@router.put("/settings")
def remplacer_reglages(corps: Any = Body(...), user: models.User = Depends(current_user),
                       db: DbSession = Depends(get_db)) -> dict:
    if not isinstance(corps, dict):
        raise HTTPException(status_code=422, detail="Les réglages doivent être un objet.")
    valides = {}
    for cle, valeur in corps.items():
        try:
            valides[cle] = valider_reglage(cle, valeur)
        except ReglageInvalide as e:
            raise HTTPException(status_code=422, detail=str(e))
    if taille_serialisee(valides) > TAILLE_MAX:
        raise HTTPException(status_code=422, detail="Réglages trop volumineux (16 Ko maximum).")
    ecrire_reglages(db, user.id, valides)
    db.commit()
    return valides
