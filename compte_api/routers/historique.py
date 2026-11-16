# compte_api/routers/historique.py
# Historique des recherches, historique des échanges avec l'assistant IA et
# relais POST /ai/ask vers assistant_ia (quota quotidien, flux SSE transmis
# tel quel, échange enregistré à la fin du flux).
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from compte_api import models
from compte_api.config import Settings
from compte_api.deps import current_user, current_user_verified, get_db, get_settings
from compte_api.quota import consommer_quota, quota_restant, rendre_quota
from compte_api.relais_ia import ErreurAssistant, Relais, RelaisJson, relais_httpx, relais_json_httpx

router = APIRouter()
journal = logging.getLogger("compte_api.historique")

MAX_RECHERCHES = 100
MAX_ECHANGES = 100
# Mêmes limites que assistant_ia/api.py (MAX_QUESTION_CHARS,
# MAX_HISTORY_MESSAGES) : une question refusée là-bas l'est ici avant de
# consommer le quota.
MAX_QUESTION_CHARS = 500
MAX_HISTORY_MESSAGES = 12
# Garde-fous de taille propres au relais (l'assistant n'en a pas) : un
# membre ne fait pas transiter un corps démesuré par le service.
MAX_CONTENU_HISTORIQUE = 20000
MAX_POSITIONS_MANUELLES = 500
MAX_CONTEXTE = 200


def _iso(dt: datetime) -> str:
    # SQLite rend des dates sans fuseau même quand on les écrit en UTC.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# --- Recherches ---------------------------------------------------------

class RechercheEntree(BaseModel):
    query: str

    @field_validator("query")
    @classmethod
    def _rogner(cls, valeur: str) -> str:
        valeur = valeur.strip()
        if not 1 <= len(valeur) <= 200:
            raise ValueError("La recherche doit faire entre 1 et 200 caractères.")
        return valeur


def _recherche(r: models.Search) -> dict:
    return {"id": r.id, "query": r.query, "created_at": _iso(r.created_at)}


