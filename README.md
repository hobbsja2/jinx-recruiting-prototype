# Jinx Recruiting Prototype

A FastAPI + Jinja2 recruiting workflow application backed by SQLite locally or Neon PostgreSQL when `DATABASE_URL` is configured.

## Included

- College, coach, player, and team-need management
- Dashboard, player metrics, school-list matching, and PDF reports
- Player/parent intake review and player-record creation
- Database-backed cropped player thumbnails that survive App Service restarts and deployments
- Microsoft Outlook OAuth with delegated `Mail.Send` only
- Real email delivery through Microsoft Graph (no mailbox-read permission)
- Optional local PDF/image attachment on every email template, streamed to Graph without application storage
- Random one-time intake invitation links stored as SHA-256 hashes
- Encrypted MSAL token-cache storage using Fernet
- Intake submission notifications sent to `jinxhsdrecruiting@outlook.com`

## Run locally (Windows)

```cmd
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
copy .env.example .env
py -m uvicorn app.main:app --reload
```

Set real local values in the git-ignored `.env`, then open `http://127.0.0.1:8000`. Without `DATABASE_URL`, the application uses `jinx_recruiting.db`. Demo seeding is disabled unless `JINX_SEED_DEMO=1` is set.

## Outlook configuration

Use a separate Microsoft identity app registration for mail delivery; do not reuse the tenant-only Easy Auth registration.

1. Allow personal Microsoft accounts (the staging registration may also allow organizational accounts).
2. Add the exact web redirect URI from `OUTLOOK_REDIRECT_URI`.
3. Add Microsoft Graph delegated permission `Mail.Send`; do not add mailbox-read permissions.
4. Create a client secret and configure all `OUTLOOK_*` settings documented in `.env.example`.
5. Generate independent strong values for `SESSION_SECRET` and `OUTLOOK_TOKEN_ENCRYPTION_KEY`.
6. Open **Integrations**, select **Connect Outlook**, sign in as `jinxhsdrecruiting@outlook.com`, and complete Microsoft consent.

The Outlook password is never handled by this application. Every email template accepts one optional PDF, PNG, JPG, or JPEG attachment up to 2 MB; the request-scoped file is sent directly to Microsoft Graph and is not retained by the application. Microsoft Graph HTTP 202 means a message was accepted for processing, not proof of final delivery. If a network timeout occurs, check Outlook Sent Items before retrying. Rotating the Fernet key invalidates the saved token cache and requires reconnecting Outlook.

## Undergraduate degree catalog

The school-interest workflow uses a normalized catalog of associate and bachelor's fields from the [U.S. Department of Education College Scorecard](https://collegescorecard.ed.gov/data/). A selected major is the primary school-list constraint; an exact recruiting class/position need adds to the fit score but does not hide academically matching colleges. College detail pages display the reported credential and four-digit CIP code.

Refresh the catalog with:

```cmd
.venv\Scripts\python.exe load_programs.py
```

The loader downloads the official current institution and field-of-study ZIPs, is idempotent, preserves source metadata, and marks prior associations inactive only after a successful institution match. Reviewed name differences are bound to explicit Scorecard UNITIDs instead of relaxing fuzzy-match confidence. The summary reports matched institutions that have no distinct field-of-study rows; those colleges remain visibly uncataloged rather than inheriting another campus's programs. `COLLEGE_SCORECARD_API_KEY` is needed only when using the optional `--api` fallback. College Scorecard field-of-study data can lag newly introduced or discontinued programs, so verify final availability with each college.

### Detailed fields, specializations, and minors

Load six-digit detailed fields with:

```cmd
.venv\Scripts\python.exe load_ipeds_programs.py
```

This importer uses the pinned 2024 NCES IPEDS Completions archive and the official CIP-2020 taxonomy. It keeps the four-digit Scorecard major as the broad filter while allowing a six-digit field to become the more specific school-list filter. A detailed field is evidence that the institution recently reported completions; it does not prove that a similarly named concentration is currently accepting students.

Minors are intentionally separate from majors and CIP data. `load_minors.py` reads `data/minors/manifest.json`, and each college entry must provide an official HTTPS catalog URL, catalog year, and explicit minor names. Empty coverage is displayed as unknown—not as proof that no minors are offered. The loader is idempotent and retains source attribution for every displayed minor.

## Intake invitation security

Family emails receive a random, expiring, one-time URL. Only its SHA-256 hash is stored. Submitting the form atomically consumes the invitation; invalid, expired, or reused links return HTTP 410. Intake data is committed before a notification attempt, so a mail failure does not discard the submission.

For hosted deployment, set `PUBLIC_BASE_URL` to the canonical HTTPS origin and expose only `/healthz`, `/static/*`, `/intake/invitation/*`, and `/intake/thanks` anonymously. Keep `/intake`, `/intakes/*`, `/email/*`, `/integrations/*`, and all other administrative routes behind Azure Easy Auth.

## Deployment notes

The existing Azure staging application runs on Linux App Service F1. Store secrets in App Service application settings, not source control. Startup creates missing tables with SQLAlchemy `create_all`; use a managed migration workflow before making non-additive schema changes in a production environment.

## Still deferred

Automatically generated attachments, QR codes, charts, unattended external-data scheduling, and scheduled recruiting sequences are not enabled.
