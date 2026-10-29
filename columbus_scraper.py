"""Playwright scraper for the Columbus Accela Citizen Access Enforcement module.

Flow: open Enforcement tab -> enter Start/End date -> Search -> read grid ->
paginate -> (optionally) open each record's detail page.
Large result sets are capped by Accela, so date windows are split automatically.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

import config
import parsers

log = logging.getLogger("columbus_scraper")


# ----------------------------------------------------------------- helpers
def _first(page: Page, selectors: list[str]):
    """Return the first locator from `selectors` that exists on the page."""
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def _wait_idle(page: Page, extra: float = 0.0):
    try:
        page.wait_for_load_state("networkidle", timeout=config.NAV_TIMEOUT_MS)
    except PWTimeout:
        pass
    # Accela shows a "Loading..." overlay during postbacks
    try:
        page.wait_for_selector("text=/^\\s*Loading\\.\\.\\.\\s*$/", state="hidden", timeout=15_000)
    except PWTimeout:
        pass
    if extra:
        time.sleep(extra)


def set_date(page: Page, key: str, value: str):
    """Enter an mm/dd/yyyy date into an Accela masked date box and verify it stuck."""
    loc = _first(page, config.SEL[key])
    if loc is None:
        raise RuntimeError(f"Could not find input '{key}'. Run discover_columbus.py and update config.SEL.")
    loc.click()
    loc.press("Control+A")
    loc.press("Delete")
    loc.type(value, delay=60)
    loc.press("Tab")
    if loc.input_value().strip() != value:
        # fallback: set value directly and fire the events the ASP.NET extenders listen for
        loc.evaluate(
            """(el, v) => { el.value = v;
                for (const t of ['input', 'change', 'blur']) el.dispatchEvent(new Event(t, {bubbles: true})); }""",
            value)
    got = loc.input_value().strip()
    if got != value:
        raise RuntimeError(f"Date field '{key}' shows {got!r}, expected {value!r}")


def portal_error(page: Page) -> str:
    """Return Accela's error banner text (e.g. 'String was not recognized...'), or ''."""
    try:
        loc = page.locator(".ACA_Message_Error").first
        if loc.count():
            return " ".join(loc.inner_text().split())
    except Exception:
        pass
    return ""


