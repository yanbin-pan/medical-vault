"""The derived relational model.

Read this file as a query optimisation, not as the source of truth. Every
column is either copied verbatim from a vault record or computed from one by
`medvault.catalog.normalize`. If a column here disagrees with the vault, the
vault is right and this table needs rebuilding.

JSON is used freely for the parts of a record whose shape is not fixed
(provider details, qualifiers, narrative). Those fields are for display and
provenance; anything that has to be queried or plotted gets a real column.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# JSONB on PostgreSQL, plain JSON on SQLite so the test suite needs no server.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who may read this tenant, mirrored from the vault's tenant.json. Held here
    # too so that authorisation survives a database rebuild.
    members: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)

    subjects: Mapped[list[Subject]] = relationship(back_populates="tenant")


class Subject(Base):
    __tablename__ = "subject"
    __table_args__ = (Index("ix_subject_tenant", "tenant_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex_at_birth: Mapped[str | None] = mapped_column(String(16), nullable=True)
    names_raw: Mapped[list[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="subjects")


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        Index("ix_document_tenant_subject_time", "tenant_id", "subject_id", "captured_at"),
        Index("ix_document_supersedes", "supersedes"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)

    supersedes: Mapped[str | None] = mapped_column(String(26), nullable=True)
    # Derived by the projection, not stored in the vault: the vault only knows
    # which document a correction replaces, not which one replaced it.
    superseded_by: Mapped[str | None] = mapped_column(String(26), nullable=True)

    provider: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    narrative: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Where this document lives inside the vault, relative to the vault root.
    # Lets the API serve the original image without re-deriving the path.
    vault_path: Mapped[str] = mapped_column(Text, nullable=False)

    observations: Mapped[list[Observation]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    @property
    def is_current(self) -> bool:
        return self.superseded_by is None


class Observation(Base):
    __tablename__ = "observation"
    __table_args__ = (
        # The index behind every chart: one analyte for one subject over time.
        Index("ix_obs_series", "tenant_id", "subject_id", "series_key", "effective_time"),
        Index("ix_obs_document", "document_id"),
        Index("ix_obs_code", "tenant_id", "analyte_code"),
        Index("ix_obs_current", "tenant_id", "subject_id", "is_current"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)

    effective_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    label_raw: Mapped[str] = mapped_column(Text, nullable=False)
    label_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyte_code: Mapped[str] = mapped_column(String(128), nullable=False)
    # analyte + body site + laterality: the identity of one plotted line.
    series_key: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="other")
    # False for observations kept verbatim because the catalogue had no entry.
    # They are real data awaiting a mapping, and the UI surfaces them as such.
    is_mapped: Mapped[bool] = mapped_column(Boolean, default=False)

    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    unit_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    canonical_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comparator: Mapped[str | None] = mapped_column(String(4), nullable=True)

    reference_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    abnormal_flag: Mapped[str | None] = mapped_column(String(16), nullable=True)

    body_site: Mapped[str | None] = mapped_column(String(64), nullable=True)
    laterality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specimen: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qualifiers: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Warnings raised while deriving this row (unconvertible unit, assumed unit).
    normalisation_notes: Mapped[list[str]] = mapped_column(JSONType, default=list)

    # Copied down from the document so charts can exclude superseded readings
    # without a join on every query.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed")

    document: Mapped[Document] = relationship(back_populates="observations")


class ProjectionState(Base):
    """One row recording the last reindex, so operators can see staleness."""

    __tablename__ = "projection_state"
    __table_args__ = (UniqueConstraint("id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_reindex_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    unmapped_count: Mapped[int] = mapped_column(Integer, default=0)
