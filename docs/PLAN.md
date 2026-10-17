# PraktikumFinder: Product & Technical Plan (MVP)

This plan covers the outputs the master prompt asks for, written to match what is actually built in this repository. Where something is a decision or an assumption, it says so. Nothing here is legal advice.

---

## 1. Product requirements (summary)

**Problem.** A student knows which field they want to explore (healthcare, IT, automotive and so on) but doesn't know where to apply for a Schülerpraktikum that fits their age, grade, school dates and a reasonable journey.

**Promise.** "Find Schülerpraktika in your preferred domain that fit your age, school dates and reasonable travel distance."

**MVP scope (built).**
* Anonymous search with transparent matching and labels that explain themselves.
* A listing detail page that answers the six key questions: can I apply, do the dates fit, am I old enough and in the right grade, can I get there, which documents do I need, and whom do I contact.
* German application helper (checklist, email, cover letter) that never invents facts.
* Saved listings and an application tracker that live only in the student's browser.
* Employer submissions with a review queue.
* An admin area covering listings, verification, domains, employers, locations and rules, scrape sources, CSV import and export, analytics and the audit log.
* Automated import from permitted sources: the BA Jobsuche API and employer JSON-LD pages.

**Non-goals for v1** (as in the brief): automatic applications, student profiles, messaging, ratings, payments, unapproved scraping, automatic legal decisions.

## 2. Assumptions and open questions

| # | Assumption (A) / open question (Q) |
|---|---|
| A1 | The scraping sources are the BA Jobsuche public API and employer pages that publish schema.org `JobPosting` data. Both publish data so that job search services can read it. Free crawling of arbitrary sites is out of scope. |
| A2 | Imported data is incomplete, often missing age, grade and dates, so imported listings start as "awaiting review / unverified" and admins fill the gaps. |
| A3 | No student accounts in the MVP. Saved items and the tracker live in the browser, which avoids processing minors' personal data on the server. |
| A4 | Travel time is a straight-line estimate, with no external routing API, and is always labelled as such. |
| A5 | Postcode centroids for Rhein-Main are approximate. National coverage needs an imported open dataset. |
| A6 | A solo founder runs it on one EU VPS. |
| Q1 | Will BA confirm that using the Jobsuche API in this product is acceptable under their terms? |
| Q2 | Which schools and teachers will pilot, and what are their exact practicum periods and rules (distance, insurance forms)? |
| Q3 | Should parents get a separate view or a printable summary? |
| Q4 | Is an English UI for parents needed in v1? |
| Q5 | Should a public-transport routing provider (for example the RMV/HAFAS API under contract) replace the estimate later? |
| Q6 | Who will legally operate the service (Impressum, controller under GDPR)? |
| Q7 | Will employers accept a "confirm your listing" email every 90 days? |

## 3. Risk register

| Risk | Likelihood | Impact | Mitigation in MVP |
|---|---|---|---|
| Stale or wrong listings mislead students | High | High | Verification status, date and source on every listing; stale queue; student reports; expiry; "Nicht bestätigt" label |
| Scraping breaches terms or robots | Medium | High | Allow-listed sources only; robots.txt enforced; rate limits; identifying user agent; sources disabled by default |
| Imported listings aimed at adults or students | High | Medium | Keyword filter for school internships; manual review before publication |
| Unsuitable or unsafe employer content | Low | High | All submissions reviewed; report button; admins can unpublish or delete |
| Personal data of minors leaks | Low | High | No student data stored server-side; no query strings in logs; no trackers |
| Admin account takeover | Low | High | Argon2id, mandatory TOTP, lockout, rate limits, audit log, no-store on admin pages |
| Travel estimate wrong | Medium | Medium | Labelled as an estimate; "school decides" message; never auto-rejects |
| Students read "Strong match" as a guarantee | Medium | Medium | Wording "Passt gut", explanation text, disclaimer on every page |
| Too few listings (cold start) | High | High | BA import, employer form, CSV import, founder outreach (see §25) |
| Solo-founder operations (backups, updates) | Medium | High | One-command Docker, encrypted backups, CI with `pip-audit` |

## 4. User personas

* **Lena, 14, Grade 9, Gymnasium in Frankfurt-Nordend.** Interested in healthcare. Has a mandatory two-week practicum in February, travels by bus and train, and wants to stay under 45 minutes from home. She has never written an application.
* **Murat, 15, Grade 9, Integrierte Gesamtschule in Offenbach.** Loves cars and wants a workshop. His parents both work, so he must find something alone.
* **Ms Weber, parent.** Wants to check that a listing is legitimate and safe and what her daughter has to send.
* **Mr Krause, training manager at a mid-sized company in Darmstadt.** Has two places a year and wants fewer, better-prepared applications.
* **Admin (the founder).** Curates and verifies listings, runs imports and answers reports.

