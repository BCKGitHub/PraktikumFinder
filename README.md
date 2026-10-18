# PraktikumFinder

A locally hosted web app that helps students in Germany (mainly grades 8–10) find a **Schülerpraktikum** that fits their preferred field, age, grade, school dates and a reasonable journey, and then prepare their first application in German.

The UI is in German first. Search works without an account, and the server stores no personal data about students.

> **Status: MVP.** The app starts without listings; real ones come from the import sources after an admin has reviewed them. **New here? Follow [docs/FIRST_IMPORT.md](docs/FIRST_IMPORT.md) for your first real import.** Before a public launch, have the privacy policy, terms and any legal statements reviewed by a qualified person. See `docs/PLAN.md` for the full plan, risks and backlog.

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

* 15 domains with subdomains, the federal states and the travel rules,
* about 9,100 **German postcodes** with coordinates (all of Germany, see [Travel time](#travel-time)),
* 5 BA Jobsuche scrape sources, all **disabled** until an admin turns them on.

No listings are created. To try the app with 12 **fictional** demo listings, set `SEED_DEMO_DATA=true` in `.env`; remove them later from the admin dashboard (**Demo-Angebote entfernen**) or with `python -m app.cli remove-demo`.

### Create an administrator

```bash
python -m app.cli create-admin you@example.org     # asks for a password (12+ characters)
```

Log in at <http://localhost:8000/admin>. On first login you set up two-factor authentication: scan the QR code with any TOTP app (Aegis, FreeOTP, Google or Microsoft Authenticator). Admins must use two-factor login.

### Run the tests

```bash
pytest -q          # matching/eligibility, text detection, scrapers, link checker, admin and security flows
ruff check .
```

## Quick start (Docker)

```bash
cp .env.example .env      # set SECRET_KEY
docker compose up --build
docker compose exec app python -m app.cli create-admin you@example.org
```

For production with automatic HTTPS (Caddy and Let's Encrypt), first point your domain's DNS at the server, then set these in `.env`: `APP_ENV=production`, `SECURE_COOKIES=true`, `DOMAIN=your.domain` (and keep `SEED_DEMO_DATA=false`). Then run:

```bash
docker compose --profile production up -d --build
```

The app container runs as a non-root user on a read-only filesystem. Data lives in the `app-data` volume. Migrations (Alembic) run automatically on start.

---

## Where the listings come from (scraping)

Listings come from three channels. **Imported listings are never shown as verified.** Each one lands as *"Wartet auf Prüfung"* (awaiting review) and *"Nicht bestätigt"* (unverified) until an admin checks and publishes it.

| Source | How | Notes |
|---|---|---|
| **Bundesagentur für Arbeit Jobsuche** (`ba_jobsuche`) | The REST interface behind arbeitsagentur.de/jobsuche (version 6, documented by the community at <https://jobsuche.api.bund.dev/>; not an official open-data API), queried with `angebotsart=34` (Praktikum/Trainee) and the keyword "Schülerpraktikum" per city | Only results that mention *Schüler*, *Schulpraktikum*, *Schnupperpraktikum*, *Betriebspraktikum* or *Berufsorientierung* are kept, so internships for university students don't appear. Five sources are pre-configured (Frankfurt, Offenbach, Darmstadt, Mainz, Wiesbaden, 15 km radius) and **disabled**. The BA keeps the rights to its listings and its terms restrict automated access: review them, and ask the BA if in doubt, before you enable these. |
| **Employer career pages** (`jsonld_jobposting`) | Reads schema.org `JobPosting` data (JSON-LD), which employers publish so that job search engines can read it | Fetches **only URLs that an admin explicitly adds** (an allow-list). No free crawling. |
| Employer submissions and CSV import | Form at `/arbeitgeber/angebot-einreichen`; CSV upload in the admin area | Reviewed before publication. |

**Reading the ad text.** Imported ads often state age, grade and dates only in the text. `app/extract.py` reads common German phrasings ("ab 14 Jahren", "Klassen 8 bis 10", "9./10. Klasse", "vom 02.02.2027 bis 13.02.2027", "Bewerbungsschluss: 15.12.2026", the email address and requested documents such as Lebenslauf or Zeugnis). It only fills **empty** fields, never guesses, and writes what it found into the listing's notes ("Automatisch aus dem Anzeigentext erkannt – bitte prüfen: …"). The listing stays in review. For the BA source, the description text comes from its job-details endpoint (`"fetch_details": true`, the default); if that endpoint doesn't answer, the run continues without it. Admins can apply the same detection to any listing with **Alter, Klasse und Termine aus dem Text erkennen**.

**Dead-link checker.** `app/linkcheck.py` checks the official link of every published or to-be-reviewed listing once a week (on the schedule, with **Alle Links jetzt prüfen** on the dashboard, per listing, or `python -m app.cli check-links`). Only 404 and 410 count as broken; these appear in the queue **Prüfung → Link defekt**. Sites that block bots, robots.txt refusals, network errors and server errors are shown as "nicht prüfbar", never as broken. Nothing is unpublished automatically.

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

On first start the app loads `app/seed_data/postcodes_de.csv.gz`: one centroid per German postcode (about 9,100), built from [GeoNames](https://www.geonames.org/) data (**CC BY 4.0**; the attribution is on the terms page) with `scripts/build_postcodes.py`. Postcodes for large customers (companies, banks, authorities) are left out. Postcodes that already exist, such as the hand-entered Rhein-Main ones or ones an admin edited, are kept. Set `LOAD_GERMANY_POSTCODES=false` to skip this.

To replace the hand-entered Rhein-Main values with the GeoNames ones, or to import your own CSV (columns `plz;ort;bundesland;lat;lon`, `bundesland` as code or name, `.csv` or `.csv.gz`):

```bash
python -m app.cli import-postcodes --bundled
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
  postcodes.py       bundled Germany-wide postcodes, CSV import
  extract.py         reads age, grade, dates, deadline, email, documents from German ad text
  linkcheck.py       polite dead-link checker
  listings.py        status workflow, validation, verification, expiry
  security.py        Argon2id, TOTP, sessions, CSRF, rate limits, headers
  appsupport.py      German email and cover-letter templates, checklists
  scrapers/          polite fetcher, BA Jobsuche, JSON-LD JobPosting, import runner
  routers/           public (students), employer, admin
  templates/ static/ German UI (server-rendered Jinja, small vanilla JS)
alembic/             database migrations
app/seed_data/       domains, locations, travel rules, Germany-wide postcodes (configurable data, no hard-coded city logic)
docs/PLAN.md         product plan, architecture, GDPR checklist, threat model, backlog
docs/FIRST_IMPORT.md step-by-step guide to the first real import
scripts/             entrypoint, backup, restore
tests/               pytest suite and fixtures
```

## Configuration

See `.env.example`. In production the app refuses to start with the default `SECRET_KEY` or without `SECURE_COOKIES=true`. `DATABASE_URL` also accepts PostgreSQL (`postgresql+psycopg://…`, install `psycopg[binary]`) if you outgrow SQLite.
