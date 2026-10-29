"""Columbus code enforcement lead pipeline.

scrape -> normalize -> merge with prior runs -> auditor enrichment -> score ->
split (address / pending) -> JSON accumulation -> GoHighLevel CSV.

Usage:
    python run_pipeline.py                 # incremental since last run (or 90 days first time)
    python run_pipeline.py --days 30
    python run_pipeline.py --start 2026-09-01 --end 2026-10-24
    python run_pipeline.py --no-details --no-enrich --headed     # quick debug run
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta

import config
import cross_match
import enrichment
import parsers
from lead_scoring import score_record

log = logging.getLogger("pipeline")

BUSINESS_RE = re.compile(
    r"\b(LLC|L\.L\.C|INC|CORP|CO|COMPANY|LTD|LP|LLP|TRUST|TRUSTEE|PROPERTIES|HOLDINGS|"
    r"INVESTMENTS?|REALTY|HOMES|BANK|ESTATE|ASSOC|PARTNERS|ENTERPRISES?)\b", re.I)


# ------------------------------------------------------------------ io helpers
def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("%s is corrupt; starting empty", path)
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ normalize
def to_lead(raw: dict) -> dict:
    addr_raw = raw.get("address") or raw.get("property_address_detail") or ""
    parts = parsers.split_city_state_zip(addr_raw)
    owner_addr = parsers.split_city_state_zip(raw.get("owner_address_raw", ""))
    lead = {f: "" for f in config.LEAD_FIELDS}
    lead.update({
        "owner_name": raw.get("owner_name", ""),
        "mailing_address": owner_addr["street"] if raw.get("owner_address_raw") else "",
        "mailing_city": owner_addr["city"],
        "mailing_state": owner_addr["state"],
        "mailing_zip": owner_addr["zip"],
        "property_address": parts["street"] if addr_raw else "",
        "property_city": parts["city"].title() if addr_raw else "",
        "property_state": parts["state"] if addr_raw else "",
        "property_zip": parts["zip"] if addr_raw else "",
        "lead_type": "Code Enforcement",
        "doc_num": raw["record_number"],
        "case_number": raw["record_number"],
        "filing_date": raw.get("date", ""),
        "record_type": raw.get("record_type", ""),
        "record_status": raw.get("record_status") or raw.get("status", ""),
        "description": parsers.redact_contact(raw.get("description") or raw.get("detail_description", "")),
        "parcel_number": raw.get("parcel_number", ""),
        "source_url": raw.get("detail_url", ""),
        # kept for scoring only; stripped before saving is unnecessary (small)
        "short_notes": raw.get("short_notes", ""),
        "detail_description": raw.get("detail_description", ""),
    })
    return lead


def merge_by_doc(existing: dict, incoming: list[dict]) -> dict:
    today = date.today().isoformat()
    for lead in incoming:
        key = lead["doc_num"]
        prev = existing.get(key, {})
        merged = {**{f: "" for f in config.LEAD_FIELDS}, **prev,
                  **{k: v for k, v in lead.items() if v not in ("", None)}}
        merged["date_added"] = prev.get("date_added") or today
        merged["last_seen"] = today
        existing[key] = merged
    return existing


# ------------------------------------------------------------------ GHL export
def split_name(owner: str) -> tuple[str, str, str]:
    """Return (first, last, company). Auditor names are usually 'LAST FIRST [MIDDLE]'."""
    owner = parsers.clean(owner)
    if not owner:
        return "", "", ""
    if BUSINESS_RE.search(owner):
        return "", "", owner.title()
    toks = owner.replace("&", " ").split()
    if len(toks) == 1:
        return "", toks[0].title(), ""
    return toks[1].title(), toks[0].title(), ""


def write_ghl_csv(records: list[dict]):
    cols = ["First Name", "Last Name", "Company Name", "Phone", "Email",
            "Property Address", "Property City", "Property State", "Property Zip",
            "Mailing Address", "Mailing City", "Mailing State", "Mailing Zip",
            "Lead Type", "Foreclosure Match", "Foreclosure Case", "Case Number", "Filing Date", "Record Type", "Record Status",
            "Lead Score", "Tags", "Land Use", "Property Class", "Homestead", "Last Sale Date",
            "Last Sale Price", "Year Built", "Source", "Source URL"]
    with open(config.GHL_CSV_FILE, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(records, key=lambda x: -int(x.get("lead_score") or 0)):
            first, last, company = split_name(r.get("owner_name", ""))
            tail = parsers.split_city_state_zip(r.get("property_address", ""))
            w.writerow({
                "First Name": first, "Last Name": last, "Company Name": company,
                "Property Address": tail["street"],
                "Property City": r.get("property_city") or tail["city"] or "Columbus",
                "Property State": r.get("property_state") or tail["state"] or "OH",
                "Property Zip": r.get("property_zip") or tail["zip"],
                "Mailing Address": r.get("mailing_address", ""),
                "Mailing City": r.get("mailing_city", ""),
                "Mailing State": r.get("mailing_state", ""),
                "Mailing Zip": r.get("mailing_zip", ""),
                "Lead Type": r["lead_type"],
                "Foreclosure Match": "YES" if r.get("foreclosure_match") else "",
                "Foreclosure Case": r.get("foreclosure_case_number", ""),
                "Case Number": r["case_number"],
                "Filing Date": r.get("filing_date", ""),
                "Record Type": r.get("record_type", ""),
                "Record Status": r.get("record_status", ""),
                "Land Use": r.get("land_use", ""), "Property Class": r.get("prop_class", ""),
                "Homestead": r.get("homestead", ""), "Last Sale Date": r.get("last_sale_date", ""),
                "Last Sale Price": r.get("last_sale_price", ""), "Year Built": r.get("year_built", ""),
                "Lead Score": r.get("lead_score", ""),
                "Tags": r.get("lead_tags", ""),
                "Source": "Columbus Code Enforcement",
                "Source URL": r.get("source_url", ""),
            })


def write_dashboard_json(records: list[dict]):
    """Feed for the combined dashboard's 'Code Enforcement' tab (includes the Added date)."""
    cols = ["date_added", "filing_date", "lead_score", "owner_name", "property_address", "property_city", "property_state", "property_zip",
            "mailing_address", "mailing_city", "mailing_state", "mailing_zip",
            "case_number", "record_type", "record_status", "description",
            "foreclosure_match", "foreclosure_case_number", "absentee_owner", "repeat_violations",
            "land_use", "prop_class", "homestead", "last_sale_date", "last_sale_price", "year_built", "lead_tags", "source_url"]
    rows = [{c: r.get(c, "") for c in cols}
            for r in sorted(records, key=lambda x: -int(x.get("lead_score") or 0))]
    payload = {"updated": datetime.now().isoformat(timespec="seconds"), "count": len(rows), "leads": rows}
    save_json(config.DOCS_DIR / "code_enforcement.json", payload)
    # same data as a script so docs/code_enforcement.html also works when opened from disk
    js = "window.CE_DATA = " + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + ";\n"
    (config.DOCS_DIR / "code_enforcement_data.js").write_text(js, encoding="utf-8")


