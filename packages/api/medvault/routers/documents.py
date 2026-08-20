"""Uploading, reading and correcting documents."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from medvault.auth import Principal, get_principal, require_editor
from medvault.deps import get_db, get_vault
from medvault.extraction import DocumentExtractor, ExtractionError, to_vault_records
from medvault.extraction.images import sniff_media_type
from medvault.models import Document, Observation, Subject
from medvault.projection import reindex
from medvault.schemas import (
    CorrectionRequest,
    DocumentDetail,
    DocumentOut,
    ReviewRequest,
    UploadResponse,
)
from medvault.vault.store import Vault, VaultError

log = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

# Phone photographs of a paper report; anything much larger is a mistake.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _load_document(session: Session, tenant_id: str, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document")
    return document


@router.get("/tenants/{tenant_id}/documents", response_model=list[DocumentOut])
def list_documents(
    tenant_id: str,
    subject_id: str | None = None,
    include_superseded: bool = False,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[Document]:
    principal.require_tenant(tenant_id)
    query = select(Document).where(Document.tenant_id == tenant_id)
    if subject_id:
        query = query.where(Document.subject_id == subject_id)
    if not include_superseded:
        query = query.where(Document.superseded_by.is_(None))
    return list(session.scalars(query.order_by(Document.captured_at.desc())).all())


@router.get("/tenants/{tenant_id}/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    tenant_id: str,
    document_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Document:
    principal.require_tenant(tenant_id)
    return _load_document(session, tenant_id, document_id)


@router.get("/tenants/{tenant_id}/documents/{document_id}/original")
def get_original(
    tenant_id: str,
    document_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    vault: Vault = Depends(get_vault),
) -> FileResponse:
    """Serve the untouched source image.

    Being able to put the photograph next to the extracted numbers is what
    makes the extraction auditable, so this is a first-class route.
    """
    principal.require_tenant(tenant_id)
    document = _load_document(session, tenant_id, document_id)
    path = vault.root / document.vault_path / document.source.get("filename", "")
    # The path is built from the projection, but resolve and re-check anyway:
    # a corrupt vault_path must not become a file-read primitive.
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(vault.root.resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the original file is missing")
    return FileResponse(
        resolved,
        media_type=document.source.get("media_type", "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post(
    "/tenants/{tenant_id}/subjects/{subject_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    tenant_id: str,
    subject_id: str,
    request: Request,
    file: UploadFile = File(...),
    captured_at: str | None = Form(None),
    hint: str | None = Form(None),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    vault: Vault = Depends(get_vault),
) -> UploadResponse:
    """Read a photograph of a medical result and file it.

    The order of operations matters. The document is written to the vault
    first and the projection is refreshed from it afterwards — never the other
    way round, so a crash between the two loses nothing but a cache entry.
    """
    principal.require_tenant(tenant_id)
    require_editor(principal, request, tenant_id)

    if session.get(Subject, (subject_id, tenant_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")

    payload = await file.read()
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the uploaded file was empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    media_type = sniff_media_type(payload, file.content_type)

    try:
        outcome = DocumentExtractor().extract(payload, media_type, hint)
        envelope, observations = to_vault_records(
            outcome,
            tenant_id,
            subject_id,
            source={"media_type": media_type, "filename": _filename_for(media_type)},
            fallback_captured_at=captured_at,
        )
    except ExtractionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    try:
        vault.ensure_tenant(tenant_id)
        stored = vault.write_document(envelope, payload, observations)
    except VaultError as exc:
        log.exception("failed to write document to the vault")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    reindex(session, vault, tenant_id=tenant_id)
    session.flush()

    document = _load_document(session, tenant_id, stored.document_id)
    return UploadResponse(
        document=DocumentDetail.model_validate(document),
        warnings=list(outcome.result.warnings),
        unmapped_labels=[o.label_raw for o in document.observations if not o.is_mapped],
    )


@router.post("/tenants/{tenant_id}/documents/{document_id}/review", response_model=DocumentDetail)
def review_document(
    tenant_id: str,
    document_id: str,
    body: ReviewRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    vault: Vault = Depends(get_vault),
) -> Document:
    """Mark an extraction as checked by a person.

    Review status is the one field written back onto an existing envelope. It
    is metadata about the record rather than a claim about the patient, and
    superseding a document just to tick a box would bury the history in noise.
    """
    principal.require_tenant(tenant_id)
    require_editor(principal, request, tenant_id)
    if body.status not in {"unreviewed", "verified", "corrected", "rejected"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown review status")

    document = _load_document(session, tenant_id, document_id)
    _write_review(vault, document, body, principal.email)
    reindex(session, vault, tenant_id=tenant_id)
    session.flush()
    return _load_document(session, tenant_id, document_id)


def _write_review(vault: Vault, document: Document, body: ReviewRequest, email: str) -> None:
    import json

    path = vault.root / document.vault_path / "envelope.json"
    envelope = json.loads(path.read_text("utf-8"))
    envelope["review"] = {
        "status": body.status,
        "reviewed_by": email,
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": body.note,
    }
    path.write_text(
        json.dumps(envelope, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.post(
    "/tenants/{tenant_id}/documents/{document_id}/corrections",
    response_model=DocumentDetail,
    status_code=status.HTTP_201_CREATED,
)
def correct_document(
    tenant_id: str,
    document_id: str,
    body: CorrectionRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
    vault: Vault = Depends(get_vault),
) -> Document:
    """Replace a document's readings with corrected ones.

    Writes a new document that supersedes the old one. The mistaken reading
    stays in the vault and stays queryable — for a medical record, knowing that
    a value was once recorded differently is part of the record.
    """
    principal.require_tenant(tenant_id)
    require_editor(principal, request, tenant_id)
    if not body.observations:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a correction needs at least one row")

    original = _load_document(session, tenant_id, document_id)
    if original.superseded_by:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this document has already been superseded; correct the current one",
        )

    vault_document = vault.read_document(vault.root / original.vault_path)
    updates: dict = {
        "review": {
            "status": "corrected",
            "reviewed_by": principal.email,
            "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "note": body.note,
        }
    }
    if body.captured_at:
        updates["captured_at"] = body.captured_at.isoformat().replace("+00:00", "Z")
    if body.document_type:
        updates["document_type"] = body.document_type

    rows = [o.model_dump(exclude_none=True) for o in body.observations]
    try:
        corrected = vault.supersede(vault_document, updates, rows)
    except VaultError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    reindex(session, vault, tenant_id=tenant_id)
    session.flush()
    return _load_document(session, tenant_id, corrected.document_id)


@router.get(
    "/tenants/{tenant_id}/documents/{document_id}/observations",
    response_model=list[dict],
)
def list_document_observations(
    tenant_id: str,
    document_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict]:
    principal.require_tenant(tenant_id)
    _load_document(session, tenant_id, document_id)
    rows = session.scalars(
        select(Observation)
        .where(Observation.document_id == document_id)
        .order_by(Observation.label_raw)
    ).all()
    return [
        {
            "id": r.id,
            "label_raw": r.label_raw,
            "label_en": r.label_en,
            "analyte_code": r.analyte_code,
            "is_mapped": r.is_mapped,
            "value_num": r.value_num,
            "value_text": r.value_text,
            "unit_raw": r.unit_raw,
            "canonical_value": r.canonical_value,
            "canonical_unit": r.canonical_unit,
            "abnormal_flag": r.abnormal_flag,
            "reference_low": r.reference_low,
            "reference_high": r.reference_high,
            "body_site": r.body_site,
            "laterality": r.laterality,
            "confidence": r.confidence,
            "source_context": r.source_context,
            "normalisation_notes": r.normalisation_notes,
        }
        for r in rows
    ]


_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
}


def _filename_for(media_type: str) -> str:
    return "original" + _EXTENSIONS.get(media_type, ".bin")
