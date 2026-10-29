"""Offline tests using synthetic Accela-style HTML. Run: python -m pytest tests -q  (or python tests/test_offline.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import enrichment as _enr
import parsers
import run_pipeline

_REAL_ENRICH = _enr.enrich  # other tests stub enrichment.enrich; keep the real one
from lead_scoring import score_record

GRID = """
<div>Showing 1-2 of 2</div>
<table id="ctl00_PlaceHolderMain_dgvPermitList_gdvPermitList">
<tr><th></th><th>Date</th><th>Record Number</th><th>Record Type</th><th>Address</th><th>Status</th><th>Description</th></tr>
<tr><td><input type="checkbox"></td><td>10/01/2026</td>
 <td><a href="/Permits/Cap/CapDetail.aspx?Module=Enforcement&amp;capID1=26CE&amp;capID2=00000&amp;capID3=01234">26CE-01234</a></td>
 <td>Housing Complaint</td><td>123 MAIN ST COLUMBUS OH 43215</td><td>Open</td><td>Vacant house, boarded windows</td></tr>
<tr><td><input type="checkbox"></td><td>09/15/2026</td>
 <td><a href="/Permits/Cap/CapDetail.aspx?Module=Enforcement&amp;capID1=26CE&amp;capID2=00000&amp;capID3=01100">26CE-01100</a></td>
 <td>Weeds and Trash</td><td>45 ELM AVE COLUMBUS OH 43201</td><td>Closed</td><td>High grass</td></tr>
