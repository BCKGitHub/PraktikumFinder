"""Student-facing pages: landing, search, results, details, application helper, info pages."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date

from docx import Document
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from ..analytics import CLIENT_EVENTS, bucket, track
from ..appsupport import application_templates, checklist_for
from ..audit import audit
from ..db import get_db
from ..geo import lookup_postcode, normalize_postcode
from ..listings import S, match_config, public_query, to_facts
from ..matching import StudentQuery, Tier, evaluate, rank
from ..models import City, Domain, FederalState, Opportunity, Postcode, Report, VerificationStatus
from ..security import enforce_rate_limit, verify_csrf
from ..web import render

router = APIRouter()


def active_domains(db: Session) -> list[Domain]:
    return db.scalars(
        select(Domain).where(Domain.active.is_(True), Domain.parent_id.is_(None))
        .options(selectinload(Domain.children)).order_by(Domain.featured.desc(), Domain.position)
    ).all()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/")
def landing(request: Request, db: Session = Depends(get_db)):
    count = len(db.scalars(public_query()).all())
    return render(request, "pages/landing.html", domains=active_domains(db), listing_count=count)


@router.get("/suche")
def search_form(request: Request, db: Session = Depends(get_db), bereich: str | None = None):
    track(db, "search_started")
    db.commit()
    preselected = {bereich} if bereich else set()
    return render(request, "student/search.html", domains=active_domains(db), q={}, preselected=preselected)


def _int(raw: str | None, lo: int, hi: int) -> int | None:
    try:
        v = int(raw) if raw not in (None, "") else None
    except ValueError:
        return None
    return v if v is not None and lo <= v <= hi else None


def _date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


@dataclass
class ParsedSearch:
    query: StudentQuery
    params: dict
    errors: list[str]
    selected_slugs: set[str]
    state_code: str | None
    school_place: str | None


def parse_search(request: Request, db: Session) -> ParsedSearch:
    p = request.query_params
    errors: list[str] = []
    slugs = set(p.getlist("bereich"))
    selected = db.scalars(select(Domain).where(Domain.slug.in_(slugs))).all() if slugs else []
    age = _int(p.get("alter"), 10, 25)
    grade = _int(p.get("klasse"), 5, 13)
    start, end = _date(p.get("von")), _date(p.get("bis"))
    if p.get("alter") and age is None:
        errors.append("Bitte gib ein Alter zwischen 10 und 25 an.")
    if p.get("klasse") and grade is None:
        errors.append("Bitte gib eine Klassenstufe zwischen 5 und 13 an.")
    if start and end and end < start:
        errors.append("Das Ende deines Praktikums liegt vor dem Beginn – bitte prüfe die Daten.")
        start = end = None
    school_pc = normalize_postcode(p.get("schule_plz"))
    home_pc = normalize_postcode(p.get("wohn_plz"))
    if p.get("schule_plz") and not school_pc:
        errors.append("Die Postleitzahl der Schule muss 5 Ziffern haben.")
    school_pt = lookup_postcode(db, school_pc)
    home_pt = lookup_postcode(db, home_pc)
    if school_pc and not school_pt:
        errors.append("Diese Schul-PLZ kennen wir noch nicht – der Weg kann nicht geschätzt werden.")
    state_code, place = None, None
    if school_pc:
        row = db.execute(
            select(City.name, City.state_id).join(Postcode).where(Postcode.code == school_pc)
        ).first()
        if row:
            place = row.name
            st = db.get(FederalState, row.state_id)
            state_code = st.code if st else None
    mode = p.get("verkehrsmittel", "oepnv")
    q = StudentQuery(
        today=date.today(),
        domain_ids={d.id for d in selected},
        related_domain_ids={d.parent_id for d in selected if d.parent_id},
        age=age, grade=grade, start=start, end=end, school=school_pt, home=home_pt,
        max_minutes=_int(p.get("max_minuten"), 5, 240), mode=mode if mode in ("oepnv", "fahrrad", "zu_fuss", "auto") else "oepnv",
        school_type=p.get("schulform") or None, mandatory=p.get("pflicht") == "1",
    )
    return ParsedSearch(q, dict(p), errors, slugs, state_code, place)


@router.get("/ergebnisse")
def results(request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, "search")
    ps = parse_search(request, db)
    cfg, rule = match_config(db, ps.state_code)
    opps = db.scalars(public_query()).all()
    employer_type = request.query_params.get("arbeitgeberart") or None
    if employer_type:
        opps = [o for o in opps if o.employer.employer_type == employer_type]
    by_id = {o.id: o for o in opps}
    matches = rank(ps.query, [to_facts(o) for o in opps], cfg)
    if ps.query.domain_ids:
        matches = [m for m in matches if m.domain_score > 0]
    show_all = request.query_params.get("alle") == "1"
    hidden = [m for m in matches if m.tier == Tier.NOT_ELIGIBLE]
    visible = matches if show_all else [m for m in matches if m.tier != Tier.NOT_ELIGIBLE]

    track(db, "search_completed")
    for slug in ps.selected_slugs:
        track(db, "domain_selected", slug)
    track(db, "results_bucket", bucket(len(visible)))
    if not visible:
        track(db, "search_zero_results")
    db.commit()

    query_string = re.sub(r"(&|^)alle=1", "", str(request.url.query))
    return render(
        request, "student/results.html",
        results=[(m, by_id[m.listing_id]) for m in visible], hidden_count=len(hidden) if not show_all else 0,
        show_all=show_all, errors=ps.errors, q=ps.params, query=ps.query, rule=rule, cfg=cfg,
        selected_slugs=ps.selected_slugs, domains=active_domains(db), school_place=ps.school_place,
        query_string=query_string,
    )


def _load_public(db: Session, opp_id: int) -> Opportunity:
    opp = db.scalar(
        select(Opportunity).where(Opportunity.id == opp_id)
        .options(selectinload(Opportunity.domains), selectinload(Opportunity.date_windows),
                 selectinload(Opportunity.documents), selectinload(Opportunity.employer),
                 selectinload(Opportunity.tags), selectinload(Opportunity.contact))
    )
    # Expired/closed listings stay viewable (clearly labelled) so saved links don't break.
    if opp is None or opp.status not in (S.published, S.expired, S.closed):
        raise HTTPException(404, "Dieses Angebot gibt es nicht (mehr).")
    return opp


@router.get("/angebote/{opp_id}")
def detail(opp_id: int, request: Request, db: Session = Depends(get_db)):
    opp = _load_public(db, opp_id)
    ps = parse_search(request, db)
    cfg, rule = match_config(db, ps.state_code)
    match = evaluate(ps.query, to_facts(opp), cfg)
    track(db, "opportunity_view")
    db.commit()
    return render(request, "student/detail.html", opp=opp, match=match, rule=rule, q=ps.params,
                  query=ps.query, query_string=str(request.url.query), sent=request.query_params.get("gemeldet"))


REPORT_REASONS = {
    "nicht_mehr_aktuell": "Angebot gibt es nicht mehr",
    "falsche_angaben": "Angaben sind falsch (z. B. Alter, Termine)",
    "kontakt_falsch": "Kontakt / Link funktioniert nicht",
    "unangemessen": "Inhalt ist unangemessen oder unseriös",
    "sonstiges": "Sonstiges",
}


@router.post("/angebote/{opp_id}/melden", dependencies=[Depends(verify_csrf)])
def report(opp_id: int, request: Request, reason: str = Form(...), message: str = Form(""),
           db: Session = Depends(get_db)):
    enforce_rate_limit(request, "report")
    opp = _load_public(db, opp_id)
    if reason not in REPORT_REASONS:
        raise HTTPException(400, "Bitte wähle einen Grund aus.")
    db.add(Report(opportunity_id=opp.id, reason=reason, message=message.strip()[:1000] or None))
    if opp.verification_status != VerificationStatus.student_reported:
        audit(db, "public", "opportunity.reported", "opportunity", opp.id, reason=reason,
              previous_verification=opp.verification_status)
        opp.verification_status = VerificationStatus.student_reported
    track(db, "report_submitted")
    db.commit()
    return RedirectResponse(f"/angebote/{opp.id}?gemeldet=1#melden", status_code=303)


@router.get("/angebote/{opp_id}/bewerbung")
def application_helper(opp_id: int, request: Request, db: Session = Depends(get_db)):
    opp = _load_public(db, opp_id)
    track(db, "helper_started")
    db.commit()
    return render(request, "student/apply.html", opp=opp, checklist=checklist_for(opp),
                  templates_json=application_templates(opp))


@router.get("/bewerbung/checkliste")
def general_checklist(request: Request):
    return render(request, "student/checklist.html", checklist=checklist_for(None))


SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")


@router.post("/bewerbung/docx", dependencies=[Depends(verify_csrf)])
def download_docx(request: Request, title: str = Form("Bewerbung"), body: str = Form(...),
                  confirmed: str = Form("")):
    """Builds a .docx from the student's own edited text. Nothing is stored or logged."""
    enforce_rate_limit(request, "docx")
    if confirmed != "1":
        raise HTTPException(400, "Bitte bestätige zuerst, dass du den Entwurf geprüft hast.")
    if len(body) > 20000:
        raise HTTPException(413, "Der Text ist zu lang.")
    doc = Document()
    for para in body.replace("\r\n", "\n").split("\n"):
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    name = SAFE_FILENAME.sub("_", title)[:40] or "Bewerbung"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"', "Cache-Control": "no-store"},
    )


