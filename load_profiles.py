"""Load researched college softball profiles from data/profiles/*.json into the DB.

Each profile file is written by one profiling agent and looks like:

    {
      "sid": 12,
      "name": "University of Kentucky",
      "division": "NCAA D1",
      "conference": "Southeastern Conference (SEC)",
      "city": "Lexington",
      "state": "KY",
      "website_url": "https://ukathletics.com/sports/softball/",
      "academic_ranking": "...",
      "tuition": 12360,
      "financial_aid": "...",
      "roster_size": 22,
      "scholarship_count": 12,
      "facilities_notes": "...",
      "program_reputation": "...",
      "notes": "...",
      "coaches": [
        {"name": "...", "title": "Head Coach", "email": "...", "phone": "...",
         "twitter": "...", "sort_order": 0, "source_note": "..."}
      ]
    }

Upsert key is the college name (matched case-insensitively). Re-running replaces
a college's coaching staff with the latest researched set, so the load is idempotent.
"""
from __future__ import annotations

import glob
import json
import os

from sqlalchemy import func, select

from app.database import SessionLocal, engine
from app.models import Base, Coach, College

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "data", "profiles")

# Columns we accept straight from a profile file onto the College row.
COLLEGE_FIELDS = [
    "name", "division", "conference", "city", "state", "academic_ranking",
    "tuition", "financial_aid", "roster_size", "scholarship_count",
    "facilities_notes", "program_reputation", "website_url", "notes",
]


def _clean(value):
    """Normalize empty strings to None so blanks stay blank in the DB."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _head_coach_summary(coaches: list[dict]) -> tuple[str | None, str | None, str | None, str | None]:
    """Derive the flat head_coach / recruiting_coordinator / email / phone fields
    the existing UI and email tools still read, from the structured staff list."""
    head = next((c for c in coaches if (c.get("sort_order") or 100) == 0), None)
    if head is None and coaches:
        head = coaches[0]
    rc = next((c for c in coaches if "recruit" in (c.get("title") or "").lower()), None)
    emails = ", ".join(dict.fromkeys(c["email"].strip() for c in coaches if _clean(c.get("email"))))
    phones = ", ".join(dict.fromkeys(c["phone"].strip() for c in coaches if _clean(c.get("phone"))))
    return (
        _clean(head.get("name")) if head else None,
        _clean(rc.get("name")) if rc else None,
        emails or None,
        phones or None,
    )


def load(profile_dir: str = PROFILE_DIR) -> dict:
    Base.metadata.create_all(bind=engine)
    files = sorted(glob.glob(os.path.join(profile_dir, "*.json")))
    loaded, coach_total = 0, 0
    with SessionLocal() as db:
        for path in files:
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            name = _clean(data.get("name"))
            if not name:
                continue
            college = db.scalar(select(College).where(func.lower(College.name) == name.lower()))
            if college is None:
                college = College(name=name)
                db.add(college)
            for field in COLLEGE_FIELDS:
                if field == "name" or field not in data:
                    continue
                setattr(college, field, _clean(data.get(field)))
            coaches = [c for c in (data.get("coaches") or []) if _clean(c.get("name"))]
            hc, rc, emails, phones = _head_coach_summary(coaches)
            college.head_coach = hc
            college.recruiting_coordinator = rc
            college.coach_emails = emails
            college.coach_phones = phones
            # Replace the structured staff wholesale (idempotent re-load).
            college.coaches.clear()
            db.flush()
            for c in coaches:
                college.coaches.append(Coach(
                    name=_clean(c.get("name")),
                    title=_clean(c.get("title")),
                    email=_clean(c.get("email")),
                    phone=_clean(c.get("phone")),
                    twitter=_clean(c.get("twitter")),
                    sort_order=int(c.get("sort_order") or 100),
                    source_note=_clean(c.get("source_note")),
                ))
                coach_total += 1
            loaded += 1
        db.commit()
        college_count = db.scalar(select(func.count()).select_from(College))
    return {"files": len(files), "loaded": loaded, "coaches": coach_total, "colleges_in_db": college_count}


if __name__ == "__main__":
    result = load()
    print(json.dumps(result, indent=2))
