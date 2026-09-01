"""Helper CLI for the admission-GPA research agent.

Provides a safe, deterministic interface for the agent to (1) list colleges that
still need an average admission GPA and (2) persist a researched value to the
Neon database. All database access goes through here so the agent never has to
write SQL directly, and every write is range-validated.

Usage
-----
List colleges still missing an average admission GPA (JSON to stdout):
    python populate_admission_gpa.py list --missing-only --limit 25 --offset 0

List every college (missing or not):
    python populate_admission_gpa.py list --limit 200

Persist a researched value (validated, prints JSON result):
    python populate_admission_gpa.py set <college_id> <gpa> --source "https://..."

Notes
-----
* GPA is stored on a 4.0-style scale. Values outside 1.0-5.0 are rejected; values
  above 4.3 are accepted but flagged as likely weighted so the caller can decide.
* `set` is idempotent from the agent's perspective: re-running overwrites the
  stored value. Use `list --missing-only` to drive a fill-only loop.
* `--source` provenance is echoed back in the JSON result and, when
  --write-note is passed, appended as a short audit line to the college notes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import func, or_, select

from app.database import SessionLocal
from app.models import College

GPA_MIN = 1.0
GPA_MAX = 5.0
WEIGHTED_FLAG = 4.3


def _college_dict(c: College) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "city": c.city,
        "state": c.state,
        "website_url": c.website_url,
        "avg_admission_gpa": c.avg_admission_gpa,
    }


def cmd_list(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        query = select(College).order_by(College.name)
        if args.missing_only:
            query = query.where(College.avg_admission_gpa.is_(None))
        if args.offset:
            query = query.offset(args.offset)
        if args.limit:
            query = query.limit(args.limit)
        colleges = db.scalars(query).all()
        remaining = db.scalar(
            select(func.count()).select_from(College).where(College.avg_admission_gpa.is_(None))
        )
    out = {
        "count": len(colleges),
        "remaining_missing": remaining,
        "colleges": [_college_dict(c) for c in colleges],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    try:
        gpa = float(args.gpa)
    except ValueError:
        print(json.dumps({"ok": False, "error": f"GPA {args.gpa!r} is not a number"}))
        return 1
    if not (GPA_MIN <= gpa <= GPA_MAX):
        print(json.dumps({
            "ok": False,
            "error": f"GPA {gpa} out of accepted range {GPA_MIN}-{GPA_MAX}",
        }))
        return 1

    with SessionLocal() as db:
        college = db.get(College, args.college_id)
        if college is None:
            print(json.dumps({"ok": False, "error": f"No college with id {args.college_id}"}))
            return 1
        previous = college.avg_admission_gpa
        college.avg_admission_gpa = gpa
        if args.write_note and args.source:
            stamp = f"[avg_admission_gpa {gpa} from {args.source} on {date.today():%Y-%m-%d}]"
            college.notes = (college.notes + "\n" + stamp) if college.notes else stamp
        db.commit()
        result = {
            "ok": True,
            "id": college.id,
            "name": college.name,
            "previous": previous,
            "avg_admission_gpa": gpa,
            "source": args.source,
            "likely_weighted": gpa > WEIGHTED_FLAG,
        }
    print(json.dumps(result))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read/write college average admission GPA in Neon.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List colleges (JSON).")
    p_list.add_argument("--missing-only", action="store_true", help="Only colleges with no avg_admission_gpa yet.")
    p_list.add_argument("--limit", type=int, default=0, help="Max rows to return (0 = no limit).")
    p_list.add_argument("--offset", type=int, default=0, help="Rows to skip (for batching).")
    p_list.set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="Persist a researched GPA for one college.")
    p_set.add_argument("college_id", type=int)
    p_set.add_argument("gpa")
    p_set.add_argument("--source", default="", help="URL/citation the value came from.")
    p_set.add_argument("--write-note", action="store_true", help="Append an audit line to college notes.")
    p_set.set_defaults(func=cmd_set)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
