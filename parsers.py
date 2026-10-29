"""Pure HTML/text parsing helpers (no browser needed, so they are unit-testable)."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import config

_WS = re.compile(r"[ \t\xa0]+")


def clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text.replace("\r", "")).strip()


def parse_date(text: str) -> str:
    """Return ISO date (YYYY-MM-DD) from mm/dd/yyyy, or '' if unparseable."""
    text = clean(text)
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


# ---------------------------------------------------------------- results grid
HEADER_MAP = {
    "date": "date",
    "record number": "record_number",
    "record type": "record_type",
    "address": "address",
    "status": "status",
    "description": "description",
    "project name": "project_name",
    "short notes": "short_notes",
    "expiration date": "expiration_date",
}


def parse_results_grid(html: str, base_url: str = config.PORTAL_ROOT) -> list[dict]:
    """Parse one page of the Accela results grid into a list of dicts."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one('table[id$="gdvPermitList"]') or soup.select_one('table[id*="PermitList"]')
    if table is None:
        return []

    # Column names come from the header row; fall back to positional guess.
    headers: list[str] = []
    for tr in table.find_all("tr"):
        ths = tr.find_all("th")
        if ths and not tr.find("a", href=re.compile("CapDetail", re.I)):
            cand = [HEADER_MAP.get(clean(th.get_text(" ")).lower(), "") for th in ths]
            if any(cand) and len(cand) >= len(headers):
                headers = cand

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        link = tr.find("a", href=re.compile(r"CapDetail", re.I))
        if link is None:
            continue
        cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
        rec: dict = {}
        if headers:
            td_offset = len(cells) - len(headers)  # leading checkbox/blank cells
            for i, name in enumerate(headers):
                idx = i + max(td_offset, 0)
                if name and idx < len(cells):
                    rec[name] = cells[idx]
        rec["record_number"] = clean(link.get_text(" ")) or rec.get("record_number", "")
        rec["detail_url"] = urljoin(base_url, link["href"].replace("&amp;", "&"))
        rec["date"] = parse_date(rec.get("date", ""))
        rows.append(rec)
    return rows


_COUNT_RE = re.compile(r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)(\+?)", re.I)


def parse_result_count(html_or_text: str) -> tuple[int, bool]:
    """Return (total, capped) from the 'Showing 1-10 of 100+' banner; (0, False) if absent."""
    m = _COUNT_RE.search(BeautifulSoup(html_or_text, "html.parser").get_text(" "))
    if not m:
        return 0, False
    return int(m.group(3)), bool(m.group(4))


# ---------------------------------------------------------------- detail page
_ADDR_TAIL = re.compile(r"^(?P<street>.*?)[,\s]+(?P<city>[A-Za-z .'-]+?)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?$")


_ADDR_COMMA = re.compile(r"^(?P<street>.+?)\s*,\s*(?P<city>[A-Za-z .'-]+?)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?$")
_ADDR_ONEWORD = re.compile(r"^(?P<street>.+?)\s+(?P<city>[A-Za-z.'-]+)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?$")


def split_city_state_zip(text: str) -> dict:
    """Split '788 E COLUMBUS ST, Columbus OH 43206' into street/city/state/zip.

    Prefers the comma before the city; without a comma assumes a one-word city
    (Columbus, Reynoldsburg...) so the street is never swallowed into the city.
    """
    text = clean(text).replace("\n", ", ")
    for pat in (_ADDR_COMMA, _ADDR_ONEWORD, _ADDR_TAIL):
        m = pat.match(text)
        if m:
            return {k: clean(v) for k, v in m.groupdict().items()}
    return {"street": text, "city": "", "state": "", "zip": ""}


_CSZ = re.compile(r"^(?P<city>.+?)[,\s]+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?$")


def split_csz(text: str) -> dict:
    """Split a bare 'DAYTON OH 45402' (Auditor MAILAD4) into city/state/zip."""
    m = _CSZ.match(clean(text).upper())
    if m:
        return {k: clean(v) for k, v in m.groupdict().items()}
    return {"city": "", "state": "", "zip": ""}


def _after_label(lines: list[str], label_re: str, take: int = 1) -> list[str]:
    pat = re.compile(label_re, re.I)
    for i, line in enumerate(lines):
        if pat.match(line):
            inline = pat.sub("", line, count=1).strip(" :")
            if inline:
                return [inline] + lines[i + 1:i + take]
            return lines[i + 1:i + 1 + take]
    return []


