"""Turning stored observations into the shapes the charts need.

Two rules run through all of it:

* **Only canonical values are plotted.** A series mixing mg/dL and mmol/L on one
  axis is worse than no chart. Points that could not be converted are counted
  and reported, never quietly averaged in.
* **Correlations are computed on paired samples, not on interpolated ones.**
  Inventing a value for a day with no blood test would manufacture the
  correlation being looked for.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from medvault.catalog.registry import get_catalog
from medvault.models import Observation

# Two measurements are "the same visit" if they fall within this window. Blood
# and imaging from one check-up are often timestamped hours apart, and a
# check-up split across two days is still one data point.
DEFAULT_PAIRING_WINDOW = timedelta(days=3)
MIN_POINTS_FOR_CORRELATION = 4


@dataclass(slots=True)
class SeriesPoint:
    t: datetime
    value: float
    document_id: str
    abnormal_flag: str | None = None
    reference_low: float | None = None
    reference_high: float | None = None
    review_status: str = "unreviewed"
    confidence: float | None = None


@dataclass(slots=True)
class TimeSeries:
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
    points: list[SeriesPoint] = field(default_factory=list)
    # Observations in this series that had no convertible numeric value.
    excluded_points: int = 0

    @property
    def latest(self) -> SeriesPoint | None:
        return self.points[-1] if self.points else None

    def trend(self) -> float | None:
        """Slope in units per year, by least squares. None with fewer than 3 points."""
        if len(self.points) < 3:
            return None
        times = np.array([p.t.timestamp() for p in self.points], dtype=float)
        values = np.array([p.value for p in self.points], dtype=float)
        if np.ptp(times) == 0:
            return None
        slope = np.polyfit(times, values, 1)[0]
        return float(slope * 365.25 * 24 * 3600)


@dataclass(slots=True)
class CorrelationPair:
    series_a: str
    series_b: str
    label_a: str
    label_b: str
    n: int
    pearson: float | None
    spearman: float | None


def _observation_query(tenant_id: str, subject_id: str, include_superseded: bool = False):
    query = select(Observation).where(
        Observation.tenant_id == tenant_id,
        Observation.subject_id == subject_id,
    )
    if not include_superseded:
        query = query.where(Observation.is_current.is_(True))
    return query.order_by(Observation.effective_time)


def build_timeseries(
    session: Session,
    tenant_id: str,
    subject_id: str,
    series_keys: list[str] | None = None,
    include_superseded: bool = False,
    include_unmapped: bool = True,
) -> list[TimeSeries]:
    """Group a subject's observations into plottable series."""
    catalog = get_catalog()
    rows = session.scalars(
        _observation_query(tenant_id, subject_id, include_superseded)
    ).all()

    grouped: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        if series_keys and row.series_key not in series_keys:
            continue
        if not include_unmapped and not row.is_mapped:
            continue
        grouped[row.series_key].append(row)

    series: list[TimeSeries] = []
    for key, observations in grouped.items():
        first = observations[0]
        analyte = catalog.get(first.analyte_code)
        points: list[SeriesPoint] = []
        excluded = 0
        for row in observations:
            if row.canonical_value is None:
                excluded += 1
                continue
            points.append(
                SeriesPoint(
                    t=row.effective_time,
                    value=row.canonical_value,
                    document_id=row.document_id,
                    abnormal_flag=row.abnormal_flag,
                    reference_low=row.reference_low,
                    reference_high=row.reference_high,
                    review_status=row.review_status,
                    confidence=row.confidence,
                )
            )
        points.sort(key=lambda p: p.t)
        series.append(
            TimeSeries(
                series_key=key,
                analyte_code=first.analyte_code,
                label=_series_label(first, analyte),
                # Keeping the printed labels visible lets the owner spot a
                # mis-mapping without opening the vault.
                label_raw_examples=sorted({o.label_raw for o in observations})[:3],
                unit=next((o.canonical_unit for o in observations if o.canonical_unit), None),
                category=first.category,
                body_site=first.body_site,
                laterality=first.laterality,
                is_mapped=first.is_mapped,
                higher_is_worse=analyte.higher_is_worse if analyte else None,
                points=points,
                excluded_points=excluded,
            )
        )

    series.sort(key=lambda s: (s.category, s.label))
    return series


