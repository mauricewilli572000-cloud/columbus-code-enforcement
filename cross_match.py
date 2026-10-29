"""Cross-match code enforcement leads against Franklin County foreclosure leads by address.

An address that has BOTH an active code violation and a foreclosure filing is a
double-distress lead. The foreclosure records.json is read as-is (list or dict keyed by
doc number); field names are matched loosely so it works with the shared lead schema.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

import parsers

log = logging.getLogger("cross_match")

_ADDR_KEYS = ("property_address", "Property Address", "address", "situs_address")
_OWNER_KEYS = ("owner_name", "Owner Name", "owner", "defendant")
_CASE_KEYS = ("case_number", "Case Number", "doc_num", "doc_number")
_MAIL_KEYS = ("mailing_address", "Mailing Address")


def _pick(rec: dict, keys) -> str:
    for k in keys:
        v = rec.get(k)
        if v:
            return str(v).strip()
    return ""


def load_records(source: str) -> list[dict]:
    """Load foreclosure records from a file path or http(s) URL. Returns [] on failure."""
    try:
        if source.lower().startswith("http"):
            r = requests.get(source, timeout=60)
            r.raise_for_status()
            data = r.json()
        else:
            data = json.loads(Path(source).read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - cross-match is optional, never fatal
        log.warning("Could not load foreclosure data from %s: %s", source, exc)
        return []
    if isinstance(data, dict):
        data = data.get("records") or data.get("leads") or list(data.values())
    return [r for r in data if isinstance(r, dict)]


def build_index(records: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for r in records:
        key = parsers.normalize_address(_pick(r, _ADDR_KEYS))
        if key and key not in idx:
            idx[key] = r
    return idx


def apply_matches(leads: list[dict], index: dict[str, dict]) -> int:
    """Flag matching leads, fill blank owner/mailing from the foreclosure record. Returns match count."""
    n = 0
    for l in leads:
        hit = index.get(parsers.normalize_address(l.get("property_address", "")))
        if not hit:
            l["foreclosure_match"] = bool(l.get("foreclosure_match", False))
            continue
        l["foreclosure_match"] = True
        l["foreclosure_case_number"] = _pick(hit, _CASE_KEYS)
        if not l.get("owner_name"):
            l["owner_name"] = _pick(hit, _OWNER_KEYS)
        if not l.get("mailing_address"):
            l["mailing_address"] = _pick(hit, _MAIL_KEYS)
        n += 1
    return n
