"""Load undergraduate degree fields from the U.S. Education College Scorecard API.

The importer reads the colleges already present in the application database,
matches each to the official Scorecard institution and field-of-study bulk files,
then upserts associate and bachelor's CIP-4 fields. Bulk ZIPs are the default;
`--api` is an optional fallback that uses COLLEGE_SCORECARD_API_KEY.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import unicodedata
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from sqlalchemy import func, select, update

from app.database import SessionLocal, engine
from app.models import AcademicProgram, Base, College, CollegeProgram

API_URL = "https://api.data.gov/ed/collegescorecard/v1/schools"
INSTITUTION_DOWNLOAD = os.environ.get(
    "COLLEGE_SCORECARD_INSTITUTION_SOURCE",
    "https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Institution_06102026.zip",
)
INSTITUTION_CSV = "Most-Recent-Cohorts-Institution.csv"
FIELD_DOWNLOAD = os.environ.get(
    "COLLEGE_SCORECARD_FIELD_SOURCE",
    "https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Field-of-Study_06102026.zip",
)
FIELD_CSV = "Most-Recent-Cohorts-Field-of-Study.csv"
SOURCE_URL = "https://collegescorecard.ed.gov/data/"
UNDERGRADUATE_LEVELS = {2: "Associate's Degree", 3: "Bachelor's Degree"}
# Reviewed aliases for repository names that differ from official Scorecard names.
# UNITIDs make these mappings deterministic without weakening fuzzy matching.
REVIEWED_UNIT_IDS = {
    ("OH", "cuyahoga community college"): 202356,
    ("KY", "lindsey wilson college"): 157216,
    ("OH", "ohio state university"): 204796,
    ("OH", "ohio university"): 204857,
    ("TN", "university of tennessee"): 221759,
    ("WV", "west virginia university potomac state college"): 237701,
    ("OH", "wright state university"): 206604,
}


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def clean_title(value: str) -> str:
    return value.strip().rstrip(".")


def api_request(params: dict[str, str], attempts: int = 4) -> dict:
    url = API_URL + "?" + urlencode(params)
    for attempt in range(attempts):
        try:
            with urlopen(url, timeout=45) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(2 ** (attempt + 1))
        except URLError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("College Scorecard request failed")


def zip_rows(url: str, expected_csv: str):
    """Stream a named CSV in an official Scorecard ZIP without retaining rows."""
    with urlopen(url, timeout=180) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    names = [
        name for name in archive.namelist()
        if name.replace("\\", "/").rsplit("/", 1)[-1] == expected_csv
    ]
    if len(names) != 1:
        raise RuntimeError(
            f"Expected exactly one {expected_csv!r} in College Scorecard archive, found {len(names)}"
        )
    with archive.open(names[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def bulk_schools() -> list[dict]:
    """Join current institution and field files into the API-compatible shape."""
    schools: dict[int, dict] = {}
    for row in zip_rows(INSTITUTION_DOWNLOAD, INSTITUTION_CSV):
        unit = str(row.get("UNITID") or "").strip()
        if not unit.isdigit():
            continue
        schools[int(unit)] = {
            "id": int(unit),
            "school.name": str(row.get("INSTNM") or "").strip(),
            "school.city": str(row.get("CITY") or "").strip(),
            "school.state": str(row.get("STABBR") or "").strip(),
            "latest.programs.cip_4_digit": [],
        }
    for row in zip_rows(FIELD_DOWNLOAD, FIELD_CSV):
        unit = str(row.get("UNITID") or "").strip()
        level = str(row.get("CREDLEV") or "").strip()
        if not unit.isdigit() or not level.isdigit() or int(level) not in UNDERGRADUATE_LEVELS:
            continue
        school = schools.get(int(unit))
        if school is None:
            continue
        school["latest.programs.cip_4_digit"].append({
            "code": str(row.get("CIPCODE") or "").strip(),
            "title": str(row.get("CIPDESC") or "").strip(),
            "credential": {"level": int(level)},
        })
    return list(schools.values())


def choose_school(manifest: dict, results: list[dict]) -> tuple[dict | None, float]:
    target_name = normalize(manifest.get("name"))
    target_city = normalize(manifest.get("city"))
    target_state = (manifest.get("state") or "").upper()
    reviewed_unit_id = REVIEWED_UNIT_IDS.get((target_state, target_name))
    if reviewed_unit_id is not None:
        reviewed = next((result for result in results if int(result.get("id") or 0) == reviewed_unit_id), None)
        return (reviewed, 2.0) if reviewed else (None, 0)
    candidates: list[tuple[float, dict]] = []
    for result in results:
        if (result.get("school.state") or "").upper() != target_state:
            continue
        source_name = normalize(result.get("school.name"))
        ratio = SequenceMatcher(None, target_name, source_name).ratio()
        score = ratio + (0.08 if normalize(result.get("school.city")) == target_city else 0)
        if source_name == target_name:
            score += 1
        candidates.append((score, result))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, result = candidates[0]
    # Exact normalized names always pass; fuzzy matches require strong similarity.
    return (result, score) if score >= 0.86 else (None, score)


def fetch_school(manifest: dict, api_key: str) -> tuple[dict | None, float]:
    payload = api_request({
        "api_key": api_key,
        "school.name": manifest["name"],
        "school.state": manifest["state"],
        "fields": ("id,school.name,school.city,school.state,"
                   "latest.programs.cip_4_digit.code,latest.programs.cip_4_digit.title,"
                   "latest.programs.cip_4_digit.credential.level"),
        "all_programs_nested": "true",
        "per_page": "100",
    })
    return choose_school(manifest, payload.get("results") or [])


def fetch_state(state: str, api_key: str) -> list[dict]:
    """Fetch a state's institutions in pages to avoid one API call per college."""
    results: list[dict] = []
    page = 0
    while True:
        payload = api_request({
            "api_key": api_key,
            "school.state": state,
            "fields": ("id,school.name,school.city,school.state,"
                       "latest.programs.cip_4_digit.code,latest.programs.cip_4_digit.title,"
                       "latest.programs.cip_4_digit.credential.level"),
            "all_programs_nested": "true",
            "per_page": "100",
            "page": str(page),
        })
        results.extend(payload.get("results") or [])
        metadata = payload.get("metadata") or {}
        if len(results) >= int(metadata.get("total") or 0) or not payload.get("results"):
            return results
        page += 1


