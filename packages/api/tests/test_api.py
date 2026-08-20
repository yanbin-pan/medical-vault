"""The HTTP surface, with multi-tenancy as the main event.

Two households sharing one deployment must never see each other's records. The
tests below try to reach across the boundary by every route the API exposes.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from medvault import config, db
from medvault.models import Base
from medvault.projection import reindex
from medvault.vault.store import Vault
from tests.conftest import make_document

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app wired to a throwaway vault and database."""
    vault_path = tmp_path / "vault"
    monkeypatch.setenv("MEDVAULT_VAULT_PATH", str(vault_path))
    monkeypatch.setenv("MEDVAULT_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("MEDVAULT_TRUST_ACCESS_HEADER", "true")
    monkeypatch.setenv("MEDVAULT_ANTHROPIC_API_KEY", "test-key-not-used")
    config.reset_settings_cache()
    db.reset_engine_cache()

    vault = Vault(vault_path)
    vault.initialise()
    # Alice owns her household; Bob owns a separate one and they share nothing.
    vault.ensure_tenant("alice-home", "Alice's Household", [{"email": ALICE, "role": "owner"}])
    vault.ensure_tenant("bob-home", "Bob's Household", [{"email": BOB, "role": "owner"}])
    for tenant, name in [("alice-home", "Alice"), ("bob-home", "Bob")]:
        vault.write_subject(
            {"tenant_id": tenant, "subject_id": "self", "display_name": name}
        )
    make_document(vault, "2026-01-01T00:00:00Z",
                  [{"label_raw": "血红蛋白", "value_num": 150, "unit_raw": "g/L"}],
                  tenant_id="alice-home")
    make_document(vault, "2026-02-01T00:00:00Z",
                  [{"label_raw": "空腹血糖", "value_num": 5.4, "unit_raw": "mmol/L"}],
                  tenant_id="bob-home")

    Base.metadata.create_all(db.get_engine())
    with db.session_scope() as session:
        reindex(session, vault)

    from medvault.main import app

    app.state.vault = vault
    with TestClient(app) as test_client:
        test_client.vault = vault
        yield test_client

    config.reset_settings_cache()
    db.reset_engine_cache()


def as_user(email: str) -> dict[str, str]:
    return {"cf-access-authenticated-user-email": email}


def test_health_reports_what_is_stored(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["documents"] == 2
    assert body["observations"] == 2


def test_a_request_with_no_identity_is_rejected(client: TestClient):
    assert client.get("/api/tenants").status_code == 401


def test_each_user_sees_only_their_own_tenant(client: TestClient):
    alice = client.get("/api/tenants", headers=as_user(ALICE)).json()
    assert [t["id"] for t in alice] == ["alice-home"]
    assert alice[0]["role"] == "owner"

    bob = client.get("/api/tenants", headers=as_user(BOB)).json()
    assert [t["id"] for t in bob] == ["bob-home"]


def test_an_unknown_user_sees_nothing(client: TestClient):
    assert client.get("/api/tenants", headers=as_user("nobody@example.com")).json() == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/tenants/bob-home/subjects",
        "/api/tenants/bob-home/documents",
        "/api/tenants/bob-home/subjects/self/series",
        "/api/tenants/bob-home/subjects/self/summary",
        "/api/tenants/bob-home/subjects/self/correlations",
    ],
)
def test_alice_cannot_reach_bobs_data_by_any_route(client: TestClient, path: str):
    """Crossing the tenant boundary returns 404, not 403.

    A 403 would confirm the tenant exists, which is itself a disclosure.
    """
    response = client.get(path, headers=as_user(ALICE))
    assert response.status_code == 404
    assert client.get(path, headers=as_user(BOB)).status_code == 200


def test_alice_cannot_read_bobs_document_even_with_its_id(client: TestClient):
    bob_documents = client.get("/api/tenants/bob-home/documents", headers=as_user(BOB)).json()
    document_id = bob_documents[0]["id"]

    # Guessing the id and asking under her own tenant must also fail.
    assert client.get(
        f"/api/tenants/alice-home/documents/{document_id}", headers=as_user(ALICE)
    ).status_code == 404
    assert client.get(
        f"/api/tenants/bob-home/documents/{document_id}", headers=as_user(ALICE)
    ).status_code == 404


def test_documents_and_observations_are_returned_for_the_owner(client: TestClient):
    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    assert len(documents) == 1
    document_id = documents[0]["id"]

    detail = client.get(
        f"/api/tenants/alice-home/documents/{document_id}", headers=as_user(ALICE)
    ).json()
    assert detail["provider"]["name_raw"] == "上海电力医院"

    rows = client.get(
        f"/api/tenants/alice-home/documents/{document_id}/observations", headers=as_user(ALICE)
    ).json()
    assert rows[0]["label_raw"] == "血红蛋白"
    assert rows[0]["label_en"] == "Haemoglobin"
    assert rows[0]["canonical_unit"] == "g/L"


