# Jinx Recruiting Prototype

A local FastAPI + SQLite recruiting workflow prototype built from the supplied application blueprints.

## Included

- College, player, and team-need CRUD screens
- Dashboard, player metrics, and filtered team-needs overview
- School-list generator that matches a player's **primary position** and graduation year to active team needs
- Fictional, idempotent demonstration data seeded on first startup
- HTML player-spotlight flyer preview
- Local-only email, PDF-export, and workflow-automation stubs that record activity but never send mail, generate files, or use credentials

## Run locally (Windows)

From this folder in a terminal:

```cmd
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The SQLite database (`jinx_recruiting.db`) is created in the working directory and is seeded only when it has no colleges.

To reset the demo data, stop the server and delete `jinx_recruiting.db`, then start it again.

## Deliberately deferred

Gmail OAuth and sending, WeasyPrint/PDF/PNG generation, uploads, QR codes, charts, external college data, and scheduled recruiting sequences are not enabled in this local prototype. Their UI routes display or record explicit safe stubs.
