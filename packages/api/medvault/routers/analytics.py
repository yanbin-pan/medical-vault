"""Charts: time series, correlations, and the catalogue behind them."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from medvault.analytics import build_correlation_matrix, build_timeseries, summarise_subject
from medvault.auth import Principal, get_principal
from medvault.catalog.registry import get_catalog
from medvault.deps import get_db
from medvault.schemas import CorrelationOut, SeriesPointOut, SummaryOut, TimeSeriesOut

router = APIRouter(tags=["analytics"])


@router.get(
    "/tenants/{tenant_id}/subjects/{subject_id}/series",
    response_model=list[TimeSeriesOut],
)
def get_series(
    tenant_id: str,
    subject_id: str,
    series_key: list[str] | None = Query(default=None),
    include_unmapped: bool = True,
    include_superseded: bool = False,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[TimeSeriesOut]:
    principal.require_tenant(tenant_id)
    series = build_timeseries(
        session,
        tenant_id,
        subject_id,
        series_keys=series_key,
        include_superseded=include_superseded,
        include_unmapped=include_unmapped,
    )
    return [
        TimeSeriesOut(
            series_key=s.series_key,
            analyte_code=s.analyte_code,
            label=s.label,
            label_raw_examples=s.label_raw_examples,
            unit=s.unit,
            category=s.category,
            body_site=s.body_site,
            laterality=s.laterality,
            is_mapped=s.is_mapped,
            higher_is_worse=s.higher_is_worse,
            trend_per_year=s.trend(),
            excluded_points=s.excluded_points,
            # asdict, not vars: these are slots dataclasses and have no __dict__.
            points=[SeriesPointOut(**asdict(p)) for p in s.points],
        )
        for s in series
    ]


@router.get(
    "/tenants/{tenant_id}/subjects/{subject_id}/correlations",
    response_model=list[CorrelationOut],
)
def get_correlations(
    tenant_id: str,
    subject_id: str,
    window_days: int = Query(default=3, ge=0, le=365),
    min_points: int = Query(default=4, ge=3, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[CorrelationOut]:
    """Correlate analytes measured at the same visits.

    `window_days` decides what counts as one visit. It is exposed because the
    right answer depends on the person's habits: a single annual check-up needs
    a wider window than monthly monitoring.
    """
    principal.require_tenant(tenant_id)
    series = build_timeseries(session, tenant_id, subject_id, include_unmapped=False)
    pairs = build_correlation_matrix(
        series, window=timedelta(days=window_days), min_points=min_points
    )
    return [CorrelationOut(**asdict(p)) for p in pairs]


@router.get("/tenants/{tenant_id}/subjects/{subject_id}/summary", response_model=SummaryOut)
def get_summary(
    tenant_id: str,
    subject_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> SummaryOut:
    principal.require_tenant(tenant_id)
    return SummaryOut(**asdict(summarise_subject(session, tenant_id, subject_id)))


@router.get("/catalog")
def get_catalog_entries(_: Principal = Depends(get_principal)) -> dict:
    """The analyte catalogue, so the UI can group and label without hardcoding it."""
    catalog = get_catalog()
    return {
        "version": catalog.version,
        "analytes": [
            {
                "code": a.code,
                "name": a.name,
                "unit": a.unit,
                "category": a.category,
                "higher_is_worse": a.higher_is_worse,
            }
            for a in sorted(catalog.analytes.values(), key=lambda a: (a.category, a.name))
        ],
        "body_sites": [
            {"code": s.code, "name": s.name} for s in catalog.body_sites.values()
        ],
    }
