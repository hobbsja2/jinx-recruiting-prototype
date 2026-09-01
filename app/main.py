from __future__ import annotations

import html
import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image as PILImage, ImageOps
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .auth import (AuthUser, SESSION_DURATION_SECONDS, create_session_cookie,
                   verify_password, verify_session_cookie)
from .database import Base, DATABASE_URL, engine, get_db, sync_sqlite_columns
from .email_templates import TEMPLATES as EMAIL_TEMPLATES, render_template
from .email import send_email
from .models import ActivityLog, College, Player, PlayerIntake, TeamNeed
from .reports import school_list_pdf
from .tuition import is_out_of_state, norm_state, tuition_for
from .seed import backfill_demo_contacts, seed_demo_data

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
STATIC_ROOT = (ROOT / "static").resolve()
PHOTO_DIR = STATIC_ROOT / "uploads" / "players"
MAX_PHOTO_BYTES = 8 * 1024 * 1024
THUMB_SIZE = (480, 480)
AUTH_COOKIE_NAME = "jinx_session"
AUTH_EXEMPT_PATHS = ("/login", "/logout", "/intake", "/intake/thanks", "/static")


def get_current_user(request: Request) -> AuthUser | None:
    return verify_session_cookie(request.cookies.get(AUTH_COOKIE_NAME))


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


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    if any(path == allowed or path.startswith(allowed + "/") for allowed in AUTH_EXEMPT_PATHS):
        return await call_next(request)
    if get_current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

COLLEGE_FIELDS = [("name", "College name", "text", True), ("division", "Division", "text", True), ("conference", "Conference", "text", False), ("city", "City", "text", False), ("state", "State", "text", False), ("academic_ranking", "Academic profile", "text", False), ("tuition", "Annual tuition (in-state + housing)", "number", False), ("in_state_tuition", "In-state tuition", "number", False), ("out_of_state_tuition", "Out-of-state tuition", "number", False), ("housing_cost", "Housing cost", "number", False), ("financial_aid", "Financial aid", "textarea", False), ("head_coach", "Head coach", "text", False), ("recruiting_coordinator", "Recruiting coordinator", "text", False), ("coach_emails", "Coach emails", "text", False), ("coach_phones", "Coach phones", "text", False), ("roster_size", "Roster size", "number", False), ("scholarship_count", "Scholarships", "number", False), ("facilities_notes", "Facilities notes", "textarea", False), ("program_reputation", "Program reputation", "textarea", False), ("website_url", "Website", "url", False), ("notes", "Recruiting notes", "textarea", False)]
PLAYER_FIELDS = [("name", "Player name", "text", True), ("grad_year", "Graduation year", "number", True), ("primary_position", "Primary position", "text", True), ("secondary_position", "Secondary position", "text", False), ("home_state", "Home state", "select", False), ("player_email", "Player email", "email", False), ("parent_email", "Parent/guardian email", "email", False), ("gpa", "GPA", "number", False), ("sat_act", "SAT / ACT", "text", False), ("height", "Height", "text", False), ("weight", "Weight", "text", False), ("throwing_hand", "Throwing hand", "text", False), ("batting_side", "Batting side", "text", False), ("home_to_first", "Home-to-first", "text", False), ("exit_velo", "Exit velocity", "text", False), ("pop_time", "Pop time", "text", False), ("pitching_velo", "Pitching velocity", "text", False), ("highlight_link", "Highlight link", "url", False), ("transcript_path", "Transcript path", "text", False), ("social_handles", "Social handles", "text", False), ("notes", "Recruiting notes", "textarea", False)]
US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
             "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
             "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
             "WV", "WI", "WY"]
