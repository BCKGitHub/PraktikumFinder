"""Administrator area. Every state-changing action is audit-logged and CSRF-protected."""
from __future__ import annotations

import csv
import io
import json
import threading
from datetime import date, timedelta

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from markupsafe import Markup
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..audit import audit
from ..db import SessionLocal, get_db
from ..geo import normalize_postcode
from ..linkcheck import LINK_LABELS, check_links, check_one
from ..listings import (
    STATUS_LABELS,
    VERIFICATION_LABELS,
    S,
    TransitionError,
    change_status,
    delete_opportunity,
    publish_blockers,
    record_verification,
    remove_demo,
    validate_opportunity,
)
from ..models import (
    ActivityTag,
    AdminUser,
    AnalyticsDaily,
    AuditLog,
    City,
    Domain,
    Employer,
    EmployerContact,
    EmployerSubmission,
    FederalState,
    Opportunity,
    OpportunityDateWindow,
    OpportunityDocument,
    Postcode,
    Report,
    ScrapeSource,
    TravelRule,
    opportunity_domains,
    utcnow,
)
from ..scrapers import REGISTRY, PoliteFetcher
from ..scrapers.runner import apply_extracted, run_source
from ..security import (
    enforce_rate_limit,
    is_locked,
    login_admin_session,
    new_totp_secret,
    register_failed_login,
    require_admin,
    totp_uri,
    verify_csrf,
    verify_password,
    verify_totp,
)
from ..settings_store import get_setting, set_setting
from ..web import EMPLOYER_TYPES, SCHOOL_TYPES, render
from .public import REPORT_REASONS

router = APIRouter(prefix="/admin")