def _series_label(row: Observation, analyte) -> str:  # noqa: ANN001
    base = (analyte.name if analyte else None) or row.label_en or row.label_raw
    qualifiers = []
    if row.laterality:
        qualifiers.append(row.laterality.capitalize())
    if row.body_site:
        site = get_catalog().body_sites.get(row.body_site)
        qualifiers.append(site.name if site else row.body_site.replace("-", " "))
    return f"{base} ({', '.join(qualifiers)})" if qualifiers else base


def build_correlation_matrix(
    series: list[TimeSeries],
    window: timedelta = DEFAULT_PAIRING_WINDOW,
    min_points: int = MIN_POINTS_FOR_CORRELATION,
) -> list[CorrelationPair]:
    """Correlate every pair of series on their genuinely paired observations.

    Pairs with too few shared visits are omitted rather than returned with a
    dramatic-looking coefficient computed from three points.
    """
    usable = [s for s in series if len(s.points) >= min_points]
    results: list[CorrelationPair] = []

    for i, first in enumerate(usable):
        for second in usable[i + 1 :]:
            xs, ys = _pair_on_time(first, second, window)
            if len(xs) < min_points:
                continue
            results.append(
                CorrelationPair(
                    series_a=first.series_key,
                    series_b=second.series_key,
                    label_a=first.label,
                    label_b=second.label,
                    n=len(xs),
                    pearson=_pearson(xs, ys),
                    spearman=_spearman(xs, ys),
                )
            )

    results.sort(key=lambda p: abs(p.pearson) if p.pearson is not None else -1, reverse=True)
    return results


def _pair_on_time(
    first: TimeSeries, second: TimeSeries, window: timedelta
) -> tuple[list[float], list[float]]:
    """Match points from two series that belong to the same visit.

    Greedy nearest-match, each point used at most once, so a series sampled
    twice as often cannot contribute the same reading to two pairs.
    """
    remaining = sorted(second.points, key=lambda p: p.t)
    used: set[int] = set()
    xs: list[float] = []
    ys: list[float] = []

    for point in sorted(first.points, key=lambda p: p.t):
        best_index: int | None = None
        best_gap: timedelta | None = None
        for index, candidate in enumerate(remaining):
            if index in used:
                continue
            gap = abs(candidate.t - point.t)
            if gap <= window and (best_gap is None or gap < best_gap):
                best_index, best_gap = index, gap
        if best_index is not None:
            used.add(best_index)
            xs.append(point.value)
            ys.append(remaining[best_index].value)
    return xs, ys


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    a, b = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    # A series that never varies has no correlation to report; numpy would
    # return nan and the UI would render it as a suspicious blank cell.
    if a.std() == 0 or b.std() == 0:
        return None
    value = float(np.corrcoef(a, b)[0, 1])
    return None if math.isnan(value) else round(value, 4)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, computed without pulling in scipy.

    Spearman is reported alongside Pearson because these series are short, and a
    single outlying reading can drive a Pearson coefficient on its own.
    """
    return _pearson(_ranks(xs), _ranks(ys))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        stop = index
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
            stop += 1
        average = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[order[position]] = average  # ties share the average rank
        index = stop + 1
    return ranks


@dataclass(slots=True)
class SubjectSummary:
    observation_count: int
    document_count: int
    series_count: int
    unmapped_count: int
    needs_review: int
    first_record: datetime | None
    last_record: datetime | None
    abnormal_latest: list[str] = field(default_factory=list)


def summarise_subject(session: Session, tenant_id: str, subject_id: str) -> SubjectSummary:
    rows = session.scalars(_observation_query(tenant_id, subject_id, True)).all()
    current = [r for r in rows if r.is_current]
    times = [r.effective_time for r in current] or None

    latest_by_series: dict[str, Observation] = {}
    for row in sorted(current, key=lambda r: r.effective_time):
        latest_by_series[row.series_key] = row

    return SubjectSummary(
        observation_count=len(current),
        document_count=len({r.document_id for r in current}),
        series_count=len({r.series_key for r in current}),
        unmapped_count=sum(1 for r in current if not r.is_mapped),
        needs_review=sum(1 for r in current if r.review_status == "unreviewed"),
        first_record=min(times) if times else None,
        last_record=max(times) if times else None,
        abnormal_latest=sorted(
            row.series_key
            for row in latest_by_series.values()
            if row.abnormal_flag in {"low", "high", "critical_low", "critical_high", "abnormal"}
        ),
    )
