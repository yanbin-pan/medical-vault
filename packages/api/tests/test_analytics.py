"""Charts must not lie.

These tests are mostly about what the analytics layer refuses to do: correlate
three points, average a left kidney with a right one, or plot a value it could
not convert.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from medvault.analytics import build_correlation_matrix, build_timeseries, summarise_subject
from medvault.projection import reindex
from medvault.vault.store import Vault
from tests.conftest import make_document


def _series_by_label(series, fragment):
    return next(s for s in series if fragment in s.label)


def test_one_series_per_analyte_site_and_side(vault: Vault, session: Session):
    for year, left, right in [(2024, 100, 102), (2025, 102, 103), (2026, 104, 106)]:
        make_document(
            vault,
            f"{year}-05-01T00:00:00Z",
            [
                {"label_raw": "左肾长径", "value_num": left, "unit_raw": "mm"},
                {"label_raw": "右肾长径", "value_num": right, "unit_raw": "mm"},
            ],
        )
    reindex(session, vault)
    series = build_timeseries(session, "acme", "self")

    kidneys = [s for s in series if s.analyte_code == "MV:kidney.length"]
    assert len(kidneys) == 2
    left_series = next(s for s in kidneys if s.laterality == "left")
    assert [p.value for p in left_series.points] == [100, 102, 104]
    assert "Left" in left_series.label and "Kidney" in left_series.label


def test_points_are_chronological_and_carry_their_document(vault: Vault, session: Session):
    for stamp in ["2026-03-01T00:00:00Z", "2024-01-01T00:00:00Z", "2025-06-01T00:00:00Z"]:
        make_document(vault, stamp, [{"label_raw": "血红蛋白", "value_num": 150, "unit_raw": "g/L"}])
    reindex(session, vault)
    series = _series_by_label(build_timeseries(session, "acme", "self"), "Haemoglobin")

    assert [p.t.year for p in series.points] == [2024, 2025, 2026]
    assert all(p.document_id for p in series.points)


def test_unconvertible_values_are_excluded_and_counted(vault: Vault, session: Session):
    """Better to show 'one point omitted' than to plot an incomparable number."""
    make_document(vault, "2025-01-01T00:00:00Z", [
        {"label_raw": "空腹血糖", "value_num": 5.5, "unit_raw": "mmol/L"}])
    make_document(vault, "2026-01-01T00:00:00Z", [
        {"label_raw": "空腹血糖", "value_num": 99, "unit_raw": "furlongs"}])
    reindex(session, vault)

    series = _series_by_label(build_timeseries(session, "acme", "self"), "Glucose")
    assert len(series.points) == 1
    assert series.excluded_points == 1


def test_mixed_units_land_on_one_axis(vault: Vault, session: Session):
    make_document(vault, "2025-01-01T00:00:00Z", [
        {"label_raw": "空腹血糖", "value_num": 92, "unit_raw": "mg/dL"}])
    make_document(vault, "2026-01-01T00:00:00Z", [
        {"label_raw": "空腹血糖", "value_num": 5.9, "unit_raw": "mmol/L"}])
    reindex(session, vault)

    series = _series_by_label(build_timeseries(session, "acme", "self"), "Glucose")
    assert series.unit == "mmol/L"
    assert [round(p.value, 2) for p in series.points] == [5.11, 5.90]


def test_trend_is_reported_per_year(vault: Vault, session: Session):
    for year in (2022, 2023, 2024, 2025):
        make_document(vault, f"{year}-01-01T00:00:00Z", [
            {"label_raw": "体重", "value_num": 70 + (year - 2022) * 2, "unit_raw": "kg"}])
    reindex(session, vault)
    series = _series_by_label(build_timeseries(session, "acme", "self"), "Body weight")
    assert series.trend() is not None
    assert 1.9 < series.trend() < 2.1  # ~2 kg per year


def test_correlation_needs_enough_paired_points(vault: Vault, session: Session):
    """Three visits is not a correlation, however striking the number looks."""
    for year in (2024, 2025, 2026):
        make_document(vault, f"{year}-01-01T00:00:00Z", [
            {"label_raw": "总胆固醇", "value_num": 4.0 + year - 2024, "unit_raw": "mmol/L"},
            {"label_raw": "甘油三酯", "value_num": 1.0 + year - 2024, "unit_raw": "mmol/L"}])
    reindex(session, vault)

    series = build_timeseries(session, "acme", "self")
    assert build_correlation_matrix(series) == []


def test_correlation_is_computed_on_genuinely_paired_visits(vault: Vault, session: Session):
    for index, year in enumerate([2021, 2022, 2023, 2024, 2025]):
        make_document(vault, f"{year}-01-01T00:00:00Z", [
            {"label_raw": "总胆固醇", "value_num": 4.0 + index * 0.5, "unit_raw": "mmol/L"},
            {"label_raw": "甘油三酯", "value_num": 1.0 + index * 0.5, "unit_raw": "mmol/L"},
            {"label_raw": "血红蛋白", "value_num": 150 - index * 0.5, "unit_raw": "g/L"}])
    reindex(session, vault)

    pairs = build_correlation_matrix(build_timeseries(session, "acme", "self"))
    lookup = {frozenset({p.label_a, p.label_b}): p for p in pairs}

    perfect = lookup[frozenset({"Cholesterol, total", "Triglycerides"})]
    assert perfect.n == 5
    assert perfect.pearson == 1.0 and perfect.spearman == 1.0

    inverse = lookup[frozenset({"Cholesterol, total", "Haemoglobin"})]
    assert inverse.pearson == -1.0


def test_unpaired_observations_are_not_correlated(vault: Vault, session: Session):
    """Two analytes never measured at the same visit must not produce a pair."""
    for year in (2020, 2021, 2022, 2023):
        make_document(vault, f"{year}-01-01T00:00:00Z", [
            {"label_raw": "总胆固醇", "value_num": 4.0 + year - 2020, "unit_raw": "mmol/L"}])
    for year in (2020, 2021, 2022, 2023):
        make_document(vault, f"{year}-07-01T00:00:00Z", [
            {"label_raw": "甘油三酯", "value_num": 1.0 + year - 2020, "unit_raw": "mmol/L"}])
    reindex(session, vault)

    series = build_timeseries(session, "acme", "self")
    # Six months apart is not one visit.
    assert build_correlation_matrix(series, window=timedelta(days=3)) == []
    # Widen the window and the same data pairs up.
    assert len(build_correlation_matrix(series, window=timedelta(days=200))) == 1


def test_a_flat_series_reports_no_correlation(vault: Vault, session: Session):
    for year in (2021, 2022, 2023, 2024):
        make_document(vault, f"{year}-01-01T00:00:00Z", [
            {"label_raw": "总胆固醇", "value_num": 5.0, "unit_raw": "mmol/L"},
            {"label_raw": "甘油三酯", "value_num": 1.0 + year - 2021, "unit_raw": "mmol/L"}])
    reindex(session, vault)
    pairs = build_correlation_matrix(build_timeseries(session, "acme", "self"))
    assert pairs and pairs[0].pearson is None


def test_spearman_survives_an_outlier_that_distorts_pearson(vault: Vault, session: Session):
    values = [(1, 1), (2, 2), (3, 3), (4, 4), (5, 100)]
    for index, (a, b) in enumerate(values):
        make_document(vault, f"{2020 + index}-01-01T00:00:00Z", [
            {"label_raw": "总胆固醇", "value_num": a, "unit_raw": "mmol/L"},
            {"label_raw": "甘油三酯", "value_num": b, "unit_raw": "mmol/L"}])
    reindex(session, vault)
    pair = build_correlation_matrix(build_timeseries(session, "acme", "self"))[0]
    # Monotonic throughout, so Spearman is exactly 1 while Pearson is dragged down.
    assert pair.spearman == 1.0
    assert pair.pearson < 0.95


def test_subject_summary_counts_what_needs_attention(vault: Vault, session: Session):
    make_document(vault, "2026-01-01T00:00:00Z", [
        {"label_raw": "空腹血糖", "value_num": 9.9, "unit_raw": "mmol/L", "abnormal_flag": "high"},
        {"label_raw": "某未知指标", "value_num": 1}])
    reindex(session, vault)

    summary = summarise_subject(session, "acme", "self")
    assert summary.observation_count == 2
    assert summary.document_count == 1
    assert summary.unmapped_count == 1
    assert summary.needs_review == 2
    assert len(summary.abnormal_latest) == 1


def test_cholesterol_is_not_filed_under_the_gallbladder(vault: Vault, session: Session):
    """Regression: 胆 (bile) is a substring of 胆固醇 (cholesterol).

    Matching one-character organ names as substrings tagged every cholesterol
    result with body_site=gallbladder, which split the series and put a lipid
    panel in the abdominal-ultrasound group. Silent, and wrong in a way nobody
    would notice until the chart looked strange years later.
    """
    make_document(vault, "2026-01-01T00:00:00Z", [
        {"label_raw": "总胆固醇", "value_num": 4.8, "unit_raw": "mmol/L"},
        {"label_raw": "高密度脂蛋白胆固醇", "value_num": 1.4, "unit_raw": "mmol/L"},
        {"label_raw": "低密度脂蛋白胆固醇", "value_num": 2.9, "unit_raw": "mmol/L"},
        {"label_raw": "胆囊壁厚", "value_num": 3, "unit_raw": "mm"}])
    reindex(session, vault)

    series = {s.analyte_code: s for s in build_timeseries(session, "acme", "self")}
    assert series["LOINC:2093-3"].body_site is None
    assert series["LOINC:2085-9"].body_site is None
    assert series["LOINC:2089-1"].body_site is None
    assert series["LOINC:2093-3"].category == "lipids"
    # ...while a genuine gallbladder measurement still resolves.
    assert series["MV:gallbladder.wall-thickness"].body_site == "gallbladder"
