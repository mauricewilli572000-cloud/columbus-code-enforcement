"""Owner / mailing-address enrichment from the Franklin County Auditor monthly parcel CSV.

Same source the E-Foreclosure scraper uses. Columns of interest:
NAME1 (owner), STADDR (situs), MAILAD3 (mailing street), MAILAD4 (combined city/state/zip).
"""
from __future__ import annotations

import csv
import gzip
import logging
import re
from datetime import date
from pathlib import Path

import requests

import config
import parsers

log = logging.getLogger("enrichment")


_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/csv,application/octet-stream,*/*",
}


def download_parcel_csv(today: date | None = None) -> Path | None:
    """Find this month's parcel CSV (falling back up to 4 months). Cached on disk.

    A local file given via --parcel-csv / config.PARCEL_CSV_PATH wins (reuse the file the
    Franklin foreclosure scraper already downloaded). Otherwise tries a browser-like request
    and both '10' and '1'-style month folders, logging the HTTP status of every attempt.
    """
    if config.PARCEL_CSV_PATH:
        p = Path(config.PARCEL_CSV_PATH)
        if p.exists():
            log.info("Using local parcel CSV: %s", p)
            return p
        log.warning("--parcel-csv %s not found", p)

    repo_file = Path(config.PARCEL_LOOKUP_FILE)
    if repo_file.exists():  # slim file committed to the repo (used by GitHub Actions)
        log.info("Using repo parcel lookup: %s", repo_file)
        return repo_file

    today = today or date.today()
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    y, m = today.year, today.month
    for step in range(config.PARCEL_LOOKBACK_MONTHS):
        dest = config.CACHE_DIR / f"Parcel_{y}_{m:02d}.csv"
        if dest.exists() and dest.stat().st_size > 1_000_000:
            return dest
        for month_str in dict.fromkeys((f"{m:02d}", str(m))):
            url = config.AUDITOR_CSV_URL.format(year=y, month=month_str)
            try:
                with requests.get(url, stream=True, timeout=180, headers=_HEADERS) as r:
                    log.info("Auditor CSV %s -> HTTP %s", url, r.status_code)
                    if r.status_code == 200:
                        with open(dest, "wb") as fh:
                            for chunk in r.iter_content(1 << 20):
                                fh.write(chunk)
                        if dest.stat().st_size > 1_000_000:
                            if step:
                                log.warning("Using Auditor parcel data from %s-%02d (%d months old); owners may "
                                            "have changed since - verify before outreach", y, m, step)
                            return dest
                        dest.unlink(missing_ok=True)
            except requests.RequestException as exc:
                log.warning("Auditor CSV %s failed: %s", url, exc)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return None


_STREET_SUFFIXES = {"ST", "AVE", "RD", "DR", "LN", "CT", "BLVD", "PL", "CIR", "PKWY", "TER", "WAY", "ALY", "TRL",
                    "TRAIL", "PIKE", "SQ", "LOOP", "PT", "XING", "HWY", "PARK", "RUN", "ROW", "PATH", "CV", "GLN"}

# Parcel columns copied onto the lead (property facts useful for filtering / scoring)
_PARCEL_FACTS = {"LANDUSE": "land_use", "PCLASS": "prop_class", "HOMSTD": "homestead",
                 "TRANDT": "last_sale_date", "PRICE": "last_sale_price", "YEARBLT": "year_built"}

_DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}

# The Auditor abbreviates street types its own way (WY, BL, TR, CR ...); map every spelling to one form.
_SFX_ALIAS = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "LA": "LN", "COURT": "CT",
    "PLACE": "PL", "BOULEVARD": "BLVD", "BL": "BLVD", "BLV": "BLVD", "WY": "WAY", "TR": "TRL", "TRAIL": "TRL",
    "CR": "CIR", "CIRCLE": "CIR", "TE": "TER", "TERR": "TER", "TERRACE": "TER", "PK": "PKWY", "PW": "PKWY",
    "PKY": "PKWY", "PARKWAY": "PKWY", "HW": "HWY", "HIGHWAY": "HWY", "AL": "ALY", "ALLEY": "ALY",
    "SQUARE": "SQ", "SQR": "SQ", "XG": "XING", "CROSSING": "XING", "COVE": "CV", "GL": "GLN", "GLEN": "GLN",
    "RN": "RUN", "POINT": "PT",
}
_SFX_WORDS = set(_SFX_ALIAS) | set(_SFX_ALIAS.values()) | _STREET_SUFFIXES

_ONES = ["", "FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH",
         "ELEVENTH", "TWELFTH", "THIRTEENTH", "FOURTEENTH", "FIFTEENTH", "SIXTEENTH", "SEVENTEENTH",
         "EIGHTEENTH", "NINETEENTH"]
_TENS = {2: "TWENTY", 3: "THIRTY", 4: "FORTY", 5: "FIFTY", 6: "SIXTY", 7: "SEVENTY", 8: "EIGHTY", 9: "NINETY"}
_TENS_ORD = {2: "TWENTIETH", 3: "THIRTIETH", 4: "FORTIETH", 5: "FIFTIETH", 6: "SIXTIETH", 7: "SEVENTIETH",
             8: "EIGHTIETH", 9: "NINETIETH"}


def _ordinal_words(n: int) -> str:
    if 1 <= n <= 19:
        return _ONES[n]
    if 20 <= n <= 99:
        t, o = divmod(n, 10)
        return _TENS_ORD[t] if o == 0 else f"{_TENS[t]} {_ONES[o]}"
    return ""


def canon_name(name: str) -> str:
    """Street name in one comparable form: '19TH' -> 'NINETEENTH', hyphens/spaces/apostrophes ignored."""
    toks = re.sub(r"[.'`]", "", (name or "").upper().replace("-", " ")).split()
    out = []
    for t in toks:
        m = re.match(r"^(\d+)(ST|ND|RD|TH)$", t)
        out.append(_ordinal_words(int(m.group(1))) or t if m else t)
    return "".join(out).replace(" ", "")


def canon_sfx(s: str) -> str:
    s = (s or "").upper().strip(". ")
    return _SFX_ALIAS.get(s, s)


def _sfx_ok(a: str, b: str) -> bool:
    """Street types agree (after aliasing); blank on either side is accepted; unknown short forms may be prefixes."""
    a, b = canon_sfx(a), canon_sfx(b)
    if not a or not b or a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 2 and long_.startswith(short)


def lead_parts(address: str):
    """(house number, bare street name key, direction, street type) for a lead address, or None."""
    toks = parsers.normalize_address(address).split()
    if len(toks) < 2 or not toks[0].isdigit():
        return None
    num, rest = str(int(toks[0])), toks[1:]
    direction = sfx = ""
    if len(rest) > 1 and rest[0] in _DIRS:
        direction, rest = rest[0], rest[1:]
    elif len(rest) > 1 and rest[-1] in _DIRS:
        direction, rest = rest[-1], rest[:-1]
    if len(rest) > 1 and rest[-1] in _SFX_WORDS:
        sfx, rest = rest[-1], rest[:-1]
    if not rest or num == "0":
        return None
    return num, canon_name(" ".join(rest)), direction, sfx


_RANGE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\b")


def _row_numbers(row: dict) -> set[str]:
    """House numbers a parcel answers to: its STHNUM plus every number inside an 'A - B' range."""
    nums: set[str] = set()
    h = parsers.clean(row.get("STHNUM", "")).lstrip("0")
    if h.isdigit():
        nums.add(h)
    m = _RANGE.match(row.get("STADDR", "") or "")
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 0 < a <= b and b - a <= 60:
            step = 2 if (b - a) % 2 == 0 else 1
            nums.update(str(x) for x in range(a, b + 1, step))
        nums.add(str(a))
    return nums


def _row_street(row: dict):
    """(name key, direction, street type) for a parcel row, from its street-part columns."""
    name = canon_name(row.get("STNAME", ""))
    d, s = parsers.clean(row.get("STDIRE", "")).upper(), parsers.clean(row.get("STSFX", "")).upper()
    if not name:  # fall back to parsing STADDR with any range collapsed to its first number
        addr = _RANGE.sub(lambda m: m.group(1), row.get("STADDR", "") or "")
        parts = lead_parts(addr)
        if not parts:
            return None
        _, name, d2, s2 = parts
        d, s = d or d2, s or s2
    return name, d, s


def _row_key(row: dict) -> str:
    """Normalized situs address for a parcel row (STADDR, else built from the street parts)."""
    key = parsers.normalize_address(row.get("STADDR", ""))
    if not key:
        key = parsers.normalize_address(" ".join(parsers.clean(row.get(c, "")) for c in
                                                 ("STHNUM", "STDIRE", "STNAME", "STSFX")))
    return key


def build_lookup(csv_path: Path, wanted: set[str]):
    """Stream the (large) CSV once.

    Returns (exact, by_street): exact maps wanted normalized addresses to a parcel row;
    by_street maps (house number, street-name key) -> [(direction, street type, row)] for the
    house numbers we care about, covering ranges, abbreviations, spelled-out ordinals, missing directions.
    """
    wanted_nums = {p[0] for p in (lead_parts(w) for w in wanted) if p}
    exact: dict[str, dict] = {}
    by_street: dict[tuple[str, str], list] = {}
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    with opener(csv_path, "rt", newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        log.info("Parcel CSV columns: %s", reader.fieldnames)
        for row in reader:
            key = _row_key(row)
            if key and key in wanted and key not in exact:
                exact[key] = row
            nums = _row_numbers(row) & wanted_nums
            if not nums:
                continue
            st = _row_street(row)
            if not st:
                continue
            name, d, s = st
            for n in nums:
                by_street.setdefault((n, name), []).append((d, s, row))
    return exact, by_street


def _candidates(by_street: dict, parts):
    num, name, d, s = parts
    return by_street.get((num, name), [])


def _pick(cands: list, parts):
    """Choose the one parcel that fits; None when none fit or several different owners do."""
    _, _, d, s = parts
    ok = [c for c in cands if _sfx_ok(s, c[1]) and (not d or not c[0] or d == c[0])]
    if len(ok) > 1 and d:
        same = [c for c in ok if c[0] == d]
        if len(same) == 1:
            ok = same
    if not ok:
        return None
    if len(ok) == 1 or len({parsers.clean(c[2].get("NAME1", "")) for c in ok}) == 1:
        return ok[0][2]
    return None


def _apply_row(r: dict, row: dict):
    r.setdefault("owner_name", "")
    if not r["owner_name"]:
        r["owner_name"] = parsers.clean(row.get("NAME1", ""))
    street = parsers.clean(row.get("MAILAD3", ""))
    tail = parsers.split_csz(row.get("MAILAD4", ""))
    if street and not r.get("mailing_address"):
        r["mailing_address"] = street
        r["mailing_city"] = tail["city"].title()
        r["mailing_state"] = tail["state"]
        r["mailing_zip"] = tail["zip"]
    if not r.get("parcel_number"):
        r["parcel_number"] = parsers.clean(row.get("PARCEL ID", "") or row.get("PARCELID", "")).replace("-", "")
    r["parcel_address"] = parsers.clean(row.get("STADDR", ""))
    if not r.get("property_zip"):
        z = parsers.clean(row.get("ZIPCODE", ""))[:5]
        if z.isdigit() and len(z) == 5:
            r["property_zip"] = z
    if not r.get("property_city") and parsers.clean(row.get("USPS_CITY", "")):
        r["property_city"] = parsers.clean(row["USPS_CITY"]).title()
    for col, field in _PARCEL_FACTS.items():
        val = parsers.clean(row.get(col, ""))
        if val:
            r[field] = val


def enrich(records: list[dict]) -> list[dict]:
    """Fill owner / mailing / parcel facts from the Auditor CSV; log and save what didn't match."""
    wanted = {parsers.normalize_address(r.get("property_address", "")) for r in records}
    wanted.discard("")
    if not wanted:
        return records
    path = download_parcel_csv()
    if path is None:
        log.warning("Auditor CSV unavailable; skipping enrichment. Download Parcel.csv in a browser "
                    "(or reuse the foreclosure scraper's copy) and re-run with --parcel-csv <path>")
        return records
    exact, by_street = build_lookup(path, wanted)

    n_exact = n_name = 0
    unmatched: list[dict] = []
    for r in records:
        addr = r.get("property_address", "")
        key = parsers.normalize_address(addr)
        row = exact.get(key)
        if row:
            n_exact += 1
        else:
            parts = lead_parts(addr)
            if parts:
                row = _pick(_candidates(by_street, parts), parts)
                if row:
                    n_name += 1
        if row:
            _apply_row(r, row)
        elif key:
            unmatched.append(r)
    log.info("Auditor matches: %d exact + %d by street name/number of %d leads (%d unmatched)",
             n_exact, n_name, len([r for r in records if r.get("property_address")]), len(unmatched))
    _write_unmatched(unmatched, by_street)
    return records


def _write_unmatched(unmatched: list[dict], by_street: dict):
    """data/debug/unmatched_addresses.csv - unmatched leads with any near-miss parcels."""
    if not unmatched:
        return
    config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DEBUG_DIR / "unmatched_addresses.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["record", "property_address", "normalized", "near_miss_parcel_addresses"])
        for r in unmatched:
            addr = r.get("property_address", "")
            parts = lead_parts(addr)
            near = []
            if parts:
                near = [f"{c[2].get('STADDR','')} / {c[2].get('USPS_CITY','')}" for c in _candidates(by_street, parts)][:3]
            w.writerow([r.get("doc_num", ""), addr, parsers.normalize_address(addr), " | ".join(near)])
    log.info("Unmatched addresses saved to %s", path)


def _collapse_range(addr: str) -> str:
    """'129 - 135 S CHAMPION AVE' -> '129 S CHAMPION AVE' (parcels list ranges; leads list one number)."""
    return _RANGE.sub(lambda m: m.group(1), addr or "")


def _numbers_in(addr: str) -> set[str]:
    return _row_numbers({"STHNUM": "", "STADDR": addr}) or (
        {str(int(m.group(1)))} if (m := re.match(r"\s*(\d+)", addr or "")) else set())


def same_address(a: str, b: str) -> bool:
    """True when two street addresses are the same place despite ranges, WY/WAY, 19TH/NINETEENTH, N/blank."""
    pa, pb = lead_parts(_collapse_range(a)), lead_parts(_collapse_range(b))
    if pa and pb:
        return (pa[1] == pb[1] and bool(_numbers_in(a) & _numbers_in(b)) and _sfx_ok(pa[3], pb[3])
                and (not pa[2] or not pb[2] or pa[2] == pb[2]))
    return parsers.normalize_address(a) == parsers.normalize_address(b)   # e.g. PO BOX addresses


def flag_absentee(rec: dict) -> bool:
    """Owner's mailing address is somewhere other than the property."""
    mail, prop = rec.get("mailing_address", ""), rec.get("property_address", "")
    if not (mail and prop):
        return False
    if same_address(mail, prop):
        return False
    # duplex / multi-unit building: owner mails to the other half of the parcel's own address range
    parcel = rec.get("parcel_address", "")
    return not (parcel and same_address(mail, parcel))
