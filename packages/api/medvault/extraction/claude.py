"""Reading a medical document with Claude's vision capability.

The output of this module is a *draft*. It is written to the vault marked
`unreviewed`, with the model, prompt version and per-row confidence recorded
alongside, so that a later reader can tell how a number got there and how much
to trust it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anthropic

from medvault.config import get_settings
from medvault.extraction.images import prepare_for_vision, sniff_media_type
from medvault.extraction.prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from medvault.extraction.schema import ExtractionResult

log = logging.getLogger(__name__)


class ExtractionError(RuntimeError):
    pass


@dataclass(slots=True)
class ExtractionOutcome:
    result: ExtractionResult
    model: str
    prompt_version: str
    extracted_at: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def envelope_fragment(self) -> dict[str, Any]:
        """The provenance block recorded in the document envelope."""
        return {
            "method": "ai_vision",
            "model": self.model,
            "prompt_version": self.prompt_version,
            "extracted_at": self.extracted_at,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "warnings": list(self.result.warnings),
        }


class DocumentExtractor:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None):
        settings = get_settings()
        self._model = model or settings.extraction_model
        self._max_tokens = settings.extraction_max_tokens
        self._max_edge = settings.max_image_edge_px
        if client is not None:
            self._client = client
        else:
            if not settings.anthropic_api_key:
                raise ExtractionError(
                    "no Anthropic API key configured; set MEDVAULT_ANTHROPIC_API_KEY"
                )
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def extract(
        self, payload: bytes, media_type: str | None = None, hint: str | None = None
    ) -> ExtractionOutcome:
        """Read one document. Raises ExtractionError rather than returning junk."""
        resolved_type = sniff_media_type(payload, media_type)
        if resolved_type == "image/heic":
            # Pillow cannot decode HEIC without a plugin that has no arm64 wheel.
            # Failing clearly beats a confusing decode error deep in the stack.
            raise ExtractionError(
                "HEIC images are not supported; convert to JPEG before uploading"
            )

        try:
            pages = prepare_for_vision(payload, resolved_type, self._max_edge)
        except Exception as exc:
            raise ExtractionError(f"could not read the uploaded file: {exc}") from exc
        if not pages:
            raise ExtractionError("the uploaded file contained no readable pages")

        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": page.media_type,
                    "data": page.data_b64,
                },
            }
            for page in pages
        ]
        content.append({"type": "text", "text": build_user_prompt(hint)})

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                # Adaptive thinking: aligning a multi-column table of Chinese
                # analyte names against its value columns is exactly the kind of
                # careful reading that benefits from it.
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": content}],
                output_format=ExtractionResult,
            )
        except anthropic.RateLimitError as exc:
            raise ExtractionError("rate limited by the Anthropic API; try again shortly") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"extraction failed ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError(f"could not reach the Anthropic API: {exc}") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("the model declined to read this document")

        parsed = response.parsed_output
        if parsed is None:
            raise ExtractionError("the model returned no structured output")

        usage = getattr(response, "usage", None)
        return ExtractionOutcome(
            result=parsed,
            model=self._model,
            prompt_version=PROMPT_VERSION,
            extracted_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )


def to_vault_records(
    outcome: ExtractionOutcome,
    tenant_id: str,
    subject_id: str,
    source: dict[str, Any],
    fallback_captured_at: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert an extraction into the envelope and observation rows to store.

    Nothing is dropped here and nothing is computed here. Codes, canonical units
    and categories are all left for the projection to derive.
    """
    result = outcome.result
    captured_at = result.captured_at or fallback_captured_at
    if not captured_at:
        # No date anywhere. Recording the upload time would silently place the
        # document on the timeline at the wrong point, so the caller is required
        # to supply one instead.
        raise ExtractionError(
            "no examination date was found on the document; supply one when uploading"
        )

    envelope = {
        "tenant_id": tenant_id,
        "subject_id": subject_id,
        "captured_at": _normalise_timestamp(captured_at),
        "document_type": result.document_type,
        "language": result.language,
        "provider": result.provider.model_dump(exclude_none=True) or None,
        "source": source,
        "extraction": outcome.envelope_fragment(),
        "review": {"status": "unreviewed"},
        "narrative": [n.model_dump(exclude_none=True) for n in result.narrative],
    }

    observations: list[dict[str, Any]] = []
    for value in result.values:
        if not value.label_raw or not value.label_raw.strip():
            continue  # a row with no label cannot be identified later; drop it
        row = value.model_dump(exclude_none=True)
        row.pop("label_raw", None)
        observations.append(
            {
                "label_raw": value.label_raw.strip(),
                **{k: v for k, v in row.items() if k != "label_raw"},
                "effective_time": envelope["captured_at"],
            }
        )
    return envelope, observations


def _normalise_timestamp(value: str) -> str:
    """Accept a date or a full timestamp and return an ISO-8601 instant."""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise ExtractionError(f"unparseable examination date: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
