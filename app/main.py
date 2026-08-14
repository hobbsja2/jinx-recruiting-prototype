from __future__ import annotations

import html
import os
import secrets
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image as PILImage, ImageOps
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from .database import Base, engine, get_db, sync_sqlite_columns
from .email_templates import EMAIL_SIGNATURE, TEMPLATES as EMAIL_TEMPLATES, render_template
from .intake_invitations import active_invitation, claim_invitation, create_invitation
from .models import (
    AcademicProgram, AcademicProgramDetail, ActivityLog, College, CollegeMinor,
    CollegeProgram, CollegeProgramDetail, Player, PlayerIntake, TeamNeed,
)
from .outlook import (
    OutlookError,
    complete_authorization,
    disconnect as disconnect_outlook,
    expected_sender,
    protect_authorization_flow,
    send_mail,
    start_authorization,
    status as outlook_status,
    unprotect_authorization_flow,
)
from .reports import school_list_pdf
from .tuition import is_out_of_state, norm_state, tuition_for
from .seed import backfill_demo_contacts, seed_demo_data

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
STATIC_ROOT = (ROOT / "static").resolve()
PHOTO_DIR = STATIC_ROOT / "uploads" / "players"
MAX_PHOTO_BYTES = 8 * 1024 * 1024
THUMB_SIZE = (480, 480)
INTAKE_LINK_PLACEHOLDER = "[A secure one-time intake link will be inserted when this email is sent]"
LOCAL_SESSION_SECRET = secrets.token_urlsafe(48)


def production_setting(name: str, local_default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if os.environ.get("WEBSITE_SITE_NAME"):
        raise RuntimeError(f"{name} must be configured in Azure App Service.")
    return local_default


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    sync_sqlite_columns()
    # Demo seeding is disabled: the database holds real college data. To restore
    # the fictional local demo set, set JINX_SEED_DEMO=1 in the environment.
    if os.environ.get("JINX_SEED_DEMO") == "1":
        with Session(bind=engine) as db:
            seed_demo_data(db)
            backfill_demo_contacts(db)
    yield


app = FastAPI(title="Jinx Recruiting", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=production_setting("SESSION_SECRET", LOCAL_SESSION_SECRET),
    https_only=bool(os.environ.get("WEBSITE_SITE_NAME")) or os.environ.get("SESSION_COOKIE_SECURE") == "1",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/healthz", include_in_schema=False)
def healthz(db: Session = Depends(get_db)):
    """Verify that the web process and database connection are available."""
    db.execute(select(1)).scalar_one()
    return {"status": "ok"}


COLLEGE_FIELDS = [("name", "College name", "text", True), ("division", "Division", "text", True), ("conference", "Conference", "text", False), ("city", "City", "text", False), ("state", "State", "text", False), ("academic_ranking", "Academic profile", "text", False), ("tuition", "Annual tuition (in-state + housing)", "number", False), ("in_state_tuition", "In-state tuition", "number", False), ("out_of_state_tuition", "Out-of-state tuition", "number", False), ("housing_cost", "Housing cost", "number", False), ("financial_aid", "Financial aid", "textarea", False), ("head_coach", "Head coach", "text", False), ("recruiting_coordinator", "Recruiting coordinator", "text", False), ("coach_emails", "Coach emails", "text", False), ("coach_phones", "Coach phones", "text", False), ("roster_size", "Roster size", "number", False), ("scholarship_count", "Scholarships", "number", False), ("facilities_notes", "Facilities notes", "textarea", False), ("program_reputation", "Program reputation", "textarea", False), ("website_url", "Website", "url", False), ("notes", "Recruiting notes", "textarea", False)]
PLAYER_FIELDS = [("name", "Player name", "text", True), ("grad_year", "Graduation year", "number", True), ("primary_position", "Primary position", "text", True), ("secondary_position", "Secondary position", "text", False), ("home_state", "Home state", "select", False), ("intended_major", "Intended major / field of study", "text", False), ("player_email", "Player email", "email", False), ("parent_email", "Parent/guardian email", "email", False), ("gpa", "GPA", "number", False), ("sat_act", "SAT / ACT", "text", False), ("height", "Height", "text", False), ("weight", "Weight", "text", False), ("throwing_hand", "Throwing hand", "text", False), ("batting_side", "Batting side", "text", False), ("home_to_first", "Home-to-first", "text", False), ("exit_velo", "Exit velocity", "text", False), ("pop_time", "Pop time", "text", False), ("pitching_velo", "Pitching velocity", "text", False), ("highlight_link", "Highlight link", "url", False), ("transcript_path", "Transcript path", "text", False), ("social_handles", "Social handles", "text", False), ("notes", "Recruiting notes", "textarea", False)]
US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
             "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
             "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
             "WV", "WI", "WY"]
# Fields rendered as a single-choice dropdown, keyed by form field name.
SELECT_OPTIONS = {"home_state": US_STATES}
DIVISION_OPTIONS = ["NCAA D1", "NCAA D2", "NCAA D3", "NAIA", "JUCO"]
CAMPUS_OPTIONS = ["", "Urban", "Suburban", "Rural", "Small campus", "Large campus"]
INTAKE_FIELDS = [
    ("player_name", "Player name", "text", True, None), ("grad_year", "Graduation year", "number", True, None),
    ("primary_position", "Primary position", "text", True, None), ("secondary_position", "Secondary position", "text", False, None),
    ("player_email", "Player email", "email", False, None), ("parent_name", "Parent/guardian name", "text", False, None),
    ("parent_email", "Parent/guardian email", "email", False, None), ("phone", "Best phone number", "text", False, None),
    ("home_state", "Home state", "select", False, [""] + US_STATES),
    ("gpa", "GPA", "number", False, None), ("sat_act", "SAT / ACT", "text", False, None),
    ("height", "Height", "text", False, None), ("weight", "Weight", "text", False, None),
    ("throwing_hand", "Throwing hand", "select", False, ["", "R", "L"]),
    ("batting_side", "Batting side", "select", False, ["", "R", "L", "Switch"]),
    ("home_to_first", "Home-to-first time", "text", False, None), ("exit_velo", "Exit velocity", "text", False, None),
    ("pop_time", "Pop time", "text", False, None), ("pitching_velo", "Pitching velocity", "text", False, None),
    ("highlight_link", "Highlight video link", "url", False, None),
    ("intended_major", "Intended major / area of study", "text", False, None),
    ("max_tuition", "Maximum tuition per year (USD)", "number", False, None),
    ("division_prefs", "Division level preference", "checkdrop", False, DIVISION_OPTIONS),
    ("preferred_locations", "Preferred states or regions", "text", False, None),
    ("campus_setting", "Campus setting", "select", False, CAMPUS_OPTIONS),
    ("notes", "Anything else we should know", "textarea", False, None),
]
NEED_FIELDS = [("class_year", "Recruiting class year", "number", True), ("position", "Position", "text", True), ("pitching_profile", "Pitching profile", "textarea", False), ("hitting_profile", "Hitting profile", "textarea", False), ("notes", "Notes", "textarea", False)]


def esc(value: object | None) -> str:
    return html.escape("" if value is None else str(value))


def redirect(path: str, notice: str = "") -> RedirectResponse:
    if notice:
        path += ("&" if "?" in path else "?") + f"notice={quote(notice)}"
    return RedirectResponse(path, status_code=303)


def page(request: Request, title: str, body: str, subtitle: str = "", notice: str = ""):
    return TEMPLATES.TemplateResponse(request, "page.html", {"title": title, "subtitle": subtitle, "body": body, "notice": notice or request.query_params.get("notice", "")})


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="The form expired. Reload the page and try again.")


def public_base_url(request: Request) -> str:
    return production_setting("PUBLIC_BASE_URL", str(request.base_url)).rstrip("/")


