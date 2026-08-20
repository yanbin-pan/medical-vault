# This is a medical record vault

If you are reading this without the application that created it, you have
everything you need. Nothing here depends on that application still existing.

Every file is UTF-8 text or an untouched original image. There is no database
file, no proprietary container and no compression. `ls`, `cat` and any JSON
parser are sufficient.

## Layout

```
MANIFEST.md            this file
schema/                the JSON Schemas these records were written against
catalog/               label -> code + canonical unit, one file per version
tenants/
  <tenant_id>/
    tenant.json
    subjects/
      <subject_id>/subject.json
    documents/
      <YYYY>/<MM>/<document_id>/
        envelope.json          what this document is, and where it came from
        original.<ext>         the photograph or scan, byte for byte
        observations.ndjson    one JSON object per line, one per measurement
```

`document_id` is a ULID, so directory listings are in chronological order.

## The three rules this vault is built on

1. **Nothing is ever mutated or deleted.** A correction is a new document whose
   envelope names the one it `supersedes`. The history stays readable.

2. **The printed label is never discarded.** Every observation keeps
   `label_raw` — the measurement's name exactly as printed, in its original
   language. Codes, translations and unit conversions are *interpretations*
   layered on top. If an interpretation turns out wrong, or a better one becomes
   possible, the raw label is still there to redo it from.

3. **Any database is disposable.** The application that wrote this vault kept a
   PostgreSQL database, but only as a cache it could rebuild by re-reading these
   files. If you are replacing that application, you do not need its database
   and should not try to recover one. Read the files.

## Derived fields, and where they are not

Each observation records **what was printed**: `label_raw` (the measurement's
name in its original language), `value_num` and `unit_raw`.

It does **not** record a code or a converted value. Those are derived — the
application that wrote this vault computed them on demand from `catalog/`, so
that improving a mapping would improve the whole history rather than only new
records. That is why `analyte_code` and `canonical_value` are absent from these
files: they were never ground truth.

`catalog/analytes.vN.yaml` is what turns a printed label into a code and a
canonical unit. Each entry lists a code, a canonical unit, and every spelling
seen for it. Versions accumulate, so you can interpret an old record with the
catalogue that was current when it was filed, or with the newest one.

## Reading it without any of the original code

Every observation ever recorded, as one JSON stream:

```bash
find tenants -name observations.ndjson -print0 | xargs -0 cat
```

One measurement's history as CSV, newest last. Match on the printed label,
because that is what the files contain:

```bash
find tenants -name observations.ndjson -print0 | xargs -0 cat \
  | jq -r 'select(.label_raw=="血红蛋白")
           | [.effective_time, .value_num, .unit_raw] | @csv' \
  | sort
```

Every spelling that has ever appeared, and how often — the first thing to run
when you want to know what is in here:

```bash
find tenants -name observations.ndjson -print0 | xargs -0 cat \
  | jq -r '.label_raw' | sort | uniq -c | sort -rn
```

To group synonyms — 血红蛋白, HGB and Haemoglobin are one measurement — look the
label up in the newest catalogue file:

```bash
CATALOG=$(ls catalog/analytes.v*.yaml | sort -V | tail -1)
grep -B4 '血红蛋白' "$CATALOG"      # -> code: LOINC:718-7, unit: g/L
```

Unit conversion is the one thing you must do yourself, and the only thing that
needs care: values are stored exactly as printed, so a series may mix mg/dL with
mmol/L. The catalogue names the canonical unit for each analyte; convert into it
before plotting.

## Verifying nothing has rotted

Each envelope records the SHA-256 of its original file:

```bash
find tenants -name envelope.json | while read -r e; do
  d=$(dirname "$e")
  want=$(jq -r .source.sha256 "$e")
  file=$d/$(jq -r .source.filename "$e")
  got=$(sha256sum "$file" | cut -d' ' -f1)
  [ "$want" = "$got" ] || echo "MISMATCH: $file"
done
```

Silence means every original is intact.