def parse_detail(html: str) -> dict:
    """Extract status, owner, parcel and narrative fields from a CapDetail page.

    Accela layouts vary between agencies, so this is label-driven over the page's
    text lines rather than tied to fixed element IDs.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [clean(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    text = "\n".join(lines)

    out: dict = {"record_status": "", "owner_name": "", "owner_address_raw": "",
                 "parcel_number": "", "detail_description": "", "property_address_detail": ""}

    v = _after_label(lines, r"^Record Status:?")
    out["record_status"] = v[0] if v else ""

    v = _after_label(lines, r"^Parcel (No\.?|Number):?")
    if v:
        out["parcel_number"] = re.sub(r"[^0-9A-Za-z]", "", v[0])
    else:
        m = re.search(r"Parcel\s*(?:No\.?|Number)?\s*:?\s*(\d{3}-?\d{6}-?\d{2})", text, re.I)
        if m:
            out["parcel_number"] = m.group(1).replace("-", "")

    # Owner block: "Owner:" followed by name then address lines (Accela "Owner" section)
    owner_lines = _after_label(lines, r"^Owner:?", take=4)
    if owner_lines and owner_lines[0].lower() not in ("contact", "email", "address", "type", "name", "status"):
        out["owner_name"] = owner_lines[0]
        addr = [x for x in owner_lines[1:] if not re.match(r"^(Primary|\*|Parcel|Record)", x, re.I)]
        out["owner_address_raw"] = ", ".join(addr[:2])

    v = _after_label(lines, r"^(Work|Violation|Detail) Description:?", take=3)
    out["detail_description"] = " ".join(v)

    m = re.search(r"^Address:?\s*\n?(\d+[^\n]+)", text, re.I | re.M)
    if m:
        out["property_address_detail"] = clean(m.group(1))
    return out


# ---------------------------------------------------------------- address utils
_SUFFIX = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
           "COURT": "CT", "BOULEVARD": "BLVD", "PLACE": "PL", "CIRCLE": "CIR",
           "PARKWAY": "PKWY", "TERRACE": "TER", "NORTH": "N", "SOUTH": "S",
           "EAST": "E", "WEST": "W"}


def normalize_address(addr: str) -> str:
    """Upper-case, strip punctuation/unit/zip and a trailing 'COLUMBUS OH' tail; abbreviate suffixes.

    Streets that contain city/state words ('E COLUMBUS ST', 'OHIO AVE') keep their names.
    """
    a = re.sub(r"[.,#]", " ", (addr or "").upper())
    a = re.sub(r"\s+", " ", a).strip()
    a = re.sub(r"\s+(APT|UNIT|STE|SUITE)\b.*$", "", a)
    a = re.sub(r"\s+\d{5}(-\d{4})?$", "", a)
    a = re.sub(r"\s+(OH|OHIO)$", "", a)
    words = [_SUFFIX.get(w, w) for w in a.split()]
    dirs = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
    stypes = set(_SUFFIX.values()) - dirs | {"WAY", "TRL", "TRAIL", "HWY", "PIKE", "SQ", "LOOP", "PT", "XING", "ALY"}
    if len(words) >= 3 and words[-1] == "COLUMBUS" and (words[-2] in stypes or words[-2] in dirs):
        words = words[:-1]
    return " ".join(words)


# ---------------------------------------------------------------- open/closed
_CLOSED_RE = re.compile(r"\b(closed|close|resolved|void(ed)?|cancel\w*|withdrawn|dismissed|abated|inactive|completed?)\b", re.I)
_COMPLIED_RE = re.compile(r"\b(complied|compliant|in compliance)\b", re.I)
_NEGATED_RE = re.compile(r"\b(not|non|never|failed to|un)[- ]*(complied|compliant|in compliance)\b", re.I)


def is_closed(status: str) -> bool:
    """True when a record status means the case is finished (so not an active lead).

    'Not Complied' / 'Non-compliant' are still open violations, so negations are honoured.
    Blank/unknown statuses are treated as open (kept) rather than silently dropped.
    """
    s = clean(status)
    if not s:
        return False
    if _CLOSED_RE.search(s):
        return True
    return bool(_COMPLIED_RE.search(s)) and not _NEGATED_RE.search(s)


# ---------------------------------------------------------------- unit / apartment filter
_UNIT_WORD = re.compile(r"(#|\b(APT|APARTMENT|UNIT|STE|SUITE|BLDG|BUILDING|FLOOR|FL|RM|ROOM|LOT|TRLR|SPC)\b)", re.I)
_SUFFIXES = (r"ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|CT|COURT|BLVD|BOULEVARD|PL|PLACE|"
             r"CIR|CIRCLE|PKWY|PARKWAY|TER|TERRACE|WAY|ALY|ALLEY|TRL|TRAIL|PIKE|SQ|HWY|LOOP|PT|XING")
_POSTDIR = r"(?:(?:N|S|E|W|NE|NW|SE|SW)\s+)?"
_TRAIL_UNIT = re.compile(rf"\b(?:{_SUFFIXES})\s+{_POSTDIR}(?:[A-Z]|\d{{1,4}}[A-Z]?)$", re.I)
_HOUSE_LETTER = re.compile(r"^\d+\s?[A-Z]\s+\S", re.I)
_HOUSE_LETTER_TIGHT = re.compile(r"^\d+[A-Z]\s+\S", re.I)


def is_unit_address(addr: str) -> bool:
    """True for apartments/units: '#4', 'Apt 2B', 'Unit 5', 'Ste 100', '123 Main St A',
    '123 Main St 2', '123A Main St'. Street-only addresses and post-directionals
    ('123 Main St NW') are not units.
    """
    street = split_city_state_zip(addr)["street"] if addr else ""
    street = clean(street).upper().replace(",", " ")
    street = re.sub(r"\s+", " ", street).strip()
    if not street:
        return False
    if _UNIT_WORD.search(street):
        return True
    if _HOUSE_LETTER_TIGHT.match(street):
        return True
    m = _TRAIL_UNIT.search(street)
    if m:
        tail = m.group(0).split()
        # 'RD 12' / 'HWY 23' are route numbers, not units
        if tail[0] in ("RD", "ROAD", "HWY", "PIKE") and tail[-1].isdigit():
            return False
        return True
    return False


# ---------------------------------------------------------------- unit in description / PII
_DESC_ADDR = re.compile(r"^(\d[^\-\u2013]{3,70}?)\s+[-\u2013]\s")


def lead_is_unit(address: str, description: str = "") -> bool:
    """Unit check on the address field AND on the address the complainant typed at the
    start of the description (e.g. '3715 BEULAH RD, B - tall weeds')."""
    if is_unit_address(address):
        return True
    m = _DESC_ADDR.match(clean(description))
    return bool(m and is_unit_address(m.group(1)))


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")


def redact_contact(text: str) -> str:
    """Strip complainants' emails/phone numbers from free text."""
    return _PHONE.sub("[phone]", _EMAIL.sub("[email]", text or ""))
