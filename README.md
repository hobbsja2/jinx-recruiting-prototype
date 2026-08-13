# Jinx Recruiting Prototype

A FastAPI + Jinja2 recruiting workflow application backed by SQLite locally or Neon PostgreSQL when `DATABASE_URL` is configured.

## Included

- College, coach, player, and team-need management
- Dashboard, player metrics, school-list matching, and PDF reports
- Player/parent intake review and player-record creation
- Microsoft Outlook OAuth with delegated `Mail.Send` only
- Real email delivery through Microsoft Graph (no mailbox-read permission)
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

The Outlook password is never handled by this application. Microsoft Graph HTTP 202 means a message was accepted for processing, not proof of final delivery. If a network timeout occurs, check Outlook Sent Items before retrying. Rotating the Fernet key invalidates the saved token cache and requires reconnecting Outlook.

## Intake invitation security

Family emails receive a random, expiring, one-time URL. Only its SHA-256 hash is stored. Submitting the form atomically consumes the invitation; invalid, expired, or reused links return HTTP 410. Intake data is committed before a notification attempt, so a mail failure does not discard the submission.

For hosted deployment, set `PUBLIC_BASE_URL` to the canonical HTTPS origin and expose only `/healthz`, `/static/*`, `/intake/invitation/*`, and `/intake/thanks` anonymously. Keep `/intake`, `/intakes/*`, `/email/*`, `/integrations/*`, and all other administrative routes behind Azure Easy Auth.

## Deployment notes

The existing Azure staging application runs on Linux App Service F1. Store secrets in App Service application settings, not source control. Startup creates missing tables with SQLAlchemy `create_all`; use a managed migration workflow before making non-additive schema changes in a production environment.

## Still deferred

Automatic file attachments, QR codes, charts, external college-data synchronization, and scheduled recruiting sequences are not enabled.
