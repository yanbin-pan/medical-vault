"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from medvault.config import get_settings
from medvault.vault.store import Vault


def get_db(request: Request) -> Session:
    return request.state.session


def get_vault(request: Request) -> Vault:
    vault = getattr(request.app.state, "vault", None)
    if vault is None:
        vault = Vault(get_settings().vault_path)
        request.app.state.vault = vault
    return vault