def test_the_original_image_is_served_back(client: TestClient):
    """Being able to see the photograph beside the numbers is what makes it auditable."""
    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    response = client.get(
        f"/api/tenants/alice-home/documents/{documents[0]['id']}/original",
        headers=as_user(ALICE),
    )
    assert response.status_code == 200
    assert response.content.startswith(b"fake-jpeg")


def test_series_endpoint_returns_plottable_points(client: TestClient):
    series = client.get(
        "/api/tenants/alice-home/subjects/self/series", headers=as_user(ALICE)
    ).json()
    haemoglobin = next(s for s in series if s["analyte_code"] == "LOINC:718-7")
    assert haemoglobin["unit"] == "g/L"
    assert haemoglobin["points"][0]["value"] == 150
    assert haemoglobin["label_raw_examples"] == ["血红蛋白"]


def test_catalog_is_exposed_for_the_ui(client: TestClient):
    catalog = client.get("/api/catalog", headers=as_user(ALICE)).json()
    assert catalog["version"] >= 1
    codes = {a["code"] for a in catalog["analytes"]}
    assert "LOINC:718-7" in codes and "MV:kidney.length" in codes


def test_a_viewer_cannot_write(client: TestClient):
    client.vault.set_members(
        "alice-home",
        [{"email": ALICE, "role": "owner"}, {"email": "read@example.com", "role": "viewer"}],
    )
    with db.session_scope() as session:
        reindex(session, client.vault)

    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    response = client.post(
        f"/api/tenants/alice-home/documents/{documents[0]['id']}/review",
        json={"status": "verified"},
        headers=as_user("read@example.com"),
    )
    assert response.status_code == 403


def test_review_marks_a_document_verified(client: TestClient):
    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    response = client.post(
        f"/api/tenants/alice-home/documents/{documents[0]['id']}/review",
        json={"status": "verified", "note": "checked against the paper"},
        headers=as_user(ALICE),
    )
    assert response.status_code == 200
    assert response.json()["review"]["status"] == "verified"
    assert response.json()["review"]["reviewed_by"] == ALICE


def test_a_correction_supersedes_and_keeps_the_original(client: TestClient):
    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    original_id = documents[0]["id"]

    response = client.post(
        f"/api/tenants/alice-home/documents/{original_id}/corrections",
        json={
            "observations": [
                {"label_raw": "血红蛋白", "value_num": 155, "unit_raw": "g/L"}
            ],
            "note": "misread 150 for 155",
        },
        headers=as_user(ALICE),
    )
    assert response.status_code == 201
    corrected = response.json()
    assert corrected["supersedes"] == original_id

    current = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    assert [d["id"] for d in current] == [corrected["id"]]

    everything = client.get(
        "/api/tenants/alice-home/documents?include_superseded=true", headers=as_user(ALICE)
    ).json()
    assert len(everything) == 2

    series = client.get(
        "/api/tenants/alice-home/subjects/self/series", headers=as_user(ALICE)
    ).json()
    haemoglobin = next(s for s in series if s["analyte_code"] == "LOINC:718-7")
    assert [p["value"] for p in haemoglobin["points"]] == [155]


def test_correcting_an_already_superseded_document_is_refused(client: TestClient):
    documents = client.get("/api/tenants/alice-home/documents", headers=as_user(ALICE)).json()
    original_id = documents[0]["id"]
    payload = {"observations": [{"label_raw": "血红蛋白", "value_num": 155, "unit_raw": "g/L"}]}
    client.post(
        f"/api/tenants/alice-home/documents/{original_id}/corrections",
        json=payload, headers=as_user(ALICE),
    )
    again = client.post(
        f"/api/tenants/alice-home/documents/{original_id}/corrections",
        json=payload, headers=as_user(ALICE),
    )
    assert again.status_code == 409


def test_upload_rejects_an_empty_file(client: TestClient):
    response = client.post(
        "/api/tenants/alice-home/subjects/self/documents",
        files={"file": ("scan.jpg", b"", "image/jpeg")},
        headers=as_user(ALICE),
    )
    assert response.status_code == 400


def test_upload_to_another_tenant_is_refused(client: TestClient):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 60), "white").save(buffer, format="JPEG")
    response = client.post(
        "/api/tenants/bob-home/subjects/self/documents",
        files={"file": ("scan.jpg", buffer.getvalue(), "image/jpeg")},
        headers=as_user(ALICE),
    )
    assert response.status_code == 404