@router.get("/merkliste")
def saved(request: Request):
    return render(request, "student/saved.html")


@router.get("/bewerbungen")
def tracker(request: Request):
    return render(request, "student/tracker.html")


@router.get("/einstellungen")
def privacy_settings(request: Request):
    return render(request, "student/settings.html")


@router.get("/api/opportunities")
def api_opportunities(ids: str = "", db: Session = Depends(get_db)):
    """Public summary data for listings the student saved in their browser."""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()][:100]
    except ValueError:
        raise HTTPException(400, "invalid ids") from None
    if not id_list:
        return JSONResponse([])
    opps = db.scalars(select(Opportunity).where(Opportunity.id.in_(id_list))
                      .options(selectinload(Opportunity.employer))).all()
    today = date.today()
    out = []
    for o in opps:
        if o.status not in (S.published, S.expired, S.closed):
            continue
        expired = o.status == S.expired or (o.expires_at is not None and o.expires_at < today)
        out.append({
            "id": o.id, "title": o.title, "employer": o.employer.name, "place": o.location_label or o.city_name,
            "deadline": o.application_deadline.isoformat() if o.application_deadline else None,
            "status": "expired" if expired else ("closed" if o.status == S.closed or o.availability == "full" else "open"),
            "url": f"/angebote/{o.id}",
        })
    return JSONResponse(out)


@router.post("/api/event")
async def api_event(request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit(request, "event")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "invalid") from None
    event = data.get("event") if isinstance(data, dict) else None
    if event in CLIENT_EVENTS:
        track(db, event)
        db.commit()
    return JSONResponse({"ok": True})


@router.get("/datenschutz")
def privacy(request: Request):
    return render(request, "pages/privacy.html")


@router.get("/nutzungsbedingungen")
def terms(request: Request):
    return render(request, "pages/terms.html")


@router.get("/hilfe")
def help_page(request: Request):
    return render(request, "pages/help.html")
