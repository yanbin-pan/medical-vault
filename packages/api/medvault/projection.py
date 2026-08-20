"""Rebuilding the database from the vault.

This module is the load-bearing proof of the whole design. If it works, the
database is a cache; if it does not, the vault is decoration and the
database has quietly become the real system.

So it is written to one rule: **it reads only the vault.** It never reads the
existing database to decide what to write. A full reindex against an empty
database and a reindex against a populated one produce byte-identical results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from medvault.catalog.normalize import normalise_observation
from medvault.catalog.registry import Catalog, get_catalog
from medvault.models import Document, Observation, ProjectionState, Subject, Tenant
from medvault.vault.store import Vault, VaultDocument

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ReindexReport:
    tenants: int = 0
    subjects: int = 0
    documents: int = 0
    observations: int = 0
    unmapped: int = 0
    superseded: int = 0
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.documents} documents, {self.observations} observations "
            f"({self.unmapped} unmapped), {self.subjects} subjects across "
            f"{self.tenants} tenants, {self.superseded} superseded"
            + (f", {len(self.problems)} problems" if self.problems else "")
        )


def _parse_dt(value: Any, fallback: datetime | None = None) -> datetime:
    """Parse an ISO-8601 timestamp leniently.

    A document with an unparseable date must not abort a reindex of 500 others,
    so the fallback is used and the problem is reported.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback or datetime.now(UTC)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def reindex(
    session: Session,
    vault: Vault,
    tenant_id: str | None = None,
    catalog: Catalog | None = None,
) -> ReindexReport:
    """Rebuild the projection for one tenant, or for the whole vault.

    Runs inside the caller's transaction, so a failure leaves the previous
    projection in place rather than an empty one.
    """
    catalog = catalog or get_catalog()
    report = ReindexReport()

    _clear(session, tenant_id)

    tenants = list(vault.iter_tenants())
    if tenant_id is not None:
        tenants = [t for t in tenants if t.get("tenant_id") == tenant_id]

    for tenant_record in tenants:
        tid = tenant_record["tenant_id"]
        session.add(
            Tenant(
                id=tid,
                display_name=tenant_record.get("display_name") or tid,
                created_at=_parse_dt(tenant_record.get("created_at")),
                members=tenant_record.get("members") or [],
            )
        )
        report.tenants += 1

        for subject_record in vault.iter_subjects(tid):
            session.add(
                Subject(
                    id=subject_record["subject_id"],
                    tenant_id=tid,
                    display_name=subject_record.get("display_name")
                    or subject_record["subject_id"],
                    birth_date=_parse_date(subject_record.get("birth_date")),
                    sex_at_birth=subject_record.get("sex_at_birth"),
                    names_raw=subject_record.get("names_raw") or [],
                    notes=subject_record.get("notes"),
                )
            )
            report.subjects += 1

    session.flush()

    # Pass 1: every document and its observations.
    supersedes_links: list[tuple[str, str]] = []
    for vault_doc in vault.iter_documents(tenant_id):
        try:
            document, observations = _project_document(vault_doc, vault, catalog, report)
        except Exception as exc:  # one bad document must not lose the rest
            report.problems.append(f"{vault_doc.document_id}: {exc}")
            log.exception("failed to project document %s", vault_doc.document_id)
            continue
        session.add(document)
        session.add_all(observations)
        report.documents += 1
        report.observations += len(observations)
        if document.supersedes:
            supersedes_links.append((document.supersedes, document.id))

    session.flush()

    # Pass 2: resolve supersession, which needs every document to exist first.
    # A document may be corrected more than once, so the newest correction wins.
    for old_id, new_id in sorted(supersedes_links, key=lambda pair: pair[1]):
        old = session.get(Document, old_id)
        if old is None:
            report.problems.append(f"{new_id} supersedes {old_id}, which is not in the vault")
            continue
        old.superseded_by = new_id
        report.superseded += 1
        for observation in old.observations:
            observation.is_current = False

    session.flush()
    _record_state(session, catalog, report)
    return report


