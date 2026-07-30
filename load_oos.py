"""Load researched out-of-state tuition from data/oos/*.json into the DB.

Each file (written by one OOS agent) looks like:

    {
      "sid": 13,
      "name": "Ohio State University",
      "out_of_state_tuition": 35019,
      "is_private_single_rate": false,
      "oos_note": "non-resident tuition & fees 2025-26 from bursar page"
    }

Matches colleges by name (case-insensitive) and sets College.out_of_state_tuition.
The oos_note is preserved on the college notes under an [OOS] line. Private
single-rate schools store out-of-state == in-state (the agent already sets that).
"""
from __future__ import annotations

import glob
import json
import os

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import College

OOS_DIR = os.path.join(os.path.dirname(__file__), "data", "oos")
NOTE_PREFIX = "[OOS] "


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_note(existing: str | None, oos_note: str | None) -> str | None:
    kept = [ln for ln in (existing or "").splitlines() if not ln.startswith(NOTE_PREFIX)]
    if oos_note:
        kept.append(NOTE_PREFIX + oos_note.strip())
    return "\n".join(kept).strip() or None


def load(oos_dir: str = OOS_DIR) -> dict:
    files = sorted(glob.glob(os.path.join(oos_dir, "*.json")))
    matched, with_oos, privates, unmatched = 0, 0, 0, []
    with SessionLocal() as db:
        for path in files:
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = (data.get("name") or "").strip()
            if not name:
                continue
            college = db.scalar(select(College).where(func.lower(College.name) == name.lower()))
            if college is None:
                unmatched.append(name)
                continue
            oos = _num(data.get("out_of_state_tuition"))
            college.out_of_state_tuition = oos
            college.notes = _merge_note(college.notes, data.get("oos_note"))
            matched += 1
            with_oos += oos is not None
            privates += bool(data.get("is_private_single_rate"))
        db.commit()
    return {
        "files": len(files),
        "matched": matched,
        "with_out_of_state_tuition": with_oos,
        "private_single_rate": privates,
        "unmatched": unmatched,
    }


if __name__ == "__main__":
    print(json.dumps(load(), indent=2))
