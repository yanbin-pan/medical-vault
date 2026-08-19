"""Turning a printed observation into a comparable one.

This is the single most important function in the projection layer, and its
defining property is that it is *pure and repeatable*. It reads a raw
observation exactly as the vault stores it and returns the derived fields. It
never mutates the vault.

That is what makes the design survive a decade. Improve the catalogue or a
conversion factor, re-run `medvault reindex`, and every observation ever
recorded is re-derived through the new rules. Nothing is stranded at the
quality of the code that happened to exist on the day it was filed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medvault.catalog.registry import Analyte, Catalog, get_catalog
from medvault.catalog.units import UnitConversionError, convert, normalise_unit


@dataclass(slots=True)
class Normalised:
    analyte_code: str
    analyte: Analyte | None
    label_en: str | None
    unit: str | None
    canonical_value: float | None
    canonical_unit: str | None
    body_site: str | None
    laterality: str | None
    category: str
    is_mapped: bool
    notes: list[str]

    @property
    def series_key(self) -> str:
        """Identity of the time series this observation belongs to.

        Body site and laterality are part of it: a left kidney and a right
        kidney are two series, and averaging them silently would hide the case
        where one is growing.
        """
        parts = [self.analyte_code]
        if self.body_site:
            parts.append(self.body_site)
        if self.laterality:
            parts.append(self.laterality)
        return "|".join(parts)


def normalise_observation(row: dict[str, Any], catalog: Catalog | None = None) -> Normalised:
    catalog = catalog or get_catalog()
    notes: list[str] = []

    label_raw = (row.get("label_raw") or "").strip()
    site_hint = row.get("body_site")
    match = catalog.match(label_raw, hint_site=site_hint)

    analyte = match.analyte
    # An explicit code on the observation beats label matching: the extractor
    # may have recognised something the catalogue's synonyms do not cover.
    stated_code = row.get("analyte_code")
    if stated_code and not str(stated_code).startswith("UNMAPPED:"):
        stated = catalog.get(stated_code)
        if stated is not None:
            analyte = stated
        elif analyte is None:
            notes.append(f"code {stated_code} is not in the catalogue; kept as stated")

    if analyte is not None:
        code = analyte.code
        category = analyte.category
        is_mapped = True
    elif stated_code and not str(stated_code).startswith("UNMAPPED:"):
        code = str(stated_code)
        category = "other"
        is_mapped = False
        notes.append("stated code is unknown to the catalogue")
    else:
        # Not an error. The observation is real and is kept in full; it simply
        # has no canonical identity yet, and will acquire one when the
        # catalogue learns this label.
        code = catalog.unmapped_code(label_raw)
        category = "unmapped"
        is_mapped = False
        notes.append("no catalogue entry matched this label")

    laterality = row.get("laterality") or match.laterality
    body_site = site_hint or match.body_site

    unit_raw = normalise_unit(row.get("unit_raw"))
    canonical_value: float | None = None
    canonical_unit: str | None = None

    value = row.get("value_num")
    if value is not None and analyte is not None and analyte.unit:
        target = normalise_unit(analyte.unit) or analyte.unit
        if unit_raw is None:
            # A unitless number against an analyte that has a canonical unit is
            # assumed to be in that unit — labs routinely print the unit once in
            # a column header. Flagged so a reviewer can see the assumption.
            canonical_value, canonical_unit = float(value), target
            notes.append(f"no unit printed; assumed {target}")
        else:
            try:
                canonical_value = convert(float(value), unit_raw, target, analyte.code)
                canonical_unit = target
            except UnitConversionError as exc:
                # Never fabricate a number. The raw value stays authoritative
                # and the series simply skips this point until it can convert.
                notes.append(f"left unconverted: {exc}")
    elif value is not None and unit_raw:
        canonical_value, canonical_unit = float(value), unit_raw

    return Normalised(
        analyte_code=code,
        analyte=analyte,
        label_en=row.get("label_en") or (analyte.name if analyte else None),
        unit=unit_raw,
        canonical_value=canonical_value,
        canonical_unit=canonical_unit,
        body_site=body_site,
        laterality=laterality,
        category=category,
        is_mapped=is_mapped,
        notes=notes,
    )
