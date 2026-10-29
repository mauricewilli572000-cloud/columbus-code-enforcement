"""Configuration for the Columbus (Franklin County, OH) code enforcement lead scraper.

Portal: Accela Citizen Access, Enforcement module.
All CSS selectors live here so they can be tuned in one place after running
discover_columbus.py against the live site.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"                # GitHub Pages dashboard feed
DEBUG_DIR = DATA_DIR / "debug"
CACHE_DIR = DATA_DIR / "parcel_cache"

ENTRY_URL = (
    "https://portal.columbus.gov/Permits/Cap/CapHome.aspx"
    "?module=Enforcement&TabName=Enforcement"
)
PORTAL_ROOT = "https://portal.columbus.gov/Permits/"

RECORDS_FILE = DATA_DIR / "records.json"          # leads WITH a property address
PENDING_FILE = DATA_DIR / "pending_records.json"  # leads held until an address is found
STATE_FILE = DATA_DIR / "state.json"              # last successful run date
GHL_CSV_FILE = DATA_DIR / "ghl_code_enforcement.csv"

# ---- Search behaviour -------------------------------------------------------
DEFAULT_LOOKBACK_DAYS = 60      # first run (no state.json)
WINDOW_DAYS = 14                # date-range chunk per search
PAGE_RETRIES = 3                # re-click Next this many times when a page does not advance
MAX_PAGES = 600                 # safety stop per window (10 rows/page); "100+" is NOT a cap
PAGE_DELAY_SEC = 1.5            # politeness delay between page loads
NAV_TIMEOUT_MS = 60_000
HEADLESS = True
EXCLUDE_UNITS = True            # drop apartments / addresses with a unit letter or number
ONLY_OPEN = True               # keep only active/open cases (drop closed)
FETCH_DETAILS = False           # anonymous detail pages show no owner/parcel; grid has everything useful
# Record types that say nothing about the property (substring match, lower-case)
EXCLUDE_TYPES = ("community noise",)

# ---- Selectors (Accela Citizen Access defaults; confirm with discover script) -
SEL = {
    "start_date": ['input[id$="txtGSStartDate"]', 'input[id*="StartDate"]'],
    "end_date": ['input[id$="txtGSEndDate"]', 'input[id*="EndDate"]'],
    "search_btn": ['a[id$="btnNewSearch"]', 'input[id$="btnNewSearch"]', 'a:has-text("Search")'],
    "results_table": ['table[id$="gdvPermitList"]', 'table[id*="PermitList"]'],
    "no_results": ['text=/no record(s)? (were )?found/i', 'text=/no results/i'],
    "next_page": ['a:has-text("Next >")', 'a:has-text("Next")'],
}

# Record types / keywords that indicate distressed or motivated-seller properties.
# (substring, points, tag)
SCORING_RULES = [
    ("vacant", 25, "vacant"),
    ("condemn", 30, "condemned"),
    ("demolition", 30, "demolition"),
    ("unsafe", 20, "unsafe-structure"),
    ("dangerous", 20, "unsafe-structure"),
    ("fire damage", 25, "fire-damage"),
    ("fire", 10, "fire"),
    ("boarded", 20, "boarded"),
    ("nuisance", 12, "nuisance"),
    ("housing", 10, "housing-code"),
    ("exterior", 8, "exterior-violation"),
    ("trash", 8, "trash-debris"),
    ("debris", 8, "trash-debris"),
    ("junk", 8, "junk-vehicles"),
    ("weeds", 6, "overgrown"),
    ("graffiti", 6, "graffiti"),
    ("inoperable", 6, "inoperable-vehicle"),
    ("high grass", 6, "overgrown"),
    ("roof", 10, "roof"),
    ("structural", 15, "structural"),
    ("illegal", 8, "illegal-use"),
    ("no heat", 15, "no-heat"),
    ("sewer", 10, "sewer"),
    ("rodent", 10, "infestation"),
    ("infest", 10, "infestation"),
]
OPEN_STATUS_HINTS = ("active", "open", "pending", "violation", "notice", "non-compliance",
                     "noncompliance", "referred", "in progress", "failed")
CLOSED_STATUS_HINTS = ("closed", "complied", "compliant", "resolved", "void", "cancel", "withdrawn")

# ---- Franklin County Auditor bulk parcel data (same source as the foreclosure scraper)
PARCEL_CSV_PATH = ""   # local Parcel.csv to use instead of downloading (or pass --parcel-csv)
PARCEL_LOOKUP_FILE = DATA_DIR / "parcel_lookup.csv.gz"   # slim owner file made by make_parcel_lookup.py
PARCEL_LOOKBACK_MONTHS = 36   # the Auditor archive can lag (latest folder may be a year old)
AUDITOR_CSV_URL = "https://apps.franklincountyauditor.com/Parcel_CSV/{year}/{month}/Parcel.csv"

# Lead schema. First block mirrors the shared county-scraper schema; the second
# block holds code-enforcement-specific fields.
LEAD_FIELDS = [
    "owner_name", "mailing_address", "mailing_city", "mailing_state", "mailing_zip",
    "property_city", "property_state", "property_zip", "parcel_address",
    "property_address", "lead_type", "doc_num", "case_number", "filing_date",
    "amount_owed", "interest_rate", "loan_type",
    # code enforcement specific
    "record_type", "record_status", "description", "parcel_number",
    "absentee_owner", "repeat_violations", "lead_score", "lead_tags",
    "land_use", "prop_class", "homestead", "last_sale_date", "last_sale_price", "year_built",
    "foreclosure_match", "foreclosure_case_number",
    "source_url", "date_added", "last_seen",
]

# Franklin County foreclosure leads to cross-match by property address. Local path or URL
# (e.g. the raw GitHub URL of the foreclosure repo's data/records.json). Override with --foreclosure.
FORECLOSURE_SOURCES: list[str] = []
FORECLOSURE_MATCH_POINTS = 35
ABSENTEE_POINTS = 15   # most code-enforcement owners are absentee, so this barely separates leads; lower it if you like