def get_or_404(db: Session, model, item_id: int):
    item = db.get(model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Record not found")
    return item


def value(item: object | None, key: str) -> str:
    return "" if item is None or getattr(item, key, None) is None else str(getattr(item, key))


def form_html(action: str, fields, item: object | None, cancel: str, title: str, extra: str = "") -> str:
    controls = []
    for key, label, kind, required in fields:
        current = esc(value(item, key))
        required_text = " required" if required else ""
        wide = " wide" if kind == "textarea" else ""
        if kind == "textarea":
            control = f'<textarea name="{key}">{current}</textarea>'
        elif kind == "select":
            raw = norm_state(value(item, key)) if key == "home_state" else value(item, key)
            options = list(SELECT_OPTIONS.get(key, []))
            if raw and raw not in options:  # keep any pre-existing value selectable
                options.append(raw)
            rendered = "".join(
                f'<option value="{esc(option)}"{" selected" if option == raw else ""}>{esc(option)}</option>'
                for option in options)
            control = f'<select name="{key}"><option value="">— select —</option>{rendered}</select>'
        else:
            step = ' step="any"' if key in {"gpa", "tuition", "in_state_tuition", "out_of_state_tuition", "housing_cost"} else ""
            control = f'<input type="{kind}" name="{key}" value="{current}"{step}{required_text}>'
        controls.append(f'<label class="{wide}">{esc(label)}{control}</label>')
    return f'<div class="card"><form class="grid" method="post" action="{action}">{extra}{"".join(controls)}<div class="wide actions"><a class="button secondary" href="{cancel}">Cancel</a><button>{esc(title)}</button></div></form></div>'


async def payload(request: Request, fields) -> dict:
    form = await request.form()
    data = {}
    for key, _, kind, required in fields:
        raw = str(form.get(key, "")).strip()
        if required and not raw:
            raise HTTPException(status_code=422, detail=f"{key.replace('_', ' ').title()} is required")
        if not raw:
            data[key] = None
        elif kind == "number":
            data[key] = float(raw) if key in {"gpa", "tuition", "in_state_tuition", "out_of_state_tuition", "housing_cost"} else int(raw)
        else:
            data[key] = raw
    return data


def facts(item: object, labels: list[tuple[str, str]]) -> str:
    parts = [f'<div><b>{esc(label)}</b>{esc(value(item, key)) or "—"}</div>' for key, label in labels]
    return f'<section class="detail">{"".join(parts)}</section>'


def tuition_cell(college: College, player: Player | None, wrap: str = "td") -> str:
    """Render a tuition table cell/value, bolded when the out-of-state rate is shown."""
    amount, oos = tuition_for(college, player)
    text = ("$" + format(amount, ",.0f")) if amount else "—"
    if oos:
        text = f'<strong title="Out-of-state rate for {esc(player.home_state)} player">{text} *</strong>'
    return f"<{wrap}>{text}</{wrap}>" if wrap else text


NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def split_name(full_name: str) -> tuple[str, str]:
    """Return (last, first) for sorting. Handles "Last, First" and name suffixes."""
    name = (full_name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        return last.strip().lower(), first.strip().lower()
    parts = [part for part in name.split() if part]
    while len(parts) > 1 and parts[-1].strip(".").lower() in NAME_SUFFIXES:
        parts.pop()
    if not parts:
        return "", ""
    return parts[-1].lower(), " ".join(parts[:-1]).lower()


def player_sort_key(player: Player) -> tuple:
    """Graduation class first, then alphabetical by last name."""
    last, first = split_name(player.name)
    return (player.grad_year if player.grad_year is not None else 9999, last, first)


def photo_url(player: Player) -> str:
    """Return a cache-busted URL for the player's stored thumbnail, or "" if none exists.

    Paths are confined to the static directory so a stray database value cannot
    point the page at an arbitrary file on disk.
    """
    relative = (getattr(player, "photo_path", "") or "").strip().replace("\\", "/")
    if not relative:
        return ""
    try:
        resolved = (STATIC_ROOT / relative).resolve()
    except (OSError, ValueError):
        return ""
    if not resolved.is_file() or STATIC_ROOT not in resolved.parents:
        return ""
    return f"/static/{resolved.relative_to(STATIC_ROOT).as_posix()}?v={int(resolved.stat().st_mtime)}"


def thumbnail_html(player: Player, size: str = "thumb-lg") -> str:
    url = photo_url(player)
    if url:
        return f'<img class="{size}" src="{esc(url)}" alt="{esc(player.name)} thumbnail">'
    initials = "".join(part[0] for part in (player.name or "?").split()[:2]).upper() or "?"
    return f'<span class="{size} thumb-placeholder" aria-hidden="true">{esc(initials)}</span>'


def log(db: Session, kind: str, detail: str) -> None:
    db.add(ActivityLog(kind=kind, detail=detail)); db.commit()


@app.get("/")
def home():
    return redirect("/admin")


@app.get("/admin")
def dashboard(request: Request, db: Session = Depends(get_db)):
    counts = [
        ("Colleges", db.scalar(select(func.count()).select_from(College))),
        ("Players", db.scalar(select(func.count()).select_from(Player))),
        ("Active team needs", db.scalar(select(func.count()).select_from(TeamNeed))),
    ]
    cards = "".join(f'<div class="card stat"><strong>{count}</strong>{esc(label)}</div>' for label, count in counts)
    activities = db.scalars(select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(5)).all()
    rows = "".join(f'<tr><td>{esc(a.created_at.strftime("%b %d, %Y"))}</td><td><span class="pill">{esc(a.kind)}</span></td><td>{esc(a.detail)}</td></tr>' for a in activities) or '<tr><td colspan="3">No activity yet.</td></tr>'
    body = f'<section class="stats">{cards}</section><div class="toolbar"><a class="button" href="/players/new">Add player</a><a class="button secondary" href="/colleges/new">Add college</a></div><section class="card"><h2>Recent local activity</h2><table><tr><th>Date</th><th>Type</th><th>Detail</th></tr>{rows}</table></section>'
    return page(request, "Admin Dashboard", body, "Recruiting pipeline overview")


@app.get("/colleges")
def colleges(request: Request, cip_code: str = "", cip6_code: str = "", division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    divisions = [d for d in division if d]; states = [s for s in state if s]
    selected_detail = db.scalar(select(AcademicProgramDetail).where(AcademicProgramDetail.cip_code == cip6_code)) if cip6_code else None
    selected_program = (db.get(AcademicProgram, selected_detail.parent_program_id) if selected_detail else
                        (db.scalar(select(AcademicProgram).where(AcademicProgram.cip_code == cip_code)) if cip_code else None))
    query = select(College).order_by(College.name)
    if selected_detail:
        query = (query.join(CollegeProgramDetail, CollegeProgramDetail.college_id == College.id)
                 .where(CollegeProgramDetail.detail_program_id == selected_detail.id,
                        CollegeProgramDetail.active.is_(True)))
    elif selected_program:
        query = (query.join(CollegeProgram, CollegeProgram.college_id == College.id)
                 .where(CollegeProgram.program_id == selected_program.id, CollegeProgram.active.is_(True)))
    if divisions: query = query.where(College.division.in_(divisions))
    if states: query = query.where(College.state.in_(states))
    if max_tuition:
        # Blank tuition is unknown, not expensive: keep those colleges visible.
        try: query = query.where(or_(College.tuition <= float(max_tuition), College.tuition.is_(None)))
        except ValueError: pass
    items = db.scalars(query).unique().all()
    degree_options = '<option value="">All undergraduate majors</option>' + "".join(
        f'<option value="{esc(p.cip_code)}"{" selected" if selected_program and not selected_detail and p.id == selected_program.id else ""}>{esc(p.name)}</option>'
        for p in program_choices(db))
    detail_options = '<option value="">All detailed fields / specializations</option>' + "".join(
        f'<option value="{esc(p.cip_code)}"{" selected" if selected_detail and p.id == selected_detail.id else ""}>{esc(p.name)} ({esc(p.cip_code)})</option>'
        for p in detail_choices(db))
    filter_form = (f'<form class="filters" method="get">'
                   f'<label class="stack">Detailed field / specialization<select name="cip6_code">{detail_options}</select></label>'
                   f'<label class="stack">Broad undergraduate major<select name="cip_code">{degree_options}</select></label>'
                   f'{checkbox_dropdown("division", "Division", distinct_values(db, College.division), divisions, "divisions")}'
                   f'{checkbox_dropdown("state", "State", distinct_values(db, College.state), states, "states")}'
                   f'<label class="stack">Maximum tuition<input name="max_tuition" placeholder="e.g. 30000" value="{esc(max_tuition)}"></label>'
                   f'<button>Apply filters</button><a class="button secondary" href="/colleges">Clear</a></form>')
    rows = "".join(f'<tr><td><a href="/colleges/{c.id}">{esc(c.name)}</a></td><td>{esc(c.division)}</td><td>{esc(c.city)}, {esc(c.state)}</td><td>{("$" + format(c.tuition, ",.0f")) if c.tuition else "—"}</td><td>{esc(c.head_coach)}</td></tr>' for c in items) or '<tr><td colspan="5">No colleges match the selected filters.</td></tr>'
    body = (f'<div class="toolbar"><a class="button" href="/colleges/new">Add college</a>'
            f'<span class="muted">{len(items)} college(s)</span></div>{filter_form}'
            f'<table><tr><th>College</th><th>Division</th><th>Location</th><th>Tuition</th><th>Head coach</th></tr>{rows}</table>')
    return page(request, "Colleges", body, "Academic, financial, and coaching profiles")


@app.get("/colleges/new")
def college_new(request: Request):
    return page(request, "Add College", form_html("/colleges", COLLEGE_FIELDS, None, "/colleges", "Save college"))


@app.post("/colleges")
async def college_create(request: Request, db: Session = Depends(get_db)):
    item = College(**await payload(request, COLLEGE_FIELDS)); db.add(item); db.commit()
    return redirect(f"/colleges/{item.id}", "College saved.")


@app.get("/colleges/{college_id}")
def college_detail(college_id: int, request: Request, player_id: int | None = None, db: Session = Depends(get_db)):
    college = get_or_404(db, College, college_id)
    player = db.get(Player, player_id) if player_id else None
    need_rows = "".join(f'<tr><td>{esc(n.class_year)}</td><td>{esc(n.position)}</td><td>{esc(n.pitching_profile or n.hitting_profile)}</td><td><a href="/needs/{n.id}/edit">Edit</a></td></tr>' for n in college.needs) or '<tr><td colspan="4">No team needs recorded.</td></tr>'
    degree_rows = db.execute(
        select(AcademicProgram, CollegeProgram)
        .join(CollegeProgram, CollegeProgram.program_id == AcademicProgram.id)
        .where(CollegeProgram.college_id == college.id, CollegeProgram.active.is_(True))
        .order_by(AcademicProgram.name, CollegeProgram.credential_level)
    ).all()
    degree_table = "".join(
        f'<tr><td>{esc(program.name)}</td><td>{esc(offering.credential_title)}</td><td>{esc(program.cip_code)}</td></tr>'
        for program, offering in degree_rows
    ) or '<tr><td colspan="3">No undergraduate catalog has been loaded for this college.</td></tr>'
    detail_rows = db.execute(
        select(AcademicProgramDetail, CollegeProgramDetail, AcademicProgram)
        .join(CollegeProgramDetail, CollegeProgramDetail.detail_program_id == AcademicProgramDetail.id)
        .join(AcademicProgram, AcademicProgram.id == AcademicProgramDetail.parent_program_id)
        .where(CollegeProgramDetail.college_id == college.id, CollegeProgramDetail.active.is_(True))
        .order_by(AcademicProgram.name, AcademicProgramDetail.name, CollegeProgramDetail.credential_level)
    ).all()
    detail_table = "".join(
        f'<tr><td>{esc(detail.name)}</td><td>{esc(parent.name)}</td>'
        f'<td>{esc(offering.credential_title)}</td><td>{esc(detail.cip_code)}</td>'
        f'<td>{offering.completion_count}</td></tr>'
        for detail, offering, parent in detail_rows
    ) or '<tr><td colspan="5">No recent IPEDS detailed-field evidence has been loaded.</td></tr>'
    minor_rows = db.scalars(
        select(CollegeMinor)
        .where(CollegeMinor.college_id == college.id, CollegeMinor.active.is_(True))
        .order_by(CollegeMinor.name)
    ).all()
    minor_table = "".join(
        f'<tr><td>{esc(minor.name)}</td><td>{esc(minor.catalog_year) or "—"}</td>'
        f'<td><a href="{esc(minor.source_url)}" target="_blank" rel="noopener">{esc(minor.source_name)}</a></td></tr>'
        for minor in minor_rows
    ) or '<tr><td colspan="3">No reviewed official-catalog minors have been loaded.</td></tr>'
    body = f'<div class="actions"><a class="button" href="/colleges/{college.id}/edit">Edit college</a><a class="button secondary" href="/needs/college/{college.id}/new">Add team need</a><form method="post" action="/colleges/{college.id}/delete"><button class="button danger">Delete</button></form></div>'

    # "View costs for player" selector: picking a player recomputes tuition in/out-of-state.
    players = db.scalars(select(Player).order_by(Player.name)).all()
    opts = '<option value="">— no player (in-state) —</option>' + "".join(
        f'<option value="{p.id}"{" selected" if player and p.id == player.id else ""}>{esc(p.name)}'
        f'{f" ({esc(p.home_state)})" if norm_state(p.home_state) else ""}</option>' for p in players)
    body += (f'<form class="filters" method="get" action="/colleges/{college.id}" data-autosubmit>'
             f'<label class="stack">View costs for player<select name="player_id">{opts}</select></label>'
             f'<button>Apply</button></form>')

    # Tuition line reflects the selected player's residency; bold when out-of-state.
    amount, oos = tuition_for(college, player)
    tuition_text = tuition_cell(college, player, wrap="")
    if oos:
        tuition_text += (f' <span class="muted">(out-of-state rate; {esc(player.name)} is from '
                         f'{esc(player.home_state)}, college is in {esc(college.state)})</span>')
    elif player is not None and norm_state(player.home_state) and norm_state(player.home_state) == norm_state(college.state):
        tuition_text += f' <span class="muted">(in-state rate; {esc(player.name)} is from {esc(college.state)})</span>'

    detail_labels = [("division", "Division"), ("conference", "Conference"), ("city", "City"), ("state", "State"), ("academic_ranking", "Academic profile")]
    detail_parts = [f'<div><b>{esc(label)}</b>{esc(value(college, key)) or "—"}</div>' for key, label in detail_labels]
    detail_parts.append(f'<div><b>Annual tuition</b>{tuition_text}</div>')
    detail_parts.append(f'<div><b>In-state tuition</b>{("$" + format(college.in_state_tuition, ",.0f")) if college.in_state_tuition else "—"}</div>')
    detail_parts.append(f'<div><b>Out-of-state tuition</b>{("$" + format(college.out_of_state_tuition, ",.0f")) if college.out_of_state_tuition else "—"}</div>')
    detail_parts.append(f'<div><b>Housing cost</b>{("$" + format(college.housing_cost, ",.0f")) if college.housing_cost else "—"}</div>')
    for key, label in [("financial_aid", "Financial aid"), ("head_coach", "Head coach"), ("recruiting_coordinator", "Recruiting coordinator"), ("coach_emails", "Coach email"), ("coach_phones", "Coach phone"), ("website_url", "Website"), ("notes", "Notes")]:
        detail_parts.append(f'<div><b>{esc(label)}</b>{esc(value(college, key)) or "—"}</div>')
    body += f'<section class="detail">{"".join(detail_parts)}</section>'

    body += (f'<h2>Undergraduate degrees</h2><p class="muted">Federal College Scorecard field-of-study data; verify current availability with the college.</p>'
             f'<table><tr><th>Field of study</th><th>Credential</th><th>CIP</th></tr>{degree_table}</table>')
    body += (f'<h2>Detailed degree fields and specializations</h2>'
             f'<p class="muted">Six-digit CIP fields with 2024 IPEDS completions. These are recent completion evidence, not a complete or current catalog; verify named concentrations with the college.</p>'
             f'<table><tr><th>Detailed field</th><th>Major family</th><th>Credential</th><th>CIP</th><th>Recent completions</th></tr>{detail_table}</table>')
    body += (f'<h2>Available minors</h2>'
             f'<p class="muted">Only minors reviewed against an official institutional catalog are shown. Missing data does not mean a college offers no minors.</p>'
             f'<table><tr><th>Minor</th><th>Catalog year</th><th>Official source</th></tr>{minor_table}</table>')
    body += f'<h2>Team needs</h2><table><tr><th>Class</th><th>Position</th><th>Profile</th><th></th></tr>{need_rows}</table>'
    return page(request, college.name, body, "College recruiting profile")


@app.get("/colleges/{college_id}/edit")
def college_edit(college_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, College, college_id)
    return page(request, f"Edit {item.name}", form_html(f"/colleges/{item.id}/edit", COLLEGE_FIELDS, item, f"/colleges/{item.id}", "Save changes"))


@app.post("/colleges/{college_id}/edit")
async def college_update(college_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, College, college_id)
    for key, val in (await payload(request, COLLEGE_FIELDS)).items(): setattr(item, key, val)
    db.commit(); return redirect(f"/colleges/{item.id}", "College updated.")


@app.post("/colleges/{college_id}/delete")
def college_delete(college_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, College, college_id)); db.commit()
    return redirect("/colleges", "College and its team needs deleted.")


@app.get("/players")
def players(request: Request, db: Session = Depends(get_db)):
    items = sorted(db.scalars(select(Player)).all(), key=player_sort_key)
    rows = "".join(f'<tr><td>{thumbnail_html(p, "thumb-sm")}</td><td><a href="/players/{p.id}">{esc(p.name)}</a></td><td>{esc(p.grad_year)}</td><td>{esc(p.primary_position)}</td><td>{esc(p.secondary_position)}</td><td>{esc(p.gpa)}</td></tr>' for p in items) or '<tr><td colspan="6">No players added.</td></tr>'
    body = f'<div class="toolbar"><a class="button" href="/players/new">Add player</a></div><table><tr><th></th><th>Player</th><th>Class</th><th>Primary</th><th>Secondary</th><th>GPA</th></tr>{rows}</table>'
    return page(request, "Players", body, "Manage athlete profiles and recruiting materials")


@app.get("/players/new")
def player_new(request: Request):
    return page(request, "Add Player", form_html("/players", PLAYER_FIELDS, None, "/players", "Save player"))


@app.post("/players")
async def player_create(request: Request, db: Session = Depends(get_db)):
    item = Player(**await payload(request, PLAYER_FIELDS)); db.add(item); db.commit()
    return redirect(f"/players/{item.id}", "Player saved.")


@app.get("/players/{player_id}")
def player_detail(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    remove_button = (f'<form method="post" action="/players/{player.id}/photo/delete"><button class="button secondary">Remove</button></form>'
                     if photo_url(player) else "")
    body = (f'<section class="player-head">{thumbnail_html(player)}'
            f'<div class="photo-upload"><h2>{esc(player.name)}</h2>'
            f'<form method="post" action="/players/{player.id}/photo" enctype="multipart/form-data" class="actions">'
            f'<input type="file" name="photo" accept="image/*" required>'
            f'<button>Upload thumbnail</button>{remove_button}</form>'
            f'<p class="muted">JPEG, PNG, WebP, or GIF up to 8 MB. Resized to a {THUMB_SIZE[0]}px thumbnail on upload.</p>'
            f'</div></section>')
    body += f'<div class="actions"><a class="button" href="/players/{player.id}/edit">Edit player</a><a class="button secondary" href="/players/{player.id}/metrics">Metrics dashboard</a><a class="button secondary" href="/school-lists/{player.id}">Recommended schools</a><a class="button secondary" href="/flyers/player/{player.id}">Flyer preview</a><a class="button secondary" href="/email/compose?player_id={player.id}">Compose email</a><form method="post" action="/players/{player.id}/delete"><button class="button danger">Delete</button></form></div>'
    body += facts(player, [("grad_year", "Graduation year"), ("primary_position", "Primary position"), ("secondary_position", "Secondary position"), ("home_state", "Home state"), ("intended_major", "Intended major"), ("player_email", "Player email"), ("parent_email", "Parent email"), ("gpa", "GPA"), ("sat_act", "SAT / ACT"), ("height", "Height"), ("weight", "Weight"), ("throwing_hand", "Throws"), ("batting_side", "Bats"), ("highlight_link", "Highlight video"), ("social_handles", "Social"), ("notes", "Notes")])
    return page(request, player.name, body, "Player recruiting profile")


@app.get("/players/{player_id}/edit")
def player_edit(player_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, Player, player_id)
    return page(request, f"Edit {item.name}", form_html(f"/players/{item.id}/edit", PLAYER_FIELDS, item, f"/players/{item.id}", "Save changes"))


@app.post("/players/{player_id}/edit")
async def player_update(player_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, Player, player_id)
    for key, val in (await payload(request, PLAYER_FIELDS)).items(): setattr(item, key, val)
    db.commit(); return redirect(f"/players/{item.id}", "Player updated.")


@app.post("/players/{player_id}/delete")
def player_delete(player_id: int, db: Session = Depends(get_db)):
    db.delete(get_or_404(db, Player, player_id)); db.commit()
    return redirect("/players", "Player deleted.")


@app.post("/players/{player_id}/photo")
async def player_photo_upload(player_id: int, photo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Store an uploaded picture as the player's thumbnail.

    The upload is re-encoded through Pillow, so only genuine images are saved, the
    file name is generated server-side, and camera metadata is dropped.
    """
    player = get_or_404(db, Player, player_id)
    data = await photo.read(MAX_PHOTO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="Choose an image file to upload.")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than the 8 MB limit.")
    try:
        image = PILImage.open(BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception:
        raise HTTPException(status_code=422, detail="That file could not be read as an image.")
    image.thumbnail(THUMB_SIZE)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    target = PHOTO_DIR / f"player-{player.id}.jpg"
    image.save(target, "JPEG", quality=85, optimize=True)
    player.photo_path = f"uploads/players/{target.name}"
    db.commit()
    log(db, "photo", f"Uploaded thumbnail for {player.name}.")
    return redirect(f"/players/{player.id}", "Thumbnail updated.")


@app.post("/players/{player_id}/photo/delete")
def player_photo_delete(player_id: int, db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    url = photo_url(player)
    if url:
        (STATIC_ROOT / (player.photo_path or "").replace("\\", "/")).unlink(missing_ok=True)
    player.photo_path = None
    db.commit()
    return redirect(f"/players/{player.id}", "Thumbnail removed.")


@app.get("/players/{player_id}/metrics")
def player_metrics(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    metrics = [("Home-to-first", player.home_to_first), ("Exit velocity", player.exit_velo), ("Pop time", player.pop_time), ("Pitching velocity", player.pitching_velo), ("GPA", player.gpa)]
    cards = "".join(f'<div class="card stat"><strong>{esc(metric or "—")}</strong>{esc(label)}</div>' for label, metric in metrics)
    body = f'<section class="stats">{cards}</section><p class="muted">Charts are intentionally deferred in this local, dependency-light prototype.</p><a class="button secondary" href="/players/{player.id}">Back to player</a>'
    return page(request, f"{player.name} Metrics", body, "Athletic and academic snapshot")


@app.get("/needs/overview")
def needs_overview(request: Request, position: str = "", class_year: str = "", state: str = "", db: Session = Depends(get_db)):
    query = select(TeamNeed).options(selectinload(TeamNeed.college)).order_by(TeamNeed.class_year, TeamNeed.position)
    if position: query = query.where(TeamNeed.position.ilike(f"%{position}%"))
    if class_year: query = query.where(TeamNeed.class_year == int(class_year))
    if state: query = query.join(TeamNeed.college).where(College.state.ilike(f"%{state}%"))
    items = db.scalars(query).all()
    filters = f'<form class="toolbar" method="get"><input name="position" placeholder="Position" value="{esc(position)}"><input name="class_year" placeholder="Class year" value="{esc(class_year)}"><input name="state" placeholder="State" value="{esc(state)}"><button>Filter</button><a class="button secondary" href="/needs/overview">Clear</a></form>'
    rows = "".join(f'<tr><td><a href="/colleges/{n.college.id}">{esc(n.college.name)}</a></td><td>{esc(n.college.state)}</td><td>{esc(n.class_year)}</td><td>{esc(n.position)}</td><td>{esc(n.pitching_profile or n.hitting_profile)}</td><td><a href="/needs/{n.id}/edit">Edit</a></td></tr>' for n in items) or '<tr><td colspan="6">No needs match the filters.</td></tr>'
    return page(request, "Team Needs", filters + f'<table><tr><th>College</th><th>State</th><th>Class</th><th>Position</th><th>Profile</th><th></th></tr>{rows}</table>', "Filter live recruiting needs")


@app.get("/needs/college/{college_id}/new")
def need_new(college_id: int, request: Request, db: Session = Depends(get_db)):
    college = get_or_404(db, College, college_id)
    extra = f'<input type="hidden" name="college_id" value="{college.id}">'
    return page(request, f"Add Need · {college.name}", form_html("/needs", NEED_FIELDS, None, f"/colleges/{college.id}", "Save team need", extra))


@app.post("/needs")
async def need_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form(); college_id = int(str(form.get("college_id", "0")))
    get_or_404(db, College, college_id)
    # Reuse the validated form parser; its second read is safe for Starlette request forms.
    item = TeamNeed(college_id=college_id, **await payload(request, NEED_FIELDS)); db.add(item); db.commit()
    return redirect(f"/colleges/{college_id}", "Team need saved.")


@app.get("/needs/{need_id}/edit")
def need_edit(need_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, TeamNeed, need_id)
    return page(request, "Edit Team Need", form_html(f"/needs/{item.id}/edit", NEED_FIELDS, item, f"/colleges/{item.college_id}", "Save changes"))


@app.post("/needs/{need_id}/edit")
async def need_update(need_id: int, request: Request, db: Session = Depends(get_db)):
    item = get_or_404(db, TeamNeed, need_id)
    for key, val in (await payload(request, NEED_FIELDS)).items(): setattr(item, key, val)
    db.commit(); return redirect(f"/colleges/{item.college_id}", "Team need updated.")


@app.post("/needs/{need_id}/delete")
def need_delete(need_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, TeamNeed, need_id); college_id = item.college_id
    db.delete(item); db.commit(); return redirect(f"/colleges/{college_id}", "Team need deleted.")


@app.get("/school-lists")
def school_list_picker(request: Request, db: Session = Depends(get_db)):
    players = db.scalars(select(Player).order_by(Player.name)).all()
    options = "".join(f'<option value="{p.id}">{esc(p.name)} · {p.grad_year} {esc(p.primary_position)}</option>' for p in players)
    body = f'<div class="card"><form method="get" action="/school-lists/choose"><label>Select a player<select name="player_id">{options}</select></label><div class="actions"><button>Generate list</button></div></form></div>'
    return page(request, "School Lists", body, "Find colleges with matching team needs")


@app.get("/school-lists/choose")
def school_list_choose(player_id: int):
    return redirect(f"/school-lists/{player_id}")


def distinct_values(db: Session, column) -> list[str]:
    return [v for v in db.scalars(select(column).distinct().order_by(column)).all() if v]


def checkbox_dropdown(name: str, label: str, options: list[str], selected: list[str], noun: str) -> str:
    """Multi-select dropdown whose first row is a Select All toggle."""
    chosen = set(selected)
    boxes = "".join(
        f'<label><input type="checkbox" name="{name}" value="{esc(option)}"{" checked" if option in chosen else ""}>{esc(option)}</label>'
        for option in options) or f'<label class="muted">No {esc(noun)} available</label>'
    summary = f"{len(chosen)} {noun} selected" if 0 < len(chosen) < len(options) else (options[0] if len(chosen) == 1 == len(options) else f"All {noun}")
    if len(chosen) == 1:
        summary = next(iter(chosen))
    return (f'<label class="stack">{esc(label)}<details class="checkdrop" data-noun="{esc(noun)}">'
            f'<summary><span class="checkdrop-label">{esc(summary)}</span></summary>'
            f'<div class="checkdrop-panel"><label class="all"><input type="checkbox" class="select-all">Select All</label>{boxes}</div>'
            f'</details></label>')


def program_choices(db: Session) -> list[AcademicProgram]:
    return db.scalars(
        select(AcademicProgram)
        .join(CollegeProgram)
        .where(CollegeProgram.active.is_(True))
        .distinct()
        .order_by(AcademicProgram.name)
    ).all()


def detail_choices(db: Session) -> list[AcademicProgramDetail]:
    return db.scalars(
        select(AcademicProgramDetail)
        .join(CollegeProgramDetail)
        .where(CollegeProgramDetail.active.is_(True))
        .distinct()
        .order_by(AcademicProgramDetail.name)
    ).all()


def resolve_detail(db: Session, player: Player, cip6_code: str) -> AcademicProgramDetail | None:
    if cip6_code:
        return db.scalar(select(AcademicProgramDetail).where(AcademicProgramDetail.cip_code == cip6_code))
    intended = (player.intended_major or "").strip().lower()
    if not intended:
        return None
    exact = db.scalar(select(AcademicProgramDetail).where(func.lower(AcademicProgramDetail.name) == intended))
    if exact:
        return exact
    return db.scalar(
        select(AcademicProgramDetail)
        .where(func.lower(AcademicProgramDetail.name).contains(intended))
        .order_by(func.length(AcademicProgramDetail.name))
        .limit(1)
    )


def resolve_program(db: Session, player: Player, cip_code: str,
                    detail: AcademicProgramDetail | None = None) -> AcademicProgram | None:
    if detail:
        return db.get(AcademicProgram, detail.parent_program_id)
    if cip_code:
        return db.scalar(select(AcademicProgram).where(AcademicProgram.cip_code == cip_code))
    intended = (player.intended_major or "").strip().lower()
    if not intended:
        return None
    exact = db.scalar(select(AcademicProgram).where(func.lower(AcademicProgram.name) == intended))
    if exact:
        return exact
    return db.scalar(
        select(AcademicProgram)
        .where(func.lower(AcademicProgram.name).contains(intended))
        .order_by(func.length(AcademicProgram.name))
        .limit(1)
    )


def school_list_matches(
    db: Session,
    player: Player,
    divisions: list[str],
    states: list[str],
    max_tuition: str,
    program: AcademicProgram | None,
    detail: AcademicProgramDetail | None = None,
):
    rows = []
    offering_model = None
    if detail:
        offering_model = CollegeProgramDetail
        query = (
            select(College, CollegeProgramDetail, TeamNeed)
            .join(CollegeProgramDetail, CollegeProgramDetail.college_id == College.id)
            .outerjoin(TeamNeed, and_(
                TeamNeed.college_id == College.id,
                TeamNeed.class_year == player.grad_year,
                TeamNeed.position == player.primary_position,
            ))
            .where(
                CollegeProgramDetail.detail_program_id == detail.id,
                CollegeProgramDetail.active.is_(True),
            )
        )
    elif program:
        offering_model = CollegeProgram
        query = (
            select(College, CollegeProgram, TeamNeed)
            .join(CollegeProgram, CollegeProgram.college_id == College.id)
            .outerjoin(TeamNeed, and_(
                TeamNeed.college_id == College.id,
                TeamNeed.class_year == player.grad_year,
                TeamNeed.position == player.primary_position,
            ))
            .where(CollegeProgram.program_id == program.id, CollegeProgram.active.is_(True))
        )
    else:
        query = (select(TeamNeed).join(TeamNeed.college).options(selectinload(TeamNeed.college))
                 .where(TeamNeed.class_year == player.grad_year, TeamNeed.position == player.primary_position))
        if divisions: query = query.where(College.division.in_(divisions))
        if states: query = query.where(College.state.in_(states))
        if max_tuition:
            try: query = query.where(or_(College.tuition <= float(max_tuition), College.tuition.is_(None)))
            except ValueError: pass
        for need in db.scalars(query.order_by(College.name)).all():
            college = need.college
            score = 100 + (5 if college.tuition and college.tuition < 30000 else 0)
            rows.append((college, need, score, None))
        rows.sort(key=lambda row: (-row[2], row[0].name))
        return rows

    if divisions: query = query.where(College.division.in_(divisions))
    if states: query = query.where(College.state.in_(states))
    if max_tuition:
        try: query = query.where(or_(College.tuition <= float(max_tuition), College.tuition.is_(None)))
        except ValueError: pass
    matches_by_college: dict[int, dict] = {}
    for college, offering, need in db.execute(
        query.order_by(College.name, offering_model.credential_level)
    ).all():
        match = matches_by_college.setdefault(
            college.id, {"college": college, "need": need, "credentials": set()},
        )
        if need is not None:
            match["need"] = need
        match["credentials"].add(offering.credential_title)
    for match in matches_by_college.values():
        college = match["college"]
        need = match["need"]
        score = 100 + (25 if need else 0) + (5 if college.tuition and college.tuition < 30000 else 0)
        rows.append((college, need, score, " / ".join(sorted(match["credentials"]))))
    rows.sort(key=lambda row: (-row[2], row[0].name))
    return rows


def filter_summary(divisions: list[str], states: list[str], max_tuition: str,
                   program: AcademicProgram | None, detail: AcademicProgramDetail | None = None) -> str:
    interest = detail.name if detail else (program.name if program else "Not selected")
    label = "Specialization" if detail else "Major"
    parts = [f"{label}: {interest}",
             f"Divisions: {', '.join(divisions) if divisions else 'All'}",
             f"States: {', '.join(states) if states else 'All'}"]
    if max_tuition: parts.append(f"Maximum tuition: ${max_tuition}")
    return " | ".join(parts)


@app.get("/school-lists/{player_id}")
def school_list(player_id: int, request: Request, cip_code: str = "", cip6_code: str = "", division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    divisions = [d for d in division if d]; states = [s for s in state if s]
    selected_detail = resolve_detail(db, player, cip6_code) if cip6_code or not cip_code else None
    selected_program = resolve_program(db, player, cip_code, selected_detail)
    selected_interest = selected_detail or selected_program
    matches = school_list_matches(db, player, divisions, states, max_tuition, selected_program, selected_detail)
    program_options = '<option value="">— select a broad major —</option>' + "".join(
        f'<option value="{esc(p.cip_code)}"{" selected" if selected_program and not selected_detail and p.id == selected_program.id else ""}>{esc(p.name)}</option>'
        for p in program_choices(db))
    detail_options = '<option value="">— select a detailed field / specialization —</option>' + "".join(
        f'<option value="{esc(p.cip_code)}"{" selected" if selected_detail and p.id == selected_detail.id else ""}>{esc(p.name)} ({esc(p.cip_code)})</option>'
        for p in detail_choices(db))
    filter_form = (f'<form class="filters" method="get">'
                   f'<label class="stack">Detailed field / specialization<select name="cip6_code">{detail_options}</select></label>'
                   f'<label class="stack">Broad undergraduate major<select name="cip_code">{program_options}</select></label>'
                   f'{checkbox_dropdown("division", "Division", distinct_values(db, College.division), divisions, "divisions")}'
                   f'{checkbox_dropdown("state", "State", distinct_values(db, College.state), states, "states")}'
                   f'<label class="stack">Maximum tuition<input name="max_tuition" placeholder="e.g. 30000" value="{esc(max_tuition)}"></label>'
                   f'<button>Apply filters</button><a class="button secondary" href="/school-lists/{player.id}">Clear</a></form>')
    query_string = request.url.query
    download = f'<a class="button" href="/school-lists/{player.id}/pdf{"?" + query_string if query_string else ""}">Download PDF</a>'
    rows = []
    any_oos = False
    for college, need, score, credentials in matches:
        cell = tuition_cell(college, player)
        any_oos = any_oos or is_out_of_state(player, college)
        degree = f'{esc(selected_interest.name)} · {esc(credentials)}' if selected_interest and credentials else "—"
        need_text = f'{esc(need.position)} · {need.class_year}' if need else "No matching need recorded"
        rows.append(f'<tr><td><a href="/colleges/{college.id}?player_id={player.id}">{esc(college.name)}</a></td><td>{degree}</td><td>{esc(college.division)}</td><td>{esc(college.state)}</td>{cell}<td>{esc(college.coach_emails)}</td><td>{need_text}</td><td><span class="pill">{score}</span></td><td><a href="/email/compose?player_id={player.id}&college_id={college.id}">Email</a></td></tr>')
    empty = "No colleges report the selected specialization with these filters." if selected_detail else ("No colleges offer the selected major with these filters." if selected_program else "Select a specialization or major to make academics the primary filter, or review direct athletic-need matches below.")
    table = "".join(rows) or f'<tr><td colspan="9">{esc(empty)}</td></tr>'
    toolbar = f'<div class="toolbar">{download}<span class="muted">{len(matches)} matching college(s)</span></div>' if matches else ""
    home_note = f' Home state: <b>{esc(player.home_state)}</b>.' if norm_state(player.home_state) else ' No home state on file — showing in-state tuition.'
    academic_note = (f' Showing colleges with recent IPEDS evidence for <b>{esc(selected_detail.name)}</b>; exact class/position need adds 25 fit points.' if selected_detail else
                     (f' Showing colleges that report <b>{esc(selected_program.name)}</b>; exact class/position need adds 25 fit points.' if selected_program else
                      ' Choose a specialization or undergraduate major above to filter by academics first.'))
    oos_legend = '<p class="muted"><strong>Bold *</strong> tuition = out-of-state rate (college is outside the player\'s home state).</p>' if any_oos else ""
    body = filter_form + f'<p class="muted">School-interest list for {esc(player.name)}: {player.grad_year} {esc(player.primary_position)}.{academic_note}{home_note}</p>{oos_legend}{toolbar}<table><tr><th>College</th><th>Degree offered</th><th>Division</th><th>State</th><th>Tuition</th><th>Coach email</th><th>Matching need</th><th>Fit score</th><th></th></tr>{table}</table>'
    return page(request, f"School List · {player.name}", body, "Academic specialization or major match first; recruiting need second")


@app.get("/school-lists/{player_id}/pdf")
def school_list_download(player_id: int, cip_code: str = "", cip6_code: str = "", division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    """Return the filtered school list as a PDF attachment."""
    player = get_or_404(db, Player, player_id)
    divisions = [d for d in division if d]; states = [s for s in state if s]
    selected_detail = resolve_detail(db, player, cip6_code) if cip6_code or not cip_code else None
    selected_program = resolve_program(db, player, cip_code, selected_detail)
    selected_interest = selected_detail or selected_program
    matches = school_list_matches(db, player, divisions, states, max_tuition, selected_program, selected_detail)
    pdf = school_list_pdf(
        player, matches,
        filter_summary(divisions, states, max_tuition, selected_program, selected_detail),
        selected_interest,
    )
    safe_name = "".join(ch if ch.isalnum() else "-" for ch in player.name).strip("-") or "player"
    log(db, "school_list_pdf", f"Downloaded school list PDF for {player.name} ({len(matches)} colleges).")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Jinx-School-List-{safe_name}.pdf"'})


@app.get("/integrations")
def integrations(request: Request, db: Session = Depends(get_db)):
    connection = outlook_status(db)
    token = csrf_token(request)
    if connection.connected:
        outlook_card = (
            f'<section class="card"><h2>Microsoft Outlook</h2><p class="pill">Connected</p>'
            f'<p>Messages are sent through Microsoft Graph as <b>{esc(connection.account_email)}</b>.</p>'
            f'<form method="post" action="/integrations/outlook/disconnect">'
            f'<input type="hidden" name="csrf_token" value="{esc(token)}"><button class="button secondary">Disconnect Outlook</button></form></section>')
    elif connection.configured:
        outlook_card = (
            f'<section class="card"><h2>Microsoft Outlook</h2><p class="pill">Not connected</p>'
            f'<p>{esc(connection.detail)} The connection requests permission to send mail only; it cannot read the inbox.</p>'
            f'<a class="button" href="/integrations/outlook/connect">Connect {esc(expected_sender())}</a></section>')
    else:
        outlook_card = (
            f'<section class="card"><h2>Microsoft Outlook</h2><p class="pill">Configuration required</p>'
            f'<p>{esc(connection.detail)}</p></section>')

    colleges = db.scalars(select(College).order_by(College.name)).all()
    first = next((c.id for c in colleges if primary_email(c)), None)
    cards = "".join(
        f'<div class="card template-card"><h3>{esc(t.label)}</h3><p class="muted">{esc(t.purpose)}</p>'
        f'<p class="pill">{esc(t.attachments or "No attachment")}</p>'
        f'<div class="actions"><a class="button" href="/email/compose?template={t.key}{f"&college_id={first}" if first else ""}">Use this email</a></div></div>'
        for t in EMAIL_TEMPLATES.values())
    body = (outlook_card
            + '<section class="card"><h2>Pre-generated emails</h2><p>Select a template and recipient. '
              'Player and parent intake messages receive a one-time, expiring form link when sent.</p></section>'
            + f'<section class="cards">{cards}</section>')
    return page(request, "Integrations", body, "Microsoft Graph delivery and recruiting outreach templates")


@app.get("/integrations/outlook/connect")
def outlook_connect(request: Request):
    try:
        flow = start_authorization()
        request.session["outlook_auth_flow"] = protect_authorization_flow(flow)
    except OutlookError as exc:
        return redirect("/integrations", str(exc))
    return RedirectResponse(flow["auth_uri"], status_code=302)


@app.get("/integrations/outlook/callback")
def outlook_callback(request: Request, db: Session = Depends(get_db)):
    protected_flow = request.session.pop("outlook_auth_flow", None)
    if not protected_flow:
        return redirect("/integrations", "The Outlook connection expired. Start it again.")
    try:
        flow = unprotect_authorization_flow(str(protected_flow))
        connection = complete_authorization(db, flow, dict(request.query_params))
    except OutlookError as exc:
        db.rollback()
        return redirect("/integrations", str(exc))
    log(db, "email_connection", f"Connected Microsoft Outlook account {connection.account_email}.")
    return redirect("/integrations", f"Outlook connected as {connection.account_email}.")


@app.post("/integrations/outlook/disconnect")
async def outlook_disconnect(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    account = outlook_status(db).account_email or expected_sender()
    disconnect_outlook(db)
    log(db, "email_connection", f"Disconnected Microsoft Outlook account {account}.")
    return redirect("/integrations", "Outlook disconnected. Microsoft account consent can also be revoked in account settings.")


@app.get("/flyers/player/{player_id}")
def flyer_preview(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    body = f'<section class="card"><p class="pill">PLAYER SPOTLIGHT</p>{thumbnail_html(player)}<h2>{esc(player.name)}</h2><p><b>{esc(player.grad_year)}</b> · {esc(player.primary_position)} / {esc(player.secondary_position)}</p><div class="detail"><div><b>GPA</b>{esc(player.gpa)}</div><div><b>Exit velo</b>{esc(player.exit_velo)}</div><div><b>Home-to-first</b>{esc(player.home_to_first)}</div><div><b>Pitching velo</b>{esc(player.pitching_velo)}</div></div><p>{esc(player.notes)}</p></section><form method="post" action="/flyers/player/{player.id}/pdf"><button>Request PDF export (stub)</button></form>'
    return page(request, f"Flyer Preview · {player.name}", body, "HTML preview only; PDF/PNG generation is not configured")


@app.post("/flyers/player/{player_id}/pdf")
def flyer_pdf_stub(player_id: int, request: Request, db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id); log(db, "flyer_stub", f"PDF export requested for {player.name}; no file was generated.")
    return redirect(f"/flyers/player/{player.id}", "PDF export is a local stub. No file was generated.")


def primary_email(college: College | None) -> str:
    """First address listed in the college's coach email field."""
    raw = (getattr(college, "coach_emails", "") or "").replace(";", ",")
    return next((part.strip() for part in raw.split(",") if part.strip()), "")


def college_for_email(db: Session, email: str) -> College | None:
    """Find the college whose coach email list contains the given address."""
    target = email.strip().lower()
    if not target:
        return None
    for college in db.scalars(select(College)).all():
        raw = (college.coach_emails or "").replace(";", ",")
        if target in [part.strip().lower() for part in raw.split(",") if part.strip()]:
            return college
    return None


@app.get("/email/compose")
def email_compose(request: Request, template: str = "intro", player_id: int | None = None, college_id: int | None = None, db: Session = Depends(get_db)):
    chosen = EMAIL_TEMPLATES.get(template, EMAIL_TEMPLATES["intro"])
    connection = outlook_status(db)
    family = chosen.audience == "family"
    colleges = [c for c in db.scalars(select(College).order_by(College.name)).all() if primary_email(c)]
    players = db.scalars(select(Player).order_by(Player.name)).all()
    college = get_or_404(db, College, college_id) if college_id else (None if family else (colleges[0] if colleges else None))
    player = get_or_404(db, Player, player_id) if player_id else None
    if family and player is None:
        player = next((p for p in players if p.player_email or p.parent_email), players[0] if players else None)
    form_url = INTAKE_LINK_PLACEHOLDER if chosen.embeds_form else public_base_url(request) + "/intake"
    subject, message = render_template(chosen, college, player, form_url)
    recipient = ", ".join(a for a in [getattr(player, "player_email", None), getattr(player, "parent_email", None)] if a) if family else primary_email(college)

    template_options = "".join(f'<option value="{t.key}"{" selected" if t.key == chosen.key else ""}>{esc(t.label)}</option>' for t in EMAIL_TEMPLATES.values())
    if family:
        family_options = "".join(
            f'<option value="{p.id}"{" selected" if player and p.id == player.id else ""}>{esc(p.name)} — {esc(", ".join(a for a in [p.player_email, p.parent_email] if a) or "no email on file")}</option>'
            for p in players) or '<option value="">No players on file</option>'
        audience_picker = f'<label class="stack">Send to player / parent<select name="player_id">{family_options}</select></label>'
    else:
        recipient_options = "".join(
            f'<option value="{c.id}"{" selected" if college and c.id == college.id else ""}>{esc(primary_email(c))} — {esc(c.name)} ({esc(c.head_coach or "head coach unknown")})</option>'
            for c in colleges) or '<option value="">No coach emails on file</option>'
        player_options = '<option value="">No specific player</option>' + "".join(
            f'<option value="{p.id}"{" selected" if player and p.id == player.id else ""}>{esc(p.name)} · {p.grad_year} {esc(p.primary_position)}</option>' for p in players)
        audience_picker = (f'<label class="stack">Coach email address<select name="college_id">{recipient_options}</select></label>'
                           f'<label class="stack">Feature a player (optional)<select name="player_id">{player_options}</select></label>')
    picker = (f'<form class="filters" method="get" action="/email/compose" data-autosubmit>'
              f'<label class="stack">Pre-generated email<select name="template">{template_options}</select></label>'
              f'{audience_picker}<button>Load template</button></form>')

    if family:
        coach_line = (f'Addressed to {esc(player.name)} and family.' if player else "Add a player with an email address to address this message.")
    else:
        coach_line = f'Salutation uses {esc(college.head_coach or "an unnamed head coach")}, head coach at {esc(college.name)}.' if college else "Add a college with a coach email to resolve the salutation."
    embedded = ""
    if chosen.embeds_form:
        embedded = ('<section class="card"><h2>Intake form preview</h2>'
                    '<p class="muted">The sent message receives a unique, one-time link that expires automatically. '
                    'Email clients strip embedded forms, so recipients complete the secure web form instead.</p>'
                    '<iframe class="embed-frame" src="/intake?embed=1" title="Player and parent intake form"></iframe>'
                    '<div class="actions"><a class="button secondary" href="/intake" target="_blank" rel="noopener">Open admin preview</a>'
                    '<a class="button secondary" href="/intakes">View submissions</a></div></section>')
    attachment_note = (f'<div class="wide notice">{esc(chosen.attachments)} is a template label only and is not attached yet.</div>'
                       if chosen.attachments else "")
    hidden = (f'<input type="hidden" name="csrf_token" value="{esc(csrf_token(request))}">'
              f'<input type="hidden" name="player_id" value="{player.id if player else ""}">'
              f'<input type="hidden" name="college_id" value="{college.id if college else ""}">'
              f'<input type="hidden" name="template" value="{chosen.key}">')
    send_control = ('<button>Send with Outlook</button>' if connection.connected
                    else '<a class="button" href="/integrations">Connect Outlook to send</a>')
    compose = (f'<div class="card"><form class="grid" method="post" action="/email/send">{hidden}'
               f'<label class="wide">To<input name="recipients" value="{esc(recipient)}" required></label>'
               f'<label class="wide">Subject<input name="subject" value="{esc(subject)}" required></label>'
               f'{attachment_note}'
               f'<label class="wide">Message<textarea name="body" rows="16">{esc(message)}</textarea></label>'
               f'<div class="wide actions"><a class="button secondary" href="/integrations">Back to templates</a>{send_control}</div></form></div>')
    state_notice = (f'Connected as {esc(connection.account_email)}. Messages are saved in Outlook Sent Items.'
                    if connection.connected else 'Outlook is not connected; sending is disabled.')
    body = (f'<div class="notice">{state_notice}</div>{picker}'
            f'<p class="muted">{esc(chosen.purpose)} {coach_line}</p>{compose}{embedded}')
    return page(request, "Email Center", body, "Microsoft Graph email delivery")


@app.post("/email/send")
async def email_send(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    recipient = str(form.get("recipients", "")).strip()
    subject = str(form.get("subject", "")).strip()
    body = str(form.get("body", "")).strip()
    if not recipient or not subject or not body:
        raise HTTPException(status_code=422, detail="Recipient, subject, and message are required")
    template_key = str(form.get("template", "intro")).strip()
    chosen = EMAIL_TEMPLATES.get(template_key, EMAIL_TEMPLATES["intro"])
    matched = None if chosen.audience == "family" else college_for_email(db, recipient)
    player_id = str(form.get("player_id", "")).strip()

    if chosen.embeds_form:
        if not player_id.isdigit():
            raise HTTPException(status_code=422, detail="Choose a player before sending an intake invitation.")
        player = get_or_404(db, Player, int(player_id))
        _, invitation_token = create_invitation(db, player.id, recipient)
        invitation_url = f"{public_base_url(request)}/intake/invitation/{invitation_token}"
        body = body.replace(INTAKE_LINK_PLACEHOLDER, invitation_url)
        if invitation_url not in body:
            body += f"\n\nSecure intake form: {invitation_url}"

    target = f"/email/compose?template={template_key}"
    if matched:
        target += f"&college_id={matched.id}"
    elif chosen.audience == "family" and player_id.isdigit():
        target += f"&player_id={player_id}"

    # Persist any invitation before contacting Graph. A Graph timeout is
    # ambiguous; committing first ensures any link Microsoft accepted remains valid.
    db.commit()
    try:
        request_id = send_mail(db, recipient, subject, body)
    except OutlookError as exc:
        db.rollback()
        log(db, "email_failed", f'Failed "{chosen.label}" email to {recipient}: {exc}')
        return redirect(target, str(exc))

    coach = (matched.head_coach if matched and matched.head_coach else "family" if chosen.audience == "family" else "recipient")
    detail = f'Microsoft Graph accepted "{chosen.label}" email to {coach} at {recipient}.'
    if request_id:
        detail += f" Microsoft request {request_id}."
    db.add(ActivityLog(kind="email_sent", detail=detail))
    db.commit()
    attachment_notice = f" {chosen.attachments} was not attached." if chosen.attachments else ""
    return redirect(target, f"Email accepted by Outlook.{attachment_notice}")


@app.post("/workflow/start")
async def workflow_stub(request: Request, db: Session = Depends(get_db)):
    form = await request.form(); player_id = str(form.get("player_id", "")).strip(); college_id = str(form.get("college_id", "")).strip()
    log(db, "workflow_stub", f"Recruiting sequence requested for player {player_id or 'unknown'} and college {college_id or 'unknown'}; no automation ran.")
    return redirect("/integrations", "Workflow is a local stub. No messages were scheduled.")


# --- Player & parent intake form -------------------------------------------------
# /intake is an authenticated admin preview. Families receive one-time links under
# /intake/invitation/{token}; Azure Easy Auth exposes only those tokenized routes.

def intake_form_html(
    action: str,
    submit_label: str = "Submit my information",
    defaults: dict[str, object] | None = None,
    csrf: str = "",
) -> str:
    defaults = defaults or {}
    controls = []
    for key, label, kind, required, options in INTAKE_FIELDS:
        required_mark = " required" if required else ""
        current = "" if defaults.get(key) is None else str(defaults[key])
        if kind == "checkdrop":
            selected = [part.strip() for part in current.split(",") if part.strip()]
            controls.append(checkbox_dropdown(key, label, options or [], selected, "divisions"))
        elif kind == "select":
            opts = "".join(
                f'<option value="{esc(option)}"{" selected" if option == current else ""}>{esc(option) if option else "— select —"}</option>'
                for option in (options or []))
            controls.append(f'<label class="stack">{esc(label)}<select name="{key}"{required_mark}>{opts}</select></label>')
        elif kind == "textarea":
            controls.append(f'<label class="wide">{esc(label)}<textarea name="{key}">{esc(current)}</textarea></label>')
        else:
            step = ' step="any"' if key in {"gpa", "max_tuition"} else ""
            controls.append(f'<label>{esc(label)}<input type="{kind}" name="{key}" value="{esc(current)}"{step}{required_mark}></label>')
    hidden = f'<input type="hidden" name="csrf_token" value="{esc(csrf)}">' if csrf else ""
    return (f'<form class="grid" method="post" action="{esc(action)}">{hidden}{"".join(controls)}'
            f'<div class="wide actions"><button>{esc(submit_label)}</button></div></form>')


async def intake_payload(request: Request) -> dict:
    form = await request.form()
    data: dict[str, object | None] = {}
    for key, label, kind, required, _ in INTAKE_FIELDS:
        if kind == "checkdrop":
            picked = [str(v).strip() for v in form.getlist(key) if str(v).strip()]
            data[key] = ", ".join(picked) or None
            continue
        raw = str(form.get(key, "")).strip()
        if required and not raw:
            raise HTTPException(status_code=422, detail=f"{label} is required")
        if not raw:
            data[key] = None
        elif kind == "number":
            data[key] = int(raw) if key == "grad_year" else float(raw)
        else:
            data[key] = raw
    return data


def notify_intake_submission(request: Request, db: Session, intake: PlayerIntake) -> None:
    subject = f"New Jinx intake: {intake.player_name} ({intake.grad_year})"
    detail_url = f"{public_base_url(request)}/intakes/{intake.id}"
    message = (f"A new player and parent intake form was submitted.\n\n"
               f"Player: {intake.player_name}\nClass: {intake.grad_year}\n"
               f"Position: {intake.primary_position}\n\nReview the protected submission:\n{detail_url}\n\n"
               f"{EMAIL_SIGNATURE}")
    try:
        request_id = send_mail(db, expected_sender(), subject, message)
    except OutlookError as exc:
        db.rollback()
        log(db, "intake_notification_failed", f"Intake #{intake.id} was saved, but Outlook notification failed: {exc}")
        return
    detail = f"Microsoft Graph accepted the intake notification for #{intake.id} to {expected_sender()}."
    if request_id:
        detail += f" Microsoft request {request_id}."
    log(db, "intake_notification_sent", detail)


def invitation_defaults(player: Player) -> dict[str, object]:
    keys = ("grad_year", "primary_position", "secondary_position", "home_state", "player_email", "gpa", "sat_act",
            "height", "weight", "throwing_hand", "batting_side", "home_to_first", "exit_velo", "pop_time",
            "pitching_velo", "highlight_link")
    defaults = {key: getattr(player, key) for key in keys if getattr(player, key, None) is not None}
    defaults["player_name"] = player.name
    defaults["parent_email"] = player.parent_email or ""
    return defaults


@app.get("/intake/invitation/{token}")
def invited_intake_form(token: str, request: Request, db: Session = Depends(get_db)):
    invitation = active_invitation(db, token)
    if not invitation:
        raise HTTPException(status_code=410, detail="This intake invitation is invalid, expired, or already used.")
    player = get_or_404(db, Player, invitation.player_id)
    intro = (f"<p>Please complete the recruiting profile and college preferences for <b>{esc(player.name)}</b>. "
             "This one-time form link expires automatically after submission.</p>")
    form = intake_form_html(f"/intake/invitation/{token}", defaults=invitation_defaults(player))
    response = TEMPLATES.TemplateResponse(request, "embed.html", {"title": "Player & Parent Intake", "body": intro + form})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.post("/intake/invitation/{token}")
async def invited_intake_submit(token: str, request: Request, db: Session = Depends(get_db)):
    data = await intake_payload(request)
    invitation = claim_invitation(db, token)
    if not invitation:
        db.rollback()
        raise HTTPException(status_code=410, detail="This intake invitation is invalid, expired, or already used.")
    intake = PlayerIntake(**data)
    db.add(intake)
    db.commit()
    log(db, "intake", f"Secure intake form submitted for {intake.player_name} (class of {intake.grad_year}).")
    notify_intake_submission(request, db, intake)
    return redirect("/intake/thanks")


@app.get("/intake")
def intake_form(request: Request, embed: int = 0):
    intro = ("<p>Please share your athlete's profile and college preferences. "
             "It takes about five minutes, and anything that does not apply yet can be left blank.</p>")
    form = intake_form_html("/intake" + ("?embed=1" if embed else ""), csrf=csrf_token(request))
    if embed:
        return TEMPLATES.TemplateResponse(request, "embed.html", {"title": "Player & Parent Intake", "body": intro + form})
    return page(request, "Player & Parent Intake", f'<section class="card">{intro}{form}</section>',
                "Player profile and college preferences")


@app.post("/intake")
async def intake_submit(request: Request, embed: int = 0, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    data = await intake_payload(request)
    intake = PlayerIntake(**data)
    db.add(intake); db.commit()
    log(db, "intake", f"Intake form submitted for {intake.player_name} (class of {intake.grad_year}).")
    notify_intake_submission(request, db, intake)
    return redirect(f"/intake/thanks{'?embed=1' if embed else ''}")


@app.get("/intake/thanks")
def intake_thanks(request: Request, embed: int = 0):
    message = ("<h2>Thank you</h2><p>Your information was received. We will use it to build school lists "
               "and coach outreach for your athlete.</p>")
    response = TEMPLATES.TemplateResponse(request, "embed.html", {"title": "Submission received", "body": message})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/intakes")
def intake_list(request: Request, db: Session = Depends(get_db)):
    items = db.scalars(select(PlayerIntake).order_by(PlayerIntake.created_at.desc())).all()
    rows = "".join(
        f'<tr><td><a href="/intakes/{i.id}">{esc(i.player_name)}</a></td><td>{esc(i.grad_year)}</td>'
        f'<td>{esc(i.primary_position)}</td><td>{esc(i.intended_major)}</td>'
        f'<td>{("$" + format(i.max_tuition, ",.0f")) if i.max_tuition else "—"}</td>'
        f'<td>{esc(i.division_prefs)}</td><td>{esc(i.preferred_locations)}</td>'
        f'<td><span class="pill">{esc(i.status)}</span></td>'
        f'<td>{esc(i.created_at.strftime("%b %d, %Y"))}</td></tr>' for i in items) or '<tr><td colspan="9">No submissions yet.</td></tr>'
    body = (f'<div class="toolbar"><a class="button" href="/intake">Open the form</a>'
            f'<a class="button secondary" href="/email/compose?template=intake">Email the form to a family</a></div>'
            f'<table><tr><th>Player</th><th>Class</th><th>Position</th><th>Major</th><th>Max tuition</th>'
            f'<th>Divisions</th><th>Locations</th><th>Status</th><th>Received</th></tr>{rows}</table>')
    return page(request, "Intake Submissions", body, "Player profile and college preference responses")


@app.get("/intakes/{intake_id}")
def intake_detail(intake_id: int, request: Request, db: Session = Depends(get_db)):
    intake = get_or_404(db, PlayerIntake, intake_id)
    body = (f'<div class="actions"><form method="post" action="/intakes/{intake.id}/create-player">'
            f'<button>Create player record</button></form>'
            f'<a class="button secondary" href="/intakes">Back to submissions</a></div>')
    body += facts(intake, [("grad_year", "Graduation year"), ("primary_position", "Primary position"), ("secondary_position", "Secondary position"),
                           ("home_state", "Home state"),
                           ("player_email", "Player email"), ("parent_name", "Parent/guardian"), ("parent_email", "Parent email"), ("phone", "Phone"),
                           ("gpa", "GPA"), ("sat_act", "SAT / ACT"), ("height", "Height"), ("weight", "Weight"),
                           ("throwing_hand", "Throws"), ("batting_side", "Bats"), ("home_to_first", "Home-to-first"),
                           ("exit_velo", "Exit velocity"), ("pop_time", "Pop time"), ("pitching_velo", "Pitching velocity"),
                           ("highlight_link", "Highlight link"), ("intended_major", "Intended major"), ("max_tuition", "Maximum tuition"),
                           ("division_prefs", "Division preference"), ("preferred_locations", "Preferred locations"),
                           ("campus_setting", "Campus setting"), ("status", "Status"), ("notes", "Notes")])
    return page(request, intake.player_name, body, "Intake submission detail")


@app.post("/intakes/{intake_id}/create-player")
def intake_create_player(intake_id: int, db: Session = Depends(get_db)):
    intake = get_or_404(db, PlayerIntake, intake_id)
    carried = ["grad_year", "primary_position", "secondary_position", "home_state", "intended_major", "player_email", "parent_email", "gpa", "sat_act",
               "height", "weight", "throwing_hand", "batting_side", "home_to_first", "exit_velo", "pop_time",
               "pitching_velo", "highlight_link"]
    preferences = " | ".join(part for part in [
        f"Major: {intake.intended_major}" if intake.intended_major else "",
        f"Max tuition: ${intake.max_tuition:,.0f}" if intake.max_tuition else "",
        f"Divisions: {intake.division_prefs}" if intake.division_prefs else "",
        f"Locations: {intake.preferred_locations}" if intake.preferred_locations else "",
        f"Campus: {intake.campus_setting}" if intake.campus_setting else "",
    ] if part)
    notes = " ".join(part for part in [intake.notes or "", f"[Preferences from intake: {preferences}]" if preferences else ""] if part)
    player = Player(name=intake.player_name, notes=notes.strip() or None, **{key: getattr(intake, key) for key in carried})
    db.add(player); intake.status = "imported"; db.commit()
    log(db, "intake", f"Created player record for {player.name} from intake #{intake.id}.")
    return redirect(f"/players/{player.id}", "Player created from intake submission.")
