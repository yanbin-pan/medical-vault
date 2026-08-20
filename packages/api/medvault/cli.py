"""Command line tools.

`reindex`, `verify` and `export` are the three that matter. Between them they
are the operational proof of the design's central claim: the database can be
thrown away, the files can be checked, and the whole dataset can leave in a
format nothing here owns.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from medvault.config import get_settings
from medvault.db import get_engine, session_scope
from medvault.models import Base
from medvault.projection import reindex
from medvault.vault.store import Vault

log = logging.getLogger("medvault")


def _vault(args: argparse.Namespace) -> Vault:
    return Vault(args.vault or get_settings().vault_path)


def cmd_init_db(args: argparse.Namespace) -> int:
    engine = get_engine()
    Base.metadata.create_all(engine)
    print(f"schema created on {engine.url.render_as_string(hide_password=True)}")
    if engine.dialect.name == "postgresql" and not args.no_rls:
        _apply_rls(engine)
        print("row-level security policies applied")
    return 0


def _apply_rls(engine) -> None:  # noqa: ANN001
    """Defence in depth.

    The API already scopes every query by tenant. These policies mean that a
    query which somehow forgets to returns nothing, rather than returning
    another household's medical records.
    """
    from sqlalchemy import text

    statements = []
    for table in ("subject", "document", "observation"):
        column = "tenant_id"
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}",
            (
                f"CREATE POLICY {table}_tenant_isolation ON {table} "
                f"USING ({column} = current_setting('app.tenant_id', true) "
                f"OR current_setting('app.tenant_id', true) IS NULL)"
            ),
        ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def cmd_reindex(args: argparse.Namespace) -> int:
    vault = _vault(args)
    with session_scope() as session:
        if getattr(args, "if_needed", False):
            reason = _reindex_reason(session)
            if reason is None:
                print("projection is current; nothing to rebuild")
                return 0
            print(f"rebuilding: {reason}")
        report = reindex(session, vault, tenant_id=args.tenant)
    print(report.summary())
    for problem in report.problems:
        print(f"  problem: {problem}", file=sys.stderr)
    return 1 if report.problems and args.strict else 0


def _reindex_reason(session) -> str | None:  # noqa: ANN001
    """Why the projection needs rebuilding, or None if it does not.

    Two things make it stale, and the second is the interesting one:

    * There is nothing in it — a new or wiped database.
    * The analyte catalogue has changed since it was built. Codes, canonical
      units and categories are all derived from the catalogue, so a new version
      means every stored row was computed under old rules. Rebuilding here is
      what makes extending the catalogue retrofit the whole history
      automatically, rather than only for documents filed afterwards.
    """
    from sqlalchemy import func, select

    from medvault.catalog.registry import get_catalog
    from medvault.models import Document, ProjectionState

    state = session.scalars(select(ProjectionState)).first()
    if state is None or state.last_reindex_at is None:
        return "the projection has never been built"

    documents = session.scalar(select(func.count()).select_from(Document)) or 0
    if documents == 0:
        return "the projection is empty"

    current = get_catalog().version
    if state.catalog_version != current:
        return (
            f"the analyte catalogue moved from version {state.catalog_version} "
            f"to {current}, so derived values are stale"
        )
    return None


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-hash every original and report anything that has changed underneath us."""
    vault = _vault(args)
    problems = vault.verify(args.tenant)
    if not problems:
        count = sum(1 for _ in vault.iter_documents(args.tenant))
        print(f"{count} documents verified; every original matches its recorded digest")
        return 0
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(problems)} problems found", file=sys.stderr)
    return 1


def cmd_export(args: argparse.Namespace) -> int:
    """Write the whole dataset out in formats nothing here owns.

    This exists so that leaving is easy. A record you cannot get out of a
    system is a record that system controls.
    """
    vault = _vault(args)
    destination = Path(args.out)
    destination.mkdir(parents=True, exist_ok=True)

    observations_path = destination / "observations.ndjson"
    documents_path = destination / "documents.ndjson"
    csv_path = destination / "observations.csv"

    documents = 0
    observations = 0
    csv_columns = [
        "effective_time", "subject_id", "analyte_code", "label_raw", "label_en",
        "value_num", "unit_raw", "canonical_value", "canonical_unit",
        "reference_low", "reference_high", "abnormal_flag", "body_site",
        "laterality", "document_id",
    ]

    from medvault.catalog.normalize import normalise_observation

    with (
        observations_path.open("w", encoding="utf-8") as obs_file,
        documents_path.open("w", encoding="utf-8") as doc_file,
        csv_path.open("w", encoding="utf-8", newline="") as csv_file,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=csv_columns, extrasaction="ignore")
        writer.writeheader()
        for document in vault.iter_documents(args.tenant):
            doc_file.write(json.dumps(document.envelope, ensure_ascii=False, sort_keys=True) + "\n")
            documents += 1
            for row in document.observations:
                obs_file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                observations += 1
                normalised = normalise_observation(row)
                writer.writerow(
                    {
                        **row,
                        "label_en": normalised.label_en,
                        "analyte_code": normalised.analyte_code,
                        "canonical_value": normalised.canonical_value,
                        "canonical_unit": normalised.canonical_unit,
                        "body_site": normalised.body_site,
                        "laterality": normalised.laterality,
                    }
                )

    print(f"exported {documents} documents and {observations} observations to {destination}")
    print("  observations.ndjson  every observation, exactly as stored")
    print("  documents.ndjson     every envelope, with provenance")
    print("  observations.csv     flattened, for a spreadsheet")
    print("\nThe original images are not copied; they are in the vault at")
    print(f"  {vault.root}")
    return 0