## 5. User stories (selection)

* As a student I can select one or more domains and see only listings in them.
* As a student I can enter my age, grade and practicum dates and see whether each listing fits, and why.
* As a student I can enter my school postcode (and optionally my home postcode) and a maximum travel time and see an approximate travel time.
* As a student I still see more distant listings, marked as needing school approval.
* As a student I can open a listing and see eligibility, dates, location, deadline, documents and the contact route, with missing data clearly marked.
* As a student I can save listings and track my applications without an account.
* As a student I can generate a German email and cover letter from my own information and edit them before use.
* As a student I can report a wrong listing.
* As an employer I can submit a listing or a change, and it is reviewed before publication.
* As an admin I can review, verify (with a recorded source), publish, close, expire and delete listings, and every action is logged.
* As an admin I can manage domains, subdomains, tags, employers, postcodes, travel rules and verification periods without code changes.
* As an admin I can run imports from approved sources and review what they produce.

## 6. Acceptance criteria (selection, all covered by tests)

* A student aged 13 searching a listing with a minimum age of 14 sees it hidden, with the reason "Du musst mindestens 14 Jahre alt sein" when shown anyway.
* A listing without age information is never labelled "Passt gut".
* A listing beyond the travel limit is shown with "Schule muss zustimmen", not removed.
* Unverified or stale listings are never labelled "Passt gut".
* A listing cannot be published without a domain, a contact route (email or URL) and a location.
* Imported and employer-submitted listings are not visible publicly before admin approval.
* The `.docx` download is refused unless the student confirmed the draft.
* Every state-changing form rejects requests without a valid CSRF token.
* The admin area requires password **and** TOTP; five failed logins lock the account for 15 minutes.

## 7. Information architecture

```
/                       Startseite (domain tiles)
/suche                  Suchformular
/ergebnisse             Ergebnisse (labels + reasons)
/angebote/{id}          Angebotsdetail ─ /bewerbung (Checkliste, E-Mail, Anschreiben)
/bewerbung/checkliste   allgemeine Checkliste
/merkliste              gespeicherte Angebote (Browser)
/bewerbungen            Bewerbungs-Tracker (Browser)
/einstellungen          Meine Daten (Export/Löschen)
/arbeitgeber            Info + /angebot-einreichen
/datenschutz /nutzungsbedingungen /hilfe
/admin                  Übersicht ─ angebote, pruefung, einreichungen, meldungen, arbeitgeber,
                        bereiche, orte, quellen, import, statistik, protokoll
```

## 8. User flows

**Student:** Landing → domain tile → search form (age, grade, dates, school postcode, travel) → results (sorted, labelled) → detail ("Passt dieses Angebot zu dir?") → save **or** "Bewerbung vorbereiten" → checklist → own details → drafts → confirm → copy or download → "Zu meinen Bewerbungen" → tracker (status, follow-up, reminders).

**Employer:** /arbeitgeber → form → validation → consent → "Danke" → admin review → approved (awaiting review) → admin publishes.

**Import:** admin enables a source → run → listings created as awaiting review and unverified, with domains suggested → admin completes the data, records verification (for example a phone call) → publishes.

## 9. Wireframe descriptions

* **Results (mobile):** title "N Angebote gefunden", the sort explanation, a rule note, a collapsible "Suche anpassen" panel, then cards with the title (link), a "☆ Merken" pill, employer and place, label chips, a one-line reason, and facts (Weg, Alter, Frist). The empty state suggests broadening the search. The footer shows the count of hidden listings with a "Trotzdem anzeigen" link.
* **Detail:** header with labels and primary actions; a "Passt dieses Angebot zu dir?" checklist (✓ / ? / ✗ per criterion with reason) plus the school-approval warning; then the sections Worum geht's, Eckdaten (key/value), Wo ist das (estimate and public-transport note), Unterlagen and Bewerbungsweg, how current the data is, and a report form.
* **Application helper:** a 4-step tab bar (checklist, details, email, cover letter). Drafts carry an "Entwurf" badge. The copy and download buttons stay disabled until the confirmation box is ticked.

(The screenshots under `docs/screenshots/` show the built UI.)

## 10. Data model