# Fields rendered as a single-choice dropdown, keyed by form field name.
SELECT_OPTIONS = {"home_state": US_STATES}
DIVISION_OPTIONS = ["NCAA D1", "NCAA D2", "NCAA D3", "NAIA", "JUCO"]
CAMPUS_OPTIONS = ["", "Urban", "Suburban", "Rural", "Small campus", "Large campus"]
MAJOR_GROUP_OPTIONS = ["Business", "STEM", "Health Sciences", "Education", "Liberal Arts"]
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
    return TEMPLATES.TemplateResponse(request, "page.html", {
        "title": title,
        "subtitle": subtitle,
        "body": body,
        "notice": notice or request.query_params.get("notice", ""),
        "user": get_current_user(request),
    })


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


@app.get("/login")
def login_form(request: Request):
    return TEMPLATES.TemplateResponse(request, "login.html", {
        "request": request,
        "title": "Sign in",
        "subtitle": "Admin login",
        "notice": request.query_params.get("notice", ""),
    })


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", "")).strip()
    if not verify_password(username, password):
        return redirect("/login", "Invalid username or password.")
    token = create_session_cookie(username)
    response = redirect("/admin")
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_DURATION_SECONDS,
    )
    return response


@app.get("/logout")
def logout():
    response = redirect("/login", "Signed out successfully.")
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response


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


@app.get("/debug-db")
def debug_db(request: Request, db: Session = Depends(get_db)):
    college_count = db.scalar(select(func.count()).select_from(College))
    player_count = db.scalar(select(func.count()).select_from(Player))
    team_need_count = db.scalar(select(func.count()).select_from(TeamNeed))
    return Response(f"DB={DATABASE_URL}\ncolleges={college_count}\nplayers={player_count}\nteam_needs={team_need_count}\n", media_type="text/plain")


