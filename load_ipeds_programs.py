"""Load detailed undergraduate fields from the latest reviewed IPEDS completions file.

Six-digit CIP fields supplement, but never replace, the four-digit College
Scorecard major filter. A row means the institution recently reported at least
one completion in that detailed field; it is not a guarantee of current catalog
availability and should be verified with the institution.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import zipfile
from datetime import datetime
from urllib.request import urlopen

from sqlalchemy import func, select, update

from app.database import SessionLocal, engine
from app.models import (
    AcademicProgram,
    AcademicProgramDetail,
    Base,
    College,
    CollegeProgram,
    CollegeProgramDetail,
)

IPEDS_COMPLETIONS_SOURCE = os.environ.get(
    "IPEDS_COMPLETIONS_SOURCE",
    "https://nces.ed.gov/ipeds/datacenter/data/C2024_A.zip",
)
CIP_TAXONOMY_SOURCE = os.environ.get(
    "CIP_TAXONOMY_SOURCE",
    "https://nces.ed.gov/ipeds/cipcode/browse.aspx?y=56",
)
IPEDS_SOURCE_URL = "https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx"
IPEDS_DATASET_YEAR = "2024"
UNDERGRADUATE_LEVELS = {3: "Associate's Degree", 5: "Bachelor's Degree"}

def clean_title(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(text).strip().rstrip(".").split())


def canonical_cip6(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    return f"{digits[:2]}.{digits[2:]}" if len(digits) == 6 else ""


def parent_cip4(cip6: str) -> str:
    return re.sub(r"\D", "", cip6)[:4]


def taxonomy_titles(source: str = CIP_TAXONOMY_SOURCE) -> dict[str, str]:
    """Read NCES CIP-2020 titles for both four- and six-digit codes."""
    with urlopen(source, timeout=180) as response:
        page = response.read().decode("utf-8", "ignore")
    titles = {}
    for code, title in re.findall(r">(\d{2}\.\d{2}(?:\d{2})?)\)\s*(.*?)</a>", page, re.I | re.S):
        cleaned = clean_title(title)
        if cleaned:
            titles[code] = cleaned
    if not any(len(re.sub(r"\D", "", code)) == 6 for code in titles):
        raise RuntimeError("NCES CIP taxonomy did not contain six-digit program titles")
    return titles


def completion_rows(source: str = IPEDS_COMPLETIONS_SOURCE):
    """Stream the single CSV from an official IPEDS ZIP archive."""
    with urlopen(source, timeout=300) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(names) != 1:
        raise RuntimeError(f"Expected one CSV in IPEDS archive, found {len(names)}")
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def college_unit_ids(db, college_ids: set[int]) -> tuple[dict[int, int], dict[int, list[str]]]:
    """Resolve stable UNITIDs already reviewed by the Scorecard catalog load."""
    unit_by_college: dict[int, int] = {}
    conflicts: dict[int, list[str]] = {}
    rows = db.execute(
        select(CollegeProgram.college_id, CollegeProgram.scorecard_unit_id)
        .where(CollegeProgram.college_id.in_(college_ids), CollegeProgram.active.is_(True))
        .distinct()
    ).all()
    grouped: dict[int, set[int]] = {}
    for college_id, unit_id in rows:
        grouped.setdefault(college_id, set()).add(unit_id)
    for college_id, units in grouped.items():
        if len(units) == 1:
            unit_by_college[college_id] = next(iter(units))
        else:
            conflicts[college_id] = [str(unit) for unit in sorted(units)]
    return unit_by_college, conflicts

def upsert_college_details(db, college: College, records: dict[tuple[str, int], int],
                           titles: dict[str, str], retrieved_at: datetime) -> int:
    db.execute(
        update(CollegeProgramDetail)
        .where(CollegeProgramDetail.college_id == college.id)
        .values(active=False)
    )
    loaded = 0
    for (cip6, level), completions in records.items():
        parent_code = parent_cip4(cip6)
        parent = db.scalar(select(AcademicProgram).where(AcademicProgram.cip_code == parent_code))
        if parent is None:
            parent = AcademicProgram(
                cip_code=parent_code,
                name=titles.get(f"{parent_code[:2]}.{parent_code[2:]}", f"CIP {parent_code}"),
            )
            db.add(parent)
            db.flush()
        detail = db.scalar(
            select(AcademicProgramDetail).where(AcademicProgramDetail.cip_code == cip6)
        )
        title = titles.get(cip6, f"CIP {cip6}")
        if detail is None:
            detail = AcademicProgramDetail(
                cip_code=cip6, parent_program_id=parent.id, name=title,
            )
            db.add(detail)
            db.flush()
        else:
            detail.parent_program_id = parent.id
            detail.name = title
        link = db.scalar(select(CollegeProgramDetail).where(
            CollegeProgramDetail.college_id == college.id,
            CollegeProgramDetail.detail_program_id == detail.id,
            CollegeProgramDetail.credential_level == level,
        ))
        if link is None:
            link = CollegeProgramDetail(
                college_id=college.id,
                detail_program_id=detail.id,
                credential_level=level,
                credential_title=UNDERGRADUATE_LEVELS[level],
                dataset_year=IPEDS_DATASET_YEAR,
                source_name="NCES IPEDS Completions",
                source_url=IPEDS_SOURCE_URL,
            )
            db.add(link)
        link.credential_title = UNDERGRADUATE_LEVELS[level]
        link.completion_count = completions
        link.dataset_year = IPEDS_DATASET_YEAR
        link.source_name = "NCES IPEDS Completions"
        link.source_url = IPEDS_SOURCE_URL
        link.retrieved_at = retrieved_at
        link.active = True
        loaded += 1
    return loaded


def load(offset: int = 0, limit: int = 0) -> dict:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        colleges = db.scalars(select(College).order_by(College.name)).all()[offset:]
        if limit:
            colleges = colleges[:limit]
        college_ids = {college.id for college in colleges}
        unit_by_college, conflicts = college_unit_ids(db, college_ids)
        target_units = set(unit_by_college.values())
        records_by_unit: dict[int, dict[tuple[str, int], int]] = {}
        for row in completion_rows():
            unit = str(row.get("UNITID") or "").strip()
            level = str(row.get("AWLEVEL") or "").strip()
            cip6 = canonical_cip6(row.get("CIPCODE"))
            if not unit.isdigit() or int(unit) not in target_units:
                continue
            if not level.isdigit() or int(level) not in UNDERGRADUATE_LEVELS or not cip6:
                continue
            count_text = str(row.get("CTOTALT") or "0").strip()
            completions = int(count_text) if count_text.isdigit() else 0
            if completions <= 0:
                continue
            key = (cip6, int(level))
            records = records_by_unit.setdefault(int(unit), {})
            records[key] = records.get(key, 0) + completions

        titles = taxonomy_titles()
        summary: dict[str, object] = {
            "requested": len(colleges),
            "matched": 0,
            "detailed_programs": 0,
            "missing_unit_id": [],
            "unit_id_conflicts": [],
            "without_recent_completions": [],
            "errors": [],
        }
        for college in colleges:
            if college.id in conflicts:
                summary["unit_id_conflicts"].append({
                    "college": college.name, "unit_ids": conflicts[college.id],
                })
                continue
            unit_id = unit_by_college.get(college.id)
            if unit_id is None:
                summary["missing_unit_id"].append(college.name)
                continue
            records = records_by_unit.get(unit_id, {})
            try:
                count = upsert_college_details(db, college, records, titles, datetime.utcnow())
                db.commit()
                summary["matched"] += 1
                summary["detailed_programs"] += count
                if not records:
                    summary["without_recent_completions"].append({
                        "college": college.name, "unit_id": unit_id,
                    })
            except (ValueError, KeyError) as exc:
                db.rollback()
                summary["errors"].append({"college": college.name, "error": str(exc)})
        summary["active_detail_links"] = db.scalar(
            select(func.count()).select_from(CollegeProgramDetail)
            .where(CollegeProgramDetail.active.is_(True))
        )
        summary["canonical_details"] = db.scalar(
            select(func.count()).select_from(AcademicProgramDetail)
        )
        summary["cataloged_colleges"] = db.scalar(
            select(func.count(func.distinct(CollegeProgramDetail.college_id)))
            .where(CollegeProgramDetail.active.is_(True))
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N colleges")
    parser.add_argument("--limit", type=int, default=0, help="Only process the next N colleges")
    args = parser.parse_args()
    print(json.dumps(load(max(0, args.offset), max(0, args.limit)), indent=2))


if __name__ == "__main__":
    main()
