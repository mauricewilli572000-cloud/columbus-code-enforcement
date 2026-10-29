# Columbus Code Enforcement Lead Scraper

Scrapes the City of Columbus Accela portal (Enforcement module) for code enforcement
records, enriches owners from the Franklin County Auditor parcel CSV, scores each
lead, and exports a GoHighLevel CSV. Same conventions as the Franklin E-Foreclosure
and Cuyahoga scrapers (shared lead schema, records.json + pending_records.json,
merge by record number, utf-8-sig CSV).

## Setup (Windows PowerShell)
    pip install -r requirements.txt
    playwright install chromium

## Step 1 - confirm selectors (one time)
    python discover_columbus.py
Opens a visible browser, runs a 7-day search, and saves form controls, the results
page and one detail page to data/debug/. Send those back and the selectors in
config.py get locked in. If the run already works, no changes are needed.

## Step 2 - run
    python run_pipeline.py --days 30 --headed --no-enrich      # smoke test
    python run_pipeline.py                                     # incremental / nightly

Outputs in data/: records.json, pending_records.json, ghl_code_enforcement.csv, state.json

## Foreclosure cross-match
    python run_pipeline.py --days 60 --foreclosure https://raw.githubusercontent.com/<you>/<repo>/main/data/records.json
Addresses that also appear in the Franklin foreclosure leads get foreclosure_match=true, a big score boost,
and fill any blank owner name. Accepts a local path or URL; repeat the flag for several sources.

## If the Auditor download fails
Reuse the Parcel.csv your foreclosure scraper already has (or download it in a browser), then:
    python run_pipeline.py --parcel-csv C:\\path\\to\\Parcel.csv
Records already saved get their owner/mailing filled on that run; no need to re-scrape everything.

## Dashboard
`docs/code_enforcement.html` is the lead dashboard (sortable, filterable, click a row for details, CSV download).
Every pipeline run refreshes `docs/code_enforcement_data.js` and `docs/code_enforcement.json`. Open the
HTML file directly, or serve `docs/` with GitHub Pages (Settings > Pages > Deploy from branch > /docs).

## Notes
- "100+" results is not a cap; the scraper pages through every result.
- Accela has no owner names. Owner + mailing address come from the Franklin Auditor CSV; skip-trace phone/email later.
- First run looks back 60 days. Apartments/unit addresses (Apt, Unit, #, Ste, trailing unit letter/number, 123A Main St) are excluded; use --include-units to keep them.
- Only active/open cases are kept (config.ONLY_OPEN). Closed cases are skipped at scrape time and removed on later runs if they close. Use --include-closed to override.
- Date-range search is split into 14-day windows; any window that hits Accela's
  result cap is halved automatically.
- Leads without a property address wait in pending_records.json.
- Scoring lives in config.SCORING_RULES - edit keywords/points freely.
- Tests (no network): python tests/test_offline.py
