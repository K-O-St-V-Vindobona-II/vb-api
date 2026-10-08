from logging.config import fileConfig
from typing import cast

from sqlalchemy import create_engine, pool, text

# Importing the module registers every model on Base.metadata.
import app.db.base
from alembic import context
from app.core.config import get_settings
from app.db.database import Base
from app.db.migration_lock import MIGRATION_LOCK_KEY

config = context.config

if config.config_file_name is not None:
    # This file also runs in-process (the test session, tools that call
    # alembic.command): fileConfig() disables every logger that already exists
    # unless told otherwise.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# The URL goes to SQLAlchemy as a string and never through the ini parser: a
# percent-encoded credential such as "p%40ss" is invalid interpolation syntax
# there and would stop every migration. Settings has already ended the
# process if the variable is missing.
DATABASE_URL = cast("str", get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            # Held until this transaction ends: a concurrent run waits here.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