def upsert_catalog(db, college: College, school: dict, retrieved_at: datetime) -> int:
    unit_id = int(school["id"])
    source_name = str(school.get("school.name") or college.name)
    raw_programs = school.get("latest.programs.cip_4_digit") or []
    programs: dict[tuple[str, int], str] = {}
    for raw in raw_programs:
        level = int((raw.get("credential") or {}).get("level") or 0)
        code = str(raw.get("code") or "").strip()
        title = clean_title(str(raw.get("title") or ""))
        if level in UNDERGRADUATE_LEVELS and code and title:
            programs[(code, level)] = title

    db.execute(update(CollegeProgram).where(CollegeProgram.college_id == college.id).values(active=False))
    for (code, level), title in programs.items():
        program = db.scalar(select(AcademicProgram).where(AcademicProgram.cip_code == code))
        if program is None:
            program = AcademicProgram(cip_code=code, name=title)
            db.add(program)
            db.flush()
        elif program.name != title:
            program.name = title
        link = db.scalar(select(CollegeProgram).where(
            CollegeProgram.college_id == college.id,
            CollegeProgram.program_id == program.id,
            CollegeProgram.credential_level == level,
        ))

        if link is None:
            link = CollegeProgram(
                college_id=college.id,
                program_id=program.id,
                credential_level=level,
                credential_title=UNDERGRADUATE_LEVELS[level],
                scorecard_unit_id=unit_id,
                source_name=source_name,
                source_url=SOURCE_URL,
            )
            db.add(link)
        link.credential_title = UNDERGRADUATE_LEVELS[level]
        link.scorecard_unit_id = unit_id
        link.source_name = source_name
        link.source_url = SOURCE_URL
        link.retrieved_at = retrieved_at
        link.active = True
    return len(programs)


def load(api_key: str, offset: int = 0, limit: int = 0, delay: float = 0.1, use_api: bool = False) -> dict:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        colleges = db.scalars(select(College).order_by(College.name)).all()[offset:]
        if limit:
            colleges = colleges[:limit]
        summary: dict[str, object] = {
            "requested": len(colleges), "matched": 0, "programs": 0,
            "unmatched_scorecard": [], "matched_without_programs": [], "errors": [],
        }
        by_state: dict[str, list[dict]] = {}
        if use_api:
            for state in sorted({(college.state or "").upper() for college in colleges if college.state}):
                try:
                    by_state[state] = fetch_state(state, api_key)
                    if delay:
                        time.sleep(delay)
                except (HTTPError, URLError, ValueError, KeyError) as exc:
                    by_state[state] = []
                    summary["errors"].append({"state": state, "error": str(exc)})
        else:
            for school in bulk_schools():
                state = (school.get("school.state") or "").upper()
                if state:
                    by_state.setdefault(state, []).append(school)

        for college in colleges:
            item = {"name": college.name, "city": college.city or "", "state": college.state or ""}
            school, score = choose_school(item, by_state.get((college.state or "").upper(), []))
            if school is None:
                summary["unmatched_scorecard"].append({
                    "college": college.name, "best_score": round(score, 3)})
                continue
            try:
                count = upsert_catalog(db, college, school, datetime.utcnow())
                db.commit()
                summary["matched"] += 1
                summary["programs"] += count
                if count == 0:
                    summary["matched_without_programs"].append({
                        "college": college.name,
                        "scorecard_name": school.get("school.name"),
                        "scorecard_unit_id": school.get("id"),
                    })
            except (ValueError, KeyError) as exc:
                db.rollback()
                summary["errors"].append({"college": college.name, "error": str(exc)})
        summary["active_links"] = db.scalar(
            select(func.count()).select_from(CollegeProgram).where(CollegeProgram.active.is_(True)))
        summary["canonical_programs"] = db.scalar(select(func.count()).select_from(AcademicProgram))
        summary["cataloged_colleges"] = db.scalar(
            select(func.count(func.distinct(CollegeProgram.college_id))).where(CollegeProgram.active.is_(True)))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N manifest schools")
    parser.add_argument("--limit", type=int, default=0, help="Only process the next N manifest schools")
    parser.add_argument("--delay", type=float, default=0.1, help="Seconds between API requests")
    parser.add_argument("--api", action="store_true", help="Use the API instead of official bulk ZIP files")
    args = parser.parse_args()
    key = (os.environ.get("COLLEGE_SCORECARD_API_KEY") or "DEMO_KEY").strip()
    print(json.dumps(load(key, max(0, args.offset), max(0, args.limit), max(0, args.delay), args.api), indent=2))


if __name__ == "__main__":
    main()