def admin_user(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    return require_admin(request, db)


def redirect(url: str, msg: str | None = None, request: Request | None = None) -> RedirectResponse:
    if msg and request is not None:
        request.session["flash"] = msg
    return RedirectResponse(url, status_code=303)


# --------------------------------------------------------------------------
# Login & MFA
# --------------------------------------------------------------------------


@router.get("/login")
def login_form(request: Request):
    return render(request, "admin/login.html", error=None)


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    enforce_rate_limit(request, "login")
    admin = db.scalar(select(AdminUser).where(AdminUser.email == email.strip().lower()))
    generic = "E-Mail oder Passwort ist falsch."
    if admin is None or not admin.active:
        verify_password("$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$ZmFrZQ", password)  # equalise timing
        return render(request, "admin/login.html", status_code=401, error=generic)
    if is_locked(admin):
        return render(request, "admin/login.html", status_code=429,
                      error="Zu viele Fehlversuche. Das Konto ist vorübergehend gesperrt.")
    if not verify_password(admin.password_hash, password):
        register_failed_login(db, admin)
        audit(db, admin.email, "admin.login_failed")
        db.commit()
        return render(request, "admin/login.html", status_code=401, error=generic)
    admin.failed_logins = 0
    db.commit()
    login_admin_session(request, admin, mfa_ok=False)
    return redirect("/admin/mfa" if admin.totp_enabled else "/admin/mfa/einrichten")


def _pending_admin(request: Request, db: Session) -> AdminUser:
    admin_id = request.session.get("admin_id")
    admin = db.get(AdminUser, admin_id) if admin_id else None
    if admin is None:
        raise HTTPException(303, headers={"Location": "/admin/login"})
    return admin


@router.get("/mfa")
def mfa_form(request: Request, db: Session = Depends(get_db)):
    _pending_admin(request, db)
    return render(request, "admin/mfa.html", error=None)


@router.post("/mfa", dependencies=[Depends(verify_csrf)])
def mfa(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    enforce_rate_limit(request, "login")
    admin = _pending_admin(request, db)
    if not admin.totp_enabled or not verify_totp(admin.totp_secret or "", code):
        register_failed_login(db, admin)
        return render(request, "admin/mfa.html", status_code=401, error="Der Code ist falsch oder abgelaufen.")
    login_admin_session(request, admin, mfa_ok=True)
    admin.last_login_at = utcnow()
    audit(db, admin.email, "admin.login")
    db.commit()
    return redirect("/admin")


@router.get("/mfa/einrichten")
def mfa_setup_form(request: Request, db: Session = Depends(get_db)):
    admin = _pending_admin(request, db)
    if admin.totp_enabled:
        return redirect("/admin/mfa")
    secret = request.session.get("totp_setup") or new_totp_secret()
    request.session["totp_setup"] = secret
    uri = totp_uri(secret, admin.email)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    svg = img.to_string(encoding="unicode")
    return render(request, "admin/mfa_setup.html", secret=secret, qr_svg=Markup(svg), error=None)


@router.post("/mfa/einrichten", dependencies=[Depends(verify_csrf)])
def mfa_setup(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    admin = _pending_admin(request, db)
    secret = request.session.get("totp_setup")
    if not secret or not verify_totp(secret, code):
        return render(request, "admin/mfa_setup.html", status_code=400, secret=secret, qr_svg="",
                      error="Der Code stimmt nicht. Bitte erneut versuchen.")
    admin.totp_secret = secret
    admin.totp_enabled = True
    request.session.pop("totp_setup", None)
    login_admin_session(request, admin, mfa_ok=True)
    audit(db, admin.email, "admin.mfa_enabled")
    db.commit()
    return redirect("/admin")


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):
    request.session.clear()
    return redirect("/admin/login")


# --------------------------------------------------------------------------
# Dashboard & verification queues
# --------------------------------------------------------------------------


def verification_queues(db: Session) -> dict[str, tuple[str, list[Opportunity]]]:
    today = date.today()
    period = int(get_setting(db, "verification_period_days"))
    not_seen_days = int(get_setting(db, "scraper_not_seen_days"))
    opts = (selectinload(Opportunity.employer), selectinload(Opportunity.date_windows), selectinload(Opportunity.reports))
    live = db.scalars(select(Opportunity).where(Opportunity.status == S.published).options(*opts)).all()
    stale_cut = today - timedelta(days=period)
    q = {
        "review": ("Warten auf Prüfung", db.scalars(select(Opportunity).where(
            Opportunity.status.in_([S.awaiting_review, S.submitted])).options(*opts)).all()),
        "reported": ("Fehler gemeldet", [o for o in db.scalars(select(Opportunity).join(Report).where(
            Report.resolved_at.is_(None)).options(*opts)).unique().all()]),
        "stale": ("Prüfung überfällig", [o for o in live if o.last_verified_at is None or o.last_verified_at < stale_cut]),
        "deadline_passed": ("Bewerbungsfrist vorbei", [o for o in live if o.application_deadline and o.application_deadline < today]),
        "dates_past": ("Praktikumstermine vorbei", [o for o in live if o.date_windows and all(w.end_date < today for w in o.date_windows)]),
        "missing_age": ("Alter/Klasse fehlt", [o for o in live if o.min_age is None and o.min_grade is None]),
        "missing_dates": ("Termine fehlen", [o for o in live if not o.date_windows and not o.flexible_dates]),
        "broken_link": ("Link defekt", db.scalars(select(Opportunity).where(
            Opportunity.status.in_([S.published, S.awaiting_review]), Opportunity.link_status == "broken").options(*opts)).all()),
        "not_seen": ("Nicht mehr in der Quelle gefunden", [
            o for o in live if o.source.startswith("scraper:") and o.last_seen_in_source_at
            and o.last_seen_in_source_at.date() < today - timedelta(days=not_seen_days)]),
    }
    return q


@router.get("")
def dashboard(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    counts = {s: db.scalar(select(func.count(Opportunity.id)).where(Opportunity.status == s)) for s in STATUS_LABELS}
    queues = verification_queues(db)
    pending_subs = db.scalar(select(func.count(EmployerSubmission.id)).where(EmployerSubmission.status == "pending"))
    open_reports = db.scalar(select(func.count(Report.id)).where(Report.resolved_at.is_(None)))
    sources = db.scalars(select(ScrapeSource)).all()
    demo_count = db.scalar(select(func.count(Opportunity.id)).where(Opportunity.is_demo.is_(True)))
    return render(request, "admin/dashboard.html", admin=admin, counts=counts, queues=queues,
                  pending_subs=pending_subs, open_reports=open_reports, sources=sources, demo_count=demo_count)


@router.get("/pruefung")
def verification_page(request: Request, queue: str = "review", admin: AdminUser = Depends(admin_user),
                      db: Session = Depends(get_db)):
    queues = verification_queues(db)
    if queue not in queues:
        queue = "review"
    return render(request, "admin/verification.html", admin=admin, queues=queues, current=queue,
                  items=queues[queue][1], period=get_setting(db, "verification_period_days"))


# --------------------------------------------------------------------------
# Opportunities
# --------------------------------------------------------------------------


@router.get("/angebote")
def opportunities(request: Request, status: str = "", verification: str = "", source: str = "", q: str = "",
                  admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    stmt = select(Opportunity).options(selectinload(Opportunity.employer), selectinload(Opportunity.domains)) \
        .order_by(Opportunity.updated_at.desc())
    if status:
        stmt = stmt.where(Opportunity.status == status)
    if verification:
        stmt = stmt.where(Opportunity.verification_status == verification)
    if source:
        stmt = stmt.where(Opportunity.source == source)
    if q:
        like = f"%{q}%"
        stmt = stmt.join(Employer).where(or_(Opportunity.title.ilike(like), Employer.name.ilike(like)))
    items = db.scalars(stmt.limit(500)).all()
    sources = db.scalars(select(Opportunity.source).distinct()).all()
    return render(request, "admin/opportunities.html", admin=admin, items=items, sources=sources,
                  f={"status": status, "verification": verification, "source": source, "q": q})


def _opp_form_ctx(db: Session) -> dict:
    return dict(
        employers=db.scalars(select(Employer).order_by(Employer.name)).all(),
        domains=db.scalars(select(Domain).where(Domain.parent_id.is_(None)).options(selectinload(Domain.children))
                           .order_by(Domain.position)).all(),
        tags=db.scalars(select(ActivityTag).order_by(ActivityTag.name)).all(),
    )


def _int_or_none(v) -> int | None:
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def _date_or_none(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _tri(v) -> bool | None:
    return {"ja": True, "nein": False}.get(v or "")


def apply_opportunity_form(db: Session, opp: Opportunity, form) -> list[str]:
    errors: list[str] = []
    emp_id = _int_or_none(form.get("employer_id"))
    if not emp_id or db.get(Employer, emp_id) is None:
        errors.append("Bitte einen Betrieb auswählen.")
    else:
        opp.employer_id = emp_id
    s = lambda k, n=250: (form.get(k) or "").strip()[:n] or None  # noqa: E731
    opp.title = s("title") or ""
    opp.description = s("description", 5000)
    opp.location_label = s("location_label")
    opp.street = s("street", 200)
    opp.address_public = form.get("address_public") == "1"
    opp.postcode_code = normalize_postcode(form.get("postcode_code"))
    opp.city_name = s("city_name", 120)
    lat, lon = (form.get("latitude") or "").strip(), (form.get("longitude") or "").strip()
    try:
        opp.latitude = float(lat) if lat else None
        opp.longitude = float(lon) if lon else None
    except ValueError:
        errors.append("Koordinaten sind ungültig.")
    if opp.latitude is None and opp.postcode_code:
        pc = db.scalar(select(Postcode).where(Postcode.code == opp.postcode_code))
        if pc:
            opp.latitude, opp.longitude = pc.latitude, pc.longitude
    opp.travel_information = s("travel_information", 1000)
    for key in ("min_age", "max_age", "min_grade", "max_grade", "number_of_places"):
        setattr(opp, key, _int_or_none(form.get(key)))
    opp.accepted_school_types = [t for t in form.getlist("accepted_school_types") if t in SCHOOL_TYPES]
    opp.mandatory_practicum_supported = _tri(form.get("mandatory_practicum_supported"))
    opp.school_agreement_required = _tri(form.get("school_agreement_required"))
    opp.language_requirements = s("language_requirements")
    opp.clothing_safety = s("clothing_safety", 2000)
    opp.school_approval_note = s("school_approval_note", 2000)
    opp.flexible_dates = form.get("flexible_dates") == "1"
    opp.flexible_dates_note = s("flexible_dates_note")
    opp.application_deadline = _date_or_none(form.get("application_deadline"))
    opp.expires_at = _date_or_none(form.get("expires_at"))
    opp.availability = form.get("availability") if form.get("availability") in ("unknown", "open", "few_places", "full") else "unknown"
    opp.application_method = s("application_method", 60)
    opp.application_email = s("application_email", 200)
    opp.official_url = s("official_url", 800)
    opp.data_quality_notes = s("data_quality_notes", 3000)

    domain_ids = [int(x) for x in form.getlist("domain_ids") if str(x).isdigit()]
    opp.domains = db.scalars(select(Domain).where(Domain.id.in_(domain_ids))).all() if domain_ids else []
    tag_ids = [int(x) for x in form.getlist("tag_ids") if str(x).isdigit()]
    opp.tags = db.scalars(select(ActivityTag).where(ActivityTag.id.in_(tag_ids))).all() if tag_ids else []

    windows = []
    for i in range(1, 5):
        a, b = _date_or_none(form.get(f"window_start_{i}")), _date_or_none(form.get(f"window_end_{i}"))
        if a and b:
            windows.append((a, b))
        elif a or b:
            errors.append(f"Zeitraum {i}: bitte Beginn und Ende angeben.")
    opp.date_windows.clear()
    for a, b in windows:
        if b < a:
            errors.append("Ein Zeitraum endet vor seinem Beginn.")
            continue
        opp.date_windows.append(OpportunityDateWindow(start_date=a, end_date=b))
    docs = [line.strip()[:200] for line in (form.get("documents") or "").splitlines() if line.strip()]
    opp.documents.clear()
    for d in docs[:20]:
        opp.documents.append(OpportunityDocument(name=d))
    if not opp.title:
        errors.append("Titel fehlt.")
    errors.extend(validate_opportunity(opp))
    return errors


@router.get("/angebote/neu")
def opportunity_new(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    return render(request, "admin/opportunity_form.html", admin=admin, opp=None, errors=[], **_opp_form_ctx(db))


@router.post("/angebote/neu", dependencies=[Depends(verify_csrf)])
async def opportunity_create(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    form = await request.form()
    opp = Opportunity(status=S.draft, source="manual", verification_status="unverified")
    errors = apply_opportunity_form(db, opp, form)
    if errors:
        db.rollback()
        return render(request, "admin/opportunity_form.html", status_code=422, admin=admin, opp=opp,
                      errors=errors, **_opp_form_ctx(db))
    db.add(opp)
    db.flush()
    audit(db, admin.email, "opportunity.create", "opportunity", opp.id, title=opp.title)
    db.commit()
    return redirect(f"/admin/angebote/{opp.id}", "Angebot angelegt (Entwurf).", request)


def _get_opp(db: Session, opp_id: int) -> Opportunity:
    opp = db.get(Opportunity, opp_id)
    if opp is None:
        raise HTTPException(404, "Angebot nicht gefunden")
    return opp


@router.get("/angebote/{opp_id}")
def opportunity_edit(opp_id: int, request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    return render(request, "admin/opportunity_form.html", admin=admin, opp=opp, errors=[],
                  blockers=publish_blockers(opp), flash=request.session.pop("flash", None), **_opp_form_ctx(db))


@router.post("/angebote/{opp_id}", dependencies=[Depends(verify_csrf)])
async def opportunity_update(opp_id: int, request: Request, admin: AdminUser = Depends(admin_user),
                             db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    form = await request.form()
    errors = apply_opportunity_form(db, opp, form)
    if errors:
        db.rollback()
        opp = _get_opp(db, opp_id)
        return render(request, "admin/opportunity_form.html", status_code=422, admin=admin, opp=opp,
                      errors=errors, blockers=publish_blockers(opp), **_opp_form_ctx(db))
    audit(db, admin.email, "opportunity.update", "opportunity", opp.id)
    db.commit()
    return redirect(f"/admin/angebote/{opp.id}", "Gespeichert.", request)


@router.post("/angebote/{opp_id}/status", dependencies=[Depends(verify_csrf)])
def opportunity_status(opp_id: int, request: Request, status: str = Form(...), note: str = Form(""),
                       admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    if status not in STATUS_LABELS:
        raise HTTPException(400, "Unbekannter Status")
    try:
        change_status(db, opp, status, admin.email, note or None)
    except TransitionError as exc:
        return redirect(f"/admin/angebote/{opp.id}", str(exc), request)
    db.commit()
    return redirect(request.headers.get("referer") or f"/admin/angebote/{opp.id}",
                    f"Status: {STATUS_LABELS[status]}", request)


@router.post("/angebote/{opp_id}/pruefen", dependencies=[Depends(verify_csrf)])
def opportunity_verify(opp_id: int, request: Request, verification_status: str = Form(...),
                       source: str = Form(""), notes: str = Form(""),
                       admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    if verification_status not in VERIFICATION_LABELS:
        raise HTTPException(400, "Unbekannter Prüfstatus")
    if verification_status not in ("unverified", "student_reported") and not source.strip():
        return redirect(f"/admin/angebote/{opp.id}#pruefung", "Bitte die Prüfquelle angeben (z. B. Telefonat am …, E-Mail vom …).", request)
    record_verification(db, opp, verification_status, source.strip()[:300] or None, admin.email, notes.strip()[:2000] or None)
    db.commit()
    return redirect(f"/admin/angebote/{opp.id}", "Prüfung gespeichert.", request)


@router.post("/angebote/{opp_id}/loeschen", dependencies=[Depends(verify_csrf)])
def opportunity_delete(opp_id: int, request: Request, confirm: str = Form(""),
                       admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    if confirm != "LÖSCHEN":
        return redirect(f"/admin/angebote/{opp.id}", "Zum Löschen bitte „LÖSCHEN“ eintippen.", request)
    audit(db, admin.email, "opportunity.delete", "opportunity", opp.id, title=opp.title)
    delete_opportunity(db, opp)
    db.commit()
    return redirect("/admin/angebote", "Angebot gelöscht.", request)


@router.post("/demo/entfernen", dependencies=[Depends(verify_csrf)])
def demo_remove(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    n = remove_demo(db, admin.email)
    return redirect("/admin", f"{n} Demo-Angebote entfernt." if n else "Es gibt keine Demo-Angebote.", request)


# --------------------------------------------------------------------------
# CSV import / export
# --------------------------------------------------------------------------

CSV_FIELDS = ["id", "employer", "title", "description", "domains", "postcode", "city", "location_label",
              "min_age", "max_age", "min_grade", "max_grade", "mandatory", "windows", "flexible_dates",
              "application_deadline", "places", "availability", "application_method", "application_email",
              "official_url", "documents", "languages", "status", "verification_status", "last_verified_at",
              "verification_source", "expires_at"]


@router.get("/export.csv")
def export_csv(admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, delimiter=";")
    w.writeheader()
    for o in db.scalars(select(Opportunity).options(selectinload(Opportunity.employer), selectinload(Opportunity.domains),
                                                    selectinload(Opportunity.date_windows), selectinload(Opportunity.documents))):
        w.writerow({
            "id": o.id, "employer": o.employer.name, "title": o.title, "description": o.description or "",
            "domains": "|".join(d.slug for d in o.domains), "postcode": o.postcode_code or "", "city": o.city_name or "",
            "location_label": o.location_label or "", "min_age": o.min_age or "", "max_age": o.max_age or "",
            "min_grade": o.min_grade or "", "max_grade": o.max_grade or "",
            "mandatory": "" if o.mandatory_practicum_supported is None else ("ja" if o.mandatory_practicum_supported else "nein"),
            "windows": "|".join(f"{w_.start_date}/{w_.end_date}" for w_ in o.date_windows),
            "flexible_dates": "ja" if o.flexible_dates else "nein",
            "application_deadline": o.application_deadline or "", "places": o.number_of_places if o.number_of_places is not None else "",
            "availability": o.availability, "application_method": o.application_method or "",
            "application_email": o.application_email or "", "official_url": o.official_url or "",
            "documents": "|".join(d.name for d in o.documents), "languages": o.language_requirements or "",
            "status": o.status, "verification_status": o.verification_status,
            "last_verified_at": o.last_verified_at or "", "verification_source": o.verification_source or "",
            "expires_at": o.expires_at or "",
        })
    audit(db, admin.email, "export.csv")
    db.commit()
    data = "﻿" + buf.getvalue()  # BOM so Excel opens umlauts correctly
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="praktikumfinder-angebote.csv"'})


@router.get("/import")
def import_form(request: Request, admin: AdminUser = Depends(admin_user)):
    return render(request, "admin/import.html", admin=admin, result=None, fields=CSV_FIELDS)


def import_rows(db: Session, text: str, actor: str) -> tuple[int, list[str]]:
    sample = text[:2000]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    domains = {d.slug: d for d in db.scalars(select(Domain))}
    created, errors = 0, []
    for i, row in enumerate(reader, start=2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        if not row.get("employer") or not row.get("title"):
            errors.append(f"Zeile {i}: employer und title sind Pflicht.")
            continue
        emp = db.scalar(select(Employer).where(Employer.name == row["employer"]))
        if emp is None:
            emp = Employer(name=row["employer"][:200], postcode_code=normalize_postcode(row.get("postcode")),
                           city_name=row.get("city") or None)
            db.add(emp)
            db.flush()
        opp = Opportunity(employer_id=emp.id, title=row["title"][:250], description=row.get("description") or None,
                          status=S.draft, source="csv_import", verification_status="unverified")
        opp.postcode_code = normalize_postcode(row.get("postcode"))
        opp.city_name = row.get("city") or None
        opp.location_label = row.get("location_label") or None
        for k_csv, k_model in (("min_age", "min_age"), ("max_age", "max_age"), ("min_grade", "min_grade"),
                               ("max_grade", "max_grade"), ("places", "number_of_places")):
            setattr(opp, k_model, _int_or_none(row.get(k_csv)))
        opp.mandatory_practicum_supported = _tri(row.get("mandatory"))
        opp.flexible_dates = row.get("flexible_dates") == "ja"
        opp.application_deadline = _date_or_none(row.get("application_deadline"))
        opp.expires_at = _date_or_none(row.get("expires_at"))
        opp.availability = row.get("availability") if row.get("availability") in ("unknown", "open", "few_places", "full") else "unknown"
        opp.application_method = row.get("application_method") or None
        opp.application_email = row.get("application_email") or None
        opp.official_url = row.get("official_url") or None
        opp.language_requirements = row.get("languages") or None
        opp.verification_source = row.get("verification_source") or "CSV-Import"
        opp.domains = [domains[s] for s in row.get("domains", "").split("|") if s in domains]
        for part in filter(None, row.get("windows", "").split("|")):
            a, _, b = part.partition("/")
            da, dbb = _date_or_none(a), _date_or_none(b)
            if da and dbb and dbb >= da:
                opp.date_windows.append(OpportunityDateWindow(start_date=da, end_date=dbb))
        for d in filter(None, row.get("documents", "").split("|")):
            opp.documents.append(OpportunityDocument(name=d[:200]))
        if opp.postcode_code:
            pc = db.scalar(select(Postcode).where(Postcode.code == opp.postcode_code))
            if pc:
                opp.latitude, opp.longitude = pc.latitude, pc.longitude
        problems = validate_opportunity(opp)
        if problems:
            errors.append(f"Zeile {i}: " + " ".join(problems))
            continue
        db.add(opp)
        created += 1
    audit(db, actor, "import.csv", created=created, errors=len(errors))
    return created, errors


@router.post("/import", dependencies=[Depends(verify_csrf)])
async def import_csv(request: Request, file: UploadFile, admin: AdminUser = Depends(admin_user),
                     db: Session = Depends(get_db)):
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "Datei zu groß (max. 2 MB).")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Bitte eine .csv-Datei hochladen.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    created, errors = import_rows(db, text, admin.email)
    db.commit()
    return render(request, "admin/import.html", admin=admin, result={"created": created, "errors": errors}, fields=CSV_FIELDS)


# --------------------------------------------------------------------------
# Domains
# --------------------------------------------------------------------------


@router.get("/bereiche")
def domains_page(request: Request, edit: int | None = None, admin: AdminUser = Depends(admin_user),
                 db: Session = Depends(get_db)):
    roots = db.scalars(select(Domain).where(Domain.parent_id.is_(None)).options(selectinload(Domain.children))
                       .order_by(Domain.position)).all()
    counts = dict(db.execute(select(opportunity_domains.c.domain_id, func.count())
                             .group_by(opportunity_domains.c.domain_id)).all())
    return render(request, "admin/domains.html", admin=admin, roots=roots, counts=counts,
                  editing=db.get(Domain, edit) if edit else None, tags=db.scalars(select(ActivityTag).order_by(ActivityTag.name)).all(),
                  flash=request.session.pop("flash", None))


def _split_list(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").replace("\n", ",").split(",") if x.strip()][:100]


@router.post("/bereiche", dependencies=[Depends(verify_csrf)])
def domain_save(request: Request, domain_id: str = Form(""), name: str = Form(...), slug: str = Form(...),
                parent_id: str = Form(""), description: str = Form(""), icon: str = Form(""),
                position: int = Form(100), active: str = Form(""), featured: str = Form(""),
                keywords: str = Form(""), related_jobs: str = Form(""),
                admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    slug = "".join(ch for ch in slug.lower().strip() if ch.isalnum() or ch == "-")[:80]
    if not slug or not name.strip():
        return redirect("/admin/bereiche", "Name und Kürzel sind Pflicht.", request)
    d = db.get(Domain, int(domain_id)) if domain_id.isdigit() else Domain()
    clash = db.scalar(select(Domain).where(Domain.slug == slug, Domain.id != (d.id or 0)))
    if clash:
        return redirect("/admin/bereiche", "Dieses Kürzel ist schon vergeben.", request)
    d.name, d.slug, d.description = name.strip()[:120], slug, description.strip() or None
    d.icon = icon.strip()[:8] or None
    d.parent_id = int(parent_id) if parent_id.isdigit() and int(parent_id) != d.id else None
    d.position, d.active, d.featured = position, active == "1", featured == "1"
    d.keywords, d.related_jobs = [k.lower() for k in _split_list(keywords)], _split_list(related_jobs)
    if d.id is None:
        db.add(d)
    db.flush()
    audit(db, admin.email, "domain.save", "domain", d.id, slug=slug, active=d.active)
    db.commit()
    return redirect("/admin/bereiche", "Bereich gespeichert.", request)


@router.post("/tags", dependencies=[Depends(verify_csrf)])
def tag_add(request: Request, name: str = Form(...), admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    name = name.strip()[:80]
    if name and not db.scalar(select(ActivityTag).where(ActivityTag.name == name)):
        db.add(ActivityTag(name=name))
        audit(db, admin.email, "tag.create", details_name=name)
        db.commit()
    return redirect("/admin/bereiche", "Tätigkeit gespeichert.", request)


# --------------------------------------------------------------------------
# Employers
# --------------------------------------------------------------------------


@router.get("/arbeitgeber")
def employers_page(request: Request, q: str = "", admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    stmt = select(Employer).options(selectinload(Employer.opportunities), selectinload(Employer.contacts)).order_by(Employer.name)
    if q:
        stmt = stmt.where(Employer.name.ilike(f"%{q}%"))
    return render(request, "admin/employers.html", admin=admin, items=db.scalars(stmt.limit(500)).all(), q=q,
                  flash=request.session.pop("flash", None))


@router.get("/arbeitgeber/{emp_id}")
def employer_edit(emp_id: int, request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    emp = db.get(Employer, emp_id) if emp_id else Employer()
    if emp is None:
        raise HTTPException(404)
    return render(request, "admin/employer_form.html", admin=admin, emp=emp, flash=request.session.pop("flash", None))


@router.post("/arbeitgeber/{emp_id}", dependencies=[Depends(verify_csrf)])
async def employer_save(emp_id: int, request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    form = await request.form()
    emp = db.get(Employer, emp_id) if emp_id else Employer()
    if emp is None:
        raise HTTPException(404)
    name = (form.get("name") or "").strip()[:200]
    if not name:
        return redirect(f"/admin/arbeitgeber/{emp_id}", "Name ist Pflicht.", request)
    emp.name = name
    emp.description = (form.get("description") or "").strip()[:3000] or None
    emp.employer_type = form.get("employer_type") if form.get("employer_type") in EMPLOYER_TYPES else None
    emp.website = (form.get("website") or "").strip()[:500] or None
    emp.address_line = (form.get("address_line") or "").strip()[:200] or None
    emp.postcode_code = normalize_postcode(form.get("postcode_code"))
    emp.city_name = (form.get("city_name") or "").strip()[:120] or None
    emp.notes = (form.get("notes") or "").strip()[:3000] or None
    if emp.id is None:
        db.add(emp)
    db.flush()
    cname, cemail = (form.get("contact_name") or "").strip(), (form.get("contact_email") or "").strip()
    if cname or cemail:
        emp.contacts.append(EmployerContact(name=cname[:120] or None, email=cemail[:200] or None,
                                            phone=(form.get("contact_phone") or "").strip()[:60] or None,
                                            role=(form.get("contact_role") or "").strip()[:120] or None,
                                            publish_allowed=form.get("contact_publish") == "1"))
    for c in list(emp.contacts):
        if form.get(f"delete_contact_{c.id}") == "1":
            emp.contacts.remove(c)
    audit(db, admin.email, "employer.save", "employer", emp.id, name=emp.name)
    db.commit()
    return redirect(f"/admin/arbeitgeber/{emp.id}", "Betrieb gespeichert.", request)


# --------------------------------------------------------------------------
# Employer submissions
# --------------------------------------------------------------------------


@router.get("/einreichungen")
def submissions_page(request: Request, status: str = "pending", admin: AdminUser = Depends(admin_user),
                     db: Session = Depends(get_db)):
    items = db.scalars(select(EmployerSubmission).where(EmployerSubmission.status == status)
                       .order_by(EmployerSubmission.created_at.desc())).all()
    return render(request, "admin/submissions.html", admin=admin, items=items, status=status,
                  flash=request.session.pop("flash", None))


@router.get("/einreichungen/{sub_id}")
def submission_detail(sub_id: int, request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    sub = db.get(EmployerSubmission, sub_id)
    if sub is None:
        raise HTTPException(404)
    target = db.get(Opportunity, sub.target_opportunity_id) if sub.target_opportunity_id else None
    return render(request, "admin/submission_detail.html", admin=admin, sub=sub, target=target)


def submission_to_opportunity(db: Session, sub: EmployerSubmission, actor: str) -> Opportunity:
    p = sub.payload
    emp = db.scalar(select(Employer).where(Employer.name == p["company_name"]))
    if emp is None:
        emp = Employer(name=p["company_name"], description=p.get("company_description") or None,
                       employer_type=p.get("employer_type") or None, website=p.get("website") or None,
                       postcode_code=p.get("postcode") or None, city_name=p.get("city") or None)
        db.add(emp)
        db.flush()
    contact = EmployerContact(name=p.get("contact_name") or None, email=p.get("contact_email"),
                              phone=p.get("contact_phone") or None, publish_allowed=bool(p.get("contact_publish")))
    emp.contacts.append(contact)
    db.flush()
    opp = Opportunity(employer_id=emp.id, source="employer_submission", status=S.awaiting_review,
                      verification_status="employer_confirmed",
                      verification_source=f"Meldung des Betriebs vom {sub.created_at:%d.%m.%Y}",
                      verified_by=actor, last_verified_at=date.today())
    apply_submission_payload(db, opp, p)
    opp.contact = contact
    db.add(opp)
    db.flush()
    return opp


def apply_submission_payload(db: Session, opp: Opportunity, p: dict) -> None:
    opp.title = p["title"]
    opp.description = p.get("description") or None
    opp.street = p.get("street") or None
    opp.postcode_code = p.get("postcode") or None
    opp.city_name = p.get("city") or None
    opp.location_label = p.get("city") or None
    pc = db.scalar(select(Postcode).where(Postcode.code == opp.postcode_code)) if opp.postcode_code else None
    if pc:
        opp.latitude, opp.longitude = pc.latitude, pc.longitude
    opp.min_age, opp.max_age = p.get("min_age"), p.get("max_age")
    opp.min_grade, opp.max_grade = p.get("min_grade"), p.get("max_grade")
    opp.accepted_school_types = p.get("school_types") or []
    opp.mandatory_practicum_supported = p.get("mandatory")
    opp.school_agreement_required = p.get("school_agreement")
    opp.flexible_dates = bool(p.get("flexible"))
    opp.flexible_dates_note = p.get("flexible_note") or None
    opp.application_deadline = _date_or_none(p.get("deadline"))
    opp.number_of_places = p.get("places")
    opp.application_method = p.get("application_method") or None
    opp.application_email = p.get("application_email") or None
    opp.official_url = p.get("official_url") or None
    opp.language_requirements = p.get("languages") or None
    opp.clothing_safety = p.get("clothing") or None
    opp.travel_information = p.get("transit") or None
    opp.domains = db.scalars(select(Domain).where(Domain.slug.in_(p.get("domains") or []))).all()
    opp.date_windows.clear()
    for s_, e_ in (("start1", "end1"), ("start2", "end2")):
        a, b = _date_or_none(p.get(s_)), _date_or_none(p.get(e_))
        if a and b and b >= a:
            opp.date_windows.append(OpportunityDateWindow(start_date=a, end_date=b))
    opp.documents.clear()
    for line in (p.get("documents") or "").splitlines():
        if line.strip():
            opp.documents.append(OpportunityDocument(name=line.strip()[:200]))


@router.post("/einreichungen/{sub_id}", dependencies=[Depends(verify_csrf)])
def submission_decide(sub_id: int, request: Request, decision: str = Form(...), note: str = Form(""),
                      admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    sub = db.get(EmployerSubmission, sub_id)
    if sub is None or sub.status != "pending":
        raise HTTPException(404)
    sub.reviewed_at, sub.reviewed_by, sub.review_note = utcnow(), admin.email, note.strip()[:2000] or None
    if decision == "approve":
        if sub.kind == "update" and sub.target_opportunity_id:
            opp = _get_opp(db, sub.target_opportunity_id)
            apply_submission_payload(db, opp, sub.payload)
            record_verification(db, opp, "employer_confirmed", f"Änderungsmeldung des Betriebs vom {sub.created_at:%d.%m.%Y}", admin.email)
        else:
            opp = submission_to_opportunity(db, sub, admin.email)
            sub.created_opportunity_id = opp.id
        sub.status = "approved"
        audit(db, admin.email, "submission.approve", "employer_submission", sub.id, opportunity_id=opp.id)
        db.commit()
        return redirect(f"/admin/angebote/{opp.id}", "Übernommen. Bitte prüfen und veröffentlichen.", request)
    sub.status = "rejected"
    audit(db, admin.email, "submission.reject", "employer_submission", sub.id)
    db.commit()
    return redirect("/admin/einreichungen", "Einreichung abgelehnt.", request)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@router.get("/meldungen")
def reports_page(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Report).options(selectinload(Report.opportunity)).order_by(
        Report.resolved_at.is_not(None), Report.created_at.desc()).limit(300)).all()
    return render(request, "admin/reports.html", admin=admin, items=items, reasons=REPORT_REASONS,
                  flash=request.session.pop("flash", None))


@router.post("/meldungen/{report_id}", dependencies=[Depends(verify_csrf)])
def report_resolve(report_id: int, request: Request, resolution: str = Form(""),
                   admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404)
    r.resolved_at, r.resolution = utcnow(), resolution.strip()[:2000] or "erledigt"
    audit(db, admin.email, "report.resolve", "report", r.id, opportunity_id=r.opportunity_id)
    db.commit()
    return redirect("/admin/meldungen", "Meldung erledigt. Prüfstatus des Angebots bitte ggf. neu setzen.", request)


# --------------------------------------------------------------------------
# Locations, travel rules, settings
# --------------------------------------------------------------------------


@router.get("/orte")
def locations_page(request: Request, q: str = "", admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    stmt = select(Postcode).options(selectinload(Postcode.city).selectinload(City.state)).order_by(Postcode.code)
    if q:
        stmt = stmt.join(City).where(or_(Postcode.code.like(f"{q}%"), City.name.ilike(f"%{q}%")))
    return render(
        request, "admin/locations.html", admin=admin, postcodes=db.scalars(stmt.limit(300)).all(), q=q,
        total=db.scalar(select(func.count(Postcode.id))),
        rules=db.scalars(select(TravelRule).options(selectinload(TravelRule.state))).all(),
        states=db.scalars(select(FederalState).order_by(FederalState.name)).all(),
        settings={k: get_setting(db, k) for k in ("verification_period_days", "deadline_warning_days",
                                                  "default_max_travel_minutes", "scraper_not_seen_days")},
        profiles=json.dumps(get_setting(db, "travel_profiles"), ensure_ascii=False, indent=2),
        flash=request.session.pop("flash", None),
    )


@router.post("/orte/plz", dependencies=[Depends(verify_csrf)])
def postcode_add(request: Request, code: str = Form(...), city: str = Form(...), state_id: int = Form(...),
                 latitude: float = Form(...), longitude: float = Form(...),
                 admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    code_n = normalize_postcode(code)
    if not code_n or not (47 <= latitude <= 55.2 and 5.8 <= longitude <= 15.1):
        return redirect("/admin/orte", "PLZ oder Koordinaten ungültig (müssen in Deutschland liegen).", request)
    c = db.scalar(select(City).where(City.name == city.strip(), City.state_id == state_id))
    if c is None:
        c = City(name=city.strip()[:120], state_id=state_id)
        db.add(c)
        db.flush()
    pc = db.scalar(select(Postcode).where(Postcode.code == code_n))
    if pc is None:
        db.add(Postcode(code=code_n, city_id=c.id, latitude=latitude, longitude=longitude))
    else:
        pc.city_id, pc.latitude, pc.longitude = c.id, latitude, longitude
    audit(db, admin.email, "postcode.save", code=code_n)
    db.commit()
    return redirect("/admin/orte", f"PLZ {code_n} gespeichert.", request)


@router.post("/orte/regeln", dependencies=[Depends(verify_csrf)])
async def rules_save(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    form = await request.form()
    rule_id = form.get("rule_id") or ""
    rule = db.get(TravelRule, int(rule_id)) if rule_id.isdigit() else TravelRule()
    rule.name = (form.get("name") or "Regel").strip()[:120]
    st = form.get("state_id") or ""
    rule.state_id = int(st) if st.isdigit() else None
    rule.reference = form.get("reference") if form.get("reference") in ("school", "home", "either") else "either"
    rule.max_travel_minutes = _int_or_none(form.get("max_travel_minutes"))
    md = (form.get("max_distance_km") or "").strip()
    rule.max_distance_km = float(md) if md.replace(".", "", 1).isdigit() else None
    rule.approval_required_beyond = form.get("approval_required_beyond") == "1"
    rule.active = form.get("active") == "1"
    rule.note = (form.get("note") or "").strip()[:2000] or None
    if rule.id is None:
        db.add(rule)
    db.flush()
    audit(db, admin.email, "travel_rule.save", "travel_rule", rule.id, reference=rule.reference,
          max_travel_minutes=rule.max_travel_minutes, active=rule.active)
    db.commit()
    return redirect("/admin/orte", "Regel gespeichert.", request)


@router.post("/einstellungen", dependencies=[Depends(verify_csrf)])
async def settings_save(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    form = await request.form()
    for key, lo, hi in (("verification_period_days", 7, 365), ("deadline_warning_days", 1, 60),
                        ("default_max_travel_minutes", 10, 180), ("scraper_not_seen_days", 1, 120)):
        v = _int_or_none(form.get(key))
        if v is not None and lo <= v <= hi:
            set_setting(db, key, v)
    raw = form.get("travel_profiles")
    if raw:
        try:
            profiles = json.loads(raw)
            assert isinstance(profiles, dict) and "oepnv" in profiles
            for p in profiles.values():
                float(p["speed_kmh"]), float(p["detour"]), float(p["overhead_min"])
            set_setting(db, "travel_profiles", profiles)
        except Exception:
            return redirect("/admin/orte", "Reiseprofile: ungültiges JSON-Format.", request)
    audit(db, admin.email, "settings.save")
    db.commit()
    return redirect("/admin/orte", "Einstellungen gespeichert.", request)


# --------------------------------------------------------------------------
# Scrape sources
# --------------------------------------------------------------------------


@router.get("/quellen")
def sources_page(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    sources = db.scalars(select(ScrapeSource).options(selectinload(ScrapeSource.runs)).order_by(ScrapeSource.id)).all()
    return render(request, "admin/sources.html", admin=admin, sources=sources, registry=REGISTRY,
                  flash=request.session.pop("flash", None))


@router.post("/quellen", dependencies=[Depends(verify_csrf)])
def source_save(request: Request, source_id: str = Form(""), scraper: str = Form(...), name: str = Form(...),
                config: str = Form("{}"), enabled: str = Form(""), auto_publish_unverified: str = Form(""),
                admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    if scraper not in REGISTRY:
        return redirect("/admin/quellen", "Unbekannter Scraper.", request)
    try:
        cfg = json.loads(config or "{}")
        assert isinstance(cfg, dict)
    except Exception:
        return redirect("/admin/quellen", "Konfiguration ist kein gültiges JSON-Objekt.", request)
    src = db.get(ScrapeSource, int(source_id)) if source_id.isdigit() else ScrapeSource()
    src.scraper, src.name, src.config = scraper, name.strip()[:200], cfg
    src.enabled, src.auto_publish_unverified = enabled == "1", auto_publish_unverified == "1"
    if src.id is None:
        db.add(src)
    db.flush()
    audit(db, admin.email, "scrape_source.save", "scrape_source", src.id, enabled=src.enabled, scraper=scraper)
    db.commit()
    return redirect("/admin/quellen", "Quelle gespeichert.", request)


def _run_in_background(source_id: int, actor: str) -> None:
    db = SessionLocal()
    try:
        src = db.get(ScrapeSource, source_id)
        if src:
            run_source(db, src, actor=actor)
    finally:
        db.close()


@router.post("/quellen/{source_id}/ausfuehren", dependencies=[Depends(verify_csrf)])
def source_run(source_id: int, request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    src = db.get(ScrapeSource, source_id)
    if src is None:
        raise HTTPException(404)
    threading.Thread(target=_run_in_background, args=(src.id, admin.email), daemon=True).start()
    return redirect("/admin/quellen", f"„{src.name}“ läuft im Hintergrund. Seite in einer Minute neu laden.", request)


def _check_links_in_background(actor: str) -> None:
    db = SessionLocal()
    try:
        check_links(db, actor=actor)
    finally:
        db.close()


@router.post("/links-pruefen", dependencies=[Depends(verify_csrf)])
def links_check(request: Request, admin: AdminUser = Depends(admin_user)):
    threading.Thread(target=_check_links_in_background, args=(admin.email,), daemon=True).start()
    return redirect("/admin/pruefung?queue=broken_link",
                    "Die Links werden im Hintergrund geprüft. Seite in ein paar Minuten neu laden.", request)


@router.post("/angebote/{opp_id}/link-pruefen", dependencies=[Depends(verify_csrf)])
def opportunity_link_check(opp_id: int, request: Request, admin: AdminUser = Depends(admin_user),
                           db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    status = check_one(PoliteFetcher(), opp)
    audit(db, admin.email, "opportunity.link_check", "opportunity", opp.id, result=status)
    db.commit()
    msg = "Kein Link hinterlegt." if status == "skipped" else f"{LINK_LABELS[status]}: {opp.link_check_note}"
    return redirect(f"/admin/angebote/{opp.id}", msg, request)


@router.post("/angebote/{opp_id}/erkennen", dependencies=[Depends(verify_csrf)])
def opportunity_detect(opp_id: int, request: Request, admin: AdminUser = Depends(admin_user),
                       db: Session = Depends(get_db)):
    opp = _get_opp(db, opp_id)
    filled = apply_extracted(opp, f"{opp.title}\n{opp.description or ''}")
    audit(db, admin.email, "opportunity.detect", "opportunity", opp.id, filled=filled)
    db.commit()
    msg = ("Erkannt und eingetragen: " + "; ".join(filled) + ". Bitte prüfen.") if filled else \
        "Im Text wurde nichts erkannt, das in leere Felder passt."
    return redirect(f"/admin/angebote/{opp.id}", msg, request)


# --------------------------------------------------------------------------
# Analytics & audit log
# --------------------------------------------------------------------------


@router.get("/statistik")
def analytics_page(request: Request, days: int = 30, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    since = date.today() - timedelta(days=max(1, min(days, 365)))
    rows = db.execute(select(AnalyticsDaily.event, AnalyticsDaily.dimension, func.sum(AnalyticsDaily.count))
                      .where(AnalyticsDaily.day >= since).group_by(AnalyticsDaily.event, AnalyticsDaily.dimension)
                      .order_by(AnalyticsDaily.event)).all()
    totals: dict[str, int] = {}
    dims: dict[str, list[tuple[str, int]]] = {}
    for ev, dim, n in rows:
        totals[ev] = totals.get(ev, 0) + n
        if dim:
            dims.setdefault(ev, []).append((dim, n))
    started, completed = totals.get("search_completed", 0), totals.get("helper_started", 0)
    return render(request, "admin/analytics.html", admin=admin, totals=totals, dims=dims, days=days,
                  zero_rate=(totals.get("search_zero_results", 0) / started * 100) if started else None,
                  helper_rate=(totals.get("helper_completed", 0) / completed * 100) if completed else None)


@router.get("/protokoll")
def audit_page(request: Request, admin: AdminUser = Depends(admin_user), db: Session = Depends(get_db)):
    items = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(500)).all()
    return render(request, "admin/audit.html", admin=admin, items=items)
