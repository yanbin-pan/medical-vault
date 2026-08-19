"""The analyte catalogue and the mapping from printed labels to codes.

Mapping is a *pure function of the catalogue*, applied when the database is
projected — never when the vault is written. That separation is what lets a
label read in 2026 and left unmapped become a proper time series in 2036: add
the synonym, run `medvault reindex`, and the history maps itself.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_PATH = Path(__file__).resolve().parent / "analytes.yaml"

# Punctuation and whitespace that carries no meaning for matching. Chinese
# reports mix full-width and half-width forms of all of these.
_NOISE = re.compile(r"[\s　()（）\[\]【】:：,，、.。/／\\\-_%*#·’'\"]+")


def fold(text: str) -> str:
    """Reduce a printed label to a comparable key.

    NFKC folds full-width characters onto their ASCII equivalents, which is what
    makes `ＷＢＣ`, `WBC` and `wbc` one thing.
    """
    if not text:
        return ""
    normalised = unicodedata.normalize("NFKC", text).lower()
    return _NOISE.sub("", normalised)


@dataclass(frozen=True, slots=True)
class Analyte:
    code: str
    name: str
    unit: str | None
    category: str
    synonyms: tuple[str, ...] = ()
    body_site: str | None = None
    higher_is_worse: bool | None = None


@dataclass(frozen=True, slots=True)
class BodySite:
    code: str
    name: str
    synonyms: tuple[str, ...] = ()


@dataclass(slots=True)
class MatchResult:
    """What a printed label was understood to mean."""

    analyte: Analyte | None
    body_site: str | None = None
    laterality: str | None = None
    matched_on: str | None = None

    @property
    def code(self) -> str:
        return self.analyte.code if self.analyte else ""


@dataclass(slots=True)
class Catalog:
    version: int
    analytes: dict[str, Analyte] = field(default_factory=dict)
    body_sites: dict[str, BodySite] = field(default_factory=dict)
    _by_synonym: dict[str, Analyte] = field(default_factory=dict, repr=False)
    _site_by_synonym: dict[str, str] = field(default_factory=dict, repr=False)
    _laterality: dict[str, str] = field(default_factory=dict, repr=False)

    # -- lookup ---------------------------------------------------------------

    def get(self, code: str) -> Analyte | None:
        return self.analytes.get(code)

    def match(self, label: str, hint_site: str | None = None) -> MatchResult:
        """Resolve a printed label to an analyte, body site and laterality.

        Handles the compound labels that imaging reports are full of: `左颈总A内径`
        is laterality `left`, site `common-carotid-artery`, analyte
        `MV:artery.diameter`.
        """
        if not label:
            return MatchResult(None)

        laterality = self._detect_laterality(label)
        site = hint_site or self._detect_site(label)
        key = fold(label)

        analyte = self._by_synonym.get(key)
        if analyte is not None:
            return MatchResult(analyte, site or analyte.body_site, laterality, "exact")

        # Strip the laterality and site words, then retry: what remains is
        # usually the measurement name on its own.
        stripped = self._strip_qualifiers(label)
        if stripped and stripped != key:
            analyte = self._by_synonym.get(stripped)
            if analyte is not None:
                return MatchResult(analyte, site or analyte.body_site, laterality, "stripped")

        # Longest containing synonym. Deliberately last and length-ordered, so
        # `内中膜厚度` wins over the shorter `内径` inside the same string.
        best: tuple[int, Analyte] | None = None
        for synonym_key, candidate in self._by_synonym.items():
            if len(synonym_key) >= 2 and synonym_key in key:
                if best is None or len(synonym_key) > best[0]:
                    best = (len(synonym_key), candidate)
        if best is not None:
            return MatchResult(best[1], site or best[1].body_site, laterality, "contains")

        return MatchResult(None, site, laterality, None)

    def unmapped_code(self, label: str) -> str:
        """A stable placeholder code for a label the catalogue does not know.

        Stable matters: the same unknown label must produce the same code on
        every reindex, or its history fragments into singletons.
        """
        slug = fold(label)[:48] or "unknown"
        return f"UNMAPPED:{slug}"

    # -- internals ------------------------------------------------------------

    def _detect_laterality(self, label: str) -> str | None:
        """Find a left/right/bilateral marker in a printed label.

        CJK markers (左, 右, 双侧) are matched as substrings, because Chinese
        labels do not delimit words. ASCII markers must match on a word boundary: `L`
        and `R` are legitimate abbreviations on their own, but matching them as
        substrings makes `LDL-C` left-sided and `RI` right-sided, which is how
        a resistive index ends up plotted as two phantom series.
        """
        folded = fold(label)
        tokenised = unicodedata.normalize("NFKC", label).lower()
        for side, markers in self._laterality_markers():
            for marker in markers:
                if marker.isascii():
                    pattern = rf"(?<![a-z0-9]){re.escape(marker.lower())}(?![a-z0-9])"
                    if re.search(pattern, tokenised):
                        return side
                elif fold(marker) and fold(marker) in folded:
                    return side
        return None

    def _laterality_markers(self):
        buckets: dict[str, list[str]] = {}
        for marker, side in self._laterality.items():
            buckets.setdefault(side, []).append(marker)
        # bilateral first: 双 is more specific than the 左/右 that may co-occur.
        for side in ("bilateral", "left", "right"):
            if side in buckets:
                yield side, buckets[side]

    def _detect_site(self, label: str) -> str | None:
        """Infer the organ or vessel from a printed label.

        Synonyms shorter than two characters are never matched as substrings.
        A single CJK character is a component of many unrelated compounds --
        胆 (bile) sits inside 胆固醇 (cholesterol), and 肝 (liver) inside 肝素
        (heparin), so one-character matching silently files a cholesterol result
        under the gallbladder. Analytes that genuinely imply a site declare it
        in the catalogue instead, and `match` falls back to that.
        """
        folded = fold(label)
        best: tuple[int, str] | None = None
        for synonym_key, site_code in self._site_by_synonym.items():
            if len(synonym_key) >= 2 and synonym_key in folded:
                if best is None or len(synonym_key) > best[0]:
                    best = (len(synonym_key), site_code)
        return best[1] if best else None

    def _strip_qualifiers(self, label: str) -> str:
        text = label
        for marker in self._laterality:
            # Same reasoning as _detect_laterality: never strip a bare ASCII
            # letter out of the middle of a word.
            if marker.isascii():
                text = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(marker)}(?![A-Za-z0-9])", "", text, flags=re.I
                )
            else:
                text = text.replace(marker, "")
        for site in self.body_sites.values():
            for synonym in site.synonyms:
                text = text.replace(synonym, "")
        return fold(text)


def load_catalog(path: Path | None = None) -> Catalog:
    raw: dict[str, Any] = yaml.safe_load((path or CATALOG_PATH).read_text("utf-8"))
    catalog = Catalog(version=int(raw.get("version", 1)))

    for entry in raw.get("analytes", []):
        analyte = Analyte(
            code=entry["code"],
            name=entry["name"],
            unit=entry.get("unit"),
            category=entry.get("category", "other"),
            synonyms=tuple(str(s) for s in entry.get("synonyms", [])),
            body_site=entry.get("body_site"),
            higher_is_worse=entry.get("higher_is_worse"),
        )
        catalog.analytes[analyte.code] = analyte
        for key in (analyte.name, analyte.code, *analyte.synonyms):
            folded = fold(key)
            # First writer wins, so an earlier, more specific entry is not
            # displaced by a later one sharing a synonym.
            if folded and folded not in catalog._by_synonym:
                catalog._by_synonym[folded] = analyte

    for entry in raw.get("body_sites", []):
        site = BodySite(
            code=entry["code"],
            name=entry["name"],
            synonyms=tuple(str(s) for s in entry.get("synonyms", [])),
        )
        catalog.body_sites[site.code] = site
        for key in (site.name, site.code, *site.synonyms):
            folded = fold(key)
            if folded and folded not in catalog._site_by_synonym:
                catalog._site_by_synonym[folded] = site.code

    for side, markers in (raw.get("laterality") or {}).items():
        for marker in markers:
            catalog._laterality[str(marker)] = side

    return catalog


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return load_catalog()
