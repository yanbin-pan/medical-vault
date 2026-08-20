"""Database engine and session handling.

Everything here builds a *cache*. The tables this module creates hold nothing
that is not already in the vault, so dropping the database and re-running
`medvault reindex` is a supported, routine operation rather than a disaster
recovery procedure.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from medvault.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # the Pi cluster drops idle connections
            pool_size=5,
            max_overflow=5,
            future=True,
        )
        if _engine.dialect.name == "sqlite":
            _configure_sqlite(_engine)
    return _engine


def _configure_sqlite(engine: Engine) -> None:
    """Pragmas SQLite needs to behave under a threaded web server.

    FastAPI runs synchronous handlers in a thread pool, so several threads can
    reach the same database at once. Left at its defaults SQLite answers that
    with `database is locked`, intermittently and under load, which is a
    miserable thing to debug.
    """

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        # Foreign keys are off unless asked for, which hides referential bugs.
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers carry on while a reindex writes. Skipped for
        # in-memory databases, where it does not apply.
        if engine.url.database not in (None, ":memory:"):
            cursor.execute("PRAGMA journal_mode=WAL")
        # Wait for a competing writer rather than failing immediately. A full
        # reindex is the longest write there is, and it is measured in seconds.
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope(tenant_id: str | None = None) -> Iterator[Session]:
    """A transaction, optionally scoped to one tenant for row-level security.

    Setting `app.tenant_id` is what the RLS policies read. It is applied with
    SET LOCAL so it dies with the transaction and cannot leak to the next
    request that borrows the same pooled connection.
    """
    session = get_session_factory()()
    try:
        if tenant_id is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Test hook: drop the memoised engine so a new database URL takes effect."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
