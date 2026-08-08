from collections.abc import Generator
from typing import cast

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Settings._validate_tier1 already exits the process if database_url is
# unset, so by the time get_settings() returns, it is guaranteed non-None.
SQLALCHEMY_DATABASE_URL = cast("str", get_settings().database_url)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    pool_recycle=3600,
    # A restore/downsync (see backup_service.py's _wipe_public_schema())
    # deliberately pg_terminate_backend()s every other session on this
    # database right before it wipes the schema - including whatever of
    # this pool's own connections happen to be idle at that moment.
    # Without pre_ping, the pool hands one of those out on the next
    # checkout exactly as-is, and the first query against it fails with an
    # OperationalError (server closed the connection unexpectedly) -
    # observed in practice: this is what silently ate job_downsync()'s own
    # scheduled_task_runs completion write immediately after a restore.
    # pre_ping cheaply tests each connection with a lightweight round trip
    # before handing it out and transparently discards+reconnects on
    # failure - the standard fix for any out-of-band connection loss, not
    # just this one.
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
