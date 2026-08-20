"""What the model is asked to return.

Note what is *not* here: no LOINC codes, no canonical units, no computed flags.
The model's job is to read the paper faithfully and nothing more. Assigning
codes and converting units is done afterwards, deterministically, by
`medvault.catalog` — because that step has to stay improvable. A code the model
guessed in 2026 would be frozen into the record; a code the catalogue assigns
is re-derived on every reindex.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedValue(BaseModel):
    """One measurement, exactly as printed."""

    label_raw: str = Field(
        description=(
            "The measurement's name copied character for character from the "
            "document, in its original language and script. Never translate "
            "this field, never expand an abbreviation, never tidy it up."
        )
    )
    label_en: str | None = Field(
        default=None, description="Plain-English rendering of label_raw."
    )
    value_num: float | None = Field(
        default=None, description="The numeric result. Null if the result is not a number."
    )
    value_text: str | None = Field(
        default=None,
        description=(
            "The result when it is not numeric, e.g. 阴性, negative, 未见异常. "
            "Copy it verbatim in the original language."
        ),
    )
    unit_raw: str | None = Field(
        default=None,
        description=(
            "The unit exactly as printed, e.g. mmol/L, mm, ×10⁹/L. Null if none "
            "is printed. Do not infer or convert a unit."
        ),
    )
    comparator: str | None = Field(
        default=None,
        description=(
            "Set to <, <=, > or >= when the result was printed as a bound such "
            "as '<0.01'. Put the number in value_num and the bound here."
        ),
    )
    reference_low: float | None = Field(
        default=None, description="Lower end of the reference range printed on THIS report."
    )
    reference_high: float | None = Field(default=None, description="Upper end of that range.")
    reference_text: str | None = Field(
        default=None, description="The reference when it is not a numeric interval, e.g. 阴性."
    )
    abnormal_flag: str | None = Field(
        default=None,
        description=(
            "How the lab flagged it, if it did: low, high, normal, abnormal, "
            "critical_low, critical_high. Read this from the report's own arrows "
            "(↑ ↓) or letters (H L). Do not decide it yourself by comparing the "
            "value to the range."
        ),
    )
    body_site: str | None = Field(
        default=None,
        description=(
            "The organ or vessel measured, in English and lowercase, when the "
            "label alone is ambiguous: kidney, liver, spleen, gallbladder, "
            "common-carotid-artery, vertebral-artery."
        ),
    )
    laterality: str | None = Field(
        default=None, description="left, right or bilateral. Null when not applicable."
    )
    specimen: str | None = Field(default=None, description="serum, whole-blood, urine, ...")
    method: str | None = Field(default=None, description="Assay or imaging method, if stated.")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are of this row, 0 to 1. Lower it for anything blurred, "
            "cut off, handwritten or ambiguous. An honest low score is far more "
            "useful than a confident guess."
        ),
    )
    source_context: str | None = Field(
        default=None,
        description="The short printed line this value was read from, so a human can check it.",
    )


class NarrativeSection(BaseModel):
    """Prose that is not a measurement — findings, impressions, history."""

    section: str = Field(
        description=(
            "One of: findings, impression, clinical_history, technique, "
            "recommendation, other."
        )
    )
    text_raw: str = Field(description="The passage verbatim, in the original language.")
    text_en: str | None = Field(default=None, description="English translation of that passage.")


class ProviderInfo(BaseModel):
    name_raw: str | None = Field(default=None, description="Institution name as printed.")
    name_en: str | None = Field(default=None, description="Its English name or a translation.")
    department_raw: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2, e.g. CN, GB.")
    accession: str | None = Field(
        default=None, description="The report's own number (报告号, 门诊号, 超声号)."
    )


class PatientHints(BaseModel):
    """Identity printed on the document.

    Used only to check the upload was filed against the right person. It is
    never used to create or rename a subject.
    """

    name_raw: str | None = None
    sex: str | None = Field(default=None, description="male, female or null.")
    age_text: str | None = Field(default=None, description="Age as printed, e.g. 31岁.")
    birth_date: str | None = Field(default=None, description="ISO date if printed.")


class ExtractionResult(BaseModel):
    """Everything read from one document."""

    document_type: str = Field(
        description=(
            "What kind of report this is: blood_panel, ultrasound, radiology, "
            "pathology, prescription, discharge_summary, vaccination, vitals, other."
        )
    )
    language: str | None = Field(
        default=None, description="BCP 47 tag of the document's main language, e.g. zh-Hans."
    )
    captured_at: str | None = Field(
        default=None,
        description=(
            "When the test or scan happened, as an ISO 8601 timestamp. Prefer the "
            "collection or examination time over the reporting time. Null if the "
            "document shows no date — never invent one."
        ),
    )
    provider: ProviderInfo = Field(default_factory=ProviderInfo)
    patient: PatientHints = Field(default_factory=PatientHints)
    values: list[ExtractedValue] = Field(
        default_factory=list, description="Every measurement on the document."
    )
    narrative: list[NarrativeSection] = Field(
        default_factory=list, description="Every prose section."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Anything that would make a human distrust this reading: a cut-off "
            "edge, glare, a column you could not align, handwriting."
        ),
    )
