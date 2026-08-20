# Medical Vault

A private, self-hosted record of medical results that is meant to still be
readable in thirty years.

You photograph a printed report — a blood panel, an ultrasound, anything, in any
language — and it becomes a structured, translated, charted part of your health
history. The immediate problem it solves is being handed a stack of Chinese
paper after a check-up in Shanghai and having no way to read it, let alone
compare it with last year's.

The harder problem it solves is that health data outlives software. This
application is designed to be **thrown away**, and for the records to survive
that without loss.

---

## The one idea

There are two stores, and only one of them matters.

```
   photograph ──► extraction ──►  THE VAULT  ──► projection ──►  PostgreSQL ──► charts
                                (plain files)                     (a cache)
                                      │                                │
                              canonical, append-only          disposable, rebuildable
```

**The vault** is a directory of plain files: the original photographs, and
alongside each one a JSON envelope and an NDJSON list of the measurements read
from it. No database, no proprietary format. Nothing is ever modified or
deleted; a correction is a new record that supersedes the old one.

**PostgreSQL holds nothing of its own.** Every row in it was computed from the
vault and can be recomputed:

```bash
medvault reindex     # drop the database, run this, and it is all back
```

That is a routine command, not a recovery procedure, and a test asserts it:
`test_database_can_be_destroyed_and_rebuilt`.

### Why it is arranged this way

The failure this design is aimed at is not disk loss — backups handle that. It
is *format decay*: in 2040 the schema has drifted through eleven migrations, the
ORM is abandoned, and the only way to read a 2026 result is to resurrect a
2026 application.

So the durable layer stores as little interpretation as possible:

| Stored in the vault (permanent) | Derived on read (improvable) |
| --- | --- |
| `label_raw` — `白细胞计数`, exactly as printed | `analyte_code` — `LOINC:6690-2` |
| `value_num` + `unit_raw` — `92`, `mg/dL` | `canonical_value` + unit — `5.11 mmol/L` |
| the original photograph | English label, category, reference banding |

Codes and unit conversions are *interpretations*. Freezing them into the record
would freeze them at the quality of the code that happened to exist that day.
Keeping only the printed label means a mapping improved in 2036 improves the
whole history — including records filed a decade earlier, without editing a byte
of them.

That is not a hypothetical. It is the test named
`test_extending_the_catalogue_retrofits_existing_history`: three years of an
unrecognised measurement become one clean time series the moment somebody adds
five lines to a YAML file.

---

## What is in the vault

```
vault/
  MANIFEST.md                  plain-English explanation of the format
  schema/                      the JSON Schemas these records were written against
  catalog/analytes.vN.yaml     label -> code + canonical unit, one file per version
  tenants/<tenant>/
    tenant.json                including who may read these records
    subjects/<subject>/subject.json
    documents/<YYYY>/<MM>/<ulid>/
      envelope.json            what this is, who issued it, how it was read
      original.jpg             the photograph, byte for byte
      observations.ndjson      one JSON object per measurement
```

The catalogue travels *inside* the vault. Without it the raw labels are ground
truth but undecodable; with it, a future reader has both the facts and the
interpretation, and needs nothing from this repository.

Everything the MANIFEST claims is executable, and is asserted in
`test_the_documented_recipes_actually_work` so the documentation cannot rot:

```bash
# one measurement's history, using nothing but jq
find tenants -name observations.ndjson -print0 | xargs -0 cat \
  | jq -r 'select(.label_raw=="血红蛋白") | [.effective_time, .value_num, .unit_raw] | @csv' | sort
```

---

## Reading a document

Uploads go to Claude with vision, which returns structured data under a strict
schema. The prompt is built around what it must *not* do:

- **Never translate `label_raw`.** The printed label is the permanent record.
- **Never convert a unit.** `92 mg/dL` is stored as `92` and `mg/dL`.
- **Never compute an abnormal flag** — only report the arrow the lab printed.
- **Never invent a date.** No date on the page means the upload is rejected and
  the date is asked for, because a guessed date gets plotted.
- **Report low confidence honestly** rather than guessing a digit.

The model, prompt version and token counts are recorded in the envelope, so an
extraction can be audited or re-run years later against the stored original. An
AI reading is a **draft** until a person confirms it, and the UI shows the
photograph beside the extracted numbers so that check is a glance rather than a
chore.

Codes are assigned afterwards, by the catalogue, not by the model — see the
table above for why.

---

## The application

