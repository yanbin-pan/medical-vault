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
            _enable_sqlite_foreign_keys(_engine)
    return _engine


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked, which hides referential bugs in tests."""

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
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
