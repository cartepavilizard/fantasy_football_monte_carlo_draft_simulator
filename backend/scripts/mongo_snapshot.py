# -*- coding: utf-8 -*-
"""
FULL-DATABASE SNAPSHOT AND RESTORE (no mongodump required)

The MongoDB Database Tools are not installed on this machine, but
`POST /league/{id}/player/sync` overwrites projected_points,
position_tier and position_max_points in place across every league
document -- and git does not protect Mongo. So this is the backup that
has to exist before any adoption sync.

Uses bson.json_util so ObjectIds, dates and every other BSON type
round-trip exactly. Every collection is dumped, not just leagues.

    python scripts/mongo_snapshot.py dump
    python scripts/mongo_snapshot.py verify <dir>
    python scripts/mongo_snapshot.py restore <dir>          # DESTRUCTIVE

`dump` writes to C:\\fantasy-football-backups\\<utc-timestamp>\\ (outside
the repo, so a branch switch can never touch it) and then re-reads what
it wrote and compares counts and checksums before reporting success. A
dump that cannot verify itself is not a backup.

`restore` DROPS each collection before reloading it, and refuses to run
without --yes.
"""
import hashlib
import os
import sys
from datetime import datetime, timezone

from bson import json_util
from pymongo import MongoClient

DB = "fantasy-football"
URI = "mongodb://localhost:27017"
ROOT = r"C:\fantasy-football-backups"


def _client():
    return MongoClient(URI)


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dump():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(ROOT, stamp)
    os.makedirs(out, exist_ok=True)
    db = _client()[DB]
    names = sorted(db.list_collection_names())
    manifest = []
    print(f"dumping {DB} -> {out}")
    for name in names:
        docs = list(db[name].find({}))
        path = os.path.join(out, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json_util.dumps(docs, indent=1))
        manifest.append((name, len(docs), _digest(path)))
        print(f"  {name:28s} {len(docs):6d} docs  {os.path.getsize(path):>10,} bytes")

    with open(os.path.join(out, "MANIFEST.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"database: {DB}\ntaken: {stamp}\n\n")
        for name, count, digest in manifest:
            fh.write(f"{name}\t{count}\t{digest}\n")

    print("\nverifying...")
    ok = verify(out, quiet=False)
    print(f"\nsnapshot {'VERIFIED' if ok else 'FAILED VERIFICATION'}: {out}")
    return 0 if ok else 1


def verify(out, quiet=False):
    db = _client()[DB]
    manifest_path = os.path.join(out, "MANIFEST.txt")
    if not os.path.exists(manifest_path):
        print(f"no MANIFEST.txt in {out}")
        return False
    ok = True
    for line in open(manifest_path, encoding="utf-8"):
        if "\t" not in line:
            continue
        name, count, digest = line.rstrip("\n").split("\t")
        path = os.path.join(out, f"{name}.json")
        live = db[name].count_documents({})
        docs = json_util.loads(open(path, encoding="utf-8").read())
        good = (
            os.path.exists(path)
            and _digest(path) == digest
            and len(docs) == int(count)
            and live == int(count)
        )
        ok = ok and good
        if not quiet:
            print(
                f"  {'ok  ' if good else 'FAIL'} {name:28s} "
                f"file {len(docs):6d} / live {live:6d} / manifest {count:>6s}"
            )
    return ok


def restore(out):
    if "--yes" not in sys.argv:
        print("restore DROPS every collection listed in the manifest.")
        print(f"re-run with --yes to confirm:  restore {out} --yes")
        return 1
    db = _client()[DB]
    for line in open(os.path.join(out, "MANIFEST.txt"), encoding="utf-8"):
        if "\t" not in line:
            continue
        name, count, _ = line.rstrip("\n").split("\t")
        docs = json_util.loads(
            open(os.path.join(out, f"{name}.json"), encoding="utf-8").read()
        )
        db[name].drop()
        if docs:
            db[name].insert_many(docs)
        print(f"  restored {name:28s} {len(docs):6d} docs (manifest said {count})")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if cmd == "dump":
        sys.exit(dump())
    elif cmd == "verify":
        sys.exit(0 if verify(sys.argv[2]) else 1)
    elif cmd == "restore":
        sys.exit(restore(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
