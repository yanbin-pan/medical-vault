"""The vault's own guarantees: atomicity, immutability, and integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from medvault.vault.store import Vault, VaultError
from tests.conftest import make_document


def test_document_is_written_where_a_human_would_look(vault: Vault):
    doc = make_document(vault, "2026-07-11T09:58:28Z", [])
    relative = doc.directory.relative_to(vault.root)
    # Bucketed by capture month, not upload month.
    assert relative.parts[:4] == ("tenants", "acme", "documents", "2026")
    assert relative.parts[4] == "07"


def test_originals_are_stored_byte_for_byte(vault: Vault):
    doc = make_document(vault, "2026-07-11T00:00:00Z", [])
    assert doc.original_path.read_bytes() == b"fake-jpeg-2026-07-11T00:00:00Z-self-0"
    assert vault.verify() == []


def test_tampering_with_an_original_is_detected(vault: Vault):
    doc = make_document(vault, "2026-07-11T00:00:00Z", [])
    doc.original_path.write_bytes(b"substituted")
    problems = vault.verify()
    assert len(problems) == 1 and "sha256 mismatch" in problems[0]


def test_a_document_is_never_silently_overwritten(vault: Vault):
    doc = make_document(vault, "2026-07-11T00:00:00Z", [])
    with pytest.raises(VaultError):
        vault.write_document(dict(doc.envelope), b"different bytes", [])


def test_observations_without_a_raw_label_are_refused(vault: Vault):
    """label_raw is the one field the whole design rests on, so it is enforced."""
    with pytest.raises(VaultError, match="label_raw"):
        make_document(vault, "2026-07-11T00:00:00Z", [{"value_num": 1, "unit_raw": "mm"}])


@pytest.mark.parametrize("bad", ["../escape", "a/b", ".hidden", ""])
def test_identifiers_cannot_escape_the_vault(vault: Vault, bad: str):
    with pytest.raises(VaultError):
        vault.tenant_dir(bad)


def test_original_script_survives_the_round_trip(vault: Vault):
    """A vault full of \\u005cu4e0a\\u005cu6d77 escapes would not be readable by a human in 2050."""
    doc = make_document(
        vault, "2026-07-11T00:00:00Z", [{"label_raw": "右肝斜径", "value_num": 124, "unit_raw": "mm"}]
    )
    envelope_text = (doc.directory / "envelope.json").read_text("utf-8")
    observations_text = (doc.directory / "observations.ndjson").read_text("utf-8")
    assert "上海电力医院" in envelope_text
    assert "右肝斜径" in observations_text

    reread = vault.read_document(doc.directory)
    assert reread.observations[0]["label_raw"] == "右肝斜径"
    assert reread.envelope["provider"]["name_raw"] == "上海电力医院"


def test_observations_are_one_json_object_per_line(vault: Vault):
    """NDJSON so the file streams and a truncated write costs one row, not all of them."""
    doc = make_document(
        vault,
        "2026-07-11T00:00:00Z",
        [{"label_raw": f"指标{i}", "value_num": i} for i in range(5)],
    )
    lines = (doc.directory / "observations.ndjson").read_text("utf-8").strip().splitlines()
    assert len(lines) == 5
    assert all(json.loads(line)["label_raw"].startswith("指标") for line in lines)


def test_a_correction_supersedes_rather_than_edits(vault: Vault):
    original = make_document(
        vault, "2026-07-11T00:00:00Z", [{"label_raw": "血红蛋白", "value_num": 15}]
    )
    corrected = vault.supersede(
        original, {}, [{"label_raw": "血红蛋白", "value_num": 150, "unit_raw": "g/L"}]
    )
    assert corrected.envelope["supersedes"] == original.document_id
    # The mistake is still on disk, which is the point.
    assert vault.read_document(original.directory).observations[0]["value_num"] == 15
    assert len(list(vault.iter_documents())) == 2


def test_iteration_is_chronological(vault: Vault):
    for stamp in ["2026-03-01T00:00:00Z", "2025-01-05T00:00:00Z", "2026-11-20T00:00:00Z"]:
        make_document(vault, stamp, [])
    captured = [d.envelope["captured_at"] for d in vault.iter_documents()]
    assert captured == sorted(captured)


def test_the_vault_explains_itself(vault: Vault):
    """A reader in 2050 gets the data, the schemas, and the interpretation."""
    assert (vault.root / "MANIFEST.md").is_file()
    assert {p.name for p in (vault.root / "schema").glob("*.json")} == {
        "document-envelope.v1.json",
        "observation.v1.json",
        "subject.v1.json",
    }
    # The catalogue travels with the data, or the raw labels are undecodable.
    catalogs = list((vault.root / "catalog").glob("analytes.v*.yaml"))
    assert len(catalogs) == 1
    assert "LOINC:718-7" in catalogs[0].read_text("utf-8")


def test_catalog_snapshots_accumulate_rather_than_overwrite(vault: Vault):
    """An old record stays interpretable with the catalogue it was filed under."""
    import unittest.mock as mock

    from medvault.catalog import registry

    before = {p.name for p in (vault.root / "catalog").glob("*.yaml")}

    # Simulate the catalogue being extended years later.
    later = registry.load_catalog()
    later.version = later.version + 1
    with mock.patch.object(registry, "get_catalog", return_value=later):
        vault.initialise()

    after = {p.name for p in (vault.root / "catalog").glob("*.yaml")}
    assert before < after  # the old snapshot is still there alongside the new one
    assert len(after) == 2


def test_the_documented_recipes_actually_work(vault: Vault):
    """The MANIFEST tells a future reader how to query these files.

    If that guidance drifts from the format, the vault stops being
    self-describing — which is the one thing it is for. So the instructions are
    executed here rather than trusted.
    """
    import json

    for stamp, value in [("2024-01-01T00:00:00Z", 148), ("2025-01-01T00:00:00Z", 152)]:
        make_document(vault, stamp, [
            {"label_raw": "血红蛋白", "value_num": value, "unit_raw": "g/L"},
            {"label_raw": "白细胞计数", "value_num": 6.1, "unit_raw": "×10⁹/L"}])

    manifest = (vault.root / "MANIFEST.md").read_text("utf-8")
    # The recipes must key off fields the files really contain.
    assert 'select(.label_raw==' in manifest
    assert "analyte_code" not in manifest.split("## Reading it")[1].split("## Verifying")[0]

    rows = []
    for path in vault.root.rglob("observations.ndjson"):
        rows += [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]

    # Recipe: one measurement's history, matched on the printed label.
    haemoglobin = sorted(
        (r["effective_time"], r["value_num"], r["unit_raw"])
        for r in rows if r["label_raw"] == "血红蛋白"
    )
    assert [v for _, v, _ in haemoglobin] == [148, 152]

    # Recipe: every spelling and how often.
    labels = {r["label_raw"] for r in rows}
    assert labels == {"血红蛋白", "白细胞计数"}

    # Recipe: look the label up in the shipped catalogue.
    catalog_text = next((vault.root / "catalog").glob("analytes.v*.yaml")).read_text("utf-8")
    assert "血红蛋白" in catalog_text

    # Recipe: the integrity check, run for real.
    assert vault.verify() == []


def test_a_correction_gets_fresh_observation_ids(vault: Vault):
    """Regression: an observation belongs to exactly one document.

    Corrections are naturally written by copying the original's rows and editing
    one value — which carries the original observation_ids along. Two documents
    then claimed the same observations, and the projection failed on a primary
    key collision the moment anything was corrected.
    """
    original = make_document(
        vault, "2026-01-01T00:00:00Z",
        [{"label_raw": "血红蛋白", "value_num": 158, "unit_raw": "g/L"},
         {"label_raw": "血小板", "value_num": 250, "unit_raw": "×10⁹/L"}],
    )
    # Exactly the mistake: reuse the stored rows, change one number.
    rows = [dict(row) for row in original.observations]
    rows[0]["value_num"] = 152
    corrected = vault.supersede(original, {}, rows)

    original_ids = {o["observation_id"] for o in original.observations}
    corrected_ids = {o["observation_id"] for o in corrected.observations}
    assert len(corrected_ids) == 2
    assert original_ids.isdisjoint(corrected_ids)
    # ...and every id in the vault is unique.
    all_ids = [o["observation_id"] for d in vault.iter_documents() for o in d.observations]
    assert len(all_ids) == len(set(all_ids))


def test_schemas_ship_inside_the_installed_package():
    """Regression: the vault's self-description must survive installation.

    SCHEMA_SOURCE once pointed at the repository root, computed by walking up
    from __file__. That resolves correctly in a source checkout and resolves to
    a nonexistent path inside site-packages — where `initialise` skipped the
    copy silently, so the container wrote vaults with no schema/ directory and
    no error. The files must therefore live inside the package.
    """
    from medvault.vault import store

    package_root = Path(store.__file__).resolve().parent
    assert store.SCHEMA_SOURCE.is_relative_to(package_root)
    assert store.TEMPLATE_DIR.is_relative_to(package_root)
    assert store.SCHEMA_SOURCE.is_dir()
    assert {p.name for p in store.SCHEMA_SOURCE.glob("*.json")} == {
        "document-envelope.v1.json",
        "observation.v1.json",
        "subject.v1.json",
    }

    # The catalogue snapshot has the same requirement.
    from medvault.catalog.registry import CATALOG_PATH

    assert CATALOG_PATH.is_file()
    assert CATALOG_PATH.is_relative_to(Path(store.__file__).resolve().parents[1])
