"""Turns scraped listings into opportunities awaiting admin review."""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit
from ..extract import extract
from ..geo import lookup_postcode, normalize_postcode
from ..listings import S
from ..models import (
    Domain,
    Employer,
    Opportunity,
    OpportunityDateWindow,
    OpportunityDocument,
    ScrapeRun,
    ScrapeSource,
    utcnow,
)
from . import REGISTRY, PoliteFetcher, ScrapedListing, ScrapeError

log = logging.getLogger(__name__)
_run_lock = threading.Lock()


def suggest_domains(db: Session, text: str) -> list[Domain]:
    """Keyword-based domain suggestion (data-driven via Domain.keywords). Admin confirms."""
    hay = f" {text.lower()} "
    hits = []
    for d in db.scalars(select(Domain).where(Domain.active.is_(True))):
        if any(k and k.lower() in hay for k in (d.keywords or [])):
            hits.append(d)
    # Prefer subdomains; keep a parent only if none of its children matched.
    child_parent_ids = {d.parent_id for d in hits if d.parent_id}
    return [d for d in hits if d.id not in child_parent_ids][:4]


def _ad_text(item: ScrapedListing) -> str:
    return "\n".join(x for x in (item.title, item.description, item.extra_text) if x)


def _employer(db: Session, name: str, item: ScrapedListing) -> Employer:
    emp = db.scalar(select(Employer).where(Employer.name == name))
    if emp is None:
        emp = Employer(name=name, postcode_code=normalize_postcode(item.postcode), city_name=item.city,
                       notes=f"Automatisch angelegt aus: {item.source_label}")
        db.add(emp)
        db.flush()
    return emp


DETECTED_NOTE = "Automatisch aus dem Anzeigentext erkannt – bitte prüfen:"


def apply_extracted(opp: Opportunity, text: str, today: date | None = None) -> list[str]:
    """Fill empty fields from the ad text. Returns German one-liners of what was filled in."""
    today = today or date.today()
    ex = extract(text, today)
    if opp.min_age is None and opp.max_age is None and (ex.min_age or ex.max_age):
        opp.min_age, opp.max_age = ex.min_age, ex.max_age
    if opp.min_grade is None and opp.max_grade is None and (ex.min_grade or ex.max_grade):
        opp.min_grade, opp.max_grade = ex.min_grade, ex.max_grade
    windows = [(a, b) for a, b in ex.windows if b >= today]
    if not opp.date_windows and windows:
        opp.date_windows = [OpportunityDateWindow(start_date=a, end_date=b) for a, b in windows]
        opp.flexible_dates, opp.flexible_dates_note = False, None
    else:
        ex.windows = []
    if opp.application_deadline is None and ex.deadline and ex.deadline >= today:
        opp.application_deadline = ex.deadline
    else:
        ex.deadline = None
    if not opp.application_email and ex.emails:
        opp.application_email = ex.emails[0][:200]
        opp.application_method = opp.application_method or "E-Mail"
    else:
        ex.emails = []
    if not opp.documents and ex.documents:
        opp.documents = [OpportunityDocument(name=d) for d in ex.documents]
    else:
        ex.documents = []
    filled = ex.summary()
    if filled:
        note = f"{DETECTED_NOTE} " + "; ".join(filled) + "."
        opp.data_quality_notes = f"{opp.data_quality_notes} {note}" if opp.data_quality_notes else note
    return filled


