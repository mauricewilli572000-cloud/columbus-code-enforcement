"""Run this LOCALLY (Windows is fine) to confirm the portal's real selectors.

    python discover_columbus.py

It opens the Enforcement tab, dumps every form control (id / name / type / label),
runs a 7-day search, saves the results page, then opens the first record and saves
its detail page. Everything lands in data/debug/. Send those files back (or just
the *_controls.txt and the two .html files) and the selectors in config.py can be
locked in exactly.
"""
import argparse
import re
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

import config

_ap = argparse.ArgumentParser()
_ap.add_argument("--days", type=int, default=7, help="look-back window for the sample search (default 7)")
ARGS = _ap.parse_args()
config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def dump_controls(page, name):
    rows = page.evaluate("""() => [...document.querySelectorAll('input,select,textarea,a[id],button')]
        .map(e => ({tag: e.tagName, id: e.id, name: e.name || '', type: e.type || '',
                    text: (e.innerText || e.value || '').trim().slice(0, 60)}))""")
    path = config.DEBUG_DIR / f"{name}_controls.txt"
    path.write_text("\n".join(f"{r['tag']:8} id={r['id']!r} name={r['name']!r} type={r['type']!r} text={r['text']!r}"
                              for r in rows if r["id"] or r["name"]), encoding="utf-8")
    print("wrote", path, f"({len(rows)} controls)")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.goto(config.ENTRY_URL)
    page.wait_for_load_state("networkidle")

    # Expand "Search Additional Criteria" so its controls are captured too
    try:
        page.locator("text=Search Additional Criteria").first.click(timeout=3000)
        page.wait_for_timeout(800)
    except Exception:
        pass
    dump_controls(page, "01_search_form")
    (config.DEBUG_DIR / "01_search_form.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(config.DEBUG_DIR / "01_search_form.png"), full_page=True)

    from columbus_scraper import set_date, portal_error
    end = date.today()
    start = end - timedelta(days=ARGS.days)
    set_date(page, "start_date", start.strftime("%m/%d/%Y"))
    set_date(page, "end_date", end.strftime("%m/%d/%Y"))
    print("dates now read:", page.locator(config.SEL["start_date"][0]).first.input_value(),
          "->", page.locator(config.SEL["end_date"][0]).first.input_value())
    page.screenshot(path=str(config.DEBUG_DIR / "01b_dates_filled.png"), full_page=True)
    for sel in config.SEL["search_btn"]:
        loc = page.locator(sel)
        if loc.count():
            loc.first.click()
            print("clicked search via", sel)
            break
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    print("portal error banner:", portal_error(page) or "(none)")
    (config.DEBUG_DIR / "02_results.html").write_text(page.content(), encoding="utf-8")
    dump_controls(page, "02_results")
    page.screenshot(path=str(config.DEBUG_DIR / "02_results.png"), full_page=True)

    link = page.locator('a[href*="CapDetail"]').first
    if link.count():
        href = link.get_attribute("href")
        print("first record:", href)
        link.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        for label in ("More Details", "Record Details"):
            try:
                page.locator(f'a:has-text("{label}")').first.click(timeout=2500)
                page.wait_for_timeout(1000)
            except Exception:
                pass
        (config.DEBUG_DIR / "03_detail.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(config.DEBUG_DIR / "03_detail.png"), full_page=True)
        dump_controls(page, "03_detail")
    else:
        print("No result rows found - check 02_results.png to see what the portal returned.")
    browser.close()
print("Done. Files are in", config.DEBUG_DIR)