</table>
"""

DETAIL = """
<div><span>Record Status:</span><span>Violation Notice Sent</span></div>
<div>More Details</div>
<div>Owner:</div><div>SMITH JOHN</div><div>PO BOX 5, DAYTON OH 45402</div>
<div>Parcel Number:</div><div>010-012345-00</div>
<div>Work Description:</div><div>Structure is unsafe</div>
"""


def test_grid():
    rows = parsers.parse_results_grid(GRID)
    assert len(rows) == 2
    r = rows[0]
    assert r["record_number"] == "26CE-01234"
    assert r["date"] == "2026-10-01"
    assert r["address"].startswith("123 MAIN ST")
    assert r["status"] == "Open"
    assert r["detail_url"].startswith("https://portal.columbus.gov/Permits/Cap/CapDetail.aspx")
    assert "&amp;" not in r["detail_url"]


def test_count():
    assert parsers.parse_result_count("<p>Showing 1-10 of 100+</p>") == (100, True)
    assert parsers.parse_result_count(GRID) == (2, False)
    assert parsers.parse_result_count("<p>nothing</p>") == (0, False)


def test_detail():
    d = parsers.parse_detail(DETAIL)
    assert d["record_status"] == "Violation Notice Sent"
    assert d["owner_name"] == "SMITH JOHN"
    assert "DAYTON" in d["owner_address_raw"]
    assert d["parcel_number"] == "01001234500"
    assert "unsafe" in d["detail_description"].lower()


def test_address_norm():
    assert parsers.normalize_address("123 Main Street, Columbus OH 43215") == "123 MAIN ST"
    assert parsers.normalize_address("45 N. Elm Avenue Apt 3") == "45 N ELM AVE"
    t = parsers.split_city_state_zip("PO BOX 5, DAYTON OH 45402")
    assert (t["city"], t["state"], t["zip"]) == ("DAYTON", "OH", "45402")


def test_scoring():
    hot = {"record_type": "Housing Complaint", "description": "Vacant house boarded",
           "record_status": "Open", "absentee_owner": True, "filing_date": "2025-01-01"}
    cold = {"record_type": "Weeds", "description": "grass", "record_status": "Closed"}
    hs, ht = score_record(hot, repeat_count=3)
    cs, _ = score_record(cold)
    assert hs > 60 and "vacant" in ht and "absentee" in ht
    assert cs < 15 and 0 <= hs <= 100


def test_open_only():
    for closed in ("Closed", "Complied", "Case Closed - Voided", "Withdrawn", "Resolved", "In Compliance"):
        assert parsers.is_closed(closed), closed
    for opened in ("Open", "Not Complied", "Non-Compliant", "Violation Notice Sent", "Failed to comply", "", "Referred"):
        assert not parsers.is_closed(opened), opened
    leads = [run_pipeline.to_lead(r) for r in parsers.parse_results_grid(GRID)]  # 2nd row is Closed
    import enrichment
    enrichment.enrich = lambda recs: recs
    records, pending = run_pipeline.finalize(run_pipeline.merge_by_doc({}, leads))
    assert set(records) == {"26CE-01234"} and not pending


def test_units():
    units = ["123 MAIN ST APT 2B", "45 ELM AVE #4", "10 OAK DR UNIT 5", "200 HIGH ST STE 100",
             "123 MAIN ST A", "123 MAIN ST 2", "123A MAIN ST", "77 BROAD ST N 3B",
             "500 FRONT ST BLDG 4", "9 MAPLE LN APT 12, COLUMBUS OH 43201", "12 PARK AVE 2ND FLOOR"]
    singles = ["123 MAIN ST", "45 N HIGH ST", "123 MAIN ST NW", "88 E LONG ST COLUMBUS OH 43215",
               "1500 STATE ROUTE 3", "4000 CLEVELAND AVE", "55 COUNTY RD 12", "", "123 1/2 MAIN ST"]
    for u in units:
        assert parsers.is_unit_address(u), u
    for s in singles:
        assert not parsers.is_unit_address(s), s


def test_real_portal_quirks():
    # unit typed inside the complainant's description, none in the address field
    assert parsers.lead_is_unit("3715 BEULAH RD, Columbus OH 43224", "3715 BEULAH RD, B - There is tall weeds")
    assert not parsers.lead_is_unit("788 E COLUMBUS ST, Columbus OH 43206", "788 E COLUMBUS ST - My wife and I own the property")
    assert not parsers.lead_is_unit("4229 MAIZE RD, Columbus OH 43224", "4229 MAIZE RD - There is a red truck")
    # portal uses 'Active' for open cases; closed words still drop
    assert not parsers.is_closed("Active") and parsers.is_closed("Inactive") and parsers.is_closed("Completed")
    # complainant contact info is redacted
    red = parsers.redact_contact("Contact: 614.589.6391 or dmzubcsek@gmail.com or (614) 555-1212")
    assert "@" not in red and "6391" not in red and "555" not in red
    # detail-page header words are not owners
    assert parsers.parse_detail("<div>Owner</div><div>Contact</div><div>Email</div>")["owner_name"] == ""


def test_split_address_variants():
    a = parsers.split_city_state_zip("788 E COLUMBUS ST, Columbus OH 43206")
    assert (a["street"], a["city"], a["state"], a["zip"]) == ("788 E COLUMBUS ST", "Columbus", "OH", "43206")
    b = parsers.split_city_state_zip("123 MAIN ST COLUMBUS OH 43215")
    assert (b["street"], b["city"]) == ("123 MAIN ST", "COLUMBUS")
    c = parsers.split_city_state_zip("4229 MAIZE RD, Columbus OH 43224-1234")
    assert c["street"] == "4229 MAIZE RD" and c["zip"] == "43224"
    d = parsers.split_city_state_zip("55 ELM ST")
    assert d["street"] == "55 ELM ST" and d["city"] == ""


def test_auditor_enrichment():
    import config, enrichment, tempfile, pathlib
    f = pathlib.Path(tempfile.mkdtemp()) / "Parcel.csv"
    f.write_text("PARCELID,NAME1,STADDR,MAILAD3,MAILAD4\n010-012345-00,DOE JOHN,788 E COLUMBUS ST,PO BOX 9,DAYTON OH 45402\n",
                 encoding="utf-8")
    old = config.PARCEL_CSV_PATH
    config.PARCEL_CSV_PATH = str(f)
    try:
        rec = _REAL_ENRICH([{"property_address": "788 E COLUMBUS ST", "owner_name": "", "mailing_address": ""}])[0]
    finally:
        config.PARCEL_CSV_PATH = old
    assert rec["owner_name"] == "DOE JOHN" and rec["mailing_address"] == "PO BOX 9"
    assert (rec["mailing_city"], rec["mailing_state"], rec["mailing_zip"]) == ("Dayton", "OH", "45402")
    assert enrichment.flag_absentee(rec)
    assert parsers.split_csz("COLUMBUS OH 43215-1234") == {"city": "COLUMBUS", "state": "OH", "zip": "43215"}


def test_tolerant_match_and_facts():
    """Property facts are copied; same street name with a different street TYPE is not matched."""
    import config, tempfile, pathlib
    f = pathlib.Path(tempfile.mkdtemp()) / "Parcel.csv"
    f.write_text("PARCEL ID,NAME1,STADDR,MAILAD3,MAILAD4,LANDUSE,PCLASS,HOMSTD,TRANDT,PRICE,YEARBLT,STHNUM,STDIRE,STNAME,STSFX\n"
                 "010-000001-00,ROE RICHARD,12 ELM DR,12 ELM DR,COLUMBUS OH 43201,510,R,Y,2015-06-01,95000,1950,12,,ELM,DR\n"
                 "010-000002-00,TWO ONE,30 OAK ST,1 X ST,COLUMBUS OH 43201,510,R,N,2001-01-01,1,1960,30,,OAK,ST\n",
                 encoding="utf-8")
    old = config.PARCEL_CSV_PATH
    config.PARCEL_CSV_PATH = str(f)
    try:
        recs = [{"property_address": "12 ELM DR", "owner_name": ""},
                {"property_address": "30 OAK RD", "owner_name": ""},     # OAK RD is not OAK ST -> no match
                {"property_address": "99 NOWHERE LN", "owner_name": ""}]
        out = _REAL_ENRICH(recs)
    finally:
        config.PARCEL_CSV_PATH = old
    assert out[0]["owner_name"] == "ROE RICHARD" and out[0]["land_use"] == "510" and out[0]["homestead"] == "Y"
    assert out[0]["last_sale_date"] == "2015-06-01" and out[0]["year_built"] == "1950"
    assert out[1]["owner_name"] == "" and out[2]["owner_name"] == ""


def test_real_auditor_formats():
    """Formats seen in the real Parcel.csv: ranges, WY/BL/TR/CR, spelled-out ordinals, missing/extra directions."""
    import config, tempfile, pathlib
    hdr = "PARCEL ID,NAME1,STADDR,MAILAD3,MAILAD4,STHNUM,STDIRE,STNAME,STSFX\n"
    P = [  # (owner, STADDR, STHNUM, STDIRE, STNAME, STSFX)
        ("CHAMPION OWNER", "129 - 135 S CHAMPION AVE", "129", "S", "CHAMPION", "AVE"),
        ("SOUTHAMPTON OWNER", "129 S SOUTHAMPTON AVE", "129", "S", "SOUTHAMPTON", "AVE"),
        ("KILBANNON OWNER", "4031 KILBANNON WY", "4031", "", "KILBANNON", "WY"),
        ("REFUGEE OWNER", "3670 - 3680 REFUGEE RD", "3670", "", "REFUGEE", "RD"),
        ("NEFF OWNER", "52 - 54 NEFF AVE", "52", "", "NEFF", "AVE"),
        ("KOSSUTH OWNER", "1698 KOSSUTH ST", "1698", "", "KOSSUTH", "ST"),
        ("MAGNOLIA OWNER", "4964 MAGNOLIA BLOSSOM BL", "4964", "", "MAGNOLIA BLOSSOM", "BL"),
        ("FREEDOM OWNER", "2774 FREEDOM TR", "2774", "", "FREEDOM", "TR"),
        ("IPSWICK OWNER", "2600 IPSWICK CR", "2600", "", "IPSWICK", "CR"),
        ("ONTARIO OWNER", "2316 N ONTARIO ST", "2316", "N", "ONTARIO", "ST"),
        ("NINETEENTH OWNER", "1445 19TH AVENUE E", "1445", "E", "NINETEENTH", "AVE"),
        ("EIGHTH OWNER", "3055 E EIGHTH AVE", "3055", "E", "EIGHTH", "AVE"),
        ("TWENTYFOURTH OWNER", "1467 E TWENTY FOURTH AVE", "1467", "E", "TWENTY FOURTH", "AVE"),
        ("HOLTZMAN OWNER", "463 HOLTZMAN AVE", "463", "", "HOLTZMAN", "AVE"),
        ("EAST OAK", "30 E OAK ST", "30", "E", "OAK", "ST"),
        ("WEST OAK", "30 W OAK ST", "30", "W", "OAK", "ST"),
        ("COLUMBUS ST OWNER", "788 E COLUMBUS ST", "788", "E", "COLUMBUS", "ST"),
    ]
    f = pathlib.Path(tempfile.mkdtemp()) / "Parcel.csv"
    f.write_text(hdr + "".join(f"1-{i},{o},{a},PO BOX 1,DAYTON OH 45402,{n},{d},{s},{x}\n"
                               for i, (o, a, n, d, s, x) in enumerate(P)), encoding="utf-8")
    cases = {  # lead address -> expected owner (None = must NOT match)
        "129 S CHAMPION AVE": "CHAMPION OWNER", "131 S CHAMPION AVE": "CHAMPION OWNER",   # range start and inside
        "4031 KILBANNON WAY": "KILBANNON OWNER", "3670 REFUGEE RD": "REFUGEE OWNER", "52 NEFF AVE": "NEFF OWNER",
        "1698 E KOSSUTH ST": "KOSSUTH OWNER", "4964 MAGNOLIA BLOSSOM BLVD": "MAGNOLIA OWNER",
        "2774 FREEDOM TRAIL": "FREEDOM OWNER", "2600 IPSWICK CIR": "IPSWICK OWNER",
        "2316 ONTARIO ST": "ONTARIO OWNER",
        "1445 E 19TH AVE": "NINETEENTH OWNER", "3055 E 8TH AVE": "EIGHTH OWNER", "1467 E 24TH AVE": "TWENTYFOURTH OWNER",
        "788 E COLUMBUS ST": "COLUMBUS ST OWNER",
        "463 HOLTON AVE": None,        # different street (HOLTZMAN)
        "1565 SMITH RD": None,         # nothing there
        "30 OAK ST": None,             # E OAK vs W OAK, different owners, no direction -> ambiguous
        "30 W OAK ST": "WEST OAK",     # direction disambiguates
        "30 OAK RD": None,             # street type differs
    }
    old = config.PARCEL_CSV_PATH
    config.PARCEL_CSV_PATH = str(f)
    try:
        out = _REAL_ENRICH([{"property_address": a, "owner_name": ""} for a in cases])
    finally:
        config.PARCEL_CSV_PATH = old
    for rec, (addr, want) in zip(out, cases.items()):
        assert (rec["owner_name"] or None) == want, (addr, rec["owner_name"], want)
    assert parsers.normalize_address("788 E COLUMBUS ST, Columbus OH 43206") == "788 E COLUMBUS ST"
    assert parsers.normalize_address("123 MAIN ST COLUMBUS OH 43215") == "123 MAIN ST"


def test_absentee_tolerates_formatting():
    import enrichment
    same = [("129 - 135 S CHAMPION AVE", "129 S CHAMPION AVE"), ("4031 KILBANNON WY", "4031 KILBANNON WAY"),
            ("1445 19TH AVENUE E", "1445 E 19TH AVE"), ("2316 N ONTARIO ST", "2316 ONTARIO ST"),
            ("100 MAIN ST APT 4", "100 MAIN ST"), ("788 E COLUMBUS ST", "788 E COLUMBUS ST"),
            ("131 S CHAMPION AVE", "129 - 135 S CHAMPION AVE")]
    diff = [("PO BOX 9", "123 MAIN ST"), ("500 OTHER RD", "123 MAIN ST"), ("123 MAIN ST", "123 ELM ST"),
            ("123 MAIN ST", "321 MAIN ST"), ("30 W OAK ST", "30 E OAK ST"), ("12 ELM DR", "12 ELM RD")]
    for a, b in same:
        assert enrichment.same_address(a, b), (a, b)
        assert not enrichment.flag_absentee({"mailing_address": a, "property_address": b}), (a, b)
    for a, b in diff:
        assert not enrichment.same_address(a, b), (a, b)
        assert enrichment.flag_absentee({"mailing_address": a, "property_address": b}), (a, b)
    assert not enrichment.flag_absentee({"mailing_address": "", "property_address": "123 MAIN ST"})
    # real case: owner mails to the other half of a duplex listed as one parcel range
    duplex = {"property_address": "1553 GAULT ST", "mailing_address": "1555 GAULT ST", "parcel_address": "1553 - 1555 GAULT ST"}
    assert not enrichment.flag_absentee(duplex)
    assert enrichment.flag_absentee({**duplex, "mailing_address": "3177 SAXON CT"})
    assert enrichment.flag_absentee({"property_address": "3195 SAXON CT", "mailing_address": "3177 SAXON CT", "parcel_address": "3195 SAXON CT"})


def test_min_score_trims_csv_only():
    import config, csv, json, tempfile, pathlib, sys
    d = pathlib.Path(tempfile.mkdtemp())
    old = (config.DATA_DIR, config.RECORDS_FILE, config.PENDING_FILE, config.STATE_FILE, config.GHL_CSV_FILE, config.DOCS_DIR)
    config.DATA_DIR = d; config.RECORDS_FILE = d / "records.json"; config.PENDING_FILE = d / "pending.json"
    config.STATE_FILE = d / "state.json"; config.GHL_CSV_FILE = d / "ghl.csv"; config.DOCS_DIR = d / "docs"
    leads = {}
    for i, sc in enumerate((90, 70, 30, 10)):
        l = run_pipeline.to_lead({"record_number": f"R-{i}", "date": "2026-10-25", "address": f"{10+i} MAIN ST, Columbus OH 43215",
                                  "status": "Active", "record_type": "Weeds"})
        l["lead_score"] = sc
        leads[l["doc_num"]] = l
    class FakeScraper:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def scrape(self, s, e): return []
    import types
    mod = types.ModuleType("columbus_scraper"); mod.ColumbusEnforcementScraper = FakeScraper
    saved_mod = sys.modules.get("columbus_scraper"); sys.modules["columbus_scraper"] = mod
    orig_finalize = run_pipeline.finalize
    run_pipeline.finalize = lambda pool, idx=None: (leads, {})
    argv = sys.argv; sys.argv = ["run_pipeline.py", "--min-score", "60", "--no-enrich"]
    try:
        run_pipeline.main()
    finally:
        sys.argv = argv; run_pipeline.finalize = orig_finalize
        if saved_mod is None: sys.modules.pop("columbus_scraper", None)
        else: sys.modules["columbus_scraper"] = saved_mod
    csv_rows = list(csv.DictReader(open(d / "ghl.csv", encoding="utf-8-sig")))
    feed = json.loads((d / "docs" / "code_enforcement.json").read_text(encoding="utf-8"))
    config.DATA_DIR, config.RECORDS_FILE, config.PENDING_FILE, config.STATE_FILE, config.GHL_CSV_FILE, config.DOCS_DIR = old
    assert len(csv_rows) == 2 and feed["count"] == 4 and len(feed["leads"]) == 4


def test_property_city_state_zip():
    import config, csv, tempfile, pathlib
    lead = run_pipeline.to_lead({"record_number": "26470-1", "date": "2026-10-25",
                                 "address": "788 E COLUMBUS ST, Columbus OH 43206"})
    assert (lead["property_address"], lead["property_city"], lead["property_state"], lead["property_zip"]) == \
        ("788 E COLUMBUS ST", "Columbus", "OH", "43206")
    # an older saved lead with only a street: Auditor fills city/zip, state defaults to OH
    f = pathlib.Path(tempfile.mkdtemp()) / "Parcel.csv"
    f.write_text("PARCEL ID,NAME1,STADDR,MAILAD3,MAILAD4,USPS_CITY,ZIPCODE\n010-1,DOE JOHN,4229 MAIZE RD,PO BOX 9,DAYTON OH 45402,COLUMBUS,43224-1234\n",
                 encoding="utf-8")
    import enrichment
    enrichment.enrich = _REAL_ENRICH
    old = config.PARCEL_CSV_PATH; config.PARCEL_CSV_PATH = str(f)
    try:
        old_lead = {"doc_num": "X-2", "case_number": "X-2", "lead_type": "Code Enforcement", "property_address": "4229 MAIZE RD",
                    "record_status": "Active"}
        recs, _ = run_pipeline.finalize({"X-2": old_lead})
    finally:
        config.PARCEL_CSV_PATH = old
    r = recs["X-2"]
    assert (r["property_city"], r["property_state"], r["property_zip"]) == ("Columbus", "OH", "43224")
    out = pathlib.Path(tempfile.mkdtemp()) / "g.csv"
    config.GHL_CSV_FILE = out
    run_pipeline.write_ghl_csv([r, lead])
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    got = {x["Property Address"]: (x["Property City"], x["Property State"], x["Property Zip"]) for x in rows}
    assert got["4229 MAIZE RD"] == ("Columbus", "OH", "43224") and got["788 E COLUMBUS ST"] == ("Columbus", "OH", "43206")


def test_foreclosure_cross_match():
    import config, cross_match, enrichment
    enrichment.enrich = lambda recs: recs
    fc = [{"property_address": "123 Main Street, Columbus OH 43215", "owner_name": "DOE JANE",
           "case_number": "25CV001234", "mailing_address": "9 Oak Dr"},
          {"property_address": "999 Other Rd", "owner_name": "NOPE"}]
    idx = cross_match.build_index(fc)
    leads = [run_pipeline.to_lead(r) for r in parsers.parse_results_grid(GRID)]
    config.ONLY_OPEN = False
    records, _ = run_pipeline.finalize(run_pipeline.merge_by_doc({}, leads), idx)
    config.ONLY_OPEN = True
    hit, miss = records["26CE-01234"], records["26CE-01100"]
    assert hit["foreclosure_match"] and hit["foreclosure_case_number"] == "25CV001234"
    assert hit["owner_name"] == "DOE JANE" and "foreclosure-match" in hit["lead_tags"]
    assert not miss["foreclosure_match"]
    assert hit["lead_score"] >= miss["lead_score"] + 30


def test_pipeline_split_and_names():
    raws = parsers.parse_results_grid(GRID)
    leads = [run_pipeline.to_lead(r) for r in raws]
    leads.append(run_pipeline.to_lead({"record_number": "X-1", "date": "2026-10-02"}))  # no address
    pool = run_pipeline.merge_by_doc({}, leads)
    import enrichment
    enrichment.enrich = lambda recs: recs  # no network
    import config
    config.ONLY_OPEN = False
    records, pending = run_pipeline.finalize(pool)
    config.ONLY_OPEN = True
    assert set(records) == {"26CE-01234", "26CE-01100"}
    assert set(pending) == {"X-1"}
    assert records["26CE-01234"]["lead_score"] > records["26CE-01100"]["lead_score"]
    assert run_pipeline.split_name("SMITH JOHN A") == ("John", "Smith", "")
    assert run_pipeline.split_name("ACME HOLDINGS LLC")[2] == "Acme Holdings Llc"
    # merge keeps date_added across runs
    again = run_pipeline.merge_by_doc(dict(pool), leads)
    assert again["26CE-01234"]["date_added"] == pool["26CE-01234"]["date_added"]


def test_slim_gz_parcel_lookup(tmp_path=None):
    import tempfile, csv, gzip, config, make_parcel_lookup
    d = Path(tempfile.mkdtemp())
    src = d / "Parcel.csv"
    with open(src, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["PARCEL ID", "NAME1", "STADDR", "STHNUM", "STNAME", "STSFX", "MAILAD3", "MAILAD4", "EXTRA"])
        w.writerow(["010-1", "DOE JOHN", "123 MAIN ST", "123", "MAIN", "ST", "9 OAK RD", "DAYTON OH 45402", "x"])
    gz = d / "lookup.csv.gz"
    assert make_parcel_lookup.build(src, gz) == 1
    with gzip.open(gz, "rt") as fh:
        assert "EXTRA" not in fh.readline()
    exact, _ = _enr.build_lookup(gz, {_enr.parsers.normalize_address("123 MAIN ST")})
    assert next(iter(exact.values()))["NAME1"] == "DOE JOHN"
    config.PARCEL_LOOKUP_FILE = gz
    config.PARCEL_CSV_PATH = ""
    try:
        assert _enr.download_parcel_csv() == gz
    finally:
        config.PARCEL_LOOKUP_FILE = config.DATA_DIR / "parcel_lookup.csv.gz"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
