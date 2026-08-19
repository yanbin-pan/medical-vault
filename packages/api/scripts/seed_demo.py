"""Fill a vault with plausible history, so the UI can be seen working.

No API key and no network needed. The numbers are synthetic but the shapes are
real: mixed units across years, Chinese labels, an ultrasound with left/right
measurements, a correction, and one label the catalogue does not know — so the
"unmapped" path is visible too.

    python scripts/seed_demo.py --vault ./vault --reindex
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from medvault.vault.store import Vault

TENANT = "pan-household"
SUBJECT = "self"


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def blood_panel(visit: int, when: datetime, rng: random.Random) -> list[dict]:
    """A yearly panel. Glucose and lipids drift upward; haemoglobin is stable."""
    drift = visit * 0.12
    # Early reports came from a lab printing mg/dL; later ones use mmol/L. The
    # application is expected to put them on one axis.
    glucose_in_mg = visit < 2
    glucose_mmol = 4.9 + drift + rng.uniform(-0.15, 0.15)

    rows = [
        {"label_raw": "白细胞计数", "value_num": round(6.1 + rng.uniform(-0.8, 0.8), 1),
         "unit_raw": "×10⁹/L", "reference_low": 3.5, "reference_high": 9.5},
        {"label_raw": "血红蛋白", "value_num": round(151 + rng.uniform(-6, 6)),
         "unit_raw": "g/L", "reference_low": 130, "reference_high": 175},
        {"label_raw": "血小板", "value_num": round(240 + rng.uniform(-30, 30)),
         "unit_raw": "×10⁹/L", "reference_low": 125, "reference_high": 350},
        {"label_raw": "总胆固醇", "value_num": round(4.4 + drift * 1.4 + rng.uniform(-0.2, 0.2), 2),
         "unit_raw": "mmol/L", "reference_low": 2.8, "reference_high": 5.2},
        {"label_raw": "甘油三酯", "value_num": round(1.1 + drift * 1.1 + rng.uniform(-0.15, 0.15), 2),
         "unit_raw": "mmol/L", "reference_low": 0.4, "reference_high": 1.7},
        {"label_raw": "高密度脂蛋白胆固醇", "value_num": round(1.42 - drift * 0.18 + rng.uniform(-0.08, 0.08), 2),
         "unit_raw": "mmol/L", "reference_low": 1.0, "reference_high": 2.0},
        {"label_raw": "低密度脂蛋白胆固醇", "value_num": round(2.6 + drift * 1.2 + rng.uniform(-0.15, 0.15), 2),
         "unit_raw": "mmol/L", "reference_low": 0.0, "reference_high": 3.4},
        {"label_raw": "丙氨酸氨基转移酶", "value_num": round(24 + drift * 12 + rng.uniform(-4, 4)),
         "unit_raw": "U/L", "reference_low": 9, "reference_high": 50},
        {"label_raw": "肌酐", "value_num": round(78 + rng.uniform(-5, 5)),
         "unit_raw": "umol/L", "reference_low": 57, "reference_high": 111},
        {"label_raw": "尿酸", "value_num": round(370 + drift * 30 + rng.uniform(-25, 25)),
         "unit_raw": "umol/L", "reference_low": 208, "reference_high": 428},
    ]
    if glucose_in_mg:
        rows.append({"label_raw": "空腹血糖", "value_num": round(glucose_mmol * 18.016, 1),
                     "unit_raw": "mg/dL", "reference_low": 70, "reference_high": 100})
    else:
        rows.append({"label_raw": "空腹血糖", "value_num": round(glucose_mmol, 2),
                     "unit_raw": "mmol/L", "reference_low": 3.9, "reference_high": 6.1,
                     "abnormal_flag": "high" if glucose_mmol > 6.1 else None})

    if visit >= 3:
        # A metric the catalogue has never heard of, to exercise the unmapped path.
        rows.append({"label_raw": "同型半胱氨酸", "value_num": round(11 + rng.uniform(-2, 2), 1),
                     "unit_raw": "umol/L"})
    return [{k: v for k, v in row.items() if v is not None} for row in rows]


def abdominal_ultrasound(visit: int, rng: random.Random) -> list[dict]:
    return [
        {"label_raw": "右肝斜径", "value_num": round(124 + visit * 1.5 + rng.uniform(-2, 2)), "unit_raw": "mm"},
        {"label_raw": "左肝厚径", "value_num": round(75 + rng.uniform(-2, 2)), "unit_raw": "mm"},
        {"label_raw": "门静脉内径", "value_num": round(8 + rng.uniform(-0.4, 0.4), 1), "unit_raw": "mm"},
        {"label_raw": "胆囊壁厚", "value_num": round(3 + rng.uniform(-0.3, 0.3), 1), "unit_raw": "mm"},
        {"label_raw": "脾脏厚", "value_num": round(33 + rng.uniform(-1.5, 1.5)), "unit_raw": "mm"},
        {"label_raw": "脾脏长径", "value_num": round(78 + rng.uniform(-3, 3)), "unit_raw": "mm"},
        {"label_raw": "左肾长径", "value_num": round(104 + rng.uniform(-2, 2)), "unit_raw": "mm"},
        {"label_raw": "右肾长径", "value_num": round(106 + rng.uniform(-2, 2)), "unit_raw": "mm"},
    ]


def carotid_ultrasound(visit: int, rng: random.Random) -> list[dict]:
    imt = 0.62 + visit * 0.035
    rows = []
    for side, base in (("左", 6.2), ("右", 5.1)):
        rows += [
            {"label_raw": f"{side}颈总A前后径", "value_num": round(base + rng.uniform(-0.2, 0.2), 1),
             "unit_raw": "mm"},
            {"label_raw": f"{side}颈总A内中膜厚度", "value_num": round(imt + rng.uniform(-0.03, 0.03), 2),
             "unit_raw": "mm"},
            {"label_raw": f"{side}颈总A峰值流速",
             "value_num": round(72 + math.sin(visit) * 5 + rng.uniform(-4, 4)), "unit_raw": "cm/s"},
            {"label_raw": f"{side}颈总A RI", "value_num": round(0.70 + rng.uniform(-0.05, 0.05), 2)},
        ]
    return rows


def seed(vault: Vault, visits: int = 6, seed_value: int = 20260819) -> int:
    rng = random.Random(seed_value)
    vault.initialise()
    vault.ensure_tenant(TENANT, "Pan Household", [])
    vault.write_subject(
        {
            "tenant_id": TENANT,
            "subject_id": SUBJECT,
            "display_name": "Pan Yan Bin",
            "names_raw": ["Pan YAN BIN", "潘彦斌"],
            "birth_date": "1995-03-02",
            "sex_at_birth": "male",
            "created_at": _stamp(datetime.now(UTC)),
        }
    )

    provider = {
        "name_raw": "上海电力医院",
        "name_en": "Shanghai Electric Power Hospital",
        "country": "CN",
    }
    start = datetime(2020, 6, 14, 8, 30, tzinfo=UTC)
    written = 0
    first_panel = None

    for visit in range(visits):
        when = start + timedelta(days=365 * visit + rng.randint(-20, 20))
        extraction = {
            "method": "ai_vision",
            "model": "claude-opus-5",
            "prompt_version": "seed",
            "extracted_at": _stamp(when),
            "warnings": [],
        }

        panel = vault.write_document(
            {
                "tenant_id": TENANT, "subject_id": SUBJECT, "captured_at": _stamp(when),
                "document_type": "blood_panel", "language": "zh-Hans", "provider": provider,
                "source": {"media_type": "image/jpeg"}, "extraction": extraction,
                "review": {"status": "verified" if visit < visits - 1 else "unreviewed"},
                "narrative": [],
            },
            f"demo-blood-panel-{visit}".encode(),
            blood_panel(visit, when, rng),
        )
        written += 1
        if visit == 0:
            first_panel = panel

        if visit % 2 == 0:
            vault.write_document(
                {
                    "tenant_id": TENANT, "subject_id": SUBJECT,
                    "captured_at": _stamp(when + timedelta(hours=1)),
                    "document_type": "ultrasound", "language": "zh-Hans", "provider": provider,
                    "source": {"media_type": "image/jpeg"}, "extraction": extraction,
                    "review": {"status": "verified"},
                    "narrative": [
                        {"section": "impression", "text_raw": "肝脏弥漫性回声改变，考虑脂肪肝",
                         "text_en": "Diffuse hepatic echo change, consistent with hepatic steatosis"},
                        {"section": "impression", "text_raw": "胆囊、胰腺、脾脏未见明显异常",
                         "text_en": "Gallbladder, pancreas and spleen show no obvious abnormality"},
                    ],
                },
                f"demo-abdominal-{visit}".encode(),
                abdominal_ultrasound(visit, rng),
            )
            written += 1

            vault.write_document(
                {
                    "tenant_id": TENANT, "subject_id": SUBJECT,
                    "captured_at": _stamp(when + timedelta(hours=2)),
                    "document_type": "ultrasound", "language": "zh-Hans", "provider": provider,
                    "source": {"media_type": "image/jpeg"}, "extraction": extraction,
                    "review": {"status": "verified"},
                    "narrative": [
                        {"section": "impression", "text_raw": "双侧颈动脉血流通畅",
                         "text_en": "Bilateral carotid arterial flow is patent"},
                    ],
                },
                f"demo-carotid-{visit}".encode(),
                carotid_ultrasound(visit, rng),
            )
            written += 1

    # One correction, so the superseding path is visible in the UI.
    if first_panel is not None:
        corrected = [
            {**row, "value_num": 152 if row["label_raw"] == "血红蛋白" else row["value_num"]}
            for row in first_panel.observations
        ]
        vault.supersede(
            first_panel,
            {"review": {"status": "corrected", "reviewed_by": "demo",
                        "note": "haemoglobin misread as 158; the paper says 152"}},
            corrected,
        )
        written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path("./vault"))
    parser.add_argument("--visits", type=int, default=6)
    parser.add_argument("--reindex", action="store_true", help="also rebuild the database")
    args = parser.parse_args()

    vault = Vault(args.vault)
    written = seed(vault, args.visits)
    print(f"wrote {written} documents to {args.vault}")

    if args.reindex:
        from medvault.db import session_scope
        from medvault.models import Base
        from medvault.db import get_engine
        from medvault.projection import reindex

        Base.metadata.create_all(get_engine())
        with session_scope() as session:
            print(reindex(session, vault).summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
