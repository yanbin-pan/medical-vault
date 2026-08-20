"""Unit normalisation.

The problem this solves is the reason the vault stores two values per
observation. A Shanghai lab prints glucose in mmol/L; a London lab prints it in
mg/dL. Plotted naively on one axis the patient appears to have become
diabetic on the flight home.

So every observation keeps what was printed (`value_num` + `unit_raw`) *and* a
value converted to a single canonical unit per analyte (`canonical_value` +
`canonical_unit`). The canonical pair is derived: it is recomputed from the raw
pair on every reindex, so fixing a conversion factor here and re-running
repairs the entire history without touching the vault.

Two kinds of conversion live here:

* **Dimensional** — mm to cm, g to mg. Analyte-independent, always safe.
* **Molar** — mg/dL to mmol/L. Requires the substance's molar mass, so it is
  keyed by analyte code. Getting this wrong is silent and clinically
  significant, so an unknown molar conversion is refused rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Written forms seen on real reports, mapped to a single spelling. Chinese
# reports frequently use full-width characters and ×10⁹/L style superscripts.
_UNIT_ALIASES = {
    "": "",
    "%": "%",
    "％": "%",
    "l": "L",
    "ml": "mL",
    "dl": "dL",
    "u/l": "U/L",
    "iu/l": "[IU]/L",
    "ui/l": "[IU]/L",
    "miu/ml": "m[IU]/mL",
    "g/l": "g/L",
    "g/dl": "g/dL",
    "mg/l": "mg/L",
    "mg/dl": "mg/dL",
    "μg/l": "ug/L",
    "ug/l": "ug/L",
    "µg/l": "ug/L",
    "μg/dl": "ug/dL",
    "ng/ml": "ng/mL",
    "pg/ml": "pg/mL",
    "mmol/l": "mmol/L",
    "umol/l": "umol/L",
    "μmol/l": "umol/L",
    "µmol/l": "umol/L",
    "nmol/l": "nmol/L",
    "pmol/l": "pmol/L",
    "mm": "mm",
    "cm": "cm",
    "mm/h": "mm/h",
    "cm/s": "cm/s",
    "m/s": "m/s",
    "mmhg": "mm[Hg]",
    "kg": "kg",
    "g": "g",
    "mg": "mg",
    "fl": "fL",
    "pg": "pg",
    "s": "s",
    "sec": "s",
    "秒": "s",
    "岁": "a",
    "mhz": "MHz",
    # Cell counts. Chinese reports write 10^9/L, 10*9/L, ×10⁹/L for the same thing.
    "10^9/l": "10*9/L",
    "10*9/l": "10*9/L",
    "×10^9/l": "10*9/L",
    "x10^9/l": "10*9/L",
    "10⁹/l": "10*9/L",
    "10^12/l": "10*12/L",
    "10*12/l": "10*12/L",
    "×10^12/l": "10*12/L",
    "x10^12/l": "10*12/L",
    "10¹²/l": "10*12/L",
    "10^6/ul": "10*6/uL",
    "10^3/ul": "10*3/uL",
    "/ul": "/uL",
    "/hp": "/[HPF]",
    "个/ul": "/uL",
}

# Pure scale factors between units of the same dimension: value * factor = base.
_DIMENSIONAL: dict[str, tuple[str, float]] = {
    "mm": ("mm", 1.0),
    "cm": ("mm", 10.0),
    "m": ("mm", 1000.0),
    "m/s": ("cm/s", 100.0),
    "cm/s": ("cm/s", 1.0),
    "g/L": ("g/L", 1.0),
    "g/dL": ("g/L", 10.0),
    "mg/L": ("mg/L", 1.0),
    "mg/dL": ("mg/L", 10.0),
    "ug/L": ("ug/L", 1.0),
    "ug/dL": ("ug/L", 10.0),
    "ng/mL": ("ug/L", 1.0),  # 1 ng/mL is 1 ug/L exactly
    "mmol/L": ("mmol/L", 1.0),
    "umol/L": ("mmol/L", 0.001),
    "nmol/L": ("mmol/L", 1e-6),
    # All count concentrations share one base of cells per litre. Splitting them
    # across a 10^9 base and a 10^12 base looks tidier but makes a white-cell
    # count printed as 6200/uL unconvertible against one printed as 6.2x10^9/L,
    # which is the same number.
    "10*9/L": ("/L", 1e9),
    "10*3/uL": ("/L", 1e9),  # 1000 per uL == 10^9 per L
    "10*12/L": ("/L", 1e12),
    "10*6/uL": ("/L", 1e12),
    "/uL": ("/L", 1e6),
    "/mL": ("/L", 1e3),
    "/L": ("/L", 1.0),
    "kg": ("kg", 1.0),
    "g": ("kg", 0.001),
    "%": ("%", 1.0),
    "U/L": ("U/L", 1.0),
    "fL": ("fL", 1.0),
    "pg": ("pg", 1.0),
    "s": ("s", 1.0),
    "mm/h": ("mm/h", 1.0),
    "mm[Hg]": ("mm[Hg]", 1.0),
    "[IU]/L": ("[IU]/L", 1.0),
}

# Molar masses in g/mol, keyed by analyte code. Only substances whose reports
# genuinely appear in both mass and molar units are listed; adding one is a
# one-line change and needs no migration.
_MOLAR_MASS: dict[str, float] = {
    "LOINC:2345-7": 180.156,  # Glucose
    "LOINC:2093-3": 386.65,   # Cholesterol, total
    "LOINC:2085-9": 386.65,   # HDL cholesterol
    "LOINC:2089-1": 386.65,   # LDL cholesterol
    "LOINC:2571-8": 885.4,    # Triglycerides
    "LOINC:2160-0": 113.12,   # Creatinine
    "LOINC:3084-1": 168.11,   # Urate
    "LOINC:1975-2": 584.66,   # Bilirubin, total
    "LOINC:1968-7": 584.66,   # Bilirubin, direct
    "LOINC:3094-0": 60.06,    # Urea nitrogen
}


class UnitConversionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Converted:
    value: float
    unit: str


# Superscript digits appear constantly on Chinese lab printouts (×10⁹/L).
_SUPERSCRIPT_CHARS = "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
_SUPERSCRIPTS = str.maketrans(_SUPERSCRIPT_CHARS, "0123456789")


def _fold_exponents(text: str) -> str:
    """Rewrite the many printed spellings of `10^9` into one.

    Handled together rather than as alias permutations because the variants
    multiply: {x, X, ×, nothing} × {^, *, nothing} × {9, ⁹} is twelve
    spellings of one unit, and that is before the /L part.
    """
    # ×10⁹/L -> ×10^9/L
    if any(ch in text for ch in _SUPERSCRIPT_CHARS):
        text = re.sub(
            r"([\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079]+)",
            lambda m: "^" + m.group(1).translate(_SUPERSCRIPTS),
            text,
        )
    # Drop a leading multiplication sign: ×10^9/L -> 10^9/L
    text = re.sub(r"^[x\u00d7*]\s*(?=10)", "", text, flags=re.IGNORECASE)
    # 10*9/L and 109/L-style separators -> 10^9/L
    text = re.sub(r"\b10\s*\*\s*(\d)", r"10^\1", text)
    return text


def normalise_unit(unit: str | None) -> str | None:
    """Map a printed unit onto its UCUM-ish canonical spelling."""
    if unit is None:
        return None
    cleaned = unit.strip().replace(" ", "")
    if not cleaned:
        return None
    cleaned = _fold_exponents(cleaned)
    return _UNIT_ALIASES.get(cleaned.lower(), cleaned)


def convert(value: float, from_unit: str, to_unit: str, analyte_code: str | None = None) -> float:
    """Convert `value` between units, refusing anything it cannot do exactly."""
    source = normalise_unit(from_unit)
    target = normalise_unit(to_unit)
    if source is None or target is None:
        raise UnitConversionError("cannot convert to or from an empty unit")
    if source == target:
        return value

    src = _DIMENSIONAL.get(source)
    dst = _DIMENSIONAL.get(target)
    if src and dst and src[0] == dst[0]:
        return value * src[1] / dst[1]

    # Mass concentration <-> molar concentration, which needs the substance.
    molar = _molar_convert(value, source, target, analyte_code)
    if molar is not None:
        return molar

    raise UnitConversionError(
        f"no known conversion from {source!r} to {target!r}"
        + (f" for {analyte_code}" if analyte_code else "")
    )


def _molar_convert(
    value: float, source: str, target: str, analyte_code: str | None
) -> float | None:
    mass_units = {"g/L": 1.0, "mg/L": 1e-3, "ug/L": 1e-6}
    molar_units = {"mmol/L": 1e-3, "umol/L": 1e-6, "nmol/L": 1e-9}

    src_mass = _DIMENSIONAL.get(source)
    dst_mass = _DIMENSIONAL.get(target)
    src_base = src_mass[0] if src_mass else None
    dst_base = dst_mass[0] if dst_mass else None

    going_to_molar = src_base in mass_units and dst_base in molar_units
    going_to_mass = src_base in molar_units and dst_base in mass_units
    if not (going_to_molar or going_to_mass):
        return None

    if analyte_code is None:
        return None
    mass = _MOLAR_MASS.get(analyte_code)
    if mass is None:
        # Refusing beats guessing. The observation keeps its printed value and
        # is simply left un-canonicalised until a molar mass is added here.
        return None

    if going_to_molar:
        grams_per_litre = value * src_mass[1] * mass_units[src_base]
        moles_per_litre = grams_per_litre / mass
        return moles_per_litre / (molar_units[dst_base] * dst_mass[1])

    moles_per_litre = value * src_mass[1] * molar_units[src_base]
    grams_per_litre = moles_per_litre * mass
    return grams_per_litre / (mass_units[dst_base] * dst_mass[1])


def can_convert(from_unit: str, to_unit: str, analyte_code: str | None = None) -> bool:
    try:
        convert(1.0, from_unit, to_unit, analyte_code)
    except UnitConversionError:
        return False
    return True
