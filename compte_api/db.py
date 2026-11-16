# compte_api/db.py
# Moteur et sessions SQLAlchemy. SQLite en local et au lancement, PostgreSQL
# possible plus tard en changeant seulement DATABASE_URL.
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        # SQLite n'applique les clés étrangères (dont ON DELETE CASCADE,
        # utilisé par la suppression de compte) que si on le lui demande.
        @event.listens_for(engine, "connect")
        def _activer_cles_etrangeres(connexion, _):
            curseur = connexion.cursor()
            curseur.execute("PRAGMA foreign_keys=ON")
            curseur.close()

        return engine
    if url.startswith("postgresql"):
        # PostgreSQL rend les dates dans le fuseau de la connexion, celui de la
        # machine par défaut : on le fixe à UTC, comme partout dans le service.
        return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