# ----------------------------------------------------------------- core class
class ColumbusEnforcementScraper:
    def __init__(self, headless: bool = config.HEADLESS, fetch_details: bool = config.FETCH_DETAILS):
        self.headless = headless
        self.fetch_details = fetch_details
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page: Page | None = None

    # context-manager plumbing
    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            viewport={"width": 1400, "height": 1000},
        )
        self._ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        self._ctx.set_default_timeout(config.NAV_TIMEOUT_MS)
        self.page = self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        for obj in (self._ctx, self._browser):
            try:
                obj.close()
            except Exception:
                pass
        if self._pw:
            self._pw.stop()

    # ---------------------------------------------------------- search
    def _open_search(self):
        self.page.goto(config.ENTRY_URL)
        _wait_idle(self.page, 1)

    def _run_search(self, start: date, end: date) -> bool:
        """Submit a date-range search. Returns False when there are no results."""
        self._open_search()
        set_date(self.page, "start_date", start.strftime("%m/%d/%Y"))
        set_date(self.page, "end_date", end.strftime("%m/%d/%Y"))
        btn = _first(self.page, config.SEL["search_btn"])
        if btn is None:
            raise RuntimeError("Search button not found; update config.SEL['search_btn'].")
        btn.click()
        _wait_idle(self.page, config.PAGE_DELAY_SEC)

        err = portal_error(self.page)
        if err:
            raise RuntimeError(f"Portal rejected search {start}..{end}: {err}")
        if _first(self.page, config.SEL["results_table"]) is not None:
            return True
        return False

    _RANGE_JS = r"(()=>{const m=document.body.innerText.match(/Showing\s+\d+\s*-\s*\d+\s+of\s+\d+\+?/);return m?m[0]:'';})()"

    def _range_text(self) -> str:
        try:
            return self.page.evaluate(self._RANGE_JS)
        except Exception:
            return ""

    def _paginate(self) -> list[dict]:
        """Collect every row by clicking 'Next >' until it disappears.

        After each click we wait until the 'Showing a-b of N' label actually changes, so a
        slow refresh is never mistaken for the end of the list. Rows are de-duplicated by
        record number.
        """
        rows: list[dict] = []
        seen: set[str] = set()
        stale = 0
        for _ in range(config.MAX_PAGES):
            batch = parsers.parse_results_grid(self.page.content())
            new = [r for r in batch if r["record_number"] not in seen]
            for r in new:
                seen.add(r["record_number"])
                rows.append(r)
            stale = 0 if new else stale + 1
            if not batch or stale >= 3:
                break
            if rows and len(rows) // 100 != (len(rows) - len(new)) // 100:
                log.info("  ... %d rows so far (%s)", len(rows), self._range_text())

            nxt = _first(self.page, config.SEL["next_page"])
            if nxt is None:            # may just be mid-render; look once more
                time.sleep(2.5)
                nxt = _first(self.page, config.SEL["next_page"])
                if nxt is None:
                    break
            before = self._range_text()
            advanced = False
            for attempt in range(1 + config.PAGE_RETRIES):
                try:
                    nxt.click()
                except Exception:
                    nxt = _first(self.page, config.SEL["next_page"])
                    if nxt is None:
                        break
                    continue
                try:
                    self.page.wait_for_function(
                        f"(prev) => {self._RANGE_JS} !== prev", arg=before, timeout=45_000)
                    advanced = True
                    break
                except PWTimeout:
                    log.warning("  page stuck at %r after %d rows (attempt %d/%d); retrying",
                                before, len(rows), attempt + 1, 1 + config.PAGE_RETRIES)
                    time.sleep(5)
                    if self._range_text() != before:   # it advanced late; clicking again would skip a page
                        advanced = True
                        break
                    nxt = _first(self.page, config.SEL["next_page"]) or nxt
            if not advanced:
                log.warning("  giving up on this window at %d rows; re-run later to fill gaps", len(rows))
                break
            _wait_idle(self.page, config.PAGE_DELAY_SEC)
        return rows

    def search_window(self, start: date, end: date) -> list[dict]:
        """Search one date window and page through ALL results.

        The portal's "100+" label is not a cap: the count keeps growing as you page past
        page 10 until the real total appears, so we just follow "Next >" to the end.
        """
        log.info("Searching %s -> %s", start, end)
        if not self._run_search(start, end):
            log.info("  no results")
            return []
        rows = self._paginate()
        total, plus = parsers.parse_result_count(self.page.content())  # final page shows real total
        if plus:
            log.warning("  portal still shows %s+ after the last page; window may be incomplete "
                        "(re-run to fill gaps, records merge by number)", total)
        if total and not plus and len(rows) < total:
            log.warning("  parsed %d rows but portal reports %d; some pages may have failed", len(rows), total)
        log.info("  %d rows (portal total: %s%s)", len(rows), total or "?", "+" if plus else "")
        return rows

    def search_range(self, start: date, end: date) -> list[dict]:
        results: dict[str, dict] = {}
        cur = start
        while cur <= end:
            win_end = min(cur + timedelta(days=config.WINDOW_DAYS - 1), end)
            for r in self.search_window(cur, win_end):
                results[r["record_number"]] = r
            cur = win_end + timedelta(days=1)
        return list(results.values())

    # ---------------------------------------------------------- details
    def fetch_detail(self, rec: dict) -> dict:
        page = self._ctx.new_page()
        try:
            page.goto(rec["detail_url"])
            _wait_idle(page, config.PAGE_DELAY_SEC)
            # Accela hides owner/parcel under the "More Details" expander
            for label in ("More Details", "Record Details"):
                try:
                    link = page.locator(f'a:has-text("{label}")').first
                    if link.count():
                        link.click(timeout=3_000)
                        _wait_idle(page, 0.5)
                except Exception:
                    pass
            return parsers.parse_detail(page.content())
        except Exception as exc:  # keep going on a single bad record
            log.warning("detail failed for %s: %s", rec.get("record_number"), exc)
            return {}
        finally:
            page.close()

    def scrape(self, start: date, end: date) -> list[dict]:
        rows = self.search_range(start, end)
        if config.ONLY_OPEN:
            before = len(rows)
            rows = [r for r in rows if not parsers.is_closed(r.get("status", ""))]
            log.info("Open cases only: kept %d of %d (dropped %d closed)", len(rows), before, before - len(rows))
        before = len(rows)
        rows = [r for r in rows if not any(t in r.get("record_type", "").lower() for t in config.EXCLUDE_TYPES)]
        log.info("Excluding non-property record types: kept %d of %d", len(rows), before)
        if config.EXCLUDE_UNITS:
            before = len(rows)
            rows = [r for r in rows if not parsers.lead_is_unit(r.get("address", ""), r.get("description", ""))]
            log.info("Excluding apartments/units: kept %d of %d", len(rows), before)
        if self.fetch_details:
            for i, rec in enumerate(rows, 1):
                log.info("detail %d/%d %s", i, len(rows), rec["record_number"])
                rec.update({k: v for k, v in self.fetch_detail(rec).items() if v})
                time.sleep(config.PAGE_DELAY_SEC)
            if config.ONLY_OPEN:  # detail page status is authoritative
                rows = [r for r in rows if not parsers.is_closed(r.get("record_status", ""))]
        return rows