The entities are implemented in `app/models.py`: FederalState, City, Postcode, School, SchoolPracticumPeriod, TravelRule, Domain (self-referencing for subdomains, plus keywords and related jobs), ActivityTag, Employer, EmployerContact, Opportunity, the opportunity-domain and opportunity-tag links, OpportunityDateWindow (date availability), OpportunityDocument (document requirement), VerificationRecord, Report, EmployerSubmission, ConsentRecord, AdminUser, AuditLog, Setting, AnalyticsDaily, ScrapeSource and ScrapeRun.

**Deliberately deferred:** User, StudentProfile, guardian consent, SavedOpportunity, ApplicationTrackerItem and Notification as server-side tables. In the MVP these live in the browser (privacy by design). They are needed only when accounts, reminders by email or cross-device sync are introduced (backlog "Later").

**Eligibility** is stored as columns on Opportunity (min/max age, min/max grade, school types, mandatory accepted) rather than a separate rule table. That is simpler and enough for the MVP; a rule table becomes useful once states or schools need custom rule logic.

## 11. Database schema

Generated by Alembic: `alembic/versions/0001_initial_schema.py`. Constraints include CHECKs for age bounds (10–25), age and grade ranges, window and period dates, and non-negative places; UNIQUE on (source, external_id) for idempotent imports; and foreign keys with cascade deletes on child tables. SQLite by default; PostgreSQL works through `DATABASE_URL`.

## 12. API design

Server-rendered HTML routes (above) plus a small JSON API:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness and DB check |
| GET | `/api/opportunities?ids=1,2` | public summaries for the browser-stored saved list and tracker (published, closed or expired only) |
| POST | `/api/event` | whitelisted anonymous counters (`saved`, `helper_completed`, `contact_click`) |
| POST | `/bewerbung/docx` | builds a .docx in memory from the student's text (CSRF, confirmation, rate limit, not stored) |

All admin mutations are POST forms with CSRF tokens. There is no public write API.

## 13. Matching logic (`app/matching.py`)

For each published listing and the student's inputs:

