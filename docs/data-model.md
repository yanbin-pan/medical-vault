# The data model

This is the contract the records are written against. The
[JSON Schemas](../packages/api/medvault/vault/schemas/) are normative; this file explains the reasoning,
which is the part that does not survive in a schema.

## The rule everything follows

> Store what was printed. Derive everything else.

A measurement has two kinds of fact attached to it:

- **What the paper said** — the label in its original script, the number, the
  unit as written, the reference range printed on that report.
- **What we think it means** — that `白细胞计数` is LOINC 6690-2, that `92 mg/dL`
  is `5.11 mmol/L`, that it belongs in the haematology group.

The first kind is permanent and goes in the vault. The second is a function of
the current catalogue and current code, is recomputed on every `reindex`, and is
never written to the vault at all.

The payoff is that improving an interpretation improves the entire archive
retroactively. The cost is that the raw files need the catalogue to be fully
understood — which is why a copy of the catalogue is written into the vault, one
file per version.

## Three record types

### Subject

A person whose records are held. Deliberately thin: this is a store of results,
not a clinical chart. `birth_date` and `sex_at_birth` exist only because some
laboratory reference ranges are stratified by them.

`names_raw` collects every spelling a person appears under — `Pan YAN BIN`,
`潘彦斌` — so an upload can be checked against the subject it was filed under.

### Document envelope

One source document and the provenance of everything derived from it.

| Field | Why it exists |
| --- | --- |
| `captured_at` | When the test happened. The clinically meaningful time, and what charts plot. |
| `recorded_at` | When it entered the vault. A 2019 report may be filed in 2031; conflating these puts it on the timeline at the wrong point. |
| `source.sha256` | Digest of the original bytes. Lets any future reader prove the image was never altered, and lets `medvault verify` find silent corruption. |
| `provider.name_raw` | The institution as printed, in the original script, with `name_en` alongside. Reference ranges and measurement conventions are provider-specific. |
| `extraction` | Model, prompt version, timestamp, token counts. Makes a reading auditable and re-runnable years later. |
| `review` | An AI extraction is a draft. `verified` means a person compared it with the paper. |
| `narrative` | Prose findings and impressions, verbatim plus translation. The wording of an impression carries meaning no code captures. |
| `supersedes` | The document this one corrects. Never a deletion. |

`document_type` is deliberately free text rather than an enum. An enum would
require a code change to file a kind of report nobody has thought of yet.

### Observation

One measured fact. The shape is generic on purpose: a blood analyte, an organ
dimension, a blood-flow velocity and a yes/no finding are all observations, so a
new kind of measurement never needs a schema change.

**`label_raw` is the foundation of the whole design.** It is the measurement's
name exactly as printed, in its original language, never translated, never
tidied, never empty — the vault refuses to store an observation without one.
Every code, translation and conversion is re-derivable from it. A translated
`label_raw` is unrecoverable data loss, which is why the extraction prompt
labours the point and the writer enforces it.

Values are polymorphic — `value_num`, `value_text`, `value_bool` — because
results are. `阴性` is a real result, not a missing number.

Some fields exist because leaving them out corrupts data silently:

- **`comparator`** — a result printed as `<0.01` is a detection limit. Storing
  `0.01` alone turns a bound into a false precise reading.
- **`reference_low` / `reference_high`** are stored *per observation*, not looked
  up globally. Ranges are per-laboratory and change over the years; the one on
  this page is the one that applies to these values.
- **`abnormal_flag`** is what the lab flagged, never what we computed. Their flag
  reflects their range and their assay.
- **`body_site` and `laterality`** make a left kidney and a right kidney two
  series instead of one interleaved mess. Averaging them would hide one of them
  growing — the single most likely way to make this application quietly useless
  for imaging.
- **`qualifiers`** is an open key/value bag: fasting state, probe frequency,
  contrast agent. The escape hatch that keeps version 1 usable for measurements
  nobody has invented yet.

## The catalogue

[`analytes.yaml`](../packages/api/medvault/catalog/analytes.yaml) maps printed
labels to codes and canonical units. It is **data, not schema**: adding a
measurement is appending an entry, with no migration and no downtime.

Codes prefer public systems so the data outlives this application — LOINC where
it exists, `MV:` for concepts it does not cover (LOINC does not code individual
organ dimensions).

An observation whose label matches nothing is stored anyway, under
`UNMAPPED:<slug>`, with the printed label intact. It is real data awaiting an
interpretation, and it is surfaced in the UI and by `medvault stats` rather than
being dropped. Once the catalogue learns the label, `medvault reindex` turns the
whole history into a proper series.

### One trap worth knowing about

Body-site synonyms shorter than two characters are never matched. A single CJK
character is a component of many unrelated compounds: `胆` (bile) sits inside
`胆固醇` (cholesterol), `肝` (liver) inside `肝素` (heparin). Matching them as
substrings filed every cholesterol result under the gallbladder — silently, and
in a way nobody would notice until the charts looked strange years later.
Analytes that genuinely imply a site declare it in the catalogue instead.

The same applies to laterality: ASCII markers match on word boundaries only, or
`LDL-C` becomes left-sided and `RI` right-sided.

## Units

Every observation keeps what was printed *and* a value converted to one
canonical unit per analyte. Two kinds of conversion exist:

- **Dimensional** — mm to cm, `10*9/L` to `10*3/uL`. Analyte-independent and
  always safe. All count concentrations share one base of cells per litre, so a
  white-cell count printed as `6200/uL` is comparable with one printed as
  `6.2×10⁹/L`.
- **Molar** — mg/dL to mmol/L. Needs the substance's molar mass, so it is keyed
  by analyte code. An unknown molar conversion is **refused, not guessed**: the
  observation keeps its printed value and is left out of the canonical series
  until a molar mass is added.

A value that cannot be converted is never fabricated and never silently plotted.
It is excluded from the chart and counted, and the UI says so.

## Corrections

Nothing is edited. A correction is a new document whose envelope names the one
it `supersedes`; the projection walks those links and marks the older document's
observations `is_current = false`. Both readings stay queryable.

For a medical record this is not fastidiousness. Knowing that a value was once
recorded differently — and when it was changed, by whom, and why — is part of
the record.

The one exception is `review.status`, which is written back onto an existing
envelope. It is metadata about the record rather than a claim about the patient,
and superseding a document to tick a box would bury the history in noise.
