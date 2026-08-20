"""API response shapes.

Kept separate from the ORM so a change to the projection's columns does not
silently change the public API, and vice versa.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TenantOut(BaseModel):
    id: str
    display_name: str
    role: str = "viewer"


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    display_name: str
    birth_date: date | None = None
    sex_at_birth: str | None = None


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    effective_time: datetime
    label_raw: str
    label_en: str | None
    analyte_code: str
    series_key: str
    category: str
    is_mapped: bool
    value_num: float | None
    value_text: str | None
    unit_raw: str | None
    canonical_value: float | None
    canonical_unit: str | None
    comparator: str | None
    reference_low: float | None
    reference_high: float | None
    reference_text: str | None
    abnormal_flag: str | None
    body_site: str | None
    laterality: str | None
    confidence: float | None
    source_context: str | None
    normalisation_notes: list[str] = []
    is_current: bool
    review_status: str


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    subject_id: str
    captured_at: datetime
    recorded_at: datetime
    document_type: str | None
    language: str | None
    supersedes: str | None
    superseded_by: str | None
    provider: dict[str, Any] | None
    source: dict[str, Any]
    extraction: dict[str, Any] | None
    review: dict[str, Any] | None
    narrative: list[dict[str, Any]] = []
    tags: list[str] = []
    notes: str | None


class DocumentDetail(DocumentOut):
    observations: list[ObservationOut] = []


class SeriesPointOut(BaseModel):
    t: datetime
    value: float
    document_id: str
    abnormal_flag: str | None = None
    reference_low: float | None = None
    reference_high: float | None = None
    review_status: str = "unreviewed"
    confidence: float | None = None


class TimeSeriesOut(BaseModel):
    series_key: str
    analyte_code: str
    label: str
    label_raw_examples: list[str]
    unit: str | None
    category: str
    body_site: str | None
    laterality: str | None
    is_mapped: bool
    higher_is_worse: bool | None
    trend_per_year: float | None
    excluded_points: int
    points: list[SeriesPointOut]


class CorrelationOut(BaseModel):
    series_a: str
    series_b: str
    label_a: str
    label_b: str
    n: int
    pearson: float | None
    spearman: float | None


class SummaryOut(BaseModel):
    observation_count: int
    document_count: int
    series_count: int
    unmapped_count: int
    needs_review: int
    first_record: datetime | None
    last_record: datetime | None
    abnormal_latest: list[str]


class UploadResponse(BaseModel):
    document: DocumentDetail
    warnings: list[str] = []
    unmapped_labels: list[str] = []


class ReviewRequest(BaseModel):
    status: str
    note: str | None = None


class ObservationCorrection(BaseModel):
    """A corrected reading, supplied by a human reviewing an extraction."""

    label_raw: str
    label_en: str | None = None
    value_num: float | None = None
    value_text: str | None = None
    unit_raw: str | None = None
    reference_low: float | None = None
    reference_high: float | None = None
    abnormal_flag: str | None = None
    body_site: str | None = None
    laterality: str | None = None
    comparator: str | None = None
    source_context: str | None = None


class CorrectionRequest(BaseModel):
    observations: list[ObservationCorrection]
    captured_at: datetime | None = None
    document_type: str | None = None
    note: str | None = None


class HealthOut(BaseModel):
    status: str
    vault_path: str
    documents: int
    observations: int
    unmapped: int
    catalog_version: int | None
    last_reindex_at: datetime | None