# ------------------------------------------------------------------ main
def finalize(all_leads: dict, foreclosure_index: dict | None = None) -> tuple[dict, dict]:
    """Enrich, flag, score, and split into (records with address, pending)."""
    leads = list(all_leads.values())
    if config.ONLY_OPEN:
        open_leads = [l for l in leads if not parsers.is_closed(l.get("record_status", ""))]
        if len(open_leads) != len(leads):
            log.info("Dropped %d closed leads", len(leads) - len(open_leads))
        leads = open_leads
    if config.EXCLUDE_UNITS:
        kept = [l for l in leads if not parsers.lead_is_unit(l.get("property_address", ""), l.get("description", ""))]
        if len(kept) != len(leads):
            log.info("Dropped %d apartment/unit leads", len(leads) - len(kept))
        leads = kept
    enrichment.enrich([l for l in leads if l.get("property_address")])

    if foreclosure_index:
        log.info("Foreclosure cross-match: %d double-distress addresses",
                 cross_match.apply_matches(leads, foreclosure_index))

    for l in leads:
        if l.get("property_address"):
            l["property_state"] = l.get("property_state") or "OH"
            l["property_city"] = l.get("property_city") or "Columbus"

    counts = Counter(parsers.normalize_address(l.get("property_address", "")) for l in leads)
    for l in leads:
        l["absentee_owner"] = enrichment.flag_absentee(l)
        reps = counts.get(parsers.normalize_address(l.get("property_address", "")), 1)
        l["repeat_violations"] = reps
        score, tags = score_record(l, reps)
        l["lead_score"], l["lead_tags"] = score, ", ".join(tags)

    records = {l["doc_num"]: l for l in leads if l.get("property_address")}
    pending = {l["doc_num"]: l for l in leads if not l.get("property_address")}
    return records, pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--details", action="store_true", help="also open each record's detail page (slow; adds little)")
    ap.add_argument("--no-details", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--foreclosure", action="append", default=[],
                    help="path or URL of Franklin foreclosure records.json to cross-match (repeatable)")
    ap.add_argument("--parcel-csv", help="path to a local Franklin Auditor Parcel.csv (skips the download)")
    ap.add_argument("--include-units", action="store_true", help="keep apartments / unit addresses")
    ap.add_argument("--include-closed", action="store_true", help="keep closed cases too")
    ap.add_argument("--min-score", type=int, default=0, help="only export leads >= score to the GHL CSV (the dashboard still shows all leads)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.parcel_csv:
        config.PARCEL_CSV_PATH = args.parcel_csv
    if args.include_units:
        config.EXCLUDE_UNITS = False
    if args.include_closed:
        config.ONLY_OPEN = False
    state = load_json(config.STATE_FILE, {})
    end = date.fromisoformat(args.end) if args.end else date.today()
    if args.start:
        start = date.fromisoformat(args.start)
    elif args.days:
        start = end - timedelta(days=args.days)
    elif state.get("last_run"):
        start = date.fromisoformat(state["last_run"]) - timedelta(days=3)  # small overlap
    else:
        start = end - timedelta(days=config.DEFAULT_LOOKBACK_DAYS)

    from columbus_scraper import ColumbusEnforcementScraper  # lazy: needs playwright

    with ColumbusEnforcementScraper(headless=not args.headed,
                                    fetch_details=args.details and not args.no_details) as scraper:
        raw = scraper.scrape(start, end)
    log.info("Scraped %d raw records (%s -> %s)", len(raw), start, end)

    existing = load_json(config.RECORDS_FILE, {})
    pending = load_json(config.PENDING_FILE, {})
    pool = {**pending, **existing}
    pool = merge_by_doc(pool, [to_lead(r) for r in raw])

    if args.no_enrich:
        enrichment.enrich = lambda recs: recs  # type: ignore[assignment]
    fc_records: list[dict] = []
    for src in (args.foreclosure or config.FORECLOSURE_SOURCES):
        fc_records += cross_match.load_records(src)
    fc_index = cross_match.build_index(fc_records) if fc_records else None
    if fc_index:
        log.info("Loaded %d foreclosure addresses for cross-match", len(fc_index))
    records, still_pending = finalize(pool, fc_index)

    save_json(config.RECORDS_FILE, records)
    save_json(config.PENDING_FILE, still_pending)
    export = [r for r in records.values() if int(r.get("lead_score") or 0) >= args.min_score]
    write_ghl_csv(export)
    write_dashboard_json(list(records.values()))   # dashboard always gets every open lead; --min-score only trims the GHL file
    save_json(config.STATE_FILE, {"last_run": end.isoformat(),
                                  "updated": datetime.now().isoformat(timespec="seconds")})
    log.info("records=%d pending=%d csv_rows=%d -> %s",
             len(records), len(still_pending), len(export), config.GHL_CSV_FILE)


if __name__ == "__main__":
    main()