1. **Checks** for age, grade, school type, mandatory practicum, dates and travel. Each returns FIT, PARTIAL, UNKNOWN, NO_FIT or NOT_ASKED, together with a German reason.
   * Dates: the student's period lies inside an offered window → FIT; it overlaps → PARTIAL; flexible dates → UNKNOWN ("frag nach"); no overlap → NO_FIT.
   * Travel: the best of school and home (per the rule's reference), using estimated minutes against the student's limit (or the rule's) and an optional maximum in km. Beyond the limit → PARTIAL plus the "Schule muss zustimmen" label. **Never a rejection.**
2. **Tier**
   * *Not eligible:* any hard NO_FIT (age, grade, school type, mandatory, dates), deadline passed, expired, or full.
   * *Strong:* every criterion the student provided is FIT, the domain matches, the listing is verified and not stale.
   * *Possible:* everything else, with the open points listed.
3. **Labels:** a primary label (strong, possible or not eligible) with an explanation, plus school approval, deadline approaching (≤ 14 days, configurable), unverified, stale (> verification period), closed, expired and deadline passed.
4. **Ranking key:** eligibility tier → date fit → travel time (10-minute buckets) → domain relevance → days since verification → deadline → availability. This follows the order in the brief.
5. **Domain filter:** selecting a parent matches listings in any of its subdomains; selecting a subdomain matches it directly, and the parent-only listing counts as "related".

## 14. School-location validation logic

* Reference points: the school postcode (default) and an optional home postcode. Exact addresses are never requested.
* The rule is chosen by the federal state of the school postcode, falling back to the default rule. A rule has a reference (school, home or either), maximum minutes, an optional maximum in km, "approval required beyond", a note shown to students, and an active flag. All of it is editable in the admin area.
* Seeded rules: a default of 60 minutes from school or home; **Hessen**, with a note paraphrasing the official guidance (reasonable reachability from home or school, preferably by public transport, and the school decides on more distant placements); and **Rheinland-Pfalz**, with a placeholder note.
* Every detail page shows: "Deine Schule muss den Praktikumsplatz möglicherweise bestätigen … ob der Platz in Ordnung ist, entscheidet deine Schule."

## 15. Verification workflow

Publication status: draft → (submitted / awaiting review) → published → closed or expired, plus rejected. Transitions are whitelisted in `listings.ALLOWED_TRANSITIONS`. Publishing requires a domain, a contact route and a location.

Verification status is a separate dimension: unverified, employer-confirmed, official-source verified, admin-verified or student-reported. Recording any verification other than unverified requires a **source** (for example "Telefonat mit Frau X am 17.10.") and stores who verified it, when, and the next review date. All of this is kept in VerificationRecord history.

Queues on the admin "Prüfung" page: awaiting review, reported, overdue (older than the configurable 30, 60 or 90 days), deadline passed, practicum dates past, missing age or grade, missing dates, and no longer found in the source. Expiry runs on start, on each scheduler tick and via `python -m app.cli expire`.

## 16. GDPR and safeguarding checklist

- [x] Anonymous search; only a postcode, never an address; nothing stored server-side
- [x] Saved list, tracker and drafts kept in the browser; export (JSON) and deletion on "Meine Daten"
- [x] Personal fields in the application helper live in sessionStorage (cleared when the tab closes)
- [x] No third-party scripts, fonts, trackers or CDNs; CSP `default-src 'self'`
- [x] Analytics as aggregate daily counters without identifiers
- [x] Access logs without query strings; Caddy config drops query strings and cookies
- [x] Employer contact data stored with a ConsentRecord (notice version); published only if allowed
- [x] Inaccuracy reports store no reporter data
- [x] No student–employer chat; data reaches an employer only when the student sends their own email
- [x] Admin access controls, two-factor login, audit log
- [x] Encryption in transit (HTTPS through Caddy, HSTS) and encrypted backups (AES-256)
- [ ] Encryption at rest of the live DB: use full-disk encryption on the VPS (provider or LUKS)
- [ ] Records of processing activities (Art. 30), DPIA screening for a service aimed at minors
- [ ] Data-processing agreements with the hosting provider and backup storage
- [ ] Impressum and controller details, reviewed privacy policy and terms (drafts in the app)
- [ ] Data-breach procedure: detect (logs, alerts) → contain → assess → notify the supervisory authority within 72 h where required → inform the people affected → document
- [ ] Retention jobs (delete resolved reports after 12 months, old analytics after 24 months), which are documented but not yet automated
- [ ] Get legal advice before launch; do not claim compliance

## 17. Security threat model (STRIDE-style summary)

| Threat | Asset | Control |
|---|---|---|
| Credential stuffing or brute force on admin | Admin accounts | Argon2id; TOTP required; per-IP rate limit; lockout after 5 failures; generic errors; timing equalisation |
| Session theft | Admin session | Signed HttpOnly cookie, SameSite=Lax, Secure under HTTPS, 8 h lifetime, rotated on login |
| CSRF | All forms | Synchronizer token in session, checked on every POST |
| XSS from listings or imports | Students | Jinja autoescaping; imported HTML stripped to text; CSP without inline scripts |
| SQL injection | DB | SQLAlchemy ORM with bound parameters only |
| IDOR | Unpublished listings | Public routes only serve published, closed or expired listings |
| Malicious listing or phishing link | Students | Review before publish, `rel="noopener noreferrer nofollow"`, report button, https-only URLs from employers |
| Email header injection | Employer and listing emails | Newlines rejected in email fields; the app sends no email |
| File upload attack | Admin CSV import | .csv only, 2 MB limit, parsed as text, never stored |
| DoS | App | Per-IP rate limits on search, report, submit, docx and events; read-only container; reverse proxy |
| Scraper abuse or SSRF | Outbound | Only admin-configured sources; JSON-LD URLs must be https; size limit; no internal hosts expected (restrict egress on the server firewall) |
| Data leakage in logs | Students | No query strings, no request bodies, no application text logged |
| Vulnerable dependencies | App | Pinned requirements; `pip-audit` in CI |

## 18. Recommended technology stack

| Concern | Choice |
|---|---|
| Backend | Python 3.12, FastAPI (modular monolith) |
| Frontend | Server-rendered Jinja2 and a small vanilla JS file; no build step, works without JS for search |
| Database | SQLite (WAL) for the MVP; PostgreSQL later via `DATABASE_URL` |
| ORM and migrations | SQLAlchemy 2, Alembic |
| Auth | Argon2id (argon2-cffi) and TOTP (pyotp); session cookie (itsdangerous) |
| Search | In-process filtering and ranking over published listings (fine to roughly 10k listings); PostgreSQL full-text and PostGIS later |
| Background jobs | In-process asyncio scheduler, plus CLI commands for cron |
| Email | None in the MVP (deliberate); later an EU SMTP provider |
| File storage | None (no uploads kept) |
| Maps and geocoding | Local postcode-centroid table; no external calls |
| Testing | pytest, httpx MockTransport for scrapers, Playwright for manual end-to-end checks |
| Deployment | Docker and Compose, Caddy for automatic HTTPS |
| Monitoring | `/health` and the Docker healthcheck; structured logs; an uptime check (for example self-hosted Uptime Kuma) |
| Backup | `scripts/backup.sh` (SQLite online backup → gzip → AES-256) to off-site EU storage |

**Compared with Node (Next.js, Prisma, Postgres):** Node gives a richer client UI, but it means a build pipeline, more moving parts and client-heavy pages, which is worse for privacy and CSP. Python and FastAPI with server rendering is faster for a solo founder, has excellent scraping and data tooling, and stays simple to self-host. **Recommendation: Python and FastAPI.**

## 19. Self-hosting architecture

```
Internet ──443──> Caddy (TLS, HSTS, log filtering) ──> app container (uvicorn, non-root, read-only FS)
                                                          │
                                                          └── volume app-data: SQLite DB, backups
cron (host): backup.sh daily → rclone/rsync to EU object storage
```

| Option | Pros | Cons |
|---|---|---|
| 1. Single-server modular monolith (**recommended**) | Cheapest (~€5–10/month at a German host), one thing to patch, simple backups | Single point of failure; scale is vertical |
| 2. Separate app and DB servers | Independent scaling, DB not exposed to the web | More cost and ops, network encryption needed |
| 3. School-local on-premises | Data stays at the school | Brief lists it as a non-goal; N installations to patch; no shared listings |

## 20. Deployment instructions

1. Rent an EU or German VPS (for example Hetzner, netcup or IONOS), enable full-disk encryption if offered, and create a non-root sudo user with SSH keys only.
2. Firewall: allow only 22, 80 and 443 (`ufw`). Turn on unattended upgrades.
3. Install Docker and the Compose plugin. `git clone` the repository and `cp .env.example .env`. Set `APP_ENV=production`, a random `SECRET_KEY`, `SECURE_COOKIES=true`, `SEED_DEMO_DATA=false`, `DOMAIN` and `BACKUP_PASSPHRASE`.
4. Point DNS at the server, then run `docker compose --profile production up -d --build`.
5. Run `docker compose exec app python -m app.cli create-admin you@…` and log in to set up two-factor login.
6. Staging: a second `.env` with `APP_ENV=staging` on a subdomain, using the same images.
7. **Rollback:** tag images per release (`docker compose build && docker tag …`). Take a backup before each upgrade. To roll back, restore that image tag and, if a migration ran, the backup.

## 21. Backup and restore plan

* Daily at 02:00: `docker compose exec -T -e BACKUP_PASSPHRASE=… app sh scripts/backup.sh`, then sync `data/backups` off-site (EU), keeping 30 days locally and 90 days off-site.
* The backup uses the SQLite online-backup API (consistent while running), checks `PRAGMA integrity_check`, gzips and encrypts with AES-256 (PBKDF2, 200k iterations).
* Keep the passphrase in a password manager, **not** on the server only.
* Monthly restore test: `scripts/restore.sh` into a scratch path, start a container against it, and check `/health` plus the listing count.
* RPO 24 h and RTO about 1 h for the MVP.

## 22. Testing strategy

* **Unit:** matching and eligibility (age bounds, grades, dates covered, partial or none, flexible, travel beyond the limit, home vs school, rule reference, deadlines, stale and unverified never strong, ranking order) and geo estimates.
* **Scrapers:** recorded BA responses and a JSON-LD page; robots disallow and an unreachable robots.txt; 429 retry; idempotent import; auto-publish guard.
* **Integration (TestClient):** search filters and validation, empty state, detail page missing-data wording, 404 for unpublished listings, CSRF, report flow, docx gate, the admin login with two-factor setup, lockout, employer submission → approve → publish → visible as "Passt gut", publish blockers, form validation, verification history, CSV import and export, domain deactivation, travel-rule changes, rate limits, expiry and status transitions.
* **Manual end-to-end:** Playwright run through the student journey on mobile and desktop (no console errors under CSP).
* **CI:** ruff, pytest and pip-audit (`.github/workflows/ci.yml`).
* Next: accessibility checks with axe and screen-reader passes, and load testing of search with about 5k listings.

## 23. Sample German interface content (in the app)

* "Welcher Bereich interessiert dich?" / "Wann findet dein Praktikum statt?" / "Wo ist deine Schule?" / "Wie lange darf dein Weg dauern?"
* "Passt dieses Angebot zu dir?" / "Was musst du für die Bewerbung einreichen?"
* "Passt gut: dein Alter, deine Klasse, dein gewählter Bereich, deine Praktikumsdaten, der geschätzte Weg passen zu diesem Angebot."
* "Könnte passen: Das Mindestalter ist nicht angegeben – bitte beim Betrieb nachfragen."
* "Schule muss zustimmen: ca. 98 Min. von deiner Schule (Luftlinie 23.3 km, grobe Schätzung) – länger als deine 30 Minuten. Deine Schule entscheidet, ob ein weiter entfernter Praktikumsplatz in Ordnung ist."
* "Nicht bestätigt: Diese Angaben wurden noch nicht vom Betrieb oder von uns bestätigt. Bitte prüfe sie direkt beim Betrieb."
* "Wichtig: Deine Schule muss den Praktikumsplatz möglicherweise bestätigen."

## 24. Sample listings (fictional, in `app/seed.py`)

* **Healthcare:** "Schülerpraktikum in der Pflege", Beispiel-Klinikum Mainufer (fiktiv), Frankfurt-Sachsenhausen, ages 15–17, grades 9–10, deadline, measles-vaccination proof required. Also a GP practice in Offenbach with flexible dates (unverified).
* **IT:** "Schülerpraktikum Softwareentwicklung", Pixelwerk Software GmbH (fiktiv), Frankfurt-Nordend, two windows, online form. Also "Praktikum IT-Support und Netzwerke" in Darmstadt with a deadline approaching.
* **Automotive:** "Schülerpraktikum Kfz-Mechatronik", Autohaus Rhein-Main Beispiel (fiktiv), Offenbach, safety shoes provided. Also "Fahrzeugtechnik und E-Mobilität" in Rüsselsheim (far, so it demonstrates "Schule muss zustimmen").
* Plus public service, lab, Kita, vet (missing data shown as "Nicht angegeben"), media (full, so it shows as closed) and airport (minimum age 16).

## 25. Validation interview questions

**Students (Grade 9):** How did you find your last practicum, or how are you looking now? What was hardest? Which fields did you consider, and why? How far would you travel, and by what means? Did you know what to send? Who helped you write the application? Show us how you'd use this page; what is unclear? Would you trust a listing marked "Nicht bestätigt"?

**Parents:** How involved were you in the search? What worries you about a placement (safety, distance, legitimacy)? What would you need to see to feel comfortable?

**School career coordinators:** What are your rules on distance, dates and approval? What do students struggle with most? Would a list of verified employers help you? How do you approve placements today?

**Employers:** How do you receive practicum requests today? What makes an application good or bad? What info do students always lack? Would you keep a listing up to date if asked every 90 days? What age and grade can you accept (Jugendarbeitsschutz)?

## 26. Product metrics (instrumented as anonymous counters)

Searches started and completed; zero-result rate; domains selected; result-count buckets; listing views; saves; application-helper starts and completions; contact clicks; reports; employer submissions. From the admin data: stale-listing rate, the share of listings with complete eligibility data, and time from submission to publication. Time to the first suitable listing needs session-level data and is deliberately not tracked; measure it in user tests instead.

## 27. Prioritised backlog

**Must have (done in this MVP):** everything above.

**Should have (next):**
* Automated broken-link checker for `official_url` and the source URL (a queue already exists).
* Employer "confirm your listing" link (tokenised, no account) to refresh verification.
* Retention jobs (reports and analytics) and an admin "data retention" page.
* Language-requirement and deadline filters in the search UI; filtering by subdomain from the results page.
* National postcode dataset import as part of setup.
* Printable "parent summary" of a listing.
* Accessibility audit (axe, screen reader) and fixes.

**Could have:**
* English UI for parents and admins (i18n catalogue).
* PDF export of the cover letter (server-side, in memory).
* Public-transport routing via a contracted HAFAS or RMV API, behind the same interface.
* More scrapers: IHK and HWK practicum exchanges, if their terms allow; city job portals with feeds.
* Admin roles (editor vs admin) and multiple admins with per-user audit.

**Later:**
* Optional student accounts (email reminders, cross-device sync) with guardian consent for under-16s, a full consent workflow, notifications.
* School portal for coordinators (approvals, class lists), which needs a DPIA first.
* PostgreSQL and PostGIS, full-text search, multi-server deployment.
* Local AI drafting assistant (self-hosted model), with templates kept as the fallback.
