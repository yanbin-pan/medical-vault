"""Reading and writing the append-only vault.

The vault is the product. This module is deliberately small, dependency-light
and free of any database import, because everything it writes has to remain
readable long after the rest of this codebase is gone.

Durability rules enforced here:

* Writes are atomic. Content goes to a temporary file, is fsync'd, then
  renamed into place. A power cut during an upload leaves either nothing or a
  complete document, never a half-written envelope.
* Existing files are never overwritten. Correcting a document means writing a
  new one that `supersedes` it.
* The schemas a record was written against are copied into the vault, so the
  contract travels with the data.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from medvault.ids import new_ulid

# Package-relative, NOT repository-relative. These files have to be present in
# an installed wheel, because every vault gets a copy of the schemas it was
# written against. Resolving them from the source tree worked in development and
# silently wrote no schema/ directory at all in the container.
SCHEMA_SOURCE = Path(__file__).resolve().parent / "schemas"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

ENVELOPE_FILENAME = "envelope.json"
OBSERVATIONS_FILENAME = "observations.ndjson"
SUBJECT_FILENAME = "subject.json"
TENANT_FILENAME = "tenant.json"


class VaultError(RuntimeError):
    pass


@dataclass(slots=True)
class VaultDocument:
    """An envelope plus the observations stored beside it."""

    envelope: dict[str, Any]
    observations: list[dict[str, Any]] = field(default_factory=list)
    directory: Path | None = None

    @property
    def document_id(self) -> str:
        return self.envelope["document_id"]

    @property
    def tenant_id(self) -> str:
        return self.envelope["tenant_id"]

    @property
    def original_path(self) -> Path | None:
        if self.directory is None:
            return None
        filename = self.envelope.get("source", {}).get("filename")
        return self.directory / filename if filename else None


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` to `path` atomically, refusing to clobber an existing file."""
    if path.exists():
        raise VaultError(f"refusing to overwrite existing vault file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        # fsync the directory too, or the rename itself may not survive a crash.
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _canonical_json(obj: Any) -> bytes:
    """Stable, human-readable JSON.

    `sort_keys` keeps diffs meaningful across versions, and `ensure_ascii=False`
    keeps 上海电力医院 legible in the file rather than as \\u escapes — the vault
    is meant to be read by people.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2).encode("utf-8")


def _ndjson(rows: list[dict[str, Any]]) -> bytes:
    lines = [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


class Vault:
    """A directory of medical records."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # -- layout ---------------------------------------------------------------

    def tenant_dir(self, tenant_id: str) -> Path:
        _guard_id(tenant_id)
        return self.root / "tenants" / tenant_id

    def subject_dir(self, tenant_id: str, subject_id: str) -> Path:
        _guard_id(subject_id)
        return self.tenant_dir(tenant_id) / "subjects" / subject_id

    def document_dir(self, tenant_id: str, document_id: str, captured_at: str) -> Path:
        """Documents are bucketed by the month they were captured.

        Bucketing keeps any one directory small enough for a filesystem and for
        a human to browse. The captured date is used rather than the upload
        date so a scan of a 2019 report files itself under 2019.
        """
        year, month = _year_month(captured_at)
        return self.tenant_dir(tenant_id) / "documents" / year / month / document_id

    # -- initialisation -------------------------------------------------------

    def initialise(self) -> None:
        """Create the vault root and copy in the self-describing files.

        Safe to call repeatedly; it only writes what is missing.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = self.root / "MANIFEST.md"
        if not manifest.exists():
            _atomic_write_bytes(manifest, (TEMPLATE_DIR / "MANIFEST.md").read_bytes())
        schema_dir = self.root / "schema"
        schema_dir.mkdir(parents=True, exist_ok=True)
        if SCHEMA_SOURCE.is_dir():
            for schema in sorted(SCHEMA_SOURCE.glob("*.json")):
                target = schema_dir / schema.name
                if not target.exists():
                    _atomic_write_bytes(target, schema.read_bytes())
        self._snapshot_catalog()

    def _snapshot_catalog(self) -> None:
        """Copy the analyte catalogue into the vault, one file per version.

        Observations store the label that was printed, never a code -- codes are
        derived, so that they can be improved later. The consequence is that the
        raw files alone do not say what 白细胞计数 means. Keeping the catalogue
        beside them closes that gap: a future reader gets the ground truth *and*
        the interpretation needed to rebuild the coded view, with no dependency
        on this application.

        Versions accumulate rather than overwrite, so a record can be
        re-interpreted with the catalogue that was current when it was filed.
        """
        try:
            from medvault.catalog.registry import CATALOG_PATH, get_catalog

            version = get_catalog().version
        except Exception:  # a broken catalogue must not stop a document being stored
            return
        if not CATALOG_PATH.is_file():
            return
        catalog_dir = self.root / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        target = catalog_dir / f"analytes.v{version}.yaml"
        if not target.exists():
            _atomic_write_bytes(target, CATALOG_PATH.read_bytes())

    def ensure_tenant(
        self,
        tenant_id: str,
        display_name: str | None = None,
        members: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Create a tenant record if it does not exist yet."""
        directory = self.tenant_dir(tenant_id)
        directory.mkdir(parents=True, exist_ok=True)
        record = directory / TENANT_FILENAME
        if not record.exists():
            _atomic_write_bytes(
                record,
                _canonical_json(
                    {
                        "schema_version": 1,
                        "tenant_id": tenant_id,
                        "display_name": display_name or tenant_id,
                        "created_at": _utcnow_iso(),
                        "members": members or [],
                    }
                ),
            )
        return directory

    def set_members(self, tenant_id: str, members: list[dict[str, Any]]) -> Path:
        """Replace a tenant's membership list.

        Membership lives in the vault rather than only in the database because
        it answers "who may read these records", and that question has to
        survive a database rebuild along with the records themselves.
        """
        self.ensure_tenant(tenant_id)
        record = self.tenant_dir(tenant_id) / TENANT_FILENAME
        tenant = json.loads(record.read_text("utf-8"))
        tenant["members"] = members
        payload = _canonical_json(tenant)
        record.unlink()
        _atomic_write_bytes(record, payload)
        return record

    def write_subject(self, subject: dict[str, Any]) -> Path:
        """Write or replace a subject record.

        Subjects are the one exception to immutability: they hold identity, not
        observations, and a corrected birth date should not require a
        superseding chain. The previous version is kept beside it.
        """
        tenant_id = subject["tenant_id"]
        subject_id = subject["subject_id"]
        directory = self.subject_dir(tenant_id, subject_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / SUBJECT_FILENAME
        payload = _canonical_json({"schema_version": 1, **subject})
        if path.exists():
            if path.read_bytes() == payload:
                return path
            archive = directory / f"subject.{_utcnow_iso().replace(':', '')}.json"
            shutil.copy2(path, archive)
            path.unlink()
        _atomic_write_bytes(path, payload)
        return path

    # -- writing --------------------------------------------------------------

    def write_document(
        self,
        envelope: dict[str, Any],
        original_bytes: bytes,
        observations: list[dict[str, Any]] | None = None,
    ) -> VaultDocument:
        """Persist one document and its observations. Never overwrites."""
        envelope = dict(envelope)
        envelope.setdefault("schema_version", 1)
        envelope.setdefault("document_id", new_ulid())
        envelope.setdefault("recorded_at", _utcnow_iso())
        envelope.setdefault("captured_at", envelope["recorded_at"])

        for required in ("tenant_id", "subject_id"):
            if not envelope.get(required):
                raise VaultError(f"envelope is missing {required}")

        source = dict(envelope.get("source") or {})
        digest = hashlib.sha256(original_bytes).hexdigest()
        if source.get("sha256") and source["sha256"] != digest:
            raise VaultError("envelope sha256 does not match the bytes supplied")
        source["sha256"] = digest
        source["byte_size"] = len(original_bytes)
        source.setdefault("media_type", "application/octet-stream")
        source.setdefault("filename", "original" + _extension_for(source["media_type"]))
        envelope["source"] = source

        directory = self.document_dir(
            envelope["tenant_id"], envelope["document_id"], envelope["captured_at"]
        )
        if directory.exists():
            raise VaultError(f"document directory already exists: {directory}")
        directory.mkdir(parents=True, exist_ok=True)

        rows = [self._prepare_observation(o, envelope) for o in (observations or [])]

        # Original first: if anything fails afterwards the irreplaceable bytes
        # are already safe on disk. Everything else can be recomputed.
        _atomic_write_bytes(directory / source["filename"], original_bytes)
        _atomic_write_bytes(directory / OBSERVATIONS_FILENAME, _ndjson(rows))
        _atomic_write_bytes(directory / ENVELOPE_FILENAME, _canonical_json(envelope))

        return VaultDocument(envelope=envelope, observations=rows, directory=directory)

    def _prepare_observation(
        self, observation: dict[str, Any], envelope: dict[str, Any]
    ) -> dict[str, Any]:
        row = dict(observation)
        row["schema_version"] = row.get("schema_version", 1)
        # A fresh id every time, even if the caller supplied one. An observation
        # belongs to exactly one document, so the readings of a *new* document
        # are new records. Honouring an incoming id lets a correction — which is
        # naturally built by copying the original's rows and editing one value —
        # carry the originals' ids into a second document, and two documents
        # then claim the same observations.
        row["observation_id"] = new_ulid()
        row["document_id"] = envelope["document_id"]
        row["tenant_id"] = envelope["tenant_id"]
        row["subject_id"] = envelope["subject_id"]
        row.setdefault("effective_time", envelope["captured_at"])
        if not row.get("label_raw"):
            raise VaultError("observation is missing label_raw, which may never be empty")
        return row

    def supersede(
        self,
        original: VaultDocument,
        envelope_updates: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> VaultDocument:
        """Record a correction as a new document pointing back at the old one."""
        if original.directory is None:
            raise VaultError("cannot supersede a document that is not on disk")
        original_path = original.original_path
        if original_path is None or not original_path.exists():
            raise VaultError("original file is missing; refusing to write a correction")

        envelope = {**original.envelope, **envelope_updates}
        envelope["document_id"] = new_ulid()
        envelope["supersedes"] = original.document_id
        envelope["recorded_at"] = _utcnow_iso()
        envelope.pop("source", None)
        envelope["source"] = {
            "media_type": original.envelope["source"]["media_type"],
            "filename": original.envelope["source"]["filename"],
        }
        return self.write_document(envelope, original_path.read_bytes(), observations)

    # -- reading --------------------------------------------------------------

    def read_document(self, directory: Path) -> VaultDocument:
        envelope = json.loads((directory / ENVELOPE_FILENAME).read_text("utf-8"))
        observations = []
        obs_file = directory / OBSERVATIONS_FILENAME
        if obs_file.exists():
            for line_no, line in enumerate(obs_file.read_text("utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    observations.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    # One malformed line must not cost us the other 200. The
                    # bad line is reported, the rest of the document loads.
                    raise VaultError(f"{obs_file}:{line_no}: {exc}") from exc
        return VaultDocument(envelope=envelope, observations=observations, directory=directory)

    def iter_documents(self, tenant_id: str | None = None) -> Iterator[VaultDocument]:
        """Walk every document in the vault, oldest first.

        This is the function `medvault reindex` is built on, and the reason the
        database is disposable.
        """
        if tenant_id is not None:
            roots = [self.tenant_dir(tenant_id)]
        else:
            tenants_dir = self.root / "tenants"
            roots = (
                sorted(p for p in tenants_dir.glob("*") if p.is_dir())
                if tenants_dir.is_dir()
                else []
            )
        for tenant_root in roots:
            documents = tenant_root / "documents"
            if not documents.is_dir():
                continue
            # ULID directory names sort chronologically, and so do YYYY/MM.
            for envelope_path in sorted(documents.glob("*/*/*/" + ENVELOPE_FILENAME)):
                yield self.read_document(envelope_path.parent)

    def iter_subjects(self, tenant_id: str) -> Iterator[dict[str, Any]]:
        subjects = self.tenant_dir(tenant_id) / "subjects"
        if not subjects.is_dir():
            return
        for path in sorted(subjects.glob("*/" + SUBJECT_FILENAME)):
            yield json.loads(path.read_text("utf-8"))

    def iter_tenants(self) -> Iterator[dict[str, Any]]:
        tenants_dir = self.root / "tenants"
        if not tenants_dir.is_dir():
            return
        for path in sorted(tenants_dir.glob("*/" + TENANT_FILENAME)):
            yield json.loads(path.read_text("utf-8"))

    # -- integrity ------------------------------------------------------------

    def verify(self, tenant_id: str | None = None) -> list[str]:
        """Re-hash every original and report anything that no longer matches.

        Returns a list of human-readable problems; empty means the vault is
        intact. Run from a cron job, not just after a scare.
        """
        problems: list[str] = []
        for document in self.iter_documents(tenant_id):
            path = document.original_path
            if path is None or not path.exists():
                problems.append(f"{document.document_id}: original file is missing")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = document.envelope.get("source", {}).get("sha256")
            if digest != expected:
                problems.append(
                    f"{document.document_id}: sha256 mismatch "
                    f"(recorded {expected}, found {digest})"
                )
        return problems


def _guard_id(value: str) -> None:
    """Reject identifiers that could escape the vault root or confuse a filesystem."""
    if not value or value in {".", ".."} or "/" in value or "\\" in value or value.startswith("."):
        raise VaultError(f"unsafe identifier for a path segment: {value!r}")


def _year_month(timestamp: str) -> tuple[str, str]:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return ("unknown", "unknown")
    return (f"{parsed.year:04d}", f"{parsed.month:02d}")


_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/tiff": ".tiff",
    "application/pdf": ".pdf",
}


def _extension_for(media_type: str) -> str:
    return _EXTENSIONS.get(media_type, ".bin")
