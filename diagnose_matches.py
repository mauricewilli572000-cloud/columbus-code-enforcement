"""Explain why leads did not match the Auditor Parcel.csv.

    python diagnose_matches.py                       # uses .\\Parcel.csv and data\\debug\\unmatched_addresses.csv
    python diagnose_matches.py --parcel-csv X.csv

Writes data/debug/match_diagnosis.txt. Send that one file back.
For every unmatched lead it checks the parcel file three ways:
  A) the street exists in the file but not that house number
  B) the house number exists on a similarly named street (spelling / naming difference)
  C) the street name does not appear anywhere in the file
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
from collections import Counter, defaultdict
from pathlib import Path

import config
import enrichment
import parsers


_DIRS = enrichment._DIRS


def street_name(addr: str) -> tuple[str, str]:
    """(house number, comparable street name) using the same rules as the matcher."""
    parts = enrichment.lead_parts(addr)
    return (parts[0], parts[1]) if parts else ("", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parcel-csv", default="Parcel.csv")
    ap.add_argument("--unmatched", default=str(config.DEBUG_DIR / "unmatched_addresses.csv"))
    ap.add_argument("--limit", type=int, default=25, help="examples to print per category")
    args = ap.parse_args()

    with open(args.unmatched, newline="", encoding="utf-8-sig") as fh:
        unmatched = list(csv.DictReader(fh))
    want = [(r["property_address"], *street_name(r["property_address"])) for r in unmatched]
    want_nums = {n for _, n, _ in want if n}
    want_names = {nm for _, _, nm in want if nm}

    by_num: dict[str, list[dict]] = defaultdict(list)
    names_in_file: Counter = Counter()
    total = 0
    cities: Counter = Counter()
    zips_seen: Counter = Counter()
    blank_staddr = 0
    with open(args.parcel_csv, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            total += 1
            cities[parsers.clean(row.get("USPS_CITY", "")).upper()] += 1
            if not parsers.clean(row.get("STADDR", "")):
                blank_staddr += 1
            nm = enrichment.canon_name(row.get("STNAME", ""))
            if nm in want_names:
                names_in_file[nm] += 1
            for num in enrichment._row_numbers(row) & want_nums:
                by_num[num].append({k: parsers.clean(row.get(k, "")) for k in
                                    ("STADDR", "STHNUM", "STDIRE", "STNAME", "STSFX", "USPS_CITY", "PARCEL ID")})

    cat_a, cat_b, cat_c = [], [], []
    for addr, num, nm in want:
        same_num = by_num.get(num, [])
        close = [p for p in same_num
                 if difflib.SequenceMatcher(None, enrichment.canon_name(p["STNAME"]), nm).ratio() >= 0.6]
        if close:
            cat_b.append((addr, close[:3]))
        elif names_in_file.get(nm):
            cat_a.append((addr, names_in_file[nm]))
        else:
            parts = enrichment.lead_parts(addr)
            d = parts[2] if parts else ""
            hints = [q for q in same_num if not d or q["STDIRE"].upper() == d][:3]
            cat_c.append((addr, hints))

    out = [f"Parcel file rows: {total:,} | rows with blank STADDR: {blank_staddr:,}",
           "Top cities in file: " + ", ".join(f"{c or '(blank)'} {n:,}" for c, n in cities.most_common(6)),
           f"Unmatched leads checked: {len(want)}",
           f"  A) street exists in file, house number missing : {len(cat_a)}",
           f"  B) same house number on a similarly named street: {len(cat_b)}",
           f"  C) street name not in file at all               : {len(cat_c)}", ""]
    out.append("== B examples (naming difference; fixable in code) ==")
    for addr, rows in cat_b[: args.limit]:
        out.append(f"{addr}  ->  " + " | ".join(
            f"STADDR={p['STADDR']!r} STDIRE={p['STDIRE']!r} STNAME={p['STNAME']!r} STSFX={p['STSFX']!r}" for p in rows))
    out.append("\n== A examples (street known, number absent) ==")
    for addr, n in cat_a[: args.limit]:
        out.append(f"{addr}  (street appears on {n} parcels)")
    out.append("\n== C examples (street not in file) ==")
    for addr, hints in cat_c[: args.limit]:
        out.append(f"{addr}  ->  same number/direction in file: " + (" | ".join(
            f"{h['STADDR']!r} ({h['USPS_CITY']})" for h in hints) or "none"))

    path = config.DEBUG_DIR / "match_diagnosis.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:8]))
    print("\nWrote", path)


if __name__ == "__main__":
    main()
