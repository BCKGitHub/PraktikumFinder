# PraktikumFinder

A locally hosted web app that helps students in Germany (mainly grades 8–10) find a **Schülerpraktikum** that fits their preferred field, age, grade, school dates and a reasonable journey, and then prepare their first application in German.

The UI is in German first. Search works without an account, and the server stores no personal data about students.

> **Status: MVP.** Demo listings are fictional and marked as such. Before a public launch, have the privacy policy, terms and any legal statements reviewed by a qualified person. See `docs/PLAN.md` for the full plan, risks and backlog.

---

## Quick start (local, no Docker)

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # optional; the defaults work for local use
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The first start creates `data/praktikumfinder.db` and loads:

* 15 domains with subdomains, the Rhein-Main postcodes, the federal states and the travel rules,
* 12 **fictional** demo listings (`SEED_DEMO_DATA=true`),
* 5 BA Jobsuche scrape sources, all **disabled** until an admin turns them on.

### Create an administrator

```bash
python -m app.cli create-admin you@example.org     # asks for a password (12+ characters)
```

Log in at <http://localhost:8000/admin>. On first login you set up two-factor authentication: scan the QR code with any TOTP app (Aegis, FreeOTP, Google or Microsoft Authenticator). Admins must use two-factor login.

### Run the tests

```bash
pytest -q          # 48 tests: matching/eligibility, scrapers, admin and security flows
ruff check .
```

## Quick start (Docker)

```bash
cp .env.example .env      # set SECRET_KEY
docker compose up --build
docker compose exec app python -m app.cli create-admin you@example.org
```

