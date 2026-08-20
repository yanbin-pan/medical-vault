"""Preparing an uploaded file for the vision API.

The original is never touched. Everything here produces a *copy* to send to the
model; what lands in the vault is always the bytes the user uploaded.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_PAGES = 8


@dataclass(slots=True)
class PreparedImage:
    media_type: str
    data_b64: str
    width: int
    height: int


def sniff_media_type(payload: bytes, declared: str | None = None) -> str:
    """Identify a file by its magic bytes.

    Phone uploads routinely arrive as `application/octet-stream` or with a
    filename extension that lies, so the declared type is only a fallback.
    """
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"%PDF"):
        return "application/pdf"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if payload[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "image/heic"
    return declared or "application/octet-stream"


def prepare_for_vision(
    payload: bytes, media_type: str, max_edge: int = 1568
) -> list[PreparedImage]:
    """Turn an upload into the page images to send to the model."""
    if media_type == "application/pdf":
        return _pdf_to_images(payload, max_edge)
    return [_prepare_single(payload, max_edge)]


def _prepare_single(payload: bytes, max_edge: int) -> PreparedImage:
    with Image.open(io.BytesIO(payload)) as image:
        # Phone cameras record rotation in EXIF rather than in the pixels. Without
        # this a sideways photograph reaches the model sideways, and the reading
        # accuracy falls off a cliff for dense tabular reports.
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90, optimize=True)
        return PreparedImage(
            media_type="image/jpeg",
            data_b64=base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
            width=image.width,
            height=image.height,
        )


def _pdf_to_images(payload: bytes, max_edge: int) -> list[PreparedImage]:
    """Render PDF pages to images.

    Rendering rather than text extraction: hospital PDFs are frequently scans
    with no text layer, and a text layer that exists is often mis-ordered for
    multi-column lab tables. The model reads the rendered page the same way a
    person would.
    """
    import pypdfium2

    pages: list[PreparedImage] = []
    document = pypdfium2.PdfDocument(io.BytesIO(payload))
    try:
        for index in range(min(len(document), MAX_PAGES)):
            page = document[index]
            # 200 DPI is the point where small Chinese characters stay legible
            # without the payload becoming unreasonable.
            bitmap = page.render(scale=200 / 72)
            image = bitmap.to_pil()
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
            pages.append(
                PreparedImage(
                    media_type="image/jpeg",
                    data_b64=base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
                    width=image.width,
                    height=image.height,
                )
            )
    finally:
        document.close()
    return pages
