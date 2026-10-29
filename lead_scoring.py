"""Lead scoring for code enforcement records."""
from __future__ import annotations

from datetime import date

import config


def score_record(rec: dict, repeat_count: int = 1) -> tuple[int, list[str]]:
    """Return (score 0-100, tags). Higher = more likely motivated seller."""
    blob = " ".join(str(rec.get(k, "")) for k in
                    ("record_type", "description", "detail_description", "short_notes",
                     "project_name")).lower()
    status = (rec.get("record_status") or rec.get("status") or "").lower()

    score, tags = 0, []
    for needle, pts, tag in config.SCORING_RULES:
        if needle in blob and tag not in tags:
            score += pts
            tags.append(tag)

    if any(h in status for h in config.CLOSED_STATUS_HINTS):
        score -= 25
        tags.append("closed")
    elif any(h in status for h in config.OPEN_STATUS_HINTS):
        score += 15
        tags.append("open")

    if repeat_count >= 2:
        score += min(30, 10 * (repeat_count - 1))
        tags.append(f"repeat-{repeat_count}")

    if rec.get("foreclosure_match"):
        score += config.FORECLOSURE_MATCH_POINTS
        tags.append("foreclosure-match")

    if rec.get("absentee_owner"):
        score += config.ABSENTEE_POINTS
        tags.append("absentee")

    filed = rec.get("filing_date") or rec.get("date")
    if filed:
        try:
            age = (date.today() - date.fromisoformat(filed)).days
            if age > 180 and "open" in tags:
                score += 10
                tags.append("aged-open")
        except ValueError:
            pass

    return max(0, min(100, score)), tags
