"""Load reviewed minors from official institutional catalog manifests.

This loader deliberately performs no web scraping or major-to-minor inference.
Each record must name an official catalog source so incomplete coverage remains
visible and auditable.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select, update

from app.database import SessionLocal, engine
from app.models import Base, College, CollegeMinor

DEFAULT_MANIFEST = "data/minors/manifest.json"


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def official_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def read_manifest(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        raise ValueError("Minor manifest must contain a sources array")
    return sources

def validated_sources(sources: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    grouped: dict[str, list[dict]] = {}
    errors: list[dict] = []
    for index, source in enumerate(sources):
        college = str(source.get("college") or "").strip()
        source_name = str(source.get("source_name") or "").strip()
        source_url = str(source.get("source_url") or "").strip()
        catalog_year = str(source.get("catalog_year") or "").strip()
        minors = source.get("minors")
        if not college or not source_name or not official_https_url(source_url):
            errors.append({"index": index, "error": "college, source_name, and official HTTPS source_url are required"})
            continue
        if not isinstance(minors, list) or not minors:
            errors.append({"index": index, "college": college, "error": "at least one reviewed minor is required"})
            continue
        records = []
        for item in minors:
            if isinstance(item, str):
                name, item_url = item.strip(), source_url
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                item_url = str(item.get("source_url") or source_url).strip()
            else:
                name, item_url = "", ""
            if not name or not official_https_url(item_url):
                errors.append({"index": index, "college": college, "error": "minor name and HTTPS source are required"})
                continue
            records.append({
                "name": name,
                "normalized_name": normalize_name(name),
                "catalog_year": catalog_year,
                "source_name": source_name,
                "source_url": item_url,
            })
        if records:
            grouped.setdefault(college, []).extend(records)
    return grouped, errors


def load(path: str = DEFAULT_MANIFEST) -> dict:
    Base.metadata.create_all(bind=engine)
    grouped, validation_errors = validated_sources(read_manifest(path))
    summary: dict[str, object] = {
        "source_colleges": len(grouped), "matched": 0, "minors": 0,
        "unmatched_colleges": [], "errors": validation_errors,
    }
    with SessionLocal() as db:
        for college_name, records in grouped.items():
            college = db.scalar(select(College).where(func.lower(College.name) == college_name.lower()))
            if college is None:
                summary["unmatched_colleges"].append(college_name)
                continue
            deduped = {record["normalized_name"]: record for record in records}
            try:
                db.execute(
                    update(CollegeMinor)
                    .where(CollegeMinor.college_id == college.id)
                    .values(active=False)
                )
                for record in deduped.values():
                    minor = db.scalar(select(CollegeMinor).where(
                        CollegeMinor.college_id == college.id,
                        CollegeMinor.normalized_name == record["normalized_name"],
                    ))
                    if minor is None:
                        minor = CollegeMinor(college_id=college.id, **record)
                        db.add(minor)
                    else:
                        for key, value in record.items():
                            setattr(minor, key, value)
                    minor.retrieved_at = datetime.utcnow()
                    minor.active = True
                db.commit()
                summary["matched"] += 1
                summary["minors"] += len(deduped)
            except (ValueError, KeyError) as exc:
                db.rollback()
                summary["errors"].append({"college": college_name, "error": str(exc)})
        summary["active_minors"] = db.scalar(
            select(func.count()).select_from(CollegeMinor).where(CollegeMinor.active.is_(True))
        )
        summary["cataloged_colleges"] = db.scalar(
            select(func.count(func.distinct(CollegeMinor.college_id))).where(CollegeMinor.active.is_(True))
        )
    return summary

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    print(json.dumps(load(args.manifest), indent=2))


if __name__ == "__main__":
    main()
