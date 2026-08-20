"""Where the database file lives changes how SQLite must be told to behave.

The failure this guards against is silent. A write-ahead log needs shared
memory, and there is none across a network mount, so a WAL database on NFS
fails at open time on some kernels and misbehaves under concurrency on others.
Neither symptom points at the cause.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from medvault import config, db


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.delenv("MEDVAULT_SQLITE_JOURNAL_MODE", raising=False)
    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


def _fake_mounts(monkeypatch, table: str):
    """Stand in for /proc/mounts so the filesystem type can be varied."""
    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == "/proc/mounts":
            return table
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


LOCAL = "/dev/sda1 / ext4 rw,relatime 0 0\n"
NFS = (
    "/dev/sda1 / ext4 rw,relatime 0 0\n"
    "192.168.1.188:/mnt/ssd/nfs/k8s /cache nfs4 rw,relatime,vers=4.2 0 0\n"
)


def test_wal_on_a_local_disk(monkeypatch, tmp_path):
    _fake_mounts(monkeypatch, LOCAL)
    assert db.choose_journal_mode(str(tmp_path / "medvault.db")) == "WAL"


def test_rollback_journal_on_nfs(monkeypatch):
    """The whole point of the detection."""
    _fake_mounts(monkeypatch, NFS)
    assert db.choose_journal_mode("/cache/medvault.db") == "DELETE"


def test_the_longest_matching_mount_wins(monkeypatch):
    """/cache is nested inside /, and the nested mount is the one that counts."""
    _fake_mounts(monkeypatch, NFS)
    assert db.filesystem_type(Path("/cache/medvault.db")) == "nfs4"
    assert db.filesystem_type(Path("/etc/hostname")) == "ext4"


@pytest.mark.parametrize("fs_type", ["nfs", "nfs4", "cifs", "smb3", "ceph", "9p"])
def test_every_network_filesystem_drops_wal(monkeypatch, fs_type: str):
    _fake_mounts(monkeypatch, f"/dev/sda1 / ext4 rw 0 0\nserver:/x /cache {fs_type} rw 0 0\n")
    assert db.choose_journal_mode("/cache/medvault.db") == "DELETE"


def test_an_explicit_setting_overrides_detection(monkeypatch):
    _fake_mounts(monkeypatch, NFS)
    monkeypatch.setenv("MEDVAULT_SQLITE_JOURNAL_MODE", "wal")
    config.reset_settings_cache()
    assert db.choose_journal_mode("/cache/medvault.db") == "WAL"


def test_in_memory_databases_need_no_journal():
    assert db.choose_journal_mode(":memory:") == "MEMORY"
    assert db.choose_journal_mode(None) == "MEMORY"


def test_detection_survives_a_missing_proc_mounts(monkeypatch, tmp_path):
    """Not every platform has /proc. Falling back beats crashing at startup."""

    def explode(self, *args, **kwargs):
        raise OSError("no /proc here")

    monkeypatch.setattr(Path, "read_text", explode)
    assert db.filesystem_type(tmp_path / "x.db") is None
    assert db.choose_journal_mode(str(tmp_path / "x.db")) == "WAL"


def test_a_path_that_does_not_exist_yet_is_still_classified(monkeypatch):
    """The database file is absent until the first write; the mount is not."""
    _fake_mounts(monkeypatch, NFS)
    assert db.choose_journal_mode("/cache/subdir/not-created-yet.db") == "DELETE"
