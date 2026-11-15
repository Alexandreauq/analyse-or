# compte_api/relais_ia.py
# Appel HTTP vers l'assistant IA (assistant_ia, route POST /ask), isolé ici
# pour que les tests le remplacent par un faux relais : aucun vrai réseau
# dans les tests. Le routeur obtient le relais par la dépendance
# `get_relais` (remplaçable via app.dependency_overrides).
#
# Contrat d'un relais : `await relais(url, jeton, user_id, payload)` renvoie
# un FluxAssistant prêt à lire, ou lève ErreurAssistant AVANT tout contenu
# (statut HTTP d'erreur de l'assistant, assistant injoignable).
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import httpx

# Lecture longue : une réponse avec recherche web peut prendre plusieurs
# minutes ; connexion courte : un assistant arrêté doit échouer vite.
_DELAIS = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

MESSAGE_INJOIGNABLE = "Je n'ai pas pu contacter l'assistant, réessaie dans un instant."
MESSAGE_ERREUR = "L'assistant ne peut pas répondre pour le moment, réessaie dans un instant."


class ErreurAssistant(Exception):
    """Échec avant le moindre contenu : `statut` et `detail` sont ceux à
    renvoyer au membre."""

    def __init__(self, statut: int, detail: str):
        super().__init__(detail)
        self.statut = statut
        self.detail = detail


@dataclass
class FluxAssistant:
    """Flux SSE ouvert : `morceaux` donne les octets bruts dans l'ordre,
    `fermer` libère la connexion (appelé en fin de flux comme en abandon)."""
    morceaux: AsyncIterator[bytes]
    fermer: Callable[[], Awaitable[None]]


Relais = Callable[[str, str, int, dict], Awaitable[FluxAssistant]]


def _detail(corps: bytes) -> str | None:
    try:
        detail = json.loads(corps.decode("utf-8")).get("detail")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return None
    return detail if isinstance(detail, str) and detail.strip() else None


async def relais_httpx(url: str, jeton: str, user_id: int, payload: dict,
                       transport: httpx.AsyncBaseTransport | None = None) -> FluxAssistant:
    """Relais réel : POST {url}/ask avec le jeton de service et l'identifiant
    du membre (X-Compte-User), qui sert de clé de limitation côté assistant.
    `transport` ne sert qu'aux tests (httpx.MockTransport)."""
    client = httpx.AsyncClient(timeout=_DELAIS, transport=transport)
    requete = client.build_request(
        "POST", url.rstrip("/") + "/ask", json=payload,
        headers={"X-Bot-Token": jeton, "X-Compte-User": str(user_id), "Accept": "text/event-stream"},
    )
    try:
        reponse = await client.send(requete, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise ErreurAssistant(503, MESSAGE_INJOIGNABLE)

    if reponse.status_code != 200:
        try:
            corps = await reponse.aread()
        except httpx.HTTPError:
            corps = b""
        finally:
            await reponse.aclose()
            await client.aclose()
        # 400 (question refusée), 429 (plafond global, fréquence) et 503
        # (assistant saturé) ont un message destiné au visiteur : il est
        # transmis tel quel. Le reste (401 = jeton de service mal réglé...)
        # ne regarde pas le membre.
        if reponse.status_code in (400, 429, 503):
            raise ErreurAssistant(reponse.status_code, _detail(corps) or MESSAGE_ERREUR)
        raise ErreurAssistant(502, MESSAGE_ERREUR)

    async def fermer() -> None:
        await reponse.aclose()
        await client.aclose()

    return FluxAssistant(morceaux=reponse.aiter_raw(), fermer=fermer)