def cmd_tenant_add(args: argparse.Namespace) -> int:
    vault = _vault(args)
    vault.initialise()
    members = [{"email": e.strip().lower(), "role": "owner"} for e in args.owner]
    vault.ensure_tenant(args.tenant_id, args.name or args.tenant_id, members)
    if members:
        vault.set_members(args.tenant_id, members)
    print(f"tenant {args.tenant_id} ready with {len(members)} owner(s)")
    return 0


def cmd_member_add(args: argparse.Namespace) -> int:
    vault = _vault(args)
    record = json.loads((vault.tenant_dir(args.tenant_id) / "tenant.json").read_text("utf-8"))
    members = [m for m in record.get("members", []) if m.get("email") != args.email.lower()]
    members.append({"email": args.email.lower(), "role": args.role})
    vault.set_members(args.tenant_id, members)
    print(f"{args.email} is now a {args.role} of {args.tenant_id}")
    return 0


def cmd_subject_add(args: argparse.Namespace) -> int:
    vault = _vault(args)
    vault.initialise()
    vault.ensure_tenant(args.tenant_id)
    vault.write_subject(
        {
            "tenant_id": args.tenant_id,
            "subject_id": args.subject_id,
            "display_name": args.name or args.subject_id,
            "birth_date": args.birth_date,
            "sex_at_birth": args.sex,
            "names_raw": args.also_known_as or [],
        }
    )
    print(f"subject {args.subject_id} written")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Read a file from disk and file it, without going through the web UI."""
    from medvault.extraction import DocumentExtractor, ExtractionError, to_vault_records
    from medvault.extraction.images import sniff_media_type

    vault = _vault(args)
    vault.initialise()
    payload = Path(args.path).read_bytes()
    media_type = sniff_media_type(payload)

    try:
        outcome = DocumentExtractor().extract(payload, media_type, args.hint)
        envelope, observations = to_vault_records(
            outcome,
            args.tenant_id,
            args.subject_id,
            source={"media_type": media_type, "filename": "original" + Path(args.path).suffix},
            fallback_captured_at=args.captured_at,
        )
    except ExtractionError as exc:
        print(f"extraction failed: {exc}", file=sys.stderr)
        return 1

    stored = vault.write_document(envelope, payload, observations)
    print(f"stored {stored.document_id} with {len(observations)} observations")
    for warning in outcome.result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)

    with session_scope() as session:
        reindex(session, vault, tenant_id=args.tenant_id)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from collections import Counter

    vault = _vault(args)
    analytes: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    documents = 0

    from medvault.catalog.normalize import normalise_observation

    for document in vault.iter_documents(args.tenant):
        documents += 1
        for row in document.observations:
            normalised = normalise_observation(row)
            analytes[normalised.analyte_code] += 1
            if not normalised.is_mapped:
                unmapped[row["label_raw"]] += 1

    print(f"{documents} documents, {sum(analytes.values())} observations, "
          f"{len(analytes)} distinct analytes")
    if unmapped:
        print(f"\n{sum(unmapped.values())} observations have no catalogue entry.")
        print("Add these labels to medvault/catalog/analytes.yaml and reindex:")
        for label, count in unmapped.most_common(20):
            print(f"  {count:>4}  {label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="medvault", description=__doc__)
    parser.add_argument("--vault", type=Path, help="path to the vault (default: from config)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the projection schema")
    p.add_argument("--no-rls", action="store_true", help="skip row-level security policies")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("reindex", help="rebuild the database from the vault")
    p.add_argument("--tenant", help="limit to one tenant")
    p.add_argument("--strict", action="store_true", help="exit non-zero if any document failed")
    p.add_argument(
        "--if-needed",
        action="store_true",
        help="rebuild only if the projection is empty or the catalogue has changed",
    )
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("verify", help="check every original against its recorded digest")
    p.add_argument("--tenant")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("export", help="write the dataset out as NDJSON and CSV")
    p.add_argument("out", help="destination directory")
    p.add_argument("--tenant")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("tenant-add", help="create a tenant")
    p.add_argument("tenant_id")
    p.add_argument("--name")
    p.add_argument("--owner", action="append", default=[], help="owner email (repeatable)")
    p.set_defaults(func=cmd_tenant_add)

    p = sub.add_parser("member-add", help="grant someone access to a tenant")
    p.add_argument("tenant_id")
    p.add_argument("email")
    p.add_argument("--role", default="viewer", choices=["owner", "editor", "viewer"])
    p.set_defaults(func=cmd_member_add)

    p = sub.add_parser("subject-add", help="add a person whose records are held")
    p.add_argument("tenant_id")
    p.add_argument("subject_id")
    p.add_argument("--name")
    p.add_argument("--birth-date")
    p.add_argument("--sex", choices=["male", "female", "intersex", "unknown"])
    p.add_argument("--also-known-as", action="append", help="another spelling of the name")
    p.set_defaults(func=cmd_subject_add)

    p = sub.add_parser("ingest", help="read a document from disk and file it")
    p.add_argument("tenant_id")
    p.add_argument("subject_id")
    p.add_argument("path")
    p.add_argument("--captured-at", help="ISO date, if the document shows none")
    p.add_argument("--hint", help="free-text context for the extractor")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("stats", help="what is in the vault, and what is unmapped")
    p.add_argument("--tenant")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
