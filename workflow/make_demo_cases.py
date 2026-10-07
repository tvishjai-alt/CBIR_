"""
make_demo_cases.py  (put in the workflow/ folder)

Pushes REAL chest X-rays through the REAL webhook (/api/cases/new), so the whole pipeline
(store -> CBIR -> priority -> routing -> worklist) runs on real images and real patient
attributes from Member 1's CSV.

Usage (workflow app must be running on port 5000):
    python make_demo_cases.py --images member1\\query_images --n 30

Notes:
- Age / gender / view position come from member1/database_pool_active.csv when the image
  is in that CSV (otherwise defaults are used and a warning is printed).
- The clinic urgency flag is a seeded random ~25% (a stand-in for the clinic's own triage).
  State this as an assumption in the report.
"""

import argparse
import json
import random
import shutil
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "member1" / "database_pool_active.csv"
IMAGES_OUT = ROOT / "static" / "images"


def post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return {"status": "error", "message": f"HTTP {err.code}: {err.read().decode('utf-8', 'ignore')}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, help="folder with chest X-ray PNG/JPG files")
    ap.add_argument("--n", type=int, default=30, help="how many cases to create")
    ap.add_argument("--start", type=int, default=2001, help="first case number (CASE-2001...)")
    ap.add_argument("--clinics", default="CLN-001", help="comma-separated clinic ids to cycle through")
    ap.add_argument("--url", default="http://127.0.0.1:5000/api/cases/new")
    ap.add_argument("--urgent-rate", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    clinics = [c.strip() for c in args.clinics.split(",") if c.strip()]
    files = sorted(p for p in Path(args.images).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    if not files:
        raise SystemExit(f"No images found in {args.images}")

    meta = pd.read_csv(CSV_PATH).set_index("Image Index") if CSV_PATH.exists() else None
    IMAGES_OUT.mkdir(parents=True, exist_ok=True)

    created = 0
    for i, src in enumerate(files[: args.n]):
        case_id = f"CASE-{args.start + i:04d}"
        dest = IMAGES_OUT / f"{case_id}{src.suffix.lower()}"
        shutil.copyfile(src, dest)

        age, gender, view, patient_ref = 50, "M", "PA", f"PT-{args.start + i:04d}"
        if meta is not None and src.name in meta.index:
            row = meta.loc[src.name]
            age = int(row["Patient Age Clean"])
            gender = str(row["Patient Gender"])
            view = str(row["View Position"])
            patient_ref = f"PT-{int(row['Patient ID']):05d}"
        else:
            print(f"  warning: {src.name} not in CSV, using default age/gender/view")

        payload = {
            "case_id": case_id,
            "clinic_id": clinics[i % len(clinics)],
            "patient_ref": patient_ref,
            "modality": "X-ray",
            "body_part": "Chest",
            "urgency_flag": 1 if rng.random() < args.urgent_rate else 0,
            "image_path": f"static/images/{dest.name}",
            "patient_age": age,
            "patient_gender": gender,
            "view_position": view,
            "source_image_index": src.name,
        }
        result = post_json(args.url, payload)
        if result.get("status") == "success":
            data = result.get("data", {})
            print(f"  {case_id}  {src.name:22s} -> {data.get('priority_label', '?'):8s} "
                  f"assigned to {data.get('assigned_rad_id')}")
            created += 1
        else:
            print(f"  {case_id} FAILED: {result.get('message')}")

    print(f"\nDone: {created} case(s) created.")


if __name__ == "__main__":
    main()