- **Charts** — one line per measurement, with the reference interval printed on
  that report shaded behind it. A left kidney and a right kidney are separate
  series on shared axes: averaging them would hide one of them changing.
- **Correlations** — a matrix over analytes measured at the *same visits*. No
  interpolation: inventing a value for a day with no blood test would manufacture
  the correlation you went looking for. Pairs with too few shared visits are
  omitted rather than shown with a dramatic coefficient from three points.
- **Documents** — every upload, its original image, its readings, its provenance,
  and its supersession history.
- **Multi-tenancy** — several households on one deployment, isolated at every
  route, with PostgreSQL row-level security behind the application's own scoping.

Every chart has a table view, and units render the way a clinician writes them
(`×10⁹/L`, not `10*9/L`).

---

## Running it locally

```bash
cd packages/api
uv venv && uv pip install -e ".[dev]"
export MEDVAULT_DEV_USER_EMAIL=you@example.com          # stands in for Cloudflare Access
export MEDVAULT_DATABASE_URL=sqlite:///./medvault.db    # or point at PostgreSQL
export MEDVAULT_ANTHROPIC_API_KEY=sk-ant-...            # only needed for uploads

python scripts/seed_demo.py --vault ./vault             # plausible history, no API key needed
python -m medvault.cli --vault ./vault member-add pan-household you@example.com --role owner
python -m medvault.cli init-db
python -m medvault.cli --vault ./vault reindex
uvicorn medvault.main:app --reload

cd ../web && npm install && npm run dev                 # http://localhost:5173
```

### Commands worth knowing

| Command | What it does |
| --- | --- |
| `medvault reindex` | Rebuild the database from the vault |
| `medvault verify` | Re-hash every original against its recorded digest |
| `medvault export <dir>` | Write everything out as NDJSON and CSV |
| `medvault stats` | What is stored, and which labels have no catalogue entry |
| `medvault ingest <file>` | File a document from the command line |

`medvault stats` is the maintenance loop: it names the printed labels nothing
recognises, you add them to `packages/api/medvault/catalog/analytes.yaml`, and
`medvault reindex` retrofits every historical record that used them.

---

## Deploying to the cluster

Manifests are in [`k8s/`](k8s/) and Flux picks them up from
[`clusters/home/medical-vault.yaml`](https://github.com/yanbin-pan/home-cluster)
in the home-cluster repository.

One manual step, once, on a machine that has the cluster's age key:

```bash
./scripts/make-secret.sh            # prompts for the Anthropic API key
git add k8s/secret.sops.yaml && git commit -m "Add the deployment secret"
```

It generates the database password, writes it to the two places that must
agree, encrypts the result with SOPS, and refuses to leave an unencrypted file
behind if anything fails. To change a value later, edit it in place with
`sops k8s/secret.sops.yaml` — regenerating would hand PostgreSQL a password it
is not expecting.

Then tag a release so the arm64 image is built, and point the manifests at it.

Notes that matter on this cluster:

- **The vault PVC omits `storageClassName`**, so it lands on `ssd` — NFS-backed
  and backed up nightly. On `local-path` it would be one node's SD card,
  unbacked-up, and gone on a node rebuild.
- **One replica, `Recreate`.** The vault PVC is ReadWriteOnce; two pods writing
  it over NFS is the failure mode this whole repository is arranged to avoid.
- **arm64 only.** These are Raspberry Pi 4s, and an amd64 image gives
  `CrashLoopBackOff` with `exec format error`, which reads like an app bug.
- **A nightly CronJob runs `medvault verify`**, because silent bit-rot is the one
  failure a backup does not save you from: the corrupted file gets backed up too.

Full detail in [`docs/operations.md`](docs/operations.md).

---

## Documentation

| File | Contents |
| --- | --- |
| [`docs/data-model.md`](docs/data-model.md) | The durable contract, field by field, and why each field exists |
| [`docs/operations.md`](docs/operations.md) | Deploying, backups, restore drills, adding a household |
|  [`packages/api/medvault/vault/schemas/`](packages/api/medvault/vault/schemas/) | JSON Schemas — the normative definition |
| `vault/MANIFEST.md` | Written into every vault; explains the format with no reference to this code |

## Tests

```bash
cd packages/api && .venv/bin/pytest -q      # 68 tests
```

They are organised around the promises this README makes rather than around the
modules: that the database is disposable, that the catalogue retrofits history,
that tenants cannot see each other, that the documented recipes actually run.
