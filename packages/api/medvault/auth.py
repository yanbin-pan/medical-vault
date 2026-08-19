"""Who is asking, and which tenants they may see.

Identity comes from Cloudflare Access, which sits in front of this service in
the cluster. Access authenticates the user at Cloudflare's edge and forwards a
verified email header; because the pod is only reachable through the tunnel,
nothing else can set that header.

That last clause is the whole security argument, so it is enforced rather than
assumed: header trust is off unless `MEDVAULT_TRUST_ACCESS_HEADER` is set. A
deployment that accidentally exposes the service without Access in front of it
rejects every request rather than believing whatever the client claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from medvault.config import Settings, get_settings
from medvault.models import Tenant


@dataclass(frozen=True, slots=True)
class Principal:
    email: str
    tenant_ids: frozenset[str]

    def require_tenant(self, tenant_id: str) -> str:
        if tenant_id not in self.tenant_ids:
            # 404, not 403: confirming a tenant exists to someone with no access
            # to it is itself a disclosure.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such tenant")
        return tenant_id

    def role_in(self, tenant_id: str, memberships: dict[str, str]) -> str:
        return memberships.get(tenant_id, "viewer")


def resolve_email(request: Request, settings: Settings) -> str:
    if settings.trust_access_header:
        email = request.headers.get(settings.access_email_header, "").strip().lower()
        if email:
            return email
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "no authenticated identity; this service must sit behind Cloudflare Access",
        )
    if settings.dev_user_email:
        return settings.dev_user_email.strip().lower()
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "authentication is not configured; set MEDVAULT_TRUST_ACCESS_HEADER "
        "behind Cloudflare Access, or MEDVAULT_DEV_USER_EMAIL for local use",
    )


def tenants_for(session: Session, email: str) -> dict[str, str]:
    """Return {tenant_id: role} for every tenant this email belongs to.

    Membership lives in the vault's tenant.json and is mirrored into the
    projection, so who-may-see-what survives a database rebuild along with
    everything else.
    """
    memberships: dict[str, str] = {}
    for tenant in session.scalars(select(Tenant)).all():
        for member in tenant.members or []:
            if str(member.get("email", "")).strip().lower() == email:
                memberships[tenant.id] = member.get("role", "viewer")
    return memberships


async def get_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Principal:
    email = resolve_email(request, settings)
    session: Session = request.state.session
    memberships = tenants_for(session, email)
    request.state.memberships = memberships
    return Principal(email=email, tenant_ids=frozenset(memberships))


def require_editor(principal: Principal, request: Request, tenant_id: str) -> None:
    """Reject read-only members from anything that writes."""
    role = (getattr(request.state, "memberships", {}) or {}).get(tenant_id, "viewer")
    if role not in {"owner", "editor"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account has read-only access")
