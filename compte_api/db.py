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
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
