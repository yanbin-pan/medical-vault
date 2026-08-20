"""Proof that the database is disposable.

The user's requirement was that an entirely new application could be rebuilt
from the dataset alone. These tests are how that claim is kept honest: they
destroy the database and rebuild it from files, and they change the catalogue
and show a decade of history re-deriving itself.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from medvault.catalog.registry import load_catalog
from medvault.models import Document, Observation, Subject, Tenant
from medvault.projection import reindex
from medvault.vault.store import Vault
from tests.conftest import make_document


def _seed(vault: Vault) -> None:
    make_document(
        vault,
        "2025-02-14T08:30:00Z",
        [
            {"label_raw": "空腹血糖", "value_num": 92, "unit_raw": "mg/dL",
             "reference_low": 70, "reference_high": 100},
            {"label_raw": "白细胞计数", "value_num": 6.2, "unit_raw": "×10⁹/L"},
        ],
    )
    make_document(
        vault,
        "2026-07-11T09:58:28Z",
        [
            {"label_raw": "空腹血糖", "value_num": 5.9, "unit_raw": "mmol/L"},
            {"label_raw": "左肾长径", "value_num": 104, "unit_raw": "mm"},
            {"label_raw": "右肾长径", "value_num": 106, "unit_raw": "mm"},
        ],
        document_type="ultrasound",
    )


def test_reindex_builds_the_projection_from_files_alone(vault: Vault, session: Session):
    _seed(vault)
    report = reindex(session, vault)

    assert report.tenants == 1
    assert report.subjects == 1
    assert report.documents == 2
    assert report.observations == 5
    assert report.problems == []
    assert session.scalar(select(func.count()).select_from(Observation)) == 5


def test_reindex_is_idempotent(vault: Vault, session: Session):
    """Running it twice must not duplicate rows or change a single value."""
    _seed(vault)
    reindex(session, vault)
    first = _snapshot(session)

    reindex(session, vault)
    assert _snapshot(session) == first


def test_database_can_be_destroyed_and_rebuilt(vault: Vault, session: Session):
    """The headline guarantee. Delete everything, rebuild, compare."""
    _seed(vault)
    reindex(session, vault)
    before = _snapshot(session)

    session.query(Observation).delete()
    session.query(Document).delete()
    session.query(Subject).delete()
    session.query(Tenant).delete()
    session.flush()
    assert session.scalar(select(func.count()).select_from(Observation)) == 0

    reindex(session, vault)
    assert _snapshot(session) == before


def test_units_are_unified_across_labs_and_years(vault: Vault, session: Session):
    """A 2025 result in mg/dL and a 2026 result in mmol/L must share an axis."""
    _seed(vault)
    reindex(session, vault)

    glucose = session.scalars(
        select(Observation)
        .where(Observation.analyte_code == "LOINC:2345-7")
        .order_by(Observation.effective_time)
    ).all()
    assert len(glucose) == 2
    assert {o.canonical_unit for o in glucose} == {"mmol/L"}
    assert round(glucose[0].canonical_value, 2) == 5.11  # 92 mg/dL
    assert round(glucose[1].canonical_value, 2) == 5.90
    # ...while what the paper actually said is untouched.
    assert (glucose[0].value_num, glucose[0].unit_raw) == (92.0, "mg/dL")


def test_left_and_right_are_separate_series(vault: Vault, session: Session):
    """Averaging a left and right kidney would hide one of them growing."""
    _seed(vault)
    reindex(session, vault)
    kidneys = session.scalars(
        select(Observation).where(Observation.analyte_code == "MV:kidney.length")
    ).all()
    assert {k.laterality for k in kidneys} == {"left", "right"}
    assert len({k.series_key for k in kidneys}) == 2


def test_unknown_measurements_are_kept_not_dropped(vault: Vault, session: Session):
    """Data the catalogue cannot explain is still data."""
    make_document(
        vault,
        "2026-08-01T00:00:00Z",
        [{"label_raw": "某种2036年的新指标", "value_num": 42, "unit_raw": "widgets"}],
    )
    reindex(session, vault)

    row = session.scalars(select(Observation)).one()
    assert row.is_mapped is False
    assert row.analyte_code.startswith("UNMAPPED:")
    assert row.label_raw == "某种2036年的新指标"  # the printed truth survives
    assert row.value_num == 42


def test_extending_the_catalogue_retrofits_existing_history(vault: Vault, session: Session, tmp_path):
    """The decade test.

    An observation filed today under a label nothing understands must become a
    first-class time series the moment someone teaches the catalogue about it —
    without touching the vault, and without re-uploading anything.
    """
    for year in (2026, 2027, 2028):
        make_document(
            vault,
            f"{year}-05-01T00:00:00Z",
            [{"label_raw": "肝脏硬度值", "value_num": 5.0 + year - 2026, "unit_raw": "kPa"}],
        )

    reindex(session, vault)
    rows = session.scalars(select(Observation)).all()
    assert len(rows) == 3
    assert all(not r.is_mapped for r in rows)

    # Ten years later somebody adds one entry to the catalogue...
    catalog_file = tmp_path / "extended.yaml"
    catalog_file.write_text(
        "version: 99\n"
        "analytes:\n"
        "  - code: MV:liver.stiffness\n"
        "    name: Liver stiffness\n"
        "    unit: kPa\n"
        "    category: liver\n"
        "    synonyms: [肝脏硬度值, liver stiffness, fibroscan]\n"
        "body_sites: []\n"
        "laterality: {}\n",
        encoding="utf-8",
    )
    reindex(session, vault, catalog=load_catalog(catalog_file))

    rows = session.scalars(select(Observation).order_by(Observation.effective_time)).all()
    assert len(rows) == 3
    assert all(r.is_mapped for r in rows)
    assert all(r.analyte_code == "MV:liver.stiffness" for r in rows)
    assert all(r.canonical_unit == "kPa" for r in rows)
    # Three years of history became one plottable series with no data migration.
    assert len({r.series_key for r in rows}) == 1
    assert [r.canonical_value for r in rows] == [5.0, 6.0, 7.0]


def test_corrections_supersede_without_losing_the_original(vault: Vault, session: Session):
    original = make_document(
        vault, "2026-06-01T00:00:00Z", [{"label_raw": "血红蛋白", "value_num": 15, "unit_raw": "g/L"}]
    )
    vault.supersede(original, {}, [{"label_raw": "血红蛋白", "value_num": 150, "unit_raw": "g/L"}])

    report = reindex(session, vault)
    assert report.superseded == 1

    documents = {d.id: d for d in session.scalars(select(Document)).all()}
    old = documents[original.document_id]
    assert old.superseded_by is not None
    assert old.is_current is False

    current = session.scalars(
        select(Observation).where(Observation.is_current.is_(True))
    ).all()
    assert len(current) == 1 and current[0].value_num == 150
    # Both readings remain queryable; only one is current.
    assert session.scalar(select(func.count()).select_from(Observation)) == 2


def test_one_broken_document_does_not_abort_the_rebuild(vault: Vault, session: Session):
    """A single unreadable envelope must cost one document, not the archive."""
    _seed(vault)
    good = list(vault.iter_documents())[0]
    (good.directory / "envelope.json").write_text('{"document_id": "X", "not": "valid"}', "utf-8")

    report = reindex(session, vault)
    assert report.documents == 1
    assert len(report.problems) == 1
    assert session.scalar(select(func.count()).select_from(Observation)) == 3


def test_tenants_are_isolated_in_the_projection(vault: Vault, session: Session):
    vault.ensure_tenant("other", "Someone Else")
    vault.write_subject(
        {"tenant_id": "other", "subject_id": "self", "display_name": "Other Person"}
    )
    _seed(vault)
    make_document(
        vault, "2026-01-01T00:00:00Z", [{"label_raw": "血糖", "value_num": 5}], tenant_id="other"
    )

    reindex(session, vault)
    assert session.scalar(
        select(func.count()).select_from(Observation).where(Observation.tenant_id == "acme")
    ) == 5
    assert session.scalar(
        select(func.count()).select_from(Observation).where(Observation.tenant_id == "other")
    ) == 1

    # Reindexing one tenant must leave the other's rows alone.
    reindex(session, vault, tenant_id="other")
    assert session.scalar(
        select(func.count()).select_from(Observation).where(Observation.tenant_id == "acme")
    ) == 5


def _snapshot(session: Session) -> list[tuple]:
    """Every field that a rebuild must reproduce exactly."""
    return [
        (
            o.id, o.document_id, o.tenant_id, o.subject_id, o.analyte_code, o.series_key,
            o.label_raw, o.label_en, o.value_num, o.unit_raw, o.canonical_value,
            o.canonical_unit, o.body_site, o.laterality, o.is_mapped, o.is_current,
            o.effective_time,
        )
        for o in session.scalars(select(Observation).order_by(Observation.id)).all()
    ]


def test_reindex_if_needed_skips_a_current_projection(vault: Vault, session: Session):
    """An ordinary deploy must not rebuild: the projection is on its own volume."""
    from medvault.cli import _reindex_reason

    _seed(vault)
    assert _reindex_reason(session) == "the projection has never been built"

    reindex(session, vault)
    assert _reindex_reason(session) is None


def test_reindex_if_needed_rebuilds_an_empty_projection(vault: Vault, session: Session):
    from medvault.cli import _reindex_reason
    from medvault.models import Document, Observation

    _seed(vault)
    reindex(session, vault)
    session.query(Observation).delete()
    session.query(Document).delete()
    session.flush()

    assert _reindex_reason(session) == "the projection is empty"


def test_a_catalogue_bump_forces_a_rebuild(vault: Vault, session: Session):
    """The retrofit trigger.

    Codes, canonical units and categories are all derived from the catalogue, so
    a new version means every stored row was computed under old rules. Noticing
    that here is what makes extending the catalogue upgrade the whole history on
    the next deploy, rather than only documents filed afterwards.
    """
    from medvault.cli import _reindex_reason
    from medvault.models import ProjectionState

    _seed(vault)
    reindex(session, vault)
    assert _reindex_reason(session) is None

    state = session.get(ProjectionState, 1)
    state.catalog_version = state.catalog_version - 1
    session.flush()

    reason = _reindex_reason(session)
    assert reason is not None and "catalogue moved from version" in reason
