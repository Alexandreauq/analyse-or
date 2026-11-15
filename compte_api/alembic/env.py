# Migrations de la base du service compte. L'adresse de la base vient de
# DATABASE_URL (même réglage que le service), jamais d'alembic.ini.
#   cd compte_api && alembic upgrade head
from logging.config import fileConfig

from alembic import context

import compte_api.models  # noqa: F401 (enregistre les tables sur Base)
from compte_api.config import Settings
from compte_api.db import Base, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
DATABASE_URL = Settings.from_env().database_url


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(DATABASE_URL)
    with engine.connect() as connection:
        # render_as_batch : SQLite ne sait pas modifier une colonne en place.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