@router.get("/searches")
def lister_recherches(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> list[dict]:
    lignes = db.scalars(select(models.Search).where(models.Search.user_id == user.id)
                        .order_by(models.Search.created_at.desc(), models.Search.id.desc())
                        .limit(MAX_RECHERCHES))
    return [_recherche(r) for r in lignes]


@router.post("/searches", status_code=201)
def ajouter_recherche(entree: RechercheEntree, response: Response,
                      user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> dict:
    derniere = db.scalar(select(models.Search).where(models.Search.user_id == user.id)
                         .order_by(models.Search.id.desc()).limit(1))
    if derniere is not None and derniere.query == entree.query:
        # Même recherche relancée : pas de doublon en tête de liste.
        response.status_code = 200
        return _recherche(derniere)
    recherche = models.Search(user_id=user.id, query=entree.query)
    db.add(recherche)
    db.flush()
    _purger(db, models.Search, user.id, MAX_RECHERCHES)
    db.commit()
    return _recherche(recherche)


@router.delete("/searches", status_code=204)
def effacer_recherches(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> Response:
    db.execute(delete(models.Search).where(models.Search.user_id == user.id))
    db.commit()
    return Response(status_code=204)


@router.delete("/searches/{recherche_id}", status_code=204)
def effacer_recherche(recherche_id: int, user: models.User = Depends(current_user),
                      db: DbSession = Depends(get_db)) -> Response:
    _supprimer_un(db, models.Search, recherche_id, user.id, "Recherche introuvable.")
    return Response(status_code=204)


# --- Historique des échanges IA -----------------------------------------

def _echange(e: models.AiExchange) -> dict:
    return {"id": e.id, "question": e.question, "answer": e.answer, "created_at": _iso(e.created_at)}


@router.get("/ai/history")
def lister_echanges(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> list[dict]:
    lignes = db.scalars(select(models.AiExchange).where(models.AiExchange.user_id == user.id)
                        .order_by(models.AiExchange.created_at.desc(), models.AiExchange.id.desc())
                        .limit(MAX_ECHANGES))
    return [_echange(e) for e in lignes]


@router.delete("/ai/history", status_code=204)
def effacer_echanges(user: models.User = Depends(current_user), db: DbSession = Depends(get_db)) -> Response:
    db.execute(delete(models.AiExchange).where(models.AiExchange.user_id == user.id))
    db.commit()
    return Response(status_code=204)


@router.delete("/ai/history/{echange_id}", status_code=204)
def effacer_echange(echange_id: int, user: models.User = Depends(current_user),
                    db: DbSession = Depends(get_db)) -> Response:
    _supprimer_un(db, models.AiExchange, echange_id, user.id, "Échange introuvable.")
    return Response(status_code=204)


def _supprimer_un(db: DbSession, modele, ligne_id: int, user_id: int, introuvable: str) -> None:
    # La ligne d'un autre membre répond comme une ligne absente (404) : on ne
    # révèle pas qu'elle existe.
    ligne = db.get(modele, ligne_id)
    if ligne is None or ligne.user_id != user_id:
        raise HTTPException(status_code=404, detail=introuvable)
    db.delete(ligne)
    db.commit()


def _purger(db: DbSession, modele, user_id: int, garder: int) -> None:
    """Ne garde que les `garder` lignes les plus récentes du membre."""
    a_garder = (select(modele.id).where(modele.user_id == user_id)
                .order_by(modele.created_at.desc(), modele.id.desc()).limit(garder))
    db.execute(delete(modele).where(modele.user_id == user_id, modele.id.not_in(a_garder)))


# --- Relais vers l'assistant --------------------------------------------

class MessageHistorique(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CONTENU_HISTORIQUE)


class QuestionIA(BaseModel):
    question: str
    history: list[MessageHistorique] | None = None
    manual_positions: list[dict[str, Any]] | None = Field(default=None, max_length=MAX_POSITIONS_MANUELLES)
    niveau: str | None = Field(default=None, max_length=32)
    contexte: str | None = Field(default=None, max_length=MAX_CONTEXTE)

    @field_validator("question")
    @classmethod
    def _question(cls, valeur: str) -> str:
        if not valeur.strip():
            raise ValueError("Question vide.")
        if len(valeur) > MAX_QUESTION_CHARS:
            raise ValueError(f"Question trop longue ({MAX_QUESTION_CHARS} caractères maximum).")
        return valeur

    @field_validator("history")
    @classmethod
    def _tronquer(cls, valeur):
        # Comme l'assistant : seuls les derniers messages comptent.
        return valeur[-MAX_HISTORY_MESSAGES:] if valeur else valeur


def get_relais() -> Relais:
    """Relais HTTP vers l'assistant ; remplacé par un faux dans les tests."""
    return relais_httpx


def get_relais_portefeuille() -> RelaisJson:
    """Relais JSON vers /portfolio-analysis* ; remplacé par un faux dans les tests."""
    return relais_json_httpx


def _en_tetes_quota(restant: int | None) -> dict:
    # Pas d'en-tête pour l'administrateur (quota illimité).
    return {} if restant is None else {"X-Quota-Restant": str(restant)}


def _erreur(statut: int, detail: str, restant: int | None) -> JSONResponse:
    corps = {"detail": detail}
    if restant is not None:
        corps["quota_restant"] = restant
    return JSONResponse(corps, status_code=statut, headers=_en_tetes_quota(restant))


def _evenements(bloc: bytes):
    """Événements JSON d'un bloc SSE (lignes `data: {...}`)."""
    for ligne in bloc.decode("utf-8", "replace").splitlines():
        if ligne.startswith("data:"):
            try:
                evenement = json.loads(ligne[5:].strip())
            except ValueError:
                continue
            if isinstance(evenement, dict):
                yield evenement


def _sse(evenement: dict) -> bytes:
    return f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n".encode("utf-8")


@router.post("/ai/ask")
async def demander_ia(entree: QuestionIA, request: Request,
                      user: models.User = Depends(current_user_verified),
                      db: DbSession = Depends(get_db),
                      settings: Settings = Depends(get_settings),
                      relais: Relais = Depends(get_relais)):
    if not settings.assistant_ia_url:
        return JSONResponse({"detail": "Assistant indisponible."}, status_code=503)

    # Le quota est pris AVANT l'appel (spec §8) et rendu si l'assistant
    # n'a finalement rien répondu.
    if not consommer_quota(db, user, settings):
        return _erreur(429, f"Tu as utilisé tes {settings.ai_quota_par_jour} questions du jour. Reviens demain !", 0)
    restant = quota_restant(db, user, settings)

    try:
        flux = await relais(settings.assistant_ia_url, settings.assistant_ia_token, user.id,
                            entree.model_dump(exclude_none=True))
    except ErreurAssistant as e:
        rendre_quota(db, user)
        return _erreur(e.statut, e.detail, quota_restant(db, user, settings))
    except Exception:
        journal.exception("Relais IA : échec inattendu avant le flux (membre %s)", user.id)
        rendre_quota(db, user)
        return _erreur(502, "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant.",
                       quota_restant(db, user, settings))

    # La session de la requête n'est pas garantie ouverte pendant le flux :
    # le générateur ouvre la sienne pour ses écritures de fin.
    ouvrir_session = request.app.state.sessionmaker
    question = entree.question

    async def transmettre():
        # Texte de la réponse reconstitué au fil des événements, comme le
        # fait le site (concaténation des événements « texte »).
        etat = {"texte": [], "erreur": False}
        termine = False

        def lire(bloc: bytes) -> None:
            for evenement in _evenements(bloc):
                if evenement.get("type") == "texte" and isinstance(evenement.get("texte"), str):
                    etat["texte"].append(evenement["texte"])
                elif evenement.get("type") == "erreur":
                    etat["erreur"] = True

        tampon = b""
        try:
            async for morceau in flux.morceaux:
                yield morceau  # transmis tel quel, sans attendre la fin
                tampon += morceau
                *blocs, tampon = tampon.split(b"\n\n")
                for bloc in blocs:
                    lire(bloc)
            lire(tampon)
            termine = True
        except Exception:
            # Coupure en cours de flux : le membre reçoit une erreur dans le
            # même format que celles de l'assistant.
            journal.warning("Relais IA : flux interrompu (membre %s)", user.id)
            etat["erreur"] = True
            yield _sse({"type": "erreur", "detail": "la connexion avec l'assistant a été interrompue"})
            yield _sse({"type": "fin"})
        finally:
            await flux.fermer()
            reponse = "".join(etat["texte"])
            with ouvrir_session() as db_flux:
                if not reponse:
                    # Rien n'a été répondu : la question n'est pas décomptée.
                    rendre_quota(db_flux, user)
                elif termine and not etat["erreur"]:
                    db_flux.add(models.AiExchange(user_id=user.id, question=question, answer=reponse))
                    db_flux.flush()
                    _purger(db_flux, models.AiExchange, user.id, MAX_ECHANGES)
                    db_flux.commit()

    return StreamingResponse(transmettre(), media_type="text/event-stream",
                             headers={**_en_tetes_quota(restant), "Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --- Relais vers l'analyse de portefeuille ------------------------------
# Même assistant, route différente (/portfolio-analysis, pas de flux SSE,
# pas de quota quotidien -- déjà limité côté assistant par IP, voir
# PORTFOLIO_RATE_MAX_PAR_MINUTE dans assistant_ia/api.py). Le jeton de
# service est déjà accepté par ces routes (_check_token côté assistant).

def _erreur_simple(statut: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=statut)


@router.post("/ai/portfolio-analysis")
async def analyser_portefeuille(request: Request,
                                user: models.User = Depends(current_user_verified),
                                settings: Settings = Depends(get_settings),
                                relais: RelaisJson = Depends(get_relais_portefeuille)):
    if not settings.assistant_ia_url:
        return _erreur_simple(503, "Assistant indisponible.")
    payload = await request.json()
    try:
        resultat = await relais("POST", "/portfolio-analysis", settings.assistant_ia_url,
                                settings.assistant_ia_token, payload)
    except ErreurAssistant as e:
        return _erreur_simple(e.statut, e.detail)
    except Exception:
        journal.exception("Relais analyse portefeuille : échec inattendu (membre %s)", user.id)
        return _erreur_simple(502, "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant.")
    return JSONResponse(resultat)


@router.get("/ai/portfolio-analysis/last")
async def derniere_analyse_portefeuille(user: models.User = Depends(current_user_verified),
                                        settings: Settings = Depends(get_settings),
                                        relais: RelaisJson = Depends(get_relais_portefeuille)):
    if not settings.assistant_ia_url:
        return _erreur_simple(503, "Assistant indisponible.")
    try:
        resultat = await relais("GET", "/portfolio-analysis/last", settings.assistant_ia_url,
                                settings.assistant_ia_token, None)
    except ErreurAssistant as e:
        return _erreur_simple(e.statut, e.detail)
    except Exception:
        journal.exception("Relais dernière analyse portefeuille : échec inattendu (membre %s)", user.id)
        return _erreur_simple(502, "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant.")
    return JSONResponse(resultat)


@router.get("/ai/portfolio-analysis/history")
async def historique_sante_portefeuille(user: models.User = Depends(current_user_verified),
                                        settings: Settings = Depends(get_settings),
                                        relais: RelaisJson = Depends(get_relais_portefeuille)):
    if not settings.assistant_ia_url:
        return _erreur_simple(503, "Assistant indisponible.")
    try:
        resultat = await relais("GET", "/portfolio-analysis/history", settings.assistant_ia_url,
                                settings.assistant_ia_token, None)
    except ErreurAssistant as e:
        return _erreur_simple(e.statut, e.detail)
    except Exception:
        journal.exception("Relais historique portefeuille : échec inattendu (membre %s)", user.id)
        return _erreur_simple(502, "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant.")
    return JSONResponse(resultat)
