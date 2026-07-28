# -*- coding: utf-8 -*-
"""
Import a paper draft-board spreadsheet and re-order a league-season's
HistoricalPick rows to match the real selection order from draft night.

Two of this app's three leagues drafted OFFLINE; the ESPN
overall_pick/round_pick values on their historical_picks rows are
fabricated. This CLI re-orders them from the paper board and marks the
ones it could place as draft_order_verified=True.

Default is a DRY RUN: it prints the derived owner map, a round-by-round
placement table, unmatched cells/picks and the match rate, but writes
nothing. Pass --apply to persist (still gated by MIN_MATCH_RATE and
uniqueness guards unless --force).

Run from backend/:
    python scripts/import_draft_board.py --league-id 123 --season 2024 \
        --file /path/to/board.xlsx
"""
import argparse
import os
import sys

os.environ.setdefault("DRAFT_YEAR", "2024")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources.draft_board_import import (  # noqa: E402
    MIN_MATCH_RATE,
    MIN_OWNER_OVERLAP,
    apply_board,
    parse_board,
)
from models.config import LOCAL  # noqa: E402


def _build_engine():
    """Construct the ODMantic engine mirroring app.py's construction."""
    from odmantic import AIOEngine
    from motor.motor_asyncio import AsyncIOMotorClient

    if LOCAL:
        print("Running locally")
        client = AsyncIOMotorClient("mongodb://localhost:27017")
    else:
        print("Running in Docker")
        client = AsyncIOMotorClient("mongodb://mongodb:27017")
    return AIOEngine(database="fantasy-football", client=client)


def _print_report(report: dict) -> None:
    print()
    print("=" * 72)
    print(
        f"league {report['espn_league_id']} season {report['season']} "
        f"| teams={report['team_count']} | match_rate={report['match_rate']:.3f}"
    )
    print("=" * 72)

    print("\nDERIVED OWNER MAP (slot -> member_guid, confidence):")
    for slot in sorted(report["owner_map"], key=lambda k: int(k)):
        guid = report["owner_map"][slot]
        conf = report["owner_confidence"].get(slot, 0.0)
        print(f"  slot {slot:>2} -> {guid:<40} (confidence {conf:.2f})")
    if not report["owner_map"]:
        print("  (owner map refused)")

    placements = report["placements"]
    by_round: dict[int, list[dict]] = {}
    for pl in placements:
        by_round.setdefault(pl["round_num"], []).append(pl)

    print("\nPLACEMENTS (round-by-round):")
    for rn in sorted(by_round):
        row = sorted(by_round[rn], key=lambda p: p["slot"])
        print(f"  Round {rn}:")
        for pl in row:
            print(
                f"    slot {pl['slot']:>2}  overall {pl['overall_pick']:>3}  "
                f"rp {pl['round_pick']:>2}  [{pl['method']:<14}]  "
                f"{pl['cell_text']:<22} -> {pl['player']}"
            )

    print("\nUNMATCHED CELLS:")
    if report["unmatched_cells"]:
        for u in report["unmatched_cells"]:
            print(
                f"  round {u['round_num']} slot {u['slot']}: {u['text']!r}"
            )
    else:
        print("  (none)")

    print("\nUNMATCHED ESPN PICKS:")
    if report["unmatched_picks"]:
        for p in report["unmatched_picks"]:
            print(f"  {p['player']!r} (guid {p['member_guid']})")
    else:
        print("  (none)")

    if report["errors"]:
        print("\nERRORS:")
        for e in report["errors"]:
            print(f"  {e}")

    if report["refused"]:
        print("\nREFUSED — no writes performed. Reasons:")
        for r in report["refusal_reasons"]:
            print(f"  {r}")
        print(
            f"\nGuards: MIN_MATCH_RATE={MIN_MATCH_RATE}, "
            f"MIN_OWNER_OVERLAP={MIN_OWNER_OVERLAP}. Override with --force."
        )
    elif report["written"]:
        print("\nWRITTEN: matched picks re-ordered and marked verified.")
    elif report["dry_run"]:
        print("\nDRY RUN — no writes. Pass --apply to persist.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-order a league-season's historical_picks from a paper "
            "draft-board spreadsheet and mark verified picks."
        )
    )
    parser.add_argument("--league-id", type=int, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--file", required=True, help="path to .xlsx board")
    parser.add_argument("--sheet", default=None, help="sheet name (default active)")
    parser.add_argument(
        "--no-snake",
        action="store_true",
        help="treat the draft as straight (non-snake) order",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist writes (default is a dry run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="override MIN_MATCH_RATE and uniqueness guards",
    )
    args = parser.parse_args()

    # Validate the board parses before touching the database
    try:
        parse_board(args.file, sheet_name=args.sheet)
    except Exception as exc:
        print(f"ERROR: could not parse board {args.file}: {exc}", file=sys.stderr)
        return 2

    engine = _build_engine()

    import asyncio

    report = asyncio.run(
        apply_board(
            engine,
            espn_league_id=args.league_id,
            season=args.season,
            board_path=args.file,
            sheet_name=args.sheet,
            snake=not args.no_snake,
            dry_run=not args.apply,
            force=args.force,
        )
    )

    _print_report(report)

    if report["refused"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
