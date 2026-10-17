"""Employer listing submissions. Everything submitted is reviewed before publication."""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import track
from ..db import get_db
from ..geo import normalize_postcode
from ..models import ConsentRecord, Domain, EmployerSubmission, Opportunity
from ..security import enforce_rate_limit, verify_csrf
from ..web import SCHOOL_TYPES, render
from .public import active_domains

router = APIRouter(prefix="/arbeitgeber")

EMPLOYER_NOTICE_VERSION = "2026-10-v1"
EMAIL_RE = re.compile(r"^[^@\s\r\n]+@[^@\s\r\n]+\.[^@\s\r\n]+$")

TEXT_FIELDS = {
    "company_name": 200, "company_description": 2000, "employer_type": 60, "website": 500,
    "contact_name": 120, "contact_email": 200, "contact_phone": 60, "title": 250, "description": 5000,
    "street": 200, "postcode": 5, "city": 120, "flexible_note": 250, "application_method": 60,
    "application_email": 200, "official_url": 800, "languages": 250, "clothing": 2000, "documents": 2000,
    "transit": 1000, "update_note": 2000,
}


def _clean(form, key: str, limit: int) -> str:
    v = form.get(key) or ""
    return str(v).strip()[:limit]


def parse_submission(form, valid_slugs: set[str]) -> tuple[dict, list[str]]:
    data: dict = {k: _clean(form, k, n) for k, n in TEXT_FIELDS.items()}
    errors: list[str] = []
    for key in ("min_age", "max_age", "min_grade", "max_grade", "places"):
        raw = _clean(form, key, 4)
        if raw:
            if not raw.isdigit():
                errors.append(f"„{key}“ muss eine Zahl sein.")
                data[key] = None
            else:
                data[key] = int(raw)
        else:
            data[key] = None
    for key in ("start1", "end1", "start2", "end2", "deadline"):
        raw = _clean(form, key, 10)
        try:
            data[key] = date.fromisoformat(raw).isoformat() if raw else None
        except ValueError:
            errors.append("Ein Datum ist ungültig.")
            data[key] = None
    data["domains"] = [s for s in form.getlist("domains") if s in valid_slugs][:6]
    data["school_types"] = [s for s in form.getlist("school_types") if s in SCHOOL_TYPES]
    data["mandatory"] = {"ja": True, "nein": False}.get(_clean(form, "mandatory", 5))
    data["school_agreement"] = {"ja": True, "nein": False}.get(_clean(form, "school_agreement", 5))
    data["flexible"] = form.get("flexible") == "1"
    data["contact_publish"] = form.get("contact_publish") == "1"
    data["postcode"] = normalize_postcode(data["postcode"]) or ""

    if not data["company_name"]:
        errors.append("Bitte gib den Namen des Betriebs an.")
    if not EMAIL_RE.match(data["contact_email"]):
        errors.append("Bitte gib eine gültige Kontakt-E-Mail an (wird nicht veröffentlicht, außer Sie erlauben es).")
    if not data["title"]:
        errors.append("Bitte gib einen Titel für das Praktikum an.")
    if not data["postcode"]:
        errors.append("Bitte gib die Postleitzahl des Praktikumsorts an (5 Ziffern).")
    if not data["domains"]:
        errors.append("Bitte wähle mindestens einen Bereich.")
    if not (data["application_email"] or data["official_url"]):
        errors.append("Bitte gib eine Bewerbungs-E-Mail oder einen Link zur Bewerbung an.")
    if data["application_email"] and not EMAIL_RE.match(data["application_email"]):
        errors.append("Die Bewerbungs-E-Mail ist ungültig.")
    if data["official_url"] and not data["official_url"].startswith("https://"):
        errors.append("Der Link muss mit https:// beginnen.")
    for lo, hi, label in (("min_age", "max_age", "Alter"), ("min_grade", "max_grade", "Klassenstufe")):
        if data[lo] is not None and data[hi] is not None and data[hi] < data[lo]:
            errors.append(f"{label}: Der Höchstwert ist kleiner als der Mindestwert.")
    for k in ("min_age", "max_age"):
        if data[k] is not None and not 10 <= data[k] <= 25:
            errors.append("Das Alter muss zwischen 10 und 25 liegen.")
            break
    for s, e in (("start1", "end1"), ("start2", "end2")):
        if data[s] and data[e] and data[e] < data[s]:
            errors.append("Ein Zeitraum endet vor seinem Beginn.")
    if not form.get("consent"):
        errors.append("Bitte bestätigen Sie die Datenschutzhinweise für Arbeitgeber.")
    return data, errors


@router.get("")
def employer_info(request: Request):
    return render(request, "employer/info.html")


@router.get("/angebot-einreichen")
def submit_form(request: Request, angebot: int | None = None, db: Session = Depends(get_db)):
    target = db.get(Opportunity, angebot) if angebot else None
    return render(request, "employer/submit.html", domains=active_domains(db), data={}, errors=[], target=target)


@router.post("/angebot-einreichen", dependencies=[Depends(verify_csrf)])
async def submit(request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, "submit")
    form = await request.form()
    if form.get("website_url_confirm"):  # honeypot field, hidden from humans
        return RedirectResponse("/arbeitgeber/danke", status_code=303)
    valid_slugs = set(db.scalars(select(Domain.slug).where(Domain.active.is_(True))).all())
    data, errors = parse_submission(form, valid_slugs)
    target_id = form.get("target_id")
    target = db.get(Opportunity, int(target_id)) if target_id and str(target_id).isdigit() else None
    if errors:
        return render(request, "employer/submit.html", status_code=422, domains=active_domains(db),
                      data=data, errors=errors, target=target)
    consent = ConsentRecord(subject_type="employer_contact", purpose="Bearbeitung der Praktikumsmeldung",
                            notice_version=EMPLOYER_NOTICE_VERSION)
    db.add(consent)
    db.flush()
    db.add(EmployerSubmission(kind="update" if target else "new", target_opportunity_id=target.id if target else None,
                              payload=data, consent_id=consent.id))
    track(db, "employer_submission")
    db.commit()
    return RedirectResponse("/arbeitgeber/danke", status_code=303)


@router.get("/danke")
def thanks(request: Request):
    return render(request, "employer/thanks.html")

