"""Load researched in-state tuition + housing costs from data/tuition/*.json.

Each file (written by one tuition agent) looks like:

    {
      "sid": 0,
      "name": "Bethany College",
      "in_state_tuition": 41898,
      "housing_cost": 8600,
      "housing_basis": "room only",
      "tuition_note": "2025-26 tuition & fees from ...; private single rate"
    }

Matches colleges by name (case-insensitive) against the already-seeded rows and sets:
  - in_state_tuition
  - housing_cost
  - tuition  = in_state_tuition + housing_cost (sum; the field the app filter/PDF use)
The tuition_note is appended to the college notes so the source/year is preserved.
"""
from __future__ import annotations

import glob
import json
import os

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import College

TUITION_DIR = os.path.join(os.path.dirname(__file__), "data", "tuition")

# Marker so re-running replaces the prior tuition note instead of stacking duplicates.
NOTE_PREFIX = "[Tuition] "


def _num(value):
    """Coerce to float, treating empty strings / non-numbers as missing."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_note(existing: str | None, tuition_note: str | None, housing_basis: str | None) -> str | None:
    """Replace any prior [Tuition] line in notes with the latest one."""
    kept = [ln for ln in (existing or "").splitlines() if not ln.startswith(NOTE_PREFIX)]
    if tuition_note:
        line = NOTE_PREFIX + tuition_note.strip()
        if housing_basis and housing_basis.strip():
            line += f" (housing = {housing_basis.strip()})"
        kept.append(line)
    return "\n".join(kept).strip() or None


def load(tuition_dir: str = TUITION_DIR) -> dict:
    files = sorted(glob.glob(os.path.join(tuition_dir, "*.json")))
    matched, with_tuition, with_housing, unmatched = 0, 0, 0, []
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
            in_state = _num(data.get("in_state_tuition"))
            housing = _num(data.get("housing_cost"))
            college.in_state_tuition = in_state
            college.housing_cost = housing
            # Combined total drives the existing filter/PDF; sum what we have.
            if in_state is not None or housing is not None:
                college.tuition = (in_state or 0) + (housing or 0)
            college.notes = _merge_note(college.notes, data.get("tuition_note"), data.get("housing_basis"))
            matched += 1
            with_tuition += in_state is not None
            with_housing += housing is not None
        db.commit()
    return {
        "files": len(files),
        "matched": matched,
        "with_in_state_tuition": with_tuition,
        "with_housing": with_housing,
        "unmatched": unmatched,
    }


if __name__ == "__main__":
    print(json.dumps(load(), indent=2))
