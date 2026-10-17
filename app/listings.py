"""Opportunity lifecycle: status transitions, validation, verification, expiry."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .audit import audit
from .geo import Point
from .matching import ListingFacts, MatchConfig, TravelRuleConfig
from .models import (
    FederalState,
    ListingStatus,
    Opportunity,
    TravelRule,
    VerificationRecord,
    VerificationStatus,
    utcnow,
)
from .settings_store import get_setting

S = ListingStatus

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    S.draft: {S.awaiting_review, S.published, S.rejected},
    S.submitted: {S.awaiting_review, S.published, S.rejected},
    S.awaiting_review: {S.published, S.rejected, S.draft},
    S.published: {S.closed, S.expired, S.awaiting_review, S.draft},
    S.closed: {S.published, S.expired, S.draft},
    S.expired: {S.published, S.draft},
    S.rejected: {S.draft, S.awaiting_review},
}

STATUS_LABELS = {
    "draft": "Entwurf",
    "submitted": "Eingereicht",
    "awaiting_review": "Wartet auf Prüfung",
    "published": "Veröffentlicht",
    "expired": "Abgelaufen",
    "closed": "Geschlossen",
    "rejected": "Abgelehnt",
}

VERIFICATION_LABELS = {
    "unverified": "Nicht bestätigt",
    "employer_confirmed": "Vom Betrieb bestätigt",
    "official_source_verified": "Aus offizieller Quelle geprüft",
    "admin_verified": "Von uns geprüft",
    "student_reported": "Fehler gemeldet – wird geprüft",
}


class TransitionError(ValueError):
    pass


def can_transition(current: str, new: str) -> bool:
    return new == current or new in ALLOWED_TRANSITIONS.get(S(current), set())


def change_status(db: Session, opp: Opportunity, new: str, actor: str, note: str | None = None) -> None:
    if not can_transition(opp.status, new):
        raise TransitionError(
            f"Statuswechsel von „{STATUS_LABELS[opp.status]}“ zu „{STATUS_LABELS[new]}“ ist nicht erlaubt."
        )
    if new == S.published:
        problems = publish_blockers(opp)
        if problems:
            raise TransitionError("Veröffentlichung nicht möglich: " + " ".join(problems))
        if opp.published_at is None:
            opp.published_at = utcnow()
    old = opp.status
    opp.status = new
    audit(db, actor, "opportunity.status", "opportunity", opp.id, old=old, new=new, note=note)


def publish_blockers(opp: Opportunity) -> list[str]:
    """Minimum data quality for publication. Missing optional data is shown as 'Nicht angegeben'."""
    problems = []
    if not opp.employer_id:
        problems.append("Kein Betrieb zugeordnet.")
    if not opp.title or len(opp.title.strip()) < 3:
        problems.append("Titel fehlt.")
    if not opp.domains:
        problems.append("Mindestens ein Bereich muss zugeordnet sein.")
    if not (opp.application_email or opp.official_url):
        problems.append("Es braucht eine Bewerbungs-E-Mail oder einen offiziellen Link.")
    if not opp.postcode_code and opp.latitude is None:
        problems.append("Ort (PLZ) fehlt.")
    return problems


def validate_opportunity(opp: Opportunity) -> list[str]:
    errors = []
    if opp.min_age is not None and not 10 <= opp.min_age <= 25:
        errors.append("Mindestalter muss zwischen 10 und 25 liegen.")
    if opp.max_age is not None and not 10 <= opp.max_age <= 25:
        errors.append("Höchstalter muss zwischen 10 und 25 liegen.")
    if opp.min_age is not None and opp.max_age is not None and opp.max_age < opp.min_age:
        errors.append("Höchstalter darf nicht kleiner als Mindestalter sein.")
    if opp.min_grade is not None and opp.max_grade is not None and opp.max_grade < opp.min_grade:
        errors.append("Höchste Klasse darf nicht kleiner als niedrigste Klasse sein.")
    for g in (opp.min_grade, opp.max_grade):
        if g is not None and not 5 <= g <= 13:
            errors.append("Klassenstufen müssen zwischen 5 und 13 liegen.")
            break
    for w in opp.date_windows:
        if w.end_date < w.start_date:
            errors.append("Ein Zeitraum endet vor seinem Beginn.")
    if opp.application_deadline and opp.date_windows:
        last_end = max(w.end_date for w in opp.date_windows)
        if opp.application_deadline > last_end:
            errors.append("Die Bewerbungsfrist liegt nach dem letzten Praktikumszeitraum.")
    if opp.number_of_places is not None and opp.number_of_places < 0:
        errors.append("Anzahl Plätze darf nicht negativ sein.")
    if opp.application_email and ("\n" in opp.application_email or "\r" in opp.application_email
                                  or "@" not in opp.application_email):
        errors.append("Ungültige Bewerbungs-E-Mail.")
    if opp.official_url and not opp.official_url.startswith(("https://", "http://")):
        errors.append("Der offizielle Link muss mit https:// beginnen.")
    return errors


def record_verification(db: Session, opp: Opportunity, status: str, source: str | None,
                        verified_by: str, notes: str | None = None) -> None:
    VerificationStatus(status)  # validates
    today = date.today()
    period = int(get_setting(db, "verification_period_days"))
    opp.verification_status = status
    opp.verification_source = source
    opp.verified_by = verified_by
    if status != VerificationStatus.unverified and status != VerificationStatus.student_reported:
        opp.last_verified_at = today
        opp.next_review_at = today + timedelta(days=period)
    opp.verifications.append(
        VerificationRecord(verification_status=status, source=source, verified_by=verified_by, notes=notes)
    )
    audit(db, verified_by, "opportunity.verify", "opportunity", opp.id, status=status, source=source)


def expire_outdated(db: Session, today: date | None = None, actor: str = "system") -> int:
    """Mark published listings expired when expiry date or all practicum dates are past."""
    today = today or date.today()
    n = 0
    for opp in db.scalars(
        select(Opportunity).where(Opportunity.status == S.published).options(selectinload(Opportunity.date_windows))
    ):
        past_windows = opp.date_windows and all(w.end_date < today for w in opp.date_windows) and not opp.flexible_dates
        if (opp.expires_at and opp.expires_at < today) or past_windows:
            change_status(db, opp, S.expired, actor, note="automatisch abgelaufen")
            n += 1
    return n


def public_query():
    today = date.today()
    return (
        select(Opportunity)
        .where(Opportunity.status == S.published)
        .where(or_(Opportunity.expires_at.is_(None), Opportunity.expires_at >= today))
        .options(
            selectinload(Opportunity.domains),
            selectinload(Opportunity.date_windows),
            selectinload(Opportunity.employer),
            selectinload(Opportunity.documents),
            selectinload(Opportunity.tags),
        )
    )


def to_facts(opp: Opportunity) -> ListingFacts:
    direct = {d.id for d in opp.domains}
    with_parents = set(direct) | {d.parent_id for d in opp.domains if d.parent_id}
    point = Point(opp.latitude, opp.longitude) if opp.latitude is not None and opp.longitude is not None else None
    return ListingFacts(
        id=opp.id,
        domain_ids=with_parents,
        direct_domain_ids=direct,
        min_age=opp.min_age,
        max_age=opp.max_age,
        min_grade=opp.min_grade,
        max_grade=opp.max_grade,
        school_types=list(opp.accepted_school_types or []),
        mandatory_supported=opp.mandatory_practicum_supported,
        windows=[(w.start_date, w.end_date) for w in opp.date_windows],
        flexible_dates=opp.flexible_dates,
        deadline=opp.application_deadline,
        availability=opp.availability,
        status=opp.status,
        verification_status=opp.verification_status,
        last_verified_at=opp.last_verified_at,
        expires_at=opp.expires_at,
        point=point,
    )


@dataclass
class RuleChoice:
    config: TravelRuleConfig
    rule: TravelRule | None


def travel_rule_for_state(db: Session, state_code: str | None) -> RuleChoice:
    rule = None
    if state_code:
        rule = db.scalar(
            select(TravelRule).join(FederalState).where(FederalState.code == state_code, TravelRule.active.is_(True))
        )
    if rule is None:
        rule = db.scalar(select(TravelRule).where(TravelRule.state_id.is_(None), TravelRule.active.is_(True)))
    default_minutes = int(get_setting(db, "default_max_travel_minutes"))
    if rule is None:
        return RuleChoice(TravelRuleConfig(max_minutes=default_minutes), None)
    return RuleChoice(
        TravelRuleConfig(
            reference=rule.reference,
            max_minutes=rule.max_travel_minutes or default_minutes,
            max_distance_km=rule.max_distance_km,
            approval_required_beyond=rule.approval_required_beyond,
        ),
        rule,
    )


def match_config(db: Session, state_code: str | None = None) -> tuple[MatchConfig, TravelRule | None]:
    choice = travel_rule_for_state(db, state_code)
    return (
        MatchConfig(
            verification_period_days=int(get_setting(db, "verification_period_days")),
            deadline_warning_days=int(get_setting(db, "deadline_warning_days")),
            travel_rule=choice.config,
            travel_profiles=get_setting(db, "travel_profiles"),
        ),
        choice.rule,
    )
