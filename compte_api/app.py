# compte_api/app.py
# Application FastAPI du service compte. Lancement :
#   uvicorn compte_api.app:app --port 8447
# Chaque fichier de compte_api/routers/ qui expose un objet `router` est
# inclus automatiquement : une nouvelle fonctionnalité n'a pas à modifier ce
# fichier (plusieurs tâches peuvent ainsi avancer en parallèle).
import importlib
import pkgutil

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import compte_api.routers as routers_pkg
from compte_api.config import Settings
from compte_api.db import Base, make_engine, make_sessionmaker
from compte_api.mailer import Mailer, build_mailer
from compte_api.security import egal_temps_constant

METHODES_MUTATION = {"POST", "PUT", "PATCH", "DELETE"}
EN_TETE_PROXY = "x-compte-proxy"
EN_TETE_CSRF = "x-requested-with"
VALEUR_CSRF = "compte"
# Routes POST appelées par un formulaire HTML (pas d'en-tête possible) :
# elles se protègent elles-mêmes par un jeton signé dans l'adresse.
SANS_CSRF = {"/alerts/unsubscribe"}


def _routeurs():
    for module in sorted(pkgutil.iter_modules(routers_pkg.__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"{routers_pkg.__name__}.{module.name}")
        router = getattr(mod, "router", None)
        if router is not None:
            yield router


def create_app(settings: Settings | None = None, mailer: Mailer | None = None,
               creer_tables: bool = False) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.verifier_production()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    engine = make_engine(settings.database_url)
    if creer_tables:
        import compte_api.models  # noqa: F401 (enregistre les tables sur Base)
        Base.metadata.create_all(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.mailer = mailer or build_mailer(settings)

    @app.middleware("http")
    async def _garde(request: Request, call_next):
        # Seul le proxy (rewrite Vercel, ou scripts/dev_compte.py en local)
        # connaît ce secret : un appel direct au VPS est refusé.
        recu = request.headers.get(EN_TETE_PROXY, "")
        if not recu or not egal_temps_constant(recu, settings.proxy_secret):
            return JSONResponse({"detail": "Accès refusé."}, status_code=403)
        # Protection CSRF : un formulaire d'un autre site ne peut pas poser
        # cet en-tête sans déclencher une requête préalable que le
        # navigateur refuserait.
        if (request.method in METHODES_MUTATION and request.url.path not in SANS_CSRF
                and request.headers.get(EN_TETE_CSRF) != VALEUR_CSRF):
            return JSONResponse({"detail": "Requête refusée."}, status_code=403)
        response = await call_next(request)
        # Réponses personnelles : jamais gardées par le CDN de Vercel ni par
        # le navigateur, sauf si la route a choisi autre chose.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        erreurs = [{"champ": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg", "")}
                   for e in exc.errors()]
        return JSONResponse({"detail": "Données invalides.", "erreurs": erreurs}, status_code=422)

    for router in _routeurs():
        app.include_router(router)
    return app


app = create_app()
