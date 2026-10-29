"""Trim the Franklin County Auditor Parcel.csv to the columns this project uses and gzip it.

Run once on your computer (and again whenever you get a newer Parcel.csv):
    python make_parcel_lookup.py .\\Parcel.csv
It writes data/parcel_lookup.csv.gz. Upload that file to the repo's data/ folder; the GitHub
workflow reads it for owner names, so it doesn't need the full (very large) Parcel.csv.
"""
import csv
import gzip
import sys
from pathlib import Path

import config

KEEP = ["PARCEL ID", "NAME1", "MAILAD3", "MAILAD4", "STADDR", "STHNUM", "STDIRE", "STNAME", "STSFX",
        "USPS_CITY", "ZIPCODE", "LANDUSE", "PCLASS", "HOMSTD", "TRANDT", "PRICE", "YEARBLT"]


def build(src: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(src, newline="", encoding="utf-8-sig", errors="replace") as fh, \
            gzip.open(dest, "wt", newline="", encoding="utf-8") as out:
        reader = csv.DictReader(fh)
        cols = [c for c in KEEP if c in (reader.fieldnames or [])]
        missing = [c for c in KEEP if c not in cols]
        if missing:
            print("Note: columns not found in the source file (skipped):", missing)
        w = csv.writer(out)
        w.writerow(cols)
        for row in reader:
            w.writerow([(row.get(c) or "").strip() for c in cols])
            n += 1
    return n


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python make_parcel_lookup.py <path to Parcel.csv>")
    src, dest = Path(sys.argv[1]), Path(config.PARCEL_LOOKUP_FILE)
    rows = build(src, dest)
    print(f"Wrote {rows:,} rows to {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    if dest.stat().st_size > 24e6:
        print("WARNING: over 24 MB - the GitHub website only accepts uploads up to 25 MB. Tell Claude and the upload steps will change.")
