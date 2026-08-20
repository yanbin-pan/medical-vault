"""The extraction pipeline, end to end, with the model call stubbed.

The network call itself is not exercised here. Everything around it is: image
preparation, the shape the model is asked for, the conversion into vault
records, and the fact that a document read in Chinese becomes a queryable
English time series without any label being translated on the way in.

The fixture data is transcribed from a real carotid ultrasound report — a table
with one row per vessel and one column per measurement, which is the layout
most likely to be mangled.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from medvault.extraction.claude import (
    DocumentExtractor,
    ExtractionError,
    ExtractionOutcome,
    to_vault_records,
)
from medvault.extraction.images import prepare_for_vision, sniff_media_type
from medvault.extraction.schema import ExtractionResult
from medvault.models import Observation
from medvault.projection import reindex
from medvault.vault.store import Vault

CAROTID_REPORT = {
    "document_type": "ultrasound",
    "language": "zh-Hans",
    "captured_at": "2026-07-11T09:55:25Z",
    "provider": {
        "name_raw": "上海电力医院",
        "name_en": "Shanghai Electric Power Hospital",
        "country": "CN",
        "accession": "96560290",
    },
    "patient": {"name_raw": "Pan YAN BIN", "sex": "male", "age_text": "31岁"},
    "values": [
        {"label_raw": "左颈总A前后径", "value_num": 6.2, "unit_raw": "mm",
         "body_site": "common-carotid-artery", "laterality": "left", "confidence": 0.95},
        {"label_raw": "右颈总A前后径", "value_num": 5.1, "unit_raw": "mm",
         "body_site": "common-carotid-artery", "laterality": "right", "confidence": 0.95},
        {"label_raw": "左颈内A前后径", "value_num": 3.6, "unit_raw": "mm",
         "body_site": "internal-carotid-artery", "laterality": "left", "confidence": 0.9},
        {"label_raw": "右颈内A前后径", "value_num": 3.6, "unit_raw": "mm",
         "body_site": "internal-carotid-artery", "laterality": "right", "confidence": 0.9},
        {"label_raw": "左颈总A RI", "value_num": 0.78, "body_site": "common-carotid-artery",
         "laterality": "left", "confidence": 0.9},
        {"label_raw": "右颈总A RI", "value_num": 0.65, "body_site": "common-carotid-artery",
         "laterality": "right", "confidence": 0.9},
        {"label_raw": "左侧椎动脉内径", "value_num": 3.4, "unit_raw": "mm",
         "body_site": "vertebral-artery", "laterality": "left", "confidence": 0.85},
        {"label_raw": "左侧椎动脉峰值流速", "value_num": 56, "unit_raw": "cm/s",
         "body_site": "vertebral-artery", "laterality": "left", "confidence": 0.85},
    ],
    "narrative": [
        {"section": "impression", "text_raw": "双侧颈动脉血流通畅",
         "text_en": "Bilateral carotid arterial flow is patent"},
        {"section": "impression", "text_raw": "双侧椎动脉血流通畅",
         "text_en": "Bilateral vertebral arterial flow is patent"},
    ],
    "warnings": ["图像质量：乙 (image quality graded B)"],
}


class StubClient:
    """Stands in for anthropic.Anthropic, recording what it was asked."""

    def __init__(self, payload: dict, stop_reason: str = "end_turn"):
        self._payload = payload
        self._stop_reason = stop_reason
        self.last_request: dict | None = None
        self.messages = SimpleNamespace(parse=self._parse)

    def _parse(self, **kwargs):
        self.last_request = kwargs
        return SimpleNamespace(
            parsed_output=ExtractionResult.model_validate(self._payload),
            stop_reason=self._stop_reason,
            usage=SimpleNamespace(input_tokens=2400, output_tokens=900),
        )


@pytest.fixture
def jpeg_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2400, 3200), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_the_model_is_sent_an_image_and_asked_for_the_schema(jpeg_bytes: bytes):
    client = StubClient(CAROTID_REPORT)
    outcome = DocumentExtractor(client=client, model="claude-opus-5").extract(jpeg_bytes)

    request = client.last_request
    assert request["model"] == "claude-opus-5"
    assert request["output_format"] is ExtractionResult
    assert request["thinking"] == {"type": "adaptive"}
    blocks = request["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert blocks[-1]["type"] == "text"
    assert outcome.model == "claude-opus-5"
    assert outcome.input_tokens == 2400


def test_oversized_photographs_are_downscaled_before_upload(jpeg_bytes: bytes):
    prepared = prepare_for_vision(jpeg_bytes, "image/jpeg", max_edge=1568)
    assert len(prepared) == 1
    assert max(prepared[0].width, prepared[0].height) <= 1568


def test_file_type_is_detected_from_content_not_from_the_name(jpeg_bytes: bytes):
    assert sniff_media_type(jpeg_bytes, declared="application/octet-stream") == "image/jpeg"
    assert sniff_media_type(b"%PDF-1.7", declared="image/png") == "application/pdf"


def test_a_refusal_is_an_error_not_an_empty_result(jpeg_bytes: bytes):
    client = StubClient(CAROTID_REPORT, stop_reason="refusal")
    with pytest.raises(ExtractionError, match="declined"):
        DocumentExtractor(client=client).extract(jpeg_bytes)


def test_heic_is_rejected_with_an_actionable_message():
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32
    with pytest.raises(ExtractionError, match="HEIC"):
        DocumentExtractor(client=StubClient(CAROTID_REPORT)).extract(heic)


def test_a_document_with_no_date_is_refused_rather_than_dated_today():
    """A guessed date would be plotted, and a wrong point on a trend line is worse than none."""
    undated = {**CAROTID_REPORT, "captured_at": None}
    outcome = _outcome(undated)
    with pytest.raises(ExtractionError, match="no examination date"):
        to_vault_records(outcome, "acme", "self", {"media_type": "image/jpeg"})

    # ...unless the uploader supplies one.
    envelope, _ = to_vault_records(
        outcome, "acme", "self", {"media_type": "image/jpeg"},
        fallback_captured_at="2026-07-11",
    )
    assert envelope["captured_at"] == "2026-07-11T00:00:00Z"


def test_extraction_carries_its_own_provenance():
    envelope, _ = to_vault_records(
        _outcome(CAROTID_REPORT), "acme", "self", {"media_type": "image/jpeg"}
    )
    extraction = envelope["extraction"]
    assert extraction["method"] == "ai_vision"
    assert extraction["model"] == "claude-opus-5"
    assert extraction["prompt_version"]
    # An AI reading is a draft until a person confirms it.
    assert envelope["review"]["status"] == "unreviewed"
    assert envelope["narrative"][0]["text_raw"] == "双侧颈动脉血流通畅"


def test_chinese_report_becomes_a_queryable_english_series(vault: Vault, session: Session):
    """The whole point of the application, in one test."""
    envelope, observations = to_vault_records(
        _outcome(CAROTID_REPORT), "acme", "self", {"media_type": "image/jpeg"}
    )
    vault.write_document(envelope, b"\xff\xd8\xff-scan", observations)
    reindex(session, vault)

    rows = session.scalars(select(Observation)).all()
    assert len(rows) == 8

    # Every printed label survived untranslated...
    assert {r.label_raw for r in rows} >= {"左颈总A前后径", "右颈总A RI", "左侧椎动脉峰值流速"}
    # ...and every one of them acquired an English name and a code.
    assert all(r.is_mapped for r in rows)
    assert all(r.label_en for r in rows)

    # Left and right are separate, comparable series.
    diameters = [r for r in rows if r.analyte_code == "MV:artery.diameter"]
    by_side = {(r.body_site, r.laterality): r.canonical_value for r in diameters}
    assert by_side[("common-carotid-artery", "left")] == 6.2
    assert by_side[("common-carotid-artery", "right")] == 5.1
    assert by_side[("internal-carotid-artery", "left")] == 3.6

    # The resistive index landed on its own analyte, not on a diameter.
    ri = [r for r in rows if r.analyte_code == "MV:artery.resistive-index"]
    assert sorted(r.canonical_value for r in ri) == [0.65, 0.78]

    velocity = [r for r in rows if r.analyte_code == "MV:artery.peak-systolic-velocity"]
    assert velocity[0].canonical_value == 56 and velocity[0].canonical_unit == "cm/s"


def test_rows_without_a_label_are_discarded_not_stored_blank():
    payload = {**CAROTID_REPORT, "values": [*CAROTID_REPORT["values"], {"label_raw": "  ", "value_num": 9}]}
    _, observations = to_vault_records(
        _outcome(payload), "acme", "self", {"media_type": "image/jpeg"}
    )
    assert len(observations) == 8


def _outcome(payload: dict) -> ExtractionOutcome:
    return ExtractionOutcome(
        result=ExtractionResult.model_validate(payload),
        model="claude-opus-5",
        prompt_version="test",
        extracted_at="2026-08-19T00:00:00Z",
        input_tokens=1,
        output_tokens=1,
    )