def upsert_listing(db: Session, source: ScrapeSource, item: ScrapedListing) -> str:
    """Returns 'created', 'updated' or 'skipped'."""
    key = f"scraper:{source.scraper}"
    now = utcnow()
    opp = db.scalar(select(Opportunity).where(Opportunity.source == key,
                                              Opportunity.external_id == item.external_id))
    if opp is not None:
        opp.last_seen_in_source_at = now
        if opp.status == S.awaiting_review:
            opp.title = item.title
            opp.description = item.description or opp.description
            opp.source_url = item.url
            if DETECTED_NOTE not in (opp.data_quality_notes or ""):
                apply_extracted(opp, _ad_text(item))
            return "updated"
        return "skipped"

    lat, lon = item.latitude, item.longitude
    pc = normalize_postcode(item.postcode)
    if (lat is None or lon is None) and pc:
        pt = lookup_postcode(db, pc)
        if pt:
            lat, lon = pt.lat, pt.lon
    notes = [f"Automatisch importiert aus „{item.source_label}“. Alle Angaben ungeprüft."]
    if item.start_date:
        notes.append(f"Starttermin laut Quelle: {item.start_date:%d.%m.%Y}.")
    notes.append("Bitte Alter, Klassenstufe, Termine und Bewerbungsweg beim Betrieb bestätigen.")
    expires = item.valid_through or ((item.published or now.date()) + timedelta(days=90))
    opp = Opportunity(
        employer_id=_employer(db, item.employer_name, item).id,
        title=item.title,
        description=item.description,
        location_label=item.city,
        street=item.street,
        address_public=False,
        postcode_code=pc,
        city_name=item.city,
        latitude=lat,
        longitude=lon,
        official_url=item.url,
        application_email=item.application_email,
        flexible_dates=True,
        flexible_dates_note="Termine laut Quelle nicht im Detail angegeben – bitte anfragen.",
        status=S.published if source.auto_publish_unverified else S.awaiting_review,
        verification_status="unverified",
        verification_source=f"Automatischer Import: {item.source_label}",
        verified_by=f"scraper:{source.scraper}",
        data_quality_notes=" ".join(notes),
        expires_at=expires,
        source=key,
        external_id=item.external_id,
        source_url=item.url,
        last_seen_in_source_at=now,
    )
    opp.domains = suggest_domains(db, f"{item.title} {item.extra_text} {item.description or ''}")
    apply_extracted(opp, _ad_text(item))
    if opp.status == S.published:
        if not opp.domains or not (opp.application_email or opp.official_url):
            opp.status = S.awaiting_review  # never publish without a domain and a contact route
        else:
            opp.published_at = now
    db.add(opp)
    db.flush()
    return "created"


def run_source(db: Session, source: ScrapeSource, fetcher: PoliteFetcher | None = None,
               actor: str = "system") -> ScrapeRun:
    run = ScrapeRun(source_id=source.id)
    db.add(run)
    db.flush()
    cls = REGISTRY.get(source.scraper)
    try:
        if cls is None:
            raise ScrapeError(f"Unbekannter Scraper „{source.scraper}“")
        scraper = cls(fetcher or PoliteFetcher())
        items = scraper.fetch(source.config or {})
        run.found = len(items)
        for item in items:
            result = upsert_listing(db, source, item)
            setattr(run, result, getattr(run, result) + 1)
        run.ok = True
        run.message = f"{run.found} gefunden, {run.created} neu, {run.updated} aktualisiert, {run.skipped} unverändert."
    except ScrapeError as exc:
        run.ok = False
        run.message = str(exc)
    except Exception as exc:  # keep the app alive on unexpected source data
        log.exception("scraper %s failed", source.scraper)
        run.ok = False
        run.message = f"Unerwarteter Fehler: {exc.__class__.__name__}"
    run.finished_at = utcnow()
    source.last_run_at = run.finished_at
    source.last_status = ("OK: " if run.ok else "Fehler: ") + (run.message or "")
    audit(db, actor, "scrape.run", "scrape_source", source.id, ok=run.ok, message=run.message)
    db.commit()
    return run


def run_all_enabled(db: Session, actor: str = "scheduler") -> list[ScrapeRun]:
    if not _run_lock.acquire(blocking=False):
        return []
    try:
        runs = []
        fetcher = PoliteFetcher()
        for src in db.scalars(select(ScrapeSource).where(ScrapeSource.enabled.is_(True))).all():
            runs.append(run_source(db, src, fetcher, actor))
        return runs
    finally:
        _run_lock.release()