@app.get("/colleges")
def colleges(request: Request, division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    divisions = [d for d in division if d]; states = [s for s in state if s]
    query = select(College).order_by(College.name)
    if divisions: query = query.where(College.division.in_(divisions))
    if states: query = query.where(College.state.in_(states))
    if max_tuition:
        # Blank tuition is unknown, not expensive: keep those colleges visible.
        try: query = query.where(or_(College.tuition <= float(max_tuition), College.tuition.is_(None)))
        except ValueError: pass
    items = db.scalars(query).all()
    filter_form = (f'<form class="filters" method="get">'
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
    body += facts(player, [("grad_year", "Graduation year"), ("primary_position", "Primary position"), ("secondary_position", "Secondary position"), ("home_state", "Home state"), ("player_email", "Player email"), ("parent_email", "Parent email"), ("gpa", "GPA"), ("sat_act", "SAT / ACT"), ("height", "Height"), ("weight", "Weight"), ("throwing_hand", "Throws"), ("batting_side", "Bats"), ("highlight_link", "Highlight video"), ("social_handles", "Social"), ("notes", "Notes")])
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


def major_group_for_value(value: str | None) -> str:
    """Normalize detailed academic majors to a single broader option.

    The recruiting app historically stores free-form intended majors, but for
    broad filters we want a single business selection that includes any related
    detail such as accounting, marketing, finance, economics, etc.
    """
    text = (value or "").strip().lower()
    if not text:
        return ""
    business_keywords = (
        "business", "business administration", "accounting", "accounting and finance",
        "finance", "financial", "financial planning", "marketing", "management",
        "economics", "entrepreneurship", "hospitality management", "human resources",
        "international business", "logistics", "supply chain", "administration",
    )
    if any(keyword in text for keyword in business_keywords):
        return "Business"
    stem_keywords = ("engineering", "computer science", "software", "math", "physics",
                     "cyber", "technology", "statistics", "biomedical", "data science")
    if any(keyword in text for keyword in stem_keywords):
        return "STEM"
    health_keywords = ("nursing", "health", "medicine", "pre-med", "exercise science",
                      "kinesiology", "physical therapy", "dietetics")
    if any(keyword in text for keyword in health_keywords):
        return "Health Sciences"
    education_keywords = ("education", "teaching", "elementary education", "special education")
    if any(keyword in text for keyword in education_keywords):
        return "Education"
    liberal_arts_keywords = ("history", "political science", "english", "psychology",
                             "sociology", "communications", "art", "music", "philosophy")
    if any(keyword in text for keyword in liberal_arts_keywords):
        return "Liberal Arts"
    return value.strip()


def dedupe_school_rows(rows: list[tuple]) -> list[tuple]:
    """Collapse duplicate colleges into a single row, keeping the strongest match."""
    by_college: dict[int, tuple] = {}
    for college, need, score in rows:
        college_id = getattr(college, "id", None)
        if college_id is None:
            by_college.setdefault(id(college), (college, need, score))
            continue
        current = by_college.get(college_id)
        if current is None or score > current[2]:
            by_college[college_id] = (college, need, score)
    return list(by_college.values())


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


def major_matches_selected(college: College, need: TeamNeed, selected_majors: set[str]) -> bool:
    if not selected_majors:
        return True
    searchable = " ".join(
        part or "" for part in [
            getattr(college, "notes", None),
            getattr(college, "program_reputation", None),
            getattr(need, "notes", None),
            getattr(need, "pitching_profile", None),
            getattr(need, "hitting_profile", None),
        ]
    ).lower()
    if not searchable:
        return False
    return any(major_group_for_value(value) in selected_majors for value in [searchable, *selected_majors])


def school_list_matches(db: Session, player: Player, divisions: list[str], states: list[str], max_tuition: str, majors: list[str] | None = None):
    query = (select(TeamNeed).join(TeamNeed.college).options(selectinload(TeamNeed.college))
             .where(TeamNeed.class_year == player.grad_year, TeamNeed.position == player.primary_position))
    if divisions: query = query.where(College.division.in_(divisions))
    if states: query = query.where(College.state.in_(states))
    if max_tuition:
        # Blank tuition is unknown, not expensive: keep those colleges visible.
        try: query = query.where(or_(College.tuition <= float(max_tuition), College.tuition.is_(None)))
        except ValueError: pass
    rows = []
    selected_majors = {major for major in (majors or []) if major}
    for need in db.scalars(query.order_by(College.name)).all():
        college = need.college
        if selected_majors and not major_matches_selected(college, need, selected_majors):
            continue
        score = 100 + (5 if college.tuition and college.tuition < 30000 else 0) + (3 if player.gpa and player.gpa >= 3.5 else 0)
        rows.append((college, need, score))
    rows.sort(key=lambda row: (-row[2], row[0].name))
    return dedupe_school_rows(rows)


def filter_summary(divisions: list[str], states: list[str], max_tuition: str, majors: list[str] | None = None) -> str:
    parts = [f"Divisions: {', '.join(divisions) if divisions else 'All'}", f"States: {', '.join(states) if states else 'All'}"]
    if majors:
        parts.append(f"Majors: {', '.join(majors)}")
    if max_tuition: parts.append(f"Maximum tuition: ${max_tuition}")
    return " | ".join(parts)


@app.get("/school-lists/{player_id}")
def school_list(player_id: int, request: Request, division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), major: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    player = get_or_404(db, Player, player_id)
    divisions = [d for d in division if d]; states = [s for s in state if s]; majors = [m for m in major if m]
    matches = school_list_matches(db, player, divisions, states, max_tuition, majors)
    filter_form = (f'<form class="filters" method="get">'
                   f'{checkbox_dropdown("division", "Division", distinct_values(db, College.division), divisions, "divisions")}'
                   f'{checkbox_dropdown("state", "State", distinct_values(db, College.state), states, "states")}'
                   f'{checkbox_dropdown("major", "Major", MAJOR_GROUP_OPTIONS, majors, "majors")}'
                   f'<label class="stack">Maximum tuition<input name="max_tuition" placeholder="e.g. 30000" value="{esc(max_tuition)}"></label>'
                   f'<button>Apply filters</button><a class="button secondary" href="/school-lists/{player.id}">Clear</a></form>')
    query_string = request.url.query
    download = f'<a class="button" href="/school-lists/{player.id}/pdf{"?" + query_string if query_string else ""}">Download PDF</a>'
    rows = []
    any_oos = False
    for college, need, score in matches:
        cell = tuition_cell(college, player)
        any_oos = any_oos or is_out_of_state(player, college)
        rows.append(f'<tr><td><a href="/colleges/{college.id}?player_id={player.id}">{esc(college.name)}</a></td><td>{esc(college.division)}</td><td>{esc(college.state)}</td>{cell}<td>{esc(college.coach_emails)}</td><td>{esc(need.position)} · {need.class_year}</td><td><span class="pill">{score}</span></td><td><a href="/email/compose?player_id={player.id}&college_id={college.id}">Email</a></td></tr>')
    table = "".join(rows) or '<tr><td colspan="8">No direct primary-position matches. Try adding team needs or adjusting the player profile.</td></tr>'
    toolbar = f'<div class="toolbar">{download}<span class="muted">{len(matches)} matching college(s)</span></div>' if matches else ""
    home_note = f' Home state: <b>{esc(player.home_state)}</b>.' if norm_state(player.home_state) else ' No home state on file — showing in-state tuition.'
    oos_legend = '<p class="muted"><strong>Bold *</strong> tuition = out-of-state rate (college is outside the player\'s home state).</p>' if any_oos else ""
    body = filter_form + f'<p class="muted">Ranked exact matches for {esc(player.name)}: {player.grad_year} {esc(player.primary_position)}.{home_note}</p>{oos_legend}{toolbar}<table><tr><th>College</th><th>Division</th><th>State</th><th>Tuition</th><th>Coach email</th><th>Matching need</th><th>Fit score</th><th></th></tr>{table}</table>'
    return page(request, f"School List · {player.name}", body, "Exact class-year and primary-position matches")


@app.get("/school-lists/{player_id}/pdf")
def school_list_download(player_id: int, division: list[str] = Query(default=[]), state: list[str] = Query(default=[]), major: list[str] = Query(default=[]), max_tuition: str = "", db: Session = Depends(get_db)):
    """Return the filtered school list as a PDF attachment (saved to the browser's download folder)."""
    player = get_or_404(db, Player, player_id)
    divisions = [d for d in division if d]; states = [s for s in state if s]; majors = [m for m in major if m]
    matches = school_list_matches(db, player, divisions, states, max_tuition, majors)
    pdf = school_list_pdf(player, matches, filter_summary(divisions, states, max_tuition, majors))
    safe_name = "".join(ch if ch.isalnum() else "-" for ch in player.name).strip("-") or "player"
    log(db, "school_list_pdf", f"Downloaded school list PDF for {player.name} ({len(matches)} colleges).")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Jinx-School-List-{safe_name}.pdf"'})


@app.get("/integrations")
def integrations(request: Request, db: Session = Depends(get_db)):
    colleges = db.scalars(select(College).order_by(College.name)).all()
    first = next((c.id for c in colleges if primary_email(c)), None)
    cards = "".join(
        f'<div class="card template-card"><h3>{esc(t.label)}</h3><p class="muted">{esc(t.purpose)}</p>'
        f'<p class="pill">{esc(t.attachments or "No attachment")}</p>'
        f'<div class="actions"><a class="button" href="/email/compose?template={t.key}{f"&college_id={first}" if first else ""}">Use this email</a></div></div>'
        for t in EMAIL_TEMPLATES.values())
    body = (f'<section class="card"><h2>Pre-generated coach emails</h2><p>Select a template, choose a coach email address, and the salutation is filled in with that school\'s head coach. '
            f'Sending, PDF export, and workflow automation are simulated locally: no messages, files, or credentials leave this machine.</p></section>'
            f'<section class="cards">{cards}</section>')
    return page(request, "Integrations", body, "Selectable outreach templates and local-only stubs")


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
    family = chosen.audience == "family"
    colleges = [c for c in db.scalars(select(College).order_by(College.name)).all() if primary_email(c)]
    players = db.scalars(select(Player).order_by(Player.name)).all()
    college = get_or_404(db, College, college_id) if college_id else (None if family else (colleges[0] if colleges else None))
    player = get_or_404(db, Player, player_id) if player_id else None
    if family and player is None:
        player = next((p for p in players if p.player_email or p.parent_email), players[0] if players else None)
    form_url = str(request.base_url).rstrip("/") + "/intake"
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
        embedded = ('<section class="card"><h2>Embedded fillable form</h2>'
                    '<p class="muted">This is the live form recipients complete. Gmail, Outlook, and most mail clients strip '
                    '<code>&lt;form&gt;</code> elements for security, so the email includes the link above as the reliable path. '
                    'Submissions land under Intake Forms.</p>'
                    f'<iframe class="embed-frame" src="/intake?embed=1" title="Player and parent intake form"></iframe>'
                    f'<div class="actions"><a class="button secondary" href="/intake" target="_blank" rel="noopener">Open form in a new tab</a>'
                    f'<a class="button secondary" href="/intakes">View submissions</a></div></section>')
    attachment_row = f'<label class="wide">Attachments (simulated)<input name="attachments" value="{esc(chosen.attachments)}"></label>'
    hidden = f'<input type="hidden" name="player_id" value="{player.id if player else ""}"><input type="hidden" name="college_id" value="{college.id if college else ""}"><input type="hidden" name="template" value="{chosen.key}">'
    compose = (f'<div class="card"><form class="grid" method="post" action="/email/send">{hidden}'
               f'<label class="wide">To<input name="recipients" value="{esc(recipient)}" required></label>'
               f'<label class="wide">Subject<input name="subject" value="{esc(subject)}" required></label>'
               f'{attachment_row}'
               f'<label class="wide">Message<textarea name="body" rows="16">{esc(message)}</textarea></label>'
               f'<div class="wide actions"><a class="button secondary" href="/integrations">Back to templates</a><button>Simulate send</button></div></form></div>')
    body = (f'<div class="notice">Local email stub: submitting only records simulated activity. Nothing is sent.</div>'
            f'{picker}<p class="muted">{esc(chosen.purpose)} {coach_line}</p>{compose}{embedded}')
    return page(request, "Email Center", body, "Pre-generated coach outreach templates")


@app.post("/email/send")
async def email_send_stub(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    recipient = str(form.get("recipients", "")).strip(); subject = str(form.get("subject", "")).strip()
    if not recipient or not subject: raise HTTPException(status_code=422, detail="Recipient and subject are required")
    template_key = str(form.get("template", "intro")).strip()
    chosen = EMAIL_TEMPLATES.get(template_key, EMAIL_TEMPLATES["intro"])
    attachments = str(form.get("attachments", "")).strip()
    matched = None if chosen.audience == "family" else college_for_email(db, recipient)
    # Try to resolve a player when sending to a family audience
    player = None
    player_id = str(form.get("player_id", "")).strip()
    if player_id.isdigit():
        player = db.get(Player, int(player_id))

    # Render the email content
    subj, body = render_template(chosen, matched, player, form_url="")

    # If SEND_EMAILS=1 in the environment, attempt real send via SMTP
    if os.environ.get("SEND_EMAILS", "0") == "1":
        sender = os.environ.get("SMTP_USER")
        ok = send_email(subj, body, recipient, sender=sender)
        if ok:
            detail = f'Sent "{chosen.label}" email to {recipient}: {subj}'
            if attachments: detail += f" [attachments: {attachments}]"
            log(db, "email_sent", detail)
            target = f"/email/compose?template={template_key}"
            if matched:
                target += f"&college_id={matched.id}"
            elif chosen.audience == "family" and player:
                target += f"&player_id={player.id}"
            return redirect(target, "Email sent successfully.")
        else:
            log(db, "email_error", f'Failed to send "{chosen.label}" email to {recipient}: {subj}')
            return redirect(f"/email/compose?template={template_key}", "Failed to send email; see server logs.")
    if chosen.audience == "family":
        detail = f'Simulated "{chosen.label}" email to {recipient}: {subject}'
    else:
        coach = (matched.head_coach if matched and matched.head_coach else "unmatched coach")
        detail = f'Simulated "{chosen.label}" email to {coach} at {recipient}: {subject}'
    if attachments: detail += f" [attachments: {attachments}]"
    log(db, "email_stub", detail)
    target = f"/email/compose?template={template_key}"
    if matched: target += f"&college_id={matched.id}"
    elif chosen.audience == "family" and str(form.get("player_id", "")).strip().isdigit(): target += f"&player_id={form.get('player_id')}"
    return redirect(target, "Email simulated and logged locally. Nothing was sent.")


@app.post("/workflow/start")
async def workflow_stub(request: Request, db: Session = Depends(get_db)):
    form = await request.form(); player_id = str(form.get("player_id", "")).strip(); college_id = str(form.get("college_id", "")).strip()
    log(db, "workflow_stub", f"Recruiting sequence requested for player {player_id or 'unknown'} and college {college_id or 'unknown'}; no automation ran.")
    return redirect("/integrations", "Workflow is a local stub. No messages were scheduled.")


# --- Player & parent intake form -------------------------------------------------
# NOTE: /intake and /intake/thanks are intentionally public so families can submit
# without an account. This prototype has no authentication of any kind, so anyone
# who can reach the port can post an intake. Add auth or a per-family token before
# exposing this beyond localhost.

def intake_form_html(action: str, submit_label: str = "Submit my information") -> str:
    controls = []
    for key, label, kind, required, options in INTAKE_FIELDS:
        required_mark = " required" if required else ""
        if kind == "checkdrop":
            controls.append(checkbox_dropdown(key, label, options or [], [], "divisions"))
        elif kind == "select":
            opts = "".join(f'<option value="{esc(o)}">{esc(o) if o else "— select —"}</option>' for o in (options or []))
            controls.append(f'<label class="stack">{esc(label)}<select name="{key}">{opts}</select></label>')
        elif kind == "textarea":
            controls.append(f'<label class="wide">{esc(label)}<textarea name="{key}"></textarea></label>')
        else:
            step = ' step="any"' if key in {"gpa", "max_tuition"} else ""
            controls.append(f'<label>{esc(label)}<input type="{kind}" name="{key}"{step}{required_mark}></label>')
    return (f'<form class="grid" method="post" action="{action}">{"".join(controls)}'
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


@app.get("/intake")
def intake_form(request: Request, embed: int = 0):
    intro = ("<p>Please share your athlete's profile and college preferences. "
             "It takes about five minutes, and anything that does not apply yet can be left blank.</p>")
    form = intake_form_html("/intake" + ("?embed=1" if embed else ""))
    if embed:
        return TEMPLATES.TemplateResponse(request, "embed.html", {"title": "Player & Parent Intake", "body": intro + form})
    return page(request, "Player & Parent Intake", f'<section class="card">{intro}{form}</section>',
                "Player profile and college preferences")


@app.post("/intake")
async def intake_submit(request: Request, embed: int = 0, db: Session = Depends(get_db)):
    data = await intake_payload(request)
    intake = PlayerIntake(**data)
    db.add(intake); db.commit()
    log(db, "intake", f"Intake form submitted for {intake.player_name} (class of {intake.grad_year}).")
    return redirect(f"/intake/thanks{'?embed=1' if embed else ''}")


@app.get("/intake/thanks")
def intake_thanks(request: Request, embed: int = 0):
    message = ("<h2>Thank you</h2><p>Your information was received. We will use it to build school lists "
               "and coach outreach for your athlete.</p>")
    if embed:
        return TEMPLATES.TemplateResponse(request, "embed.html", {"title": "Submission received", "body": message})
    return page(request, "Submission Received", f'<section class="card">{message}<div class="actions"><a class="button secondary" href="/intake">Submit another</a></div></section>')


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
    carried = ["grad_year", "primary_position", "secondary_position", "home_state", "player_email", "parent_email", "gpa", "sat_act",
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