def _clear(session: Session, tenant_id: str | None) -> None:
    """Remove what is about to be rebuilt.

    Scoped by tenant so reindexing one person's records does not blank the
    others'. Observations go first: the cascade would handle it on PostgreSQL,
    but not every dialect honours it the same way.
    """
    if tenant_id is None:
        session.execute(delete(Observation))
        session.execute(delete(Document))
        session.execute(delete(Subject))
        session.execute(delete(Tenant))
    else:
        session.execute(delete(Observation).where(Observation.tenant_id == tenant_id))
        session.execute(delete(Document).where(Document.tenant_id == tenant_id))
        session.execute(delete(Subject).where(Subject.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    session.flush()


def _project_document(
    vault_doc: VaultDocument,
    vault: Vault,
    catalog: Catalog,
    report: ReindexReport,
) -> tuple[Document, list[Observation]]:
    envelope = vault_doc.envelope
    captured_at = _parse_dt(envelope.get("captured_at"))
    recorded_at = _parse_dt(envelope.get("recorded_at"), fallback=captured_at)

    if vault_doc.directory is None:
        raise ValueError("document has no directory on disk")
    vault_path = str(vault_doc.directory.relative_to(vault.root))

    review = envelope.get("review") or {}
    review_status = review.get("status") or "unreviewed"

    document = Document(
        id=envelope["document_id"],
        tenant_id=envelope["tenant_id"],
        subject_id=envelope["subject_id"],
        captured_at=captured_at,
        recorded_at=recorded_at,
        document_type=envelope.get("document_type"),
        language=envelope.get("language"),
        supersedes=envelope.get("supersedes"),
        superseded_by=None,
        provider=envelope.get("provider"),
        source=envelope.get("source") or {},
        extraction=envelope.get("extraction"),
        review=review or None,
        narrative=envelope.get("narrative") or [],
        tags=envelope.get("tags") or [],
        notes=envelope.get("notes"),
        vault_path=vault_path,
    )

    observations: list[Observation] = []
    for row in vault_doc.observations:
        normalised = normalise_observation(row, catalog)
        if not normalised.is_mapped:
            report.unmapped += 1
        observations.append(
            Observation(
                id=row["observation_id"],
                document_id=document.id,
                tenant_id=document.tenant_id,
                subject_id=document.subject_id,
                effective_time=_parse_dt(row.get("effective_time"), fallback=captured_at),
                label_raw=row["label_raw"],
                label_en=normalised.label_en,
                analyte_code=normalised.analyte_code,
                series_key=normalised.series_key,
                category=normalised.category,
                is_mapped=normalised.is_mapped,
                value_num=row.get("value_num"),
                value_text=row.get("value_text"),
                value_bool=row.get("value_bool"),
                unit_raw=normalised.unit,
                canonical_value=normalised.canonical_value,
                canonical_unit=normalised.canonical_unit,
                comparator=row.get("comparator"),
                reference_low=row.get("reference_low"),
                reference_high=row.get("reference_high"),
                reference_text=row.get("reference_text"),
                abnormal_flag=row.get("abnormal_flag"),
                body_site=normalised.body_site,
                laterality=normalised.laterality,
                method=row.get("method"),
                specimen=row.get("specimen"),
                qualifiers=row.get("qualifiers") or {},
                confidence=row.get("confidence"),
                source_context=row.get("source_context"),
                normalisation_notes=normalised.notes,
                is_current=True,
                review_status=review_status,
            )
        )
    return document, observations


def _record_state(session: Session, catalog: Catalog, report: ReindexReport) -> None:
    """Record what the projection now contains.

    Counted from the tables rather than taken from the report, because a
    tenant-scoped reindex touches only part of the projection and the report
    describes that part, not the whole.
    """
    state = session.get(ProjectionState, 1)
    if state is None:
        state = ProjectionState(id=1)
        session.add(state)
    state.last_reindex_at = datetime.now(UTC)
    state.catalog_version = catalog.version
    state.document_count = session.scalar(select(func.count()).select_from(Document)) or 0
    state.observation_count = session.scalar(select(func.count()).select_from(Observation)) or 0
    state.unmapped_count = (
        session.scalar(
            select(func.count()).select_from(Observation).where(Observation.is_mapped.is_(False))
        )
        or 0
    )


def reindex_from_path(session: Session, vault_path: Path | str, **kwargs: Any) -> ReindexReport:
    return reindex(session, Vault(vault_path), **kwargs)