For production with automatic HTTPS (Caddy and Let's Encrypt), first point your domain's DNS at the server, then set these in `.env`: `APP_ENV=production`, `SECURE_COOKIES=true`, `SEED_DEMO_DATA=false`, `DOMAIN=your.domain`. Then run:

```bash
docker compose --profile production up -d --build
```

The app container runs as a non-root user on a read-only filesystem. Data lives in the `app-data` volume. Migrations (Alembic) run automatically on start.

---

## Where the listings come from (scraping)

Listings come from three channels. **Imported listings are never shown as verified.** Each one lands as *"Wartet auf Prüfung"* (awaiting review) and *"Nicht bestätigt"* (unverified) until an admin checks and publishes it.

| Source | How | Notes |
|---|---|---|
| **Bundesagentur für Arbeit Jobsuche** (`ba_jobsuche`) | The official public REST API behind arbeitsagentur.de/jobsuche (documented at <https://jobsuche.api.bund.dev/>), queried with `angebotsart=34` (Praktikum/Trainee) and the keyword "Schülerpraktikum" per city | Only results that mention *Schüler*, *Schulpraktikum*, *Schnupperpraktikum*, *Betriebspraktikum* or *Berufsorientierung* are kept, so internships for university students don't appear. Five sources are pre-configured (Frankfurt, Offenbach, Darmstadt, Mainz, Wiesbaden, 15 km radius) and **disabled**. Review the BA's terms of use before you enable them. |
| **Employer career pages** (`jsonld_jobposting`) | Reads schema.org `JobPosting` data (JSON-LD), which employers publish so that job search engines can read it | Fetches **only URLs that an admin explicitly adds** (an allow-list). No free crawling. |
| Employer submissions and CSV import | Form at `/arbeitgeber/angebot-einreichen`; CSV upload in the admin area | Reviewed before publication. |

Rules every scraper follows (`app/scrapers/base.py`):

* checks `robots.txt` before each request. A missing robots.txt allows the fetch; an unreachable one blocks it.
* waits at least `SCRAPER_MIN_DELAY_SECONDS` (default 2 s) between requests to the same host,
* sends an identifying user agent with a contact address (set `SCRAPER_USER_AGENT`),
* retries with backoff on 429 and 5xx responses and honours `Retry-After`,
* limits response size, and fetches nothing that has to be logged in to.

Run sources from **Admin → Quellen → Jetzt ausführen**, on a schedule (`SCRAPE_INTERVAL_HOURS=24`), or with `python -m app.cli run-scrapers`. Imported listings get suggested domains from keywords you can edit per domain. Listings that are no longer found in the source are flagged in the verification queue.

> These scrapers were tested against recorded sample responses (`tests/fixtures/`). The development sandbox could not reach the BA API, so do one supervised manual run before you enable the schedule.

Adding a source means writing a subclass of `Scraper` with a `fetch(config)` method that returns `ScrapedListing` objects, then registering it in `app/scrapers/__init__.py`.

---

## What's in the MVP

**Students** (no account needed)
* Landing page with domain tiles. Search by domain or subdomain, age, grade, school type, compulsory practicum, practicum dates, school postcode, optional home postcode, maximum travel time and transport mode.
* Transparent labels, each with a reason: *Passt gut* (strong match), *Könnte passen* (possible match), *Schule muss zustimmen* (school must approve), *Frist läuft bald ab* (deadline soon), *Nicht bestätigt* (unverified), *Länger nicht geprüft* (not checked recently), *Geschlossen* (closed), *Abgelaufen* (expired), *Frist abgelaufen* (deadline passed).
* Listings that are farther away are **never hidden**; they are marked "Schule muss zustimmen". Clearly ineligible listings (age, grade, dates or deadline) are hidden, with a count and a "show anyway" link.
* Detail page answers: Can I apply? Do the dates fit? Is it right for my age and grade? Can I get there? What do I need to send? Who do I contact? Missing data shows as "Nicht angegeben" and is never filled in by guesswork.
* Application helper: checklist, German email and cover-letter drafts filled in **in the browser** (missing information becomes a visible `[bitte ergänzen: …]` placeholder), editable, plus copy, `.txt` and `.docx` download after the student confirms.
* Saved list, application tracker (9 statuses, follow-up dates, reminders for deadlines, documents, school approval and parent consent), and data export or deletion. All of it lives in the browser's local storage.
* Report an inaccuracy on any listing.

**Employers:** submission and change-request form with a consent record. Everything is reviewed before publication.

**Admins** (password with Argon2id, mandatory TOTP two-factor login, lockout after failed attempts):
dashboard with to-do queues; listing create, edit, status workflow, verification with source and history, and delete; CSV import and export; domain, subdomain and activity-tag management; employers and contacts; the employer-submission review queue; inaccuracy reports; postcodes, travel rules per federal state and settings (verification period, deadline warning, travel profiles); scrape sources; privacy-preserving analytics; and an audit log of every admin action.

**Security and privacy:** CSRF tokens on every form, strict Content-Security-Policy (no inline scripts), security headers, rate limits, signed HttpOnly SameSite session cookie (Secure under HTTPS), no query strings in logs, no third-party scripts or trackers, analytics stored only as daily counters.

## Travel time

No external routing service is called, so no location data leaves the server. The app takes the straight-line distance between postcode centroids and converts it to minutes with per-mode profiles: `minutes = overhead + km × detour ÷ speed`. Admins can change the profiles under **Orte & Regeln**. The result is always labelled as a rough estimate.

The bundled postcodes cover the Rhein-Main launch region and are hand-entered approximations (about 1–2 km accuracy). To cover all of Germany, import a postcode-centroid CSV, for example one derived from OpenStreetMap or OpenGeoDB (columns `plz;ort;bundesland;lat;lon`):

```bash
python -m app.cli import-postcodes plz.csv
```

## Backups

```bash
BACKUP_PASSPHRASE=… scripts/backup.sh                       # consistent snapshot, integrity check, gzip, AES-256
BACKUP_PASSPHRASE=… scripts/restore.sh data/backups/<file>   # stop the app first
```

Run the backup daily from cron. Copy the files off the server, for example to EU object storage, and test a restore every month.

## Project layout

```
app/
  main.py            app factory, middleware, error pages, scheduler
  matching.py        eligibility, labels with reasons, ranking (pure, unit-tested)
  geo.py             distance and travel-time estimate, postcode lookup
  listings.py        status workflow, validation, verification, expiry
  security.py        Argon2id, TOTP, sessions, CSRF, rate limits, headers
  appsupport.py      German email and cover-letter templates, checklists
  scrapers/          polite fetcher, BA Jobsuche, JSON-LD JobPosting, import runner
  routers/           public (students), employer, admin
  templates/ static/ German UI (server-rendered Jinja, small vanilla JS)
alembic/             database migrations
data/seed/           domains, locations and travel rules (configurable data, no hard-coded city logic)
docs/PLAN.md         product plan, architecture, GDPR checklist, threat model, backlog
scripts/             entrypoint, backup, restore
tests/               pytest suite and fixtures
```

## Configuration

See `.env.example`. In production the app refuses to start with the default `SECRET_KEY` or without `SECURE_COOKIES=true`. `DATABASE_URL` also accepts PostgreSQL (`postgresql+psycopg://…`, install `psycopg[binary]`) if you outgrow SQLite.
