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

### Using Neon Postgres

The app can also connect to a Neon-hosted Postgres database by setting `DATABASE_URL` in `.env` or your environment. The project already supports Postgres URLs and will use SQLite only when `DATABASE_URL` is absent.

Example `.env` values:

```env
DATABASE_URL=postgresql+psycopg://<username>:<password>@ep-wandering-dream-au7b7026.neonauth.c-10.us-east-1.aws.neon.tech/neondb
NEON_AUTH_URL=https://ep-wandering-dream-au7b7026.neonauth.c-10.us-east-1.aws.neon.tech/neondb/auth
NEON_JWKS_URL=https://ep-wandering-dream-au7b7026.neonauth.c-10.us-east-1.aws.neon.tech/neondb/auth/.well-known/jwks.json
NEON_API_URL=https://ep-wandering-dream-au7b7026.apirest.c-10.us-east-1.aws.neon.tech/neondb/rest/v1
```

Restart the server after updating `.env`.

To reset the demo data, stop the server and delete `jinx_recruiting.db`, then start it again.

## Deploying to Render

This app can be deployed directly from a GitHub repo to Render using the included `render.yaml` service manifest.

Render environment variables required for auth and database access:

```env
DATABASE_URL=postgresql+psycopg://<username>:<password>@<host>/<db>
SECRET_KEY=<long-random-secret>
AUTH_USERS=jinxadmin:pbkdf2_sha256$200000$MVExs_bSjAKiKpHNWrLuCQ$AXLQiXvfEjK9MXIaeIVks3KdLLIWJ5tkC3g6r8-TJeA,jinxcoach:pbkdf2_sha256$200000$iZCvtc7-i-Oz7rPnAjRSrw$eedF1TLvNvPkPETooMHZ61j830xoE_t7eClqTLn_Cso
```

- `SECRET_KEY` should be a long random string.
- `AUTH_USERS` must contain comma-separated `username:hash` entries.
- The login page is available at `/login`.

### Adding more users

To add more users, generate a new PBKDF2 hash and append it to `AUTH_USERS`.
Run this helper from the repo root:

```cmd
python generate_auth_hash.py <username>
```

Then add the output line into the `AUTH_USERS` value.

## Deliberately deferred

Gmail OAuth and sending, WeasyPrint/PDF/PNG generation, uploads, QR codes, charts, external college data, and scheduled recruiting sequences are not enabled in this local prototype. Their UI routes display or record explicit safe stubs.
