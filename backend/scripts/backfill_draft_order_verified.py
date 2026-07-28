# -*- coding: utf-8 -*-
"""
Backfill HistoricalPick.draft_order_verified on rows ingested before the
field existed.

Every pick already in Mongo predates the flag, so it reads back as False --
which means order-dependent owner metrics (reach, run participation,
post-miss) would silently go to an empty sample for EVERY owner, including
the ones whose draft order is genuinely real. This sets the flag from
ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS the same way ingestion now does, so the
gating takes effect without re-fetching six seasons from ESPN just to write
one boolean.

Picks whose real order came from a draft-board import are already verified
and are left alone: their league is not in the verified-league list (that
list means "drafted online in ESPN"), so a blind rewrite would un-verify
exactly the work the importer did.

  python scripts/backfill_draft_order_verified.py            # dry run
  python scripts/backfill_draft_order_verified.py --apply
"""
import argparse
import asyncio
import os
import sys

# Run from anywhere: put backend/ on the path like the sibling scripts do
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.config import (  # noqa: E402
    ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS,
    LOCAL,
)
from models.sources import HistoricalPick  # noqa: E402


def _build_engine():
    """Construct the ODMantic engine mirroring app.py's construction."""
    from odmantic import AIOEngine
    from motor.motor_asyncio import AsyncIOMotorClient

    if LOCAL:
        client = AsyncIOMotorClient("mongodb://localhost:27017")
    else:
        client = AsyncIOMotorClient("mongodb://mongodb:27017")
    return AIOEngine(database="fantasy-football", client=client)


async def backfill(engine, apply: bool) -> dict:
    collection = engine.get_collection(HistoricalPick)
    verified_ids = list(ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS)

    # Rows that SHOULD be verified but are not yet.
    to_verify = {
        "espn_league_id": {"$in": verified_ids},
        "draft_order_verified": {"$ne": True},
    }
    pending = await collection.count_documents(to_verify) if verified_ids else 0

    # Rows with NO flag at all, outside the verified leagues. These are the
    # dangerous ones: whatever the model default happens to be is what they
    # evaluate to, so write False explicitly and stop depending on it.
    missing_flag = {
        "espn_league_id": {"$nin": verified_ids},
        "draft_order_verified": {"$exists": False},
    }
    unset = await collection.count_documents(missing_flag)

    already = await collection.count_documents({"draft_order_verified": True})
    total = await collection.count_documents({})

    report = {
        "verified_league_ids": verified_ids,
        "total_picks": total,
        "already_verified": already,
        "to_verify": pending,
        "to_mark_unverified": unset,
        "applied": False,
    }

    if apply and (pending or unset):
        verified_result = await collection.update_many(
            to_verify, {"$set": {"draft_order_verified": True}}
        )
        unverified_result = await collection.update_many(
            missing_flag, {"$set": {"draft_order_verified": False}}
        )
        report["applied"] = True
        report["modified_verified"] = verified_result.modified_count
        report["modified_unverified"] = unverified_result.modified_count

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill draft_order_verified on existing historical picks"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist the change (default is a dry run)",
    )
    args = parser.parse_args()

    if not ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS:
        print(
            "ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS is empty -- nothing would be\n"
            "marked verified. Set it in backend/.env to the league ids whose\n"
            "draft actually ran online inside ESPN, then re-run."
        )
        return 1

    engine = _build_engine()
    report = asyncio.run(backfill(engine, apply=args.apply))

    print(f"verified league ids : {report['verified_league_ids']}")
    print(f"total picks         : {report['total_picks']}")
    print(f"already verified    : {report['already_verified']}")
    print(f"to mark verified    : {report['to_verify']}")
    print(f"to mark unverified  : {report['to_mark_unverified']}")
    if report["applied"]:
        print(f"MODIFIED verified   : {report['modified_verified']}")
        print(f"MODIFIED unverified : {report['modified_unverified']}")
    else:
        print("DRY RUN - no writes. Pass --apply to persist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
