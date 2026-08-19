from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from medvault.models import Base
from medvault.vault.store import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path / "vault")
    v.initialise()
    v.ensure_tenant("acme", "Acme Household")
    v.write_subject(
        {
            "tenant_id": "acme",
            "subject_id": "self",
            "display_name": "Pan Yan Bin",
            "names_raw": ["Pan YAN BIN", "潘彦斌"],
            "birth_date": "1995-03-02",
            "sex_at_birth": "male",
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    return v


@pytest.fixture
def session() -> Session:
    """An in-memory database.

    SQLite rather than PostgreSQL so the suite runs anywhere. The projection
    uses no PostgreSQL-specific feature, which is itself worth protecting:
    a projection that only rebuilds on one vendor's database is a weaker
    guarantee than one that rebuilds anywhere.
    """
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s


def make_document(
    vault: Vault,
    captured_at: str,
    observations: list[dict],
    *,
    subject_id: str = "self",
    tenant_id: str = "acme",
    document_type: str = "blood_panel",
    **envelope_extra,
):
    envelope = {
        "tenant_id": tenant_id,
        "subject_id": subject_id,
        "captured_at": captured_at,
        "document_type": document_type,
        "language": "zh-Hans",
        "provider": {"name_raw": "上海电力医院", "name_en": "Shanghai Electric Power Hospital"},
        "source": {"media_type": "image/jpeg"},
        **envelope_extra,
    }
    payload = f"fake-jpeg-{captured_at}-{subject_id}-{len(observations)}".encode()
    return vault.write_document(envelope, payload, observations)
