"""Tenants, subjects, and what the signed-in person is allowed to see."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from medvault.auth import Principal, get_principal
from medvault.deps import get_db
from medvault.models import Subject, Tenant
from medvault.schemas import SubjectOut, TenantOut

router = APIRouter(tags=["tenants"])


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[TenantOut]:
    memberships = getattr(request.state, "memberships", {}) or {}
    tenants = session.scalars(
        select(Tenant).where(Tenant.id.in_(principal.tenant_ids or {""}))
    ).all()
    return [
        TenantOut(
            id=t.id, display_name=t.display_name, role=memberships.get(t.id, "viewer")
        )
        for t in tenants
    ]


@router.get("/tenants/{tenant_id}/subjects", response_model=list[SubjectOut])
def list_subjects(
    tenant_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[Subject]:
    principal.require_tenant(tenant_id)
    return list(
        session.scalars(
            select(Subject).where(Subject.tenant_id == tenant_id).order_by(Subject.display_name)
        ).all()
    )


@router.get("/tenants/{tenant_id}/subjects/{subject_id}", response_model=SubjectOut)
def get_subject(
    tenant_id: str,
    subject_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> Subject:
    principal.require_tenant(tenant_id)
    subject = session.get(Subject, (subject_id, tenant_id))
    if subject is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return subject
