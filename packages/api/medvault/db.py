"""Database engine and session handling.

Everything here builds a *cache*. The tables this module creates hold nothing
that is not already in the vault, so dropping the database and re-running
`medvault reindex` is a supported, routine operation rather than a disaster
recovery procedure.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from medvault.config import get_settings

log = logging.getLogger(__name__)

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


# Filesystems where SQLite's write-ahead log cannot work. WAL coordinates
# readers and writers through a shared-memory `-shm` file, and there is no
# shared memory across a network mount — SQLite's own documentation states that
# every process using a WAL database must be on the same host.
_NETWORK_FILESYSTEMS = {
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "9p",
    "fuse.sshfs", "fuse.glusterfs", "ceph", "lustre", "gfs2", "ocfs2",
}


def filesystem_type(path: Path) -> str | None:
    """Return the filesystem type backing `path`, from /proc/mounts.

    Deliberately does not require `path` to exist. The database file is absent
    until the first write, and an earlier version of this walked up to the
    nearest existing ancestor — which lands on `/` and reports the root
    filesystem, exactly wrong for a database on a volume that is mounted but
    still empty. Matching a mount point is pure path comparison and needs no
    file on disk.
    """
    try:
        mounts = Path("/proc/mounts").read_text("utf-8").splitlines()
    except OSError:
        return None  # not Linux, or /proc is not mounted; fall back to defaults

    target = path.resolve()

    best_type: str | None = None
    best_len = -1
    for line in mounts:
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point, fs_type = fields[1], fields[2]
        # /proc/mounts octal-escapes spaces and similar in mount points.
        mount_point = mount_point.encode().decode("unicode_escape")
        try:
            resolved = Path(mount_point)
        except (ValueError, OSError):
            continue
        if (target == resolved or resolved in target.parents) and len(mount_point) > best_len:
            best_type, best_len = fs_type, len(mount_point)
    return best_type


def choose_journal_mode(database_path: str | None) -> str:
    """Pick a SQLite journal mode appropriate to where the file lives.

    Detected rather than configured because getting this wrong is silent until
    it is not: a WAL database on NFS fails at open time on some kernels and
    misbehaves under concurrency on others, and the cause is not obvious from
    the error.
    """
    configured = get_settings().sqlite_journal_mode
    if configured:
        return configured.upper()
    if database_path in (None, "", ":memory:"):
        return "MEMORY"

    fs_type = filesystem_type(Path(database_path))
    if fs_type is not None and fs_type.lower() in _NETWORK_FILESYSTEMS:
        log.info(
            "database is on a %s mount; using journal_mode=DELETE because "
            "write-ahead logging cannot work over a network filesystem",
            fs_type,
        )
        return "DELETE"
    return "WAL"


def _configure_sqlite(engine: Engine) -> None:
    """Pragmas SQLite needs to behave under a threaded web server.

    FastAPI runs synchronous handlers in a thread pool, so several threads can
    reach the same database at once. Left at its defaults SQLite answers that
    with `database is locked`, intermittently and under load, which is a
    miserable thing to debug.
    """
    journal_mode = choose_journal_mode(engine.url.database)

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        # Foreign keys are off unless asked for, which hides referential bugs.
        cursor.execute("PRAGMA foreign_keys=ON")
        if journal_mode != "MEMORY":
            cursor.execute(f"PRAGMA journal_mode={journal_mode}")
        # Wait for a competing writer rather than failing immediately. A full
        # reindex is the longest write there is, and it is measured in seconds.
        # The wait matters more on NFS, where a lock round trip is not free.
        cursor.execute("PRAGMA busy_timeout=30000")
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
