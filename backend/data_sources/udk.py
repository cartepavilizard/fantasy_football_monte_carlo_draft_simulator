# -*- coding: utf-8 -*-
"""
FANTASY FOOTBALLERS ULTIMATE DRAFT KIT — FILE-DROP PARSING

Deliberately NOT a scraper: the UDK is login-walled paid content with real
ToS exposure and an annually changing markup, so the sanctioned path is
the subscriber's own CSV/sheet export, uploaded to POST /rankings/udk
(the one intentionally manual step in the pipeline).

Column names vary across UDK seasons/exports, so headers are mapped by
alias rather than fixed names.
"""
from typing import List, Tuple

from .base import SourceRecord

# field -> accepted header spellings (compared lowercased/stripped)
COLUMN_ALIASES = {
    "name": ["player", "name", "player name"],
    "position": ["pos", "position"],
    "nfl_team": ["team", "tm", "nfl team"],
    "rank": ["rank", "overall", "overall rank", "ovr", "udk rank"],
    "position_rank": ["pos rank", "position rank", "positional rank"],
    "tier": ["tier", "udk tier"],
    "projection": [
        "proj ffp",
        "projected ffp",
        "projection",
        "proj pts",
        "proj points",
        "projected points",
        "fantasy points",
        "proj",
    ],
}

REQUIRED_FIELDS = ("name", "position")
# An export with none of these carries no ranking signal at all
VALUE_FIELDS = ("rank", "position_rank", "tier", "projection")


def map_headers(fieldnames) -> Tuple[dict, list]:
    """
    Match CSV headers to fields; returns (field -> actual header, problems)
    """
    normalized = {str(name).strip().lower(): name for name in fieldnames if name}
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    problems = [
        f"missing a column for '{field}' (looked for: {COLUMN_ALIASES[field]})"
        for field in REQUIRED_FIELDS
        if field not in mapping
    ]
    if not any(field in mapping for field in VALUE_FIELDS):
        problems.append(
            "no ranking value column found (need at least one of rank/tier/"
            "position rank/projection)"
        )
    return mapping, problems


def _number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_udk_rows(rows: list) -> Tuple[List[SourceRecord], list]:
    """Turn UDK CSV rows into SourceRecords; returns (records, problems)"""
    mapping, problems = map_headers(rows[0].keys() if rows else [])
    if problems:
        return [], problems

    def field(row, name):
        header = mapping.get(name)
        value = row.get(header) if header else None
        return value.strip() if isinstance(value, str) else value

    records = []
    for row in rows:
        name = field(row, "name")
        position = field(row, "position")
        if not name or not position:
            continue
        tier = _number(field(row, "tier"))
        records.append(
            SourceRecord(
                raw_name=name,
                position=position,
                nfl_team=field(row, "nfl_team") or None,
                rank=_number(field(row, "rank")),
                position_rank=_number(field(row, "position_rank")),
                tier=int(tier) if tier is not None else None,
                projection=_number(field(row, "projection")),
            )
        )
    if not records:
        problems.append("no usable rows in the export")
    return records, problems
