"""Matching, eligibility and ranking.

Pure functions over plain dataclasses so the logic is easy to test and to
explain. Every decision produces a human-readable German reason; nothing is
hidden from the student. Unknown listing data is never treated as a fit.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date

from .geo import DEFAULT_TRAVEL_PROFILES, Point, estimate_minutes, haversine_km


class Fit(str, enum.Enum):
    FIT = "fit"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NO_FIT = "no_fit"
    NOT_ASKED = "not_asked"  # the student did not provide this criterion


class Tier(str, enum.Enum):
    STRONG = "strong"
    POSSIBLE = "possible"
    NOT_ELIGIBLE = "not_eligible"


@dataclass
class TravelRuleConfig:
    reference: str = "either"  # school | home | either
    max_minutes: int | None = 60
    max_distance_km: float | None = None
    approval_required_beyond: bool = True


@dataclass
class StudentQuery:
    today: date
    domain_ids: set[int] = field(default_factory=set)
    # Parents of selected subdomains: a listing in the parent domain is "related".
    related_domain_ids: set[int] = field(default_factory=set)
    age: int | None = None
    grade: int | None = None
    start: date | None = None
    end: date | None = None
    school: Point | None = None
    home: Point | None = None
    max_minutes: int | None = None
    mode: str = "oepnv"
    school_type: str | None = None
    mandatory: bool = False


@dataclass
class ListingFacts:
    id: int
    domain_ids: set[int] = field(default_factory=set)  # includes parents of subdomains
    direct_domain_ids: set[int] = field(default_factory=set)
    min_age: int | None = None
    max_age: int | None = None
    min_grade: int | None = None
    max_grade: int | None = None
    school_types: list[str] = field(default_factory=list)
    mandatory_supported: bool | None = None
    windows: list[tuple[date, date]] = field(default_factory=list)
    flexible_dates: bool = False
    deadline: date | None = None
    availability: str = "unknown"
    status: str = "published"
    verification_status: str = "unverified"
    last_verified_at: date | None = None
    expires_at: date | None = None
    point: Point | None = None


@dataclass
class Label:
    key: str
    text: str
    explanation: str
    tone: str  # good | info | warn | bad


@dataclass
class TravelEstimate:
    reference: str  # "school" | "home"
    distance_km: float
    minutes: int
    precision: str


@dataclass
class MatchResult:
    listing_id: int
    tier: Tier
    labels: list[Label]
    checks: dict[str, Fit]
    reasons: dict[str, str]
    travel: TravelEstimate | None
    domain_score: int
    sort_key: tuple

    @property
    def primary_label(self) -> Label:
        return self.labels[0]


@dataclass
class MatchConfig:
    verification_period_days: int = 90
    deadline_warning_days: int = 14
    travel_rule: TravelRuleConfig = field(default_factory=TravelRuleConfig)
    travel_profiles: dict = field(default_factory=lambda: dict(DEFAULT_TRAVEL_PROFILES))


def _fmt(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def check_age(q: StudentQuery, l: ListingFacts) -> tuple[Fit, str]:
    if q.age is None:
        return Fit.NOT_ASKED, "Du hast kein Alter angegeben."
    if l.min_age is None and l.max_age is None:
        return Fit.UNKNOWN, "Das Mindestalter ist nicht angegeben – bitte beim Betrieb nachfragen."
    if l.min_age is not None and q.age < l.min_age:
        return Fit.NO_FIT, f"Du musst mindestens {l.min_age} Jahre alt sein (du bist {q.age})."
    if l.max_age is not None and q.age > l.max_age:
        return Fit.NO_FIT, f"Das Angebot ist für Schüler*innen bis {l.max_age} Jahre (du bist {q.age})."
    rng = (
        f"ab {l.min_age} Jahren" if l.max_age is None
        else f"bis {l.max_age} Jahre" if l.min_age is None
        else f"{l.min_age}–{l.max_age} Jahre"
    )
    return Fit.FIT, f"Dein Alter passt ({rng})."


def check_grade(q: StudentQuery, l: ListingFacts) -> tuple[Fit, str]:
    if q.grade is None:
        return Fit.NOT_ASKED, "Du hast keine Klassenstufe angegeben."
    if l.min_grade is None and l.max_grade is None:
        return Fit.UNKNOWN, "Die Klassenstufe ist nicht angegeben – bitte nachfragen."
    if l.min_grade is not None and q.grade < l.min_grade:
        return Fit.NO_FIT, f"Das Angebot ist ab Klasse {l.min_grade} (du bist in Klasse {q.grade})."
    if l.max_grade is not None and q.grade > l.max_grade:
        return Fit.NO_FIT, f"Das Angebot ist bis Klasse {l.max_grade} (du bist in Klasse {q.grade})."
    return Fit.FIT, f"Deine Klassenstufe ({q.grade}) passt."


def check_school_type(q: StudentQuery, l: ListingFacts) -> tuple[Fit, str]:
    if not q.school_type:
        return Fit.NOT_ASKED, ""
    if not l.school_types:
        return Fit.UNKNOWN, "Keine Einschränkung der Schulform angegeben."
    if q.school_type in l.school_types:
        return Fit.FIT, "Deine Schulform wird angenommen."
    return Fit.NO_FIT, "Deine Schulform ist laut Angebot nicht vorgesehen: " + ", ".join(l.school_types) + "."


def check_mandatory(q: StudentQuery, l: ListingFacts) -> tuple[Fit, str]:
    if not q.mandatory:
        return Fit.NOT_ASKED, ""
    if l.mandatory_supported is None:
        return Fit.UNKNOWN, "Nicht angegeben, ob Pflichtpraktika der Schule angenommen werden."
    if l.mandatory_supported:
        return Fit.FIT, "Pflichtpraktika der Schule werden angenommen."
    return Fit.NO_FIT, "Pflichtpraktika der Schule werden laut Angebot nicht angenommen."


def check_dates(q: StudentQuery, l: ListingFacts) -> tuple[Fit, str]:
    if not q.start or not q.end:
        return Fit.NOT_ASKED, "Gib deine Praktikumsdaten an, damit wir die Termine prüfen können."
    if not l.windows:
        if l.flexible_dates:
            return Fit.UNKNOWN, "Der Betrieb bietet flexible Termine an – frag nach, ob dein Zeitraum passt."
        return Fit.UNKNOWN, "Keine Termine angegeben – bitte beim Betrieb nachfragen."
    covering = [w for w in l.windows if w[0] <= q.start and w[1] >= q.end]
    if covering:
        w = covering[0]
        return Fit.FIT, f"Dein Zeitraum liegt im angebotenen Zeitraum ({_fmt(w[0])}–{_fmt(w[1])})."
    overlapping = [w for w in l.windows if w[0] <= q.end and w[1] >= q.start]
    if overlapping:
        w = overlapping[0]
        return Fit.PARTIAL, (
            f"Der angebotene Zeitraum ({_fmt(w[0])}–{_fmt(w[1])}) deckt deinen Zeitraum nur teilweise ab."
        )
    if l.flexible_dates:
        return Fit.UNKNOWN, "Die festen Termine passen nicht, aber der Betrieb ist flexibel – frag nach."
    return Fit.NO_FIT, "Die angebotenen Termine passen nicht zu deinem Praktikumszeitraum."


def domain_score(q: StudentQuery, l: ListingFacts) -> int:
    """2 = direct match, 1 = related (same parent), 0 = no match. No selection = 1 (neutral)."""
    if not q.domain_ids:
        return 1
    if q.domain_ids & l.domain_ids:
        return 2
    if q.related_domain_ids & l.domain_ids:
        return 1
    return 0


def estimate_travel(q: StudentQuery, l: ListingFacts, cfg: MatchConfig) -> TravelEstimate | None:
    if l.point is None:
        return None
    refs: list[tuple[str, Point]] = []
    rule_ref = cfg.travel_rule.reference
    if q.school and rule_ref in ("school", "either"):
        refs.append(("school", q.school))
    if q.home and rule_ref in ("home", "either"):
        refs.append(("home", q.home))
    if not refs:
        return None
    best: TravelEstimate | None = None
    for name, pt in refs:
        km = haversine_km(pt, l.point)
        mins = estimate_minutes(km, q.mode, cfg.travel_profiles)
        precision = "prefix" if "prefix" in (pt.precision, l.point.precision) else "postcode"
        est = TravelEstimate(name, round(km, 1), mins, precision)
        if best is None or est.minutes < best.minutes:
            best = est
    return best


def check_travel(q: StudentQuery, est: TravelEstimate | None, cfg: MatchConfig) -> tuple[Fit, str]:
    if not q.school and not q.home:
        return Fit.NOT_ASKED, "Gib die Postleitzahl deiner Schule an, damit wir den Weg schätzen können."
    if est is None:
        return Fit.UNKNOWN, "Der Weg kann nicht geschätzt werden (Ort unbekannt)."
    limit = q.max_minutes or cfg.travel_rule.max_minutes
    ref = "deiner Schule" if est.reference == "school" else "deinem Zuhause"
    base = f"ca. {est.minutes} Min. von {ref} (Luftlinie {est.distance_km:.1f} km, grobe Schätzung)"
    if cfg.travel_rule.max_distance_km and est.distance_km > cfg.travel_rule.max_distance_km:
        return Fit.PARTIAL, base + f" – weiter als {cfg.travel_rule.max_distance_km:g} km."
    if limit and est.minutes > limit:
        return Fit.PARTIAL, base + f" – länger als deine {limit} Minuten."
    return Fit.FIT, base + "."


def evaluate(q: StudentQuery, l: ListingFacts, cfg: MatchConfig | None = None) -> MatchResult:
    cfg = cfg or MatchConfig()
    checks: dict[str, Fit] = {}
    reasons: dict[str, str] = {}
    for key, fn in (
        ("age", check_age),
        ("grade", check_grade),
        ("school_type", check_school_type),
        ("mandatory", check_mandatory),
        ("dates", check_dates),
    ):
        checks[key], reasons[key] = fn(q, l)
    travel = estimate_travel(q, l, cfg)
    checks["travel"], reasons["travel"] = check_travel(q, travel, cfg)
    dscore = domain_score(q, l)
    checks["domain"] = Fit.NOT_ASKED if not q.domain_ids else (Fit.FIT if dscore else Fit.NO_FIT)
    reasons["domain"] = "Passt zu deinem gewählten Bereich." if dscore == 2 else (
        "Verwandter Bereich." if dscore == 1 and q.domain_ids else ""
    )

    labels: list[Label] = []
    expired = l.status == "expired" or (l.expires_at is not None and l.expires_at < q.today)
    closed = l.status == "closed" or l.availability == "full"
    deadline_passed = l.deadline is not None and l.deadline < q.today
    stale = l.last_verified_at is None or (q.today - l.last_verified_at).days > cfg.verification_period_days
    unverified = l.verification_status in ("unverified", "student_reported")

    hard_fail = [k for k in ("age", "grade", "school_type", "mandatory", "dates") if checks[k] == Fit.NO_FIT]
    if hard_fail or expired or closed or deadline_passed:
        tier = Tier.NOT_ELIGIBLE
    else:
        asked = [k for k in ("age", "grade", "school_type", "mandatory", "dates", "travel") if checks[k] != Fit.NOT_ASKED]
        all_fit = bool(asked) and all(checks[k] == Fit.FIT for k in asked)
        tier = Tier.STRONG if (all_fit and dscore >= 1 and not unverified and not stale) else Tier.POSSIBLE

    # Primary label first
    if tier == Tier.STRONG:
        names = {"age": "dein Alter", "grade": "deine Klasse", "dates": "deine Praktikumsdaten",
                 "travel": "der geschätzte Weg", "school_type": "deine Schulform", "mandatory": "Pflichtpraktikum"}
        fitting = [names[k] for k in names if checks[k] == Fit.FIT]
        if q.domain_ids:
            fitting.insert(2 if len(fitting) > 2 else len(fitting), "dein gewählter Bereich")
        labels.append(Label("strong", "Passt gut",
                            "Passt gut: " + ", ".join(fitting) + " passen zu diesem Angebot.", "good"))
    elif tier == Tier.POSSIBLE:
        open_points = [reasons[k] for k in ("age", "grade", "school_type", "mandatory", "dates", "travel")
                       if checks[k] in (Fit.UNKNOWN, Fit.PARTIAL)]
        why = " ".join(open_points) if open_points else "Einige Angaben sind noch nicht bestätigt."
        if unverified or stale:
            why = (why + " Die Angaben sind nicht aktuell bestätigt.").strip()
        labels.append(Label("possible", "Könnte passen", "Könnte passen: " + why, "info"))
    else:
        why_parts = [reasons[k] for k in hard_fail]
        if deadline_passed:
            why_parts.append(f"Die Bewerbungsfrist ist am {_fmt(l.deadline)} abgelaufen.")
        if expired:
            why_parts.append("Das Angebot ist abgelaufen.")
        if closed:
            why_parts.append("Laut Angebot sind keine Plätze mehr frei.")
        labels.append(Label("not_eligible", "Passt nicht", "Passt nicht: " + " ".join(why_parts), "bad"))

    if checks["travel"] == Fit.PARTIAL and cfg.travel_rule.approval_required_beyond:
        labels.append(Label("school_approval", "Schule muss zustimmen",
                            reasons["travel"] + " Deine Schule entscheidet, ob ein weiter entfernter "
                            "Praktikumsplatz in Ordnung ist.", "warn"))
    if l.deadline and not deadline_passed and (l.deadline - q.today).days <= cfg.deadline_warning_days:
        days = (l.deadline - q.today).days
        labels.append(Label("deadline", "Frist läuft bald ab",
                            f"Bewerbungsfrist: {_fmt(l.deadline)} (noch {days} Tag{'e' if days != 1 else ''}).", "warn"))
    if unverified:
        labels.append(Label("unverified", "Nicht bestätigt",
                            "Diese Angaben wurden noch nicht vom Betrieb oder von uns bestätigt. "
                            "Bitte prüfe sie direkt beim Betrieb.", "warn"))
    elif stale:
        labels.append(Label("stale", "Länger nicht geprüft",
                            f"Zuletzt vor mehr als {cfg.verification_period_days} Tagen geprüft – "
                            "Angaben können sich geändert haben.", "warn"))
    if closed:
        labels.append(Label("closed", "Geschlossen", "Laut Angebot sind keine Plätze mehr frei.", "bad"))
    if expired:
        labels.append(Label("expired", "Abgelaufen", "Dieses Angebot ist abgelaufen.", "bad"))
    if deadline_passed and not expired:
        labels.append(Label("deadline_passed", "Frist abgelaufen",
                            f"Die Bewerbungsfrist war am {_fmt(l.deadline)}.", "bad"))

    tier_rank = {Tier.STRONG: 0, Tier.POSSIBLE: 1, Tier.NOT_ELIGIBLE: 2}[tier]
    date_rank = {Fit.FIT: 0, Fit.NOT_ASKED: 1, Fit.PARTIAL: 1, Fit.UNKNOWN: 2, Fit.NO_FIT: 3}[checks["dates"]]
    travel_bucket = (travel.minutes // 10) if travel else 999
    verify_age = (q.today - l.last_verified_at).days if l.last_verified_at else 99999
    deadline_ord = l.deadline.toordinal() if l.deadline and not deadline_passed else 10**7
    avail_rank = {"open": 0, "few_places": 1, "unknown": 2, "full": 3}.get(l.availability, 2)
    sort_key = (tier_rank, date_rank, travel_bucket, -dscore, verify_age, deadline_ord, avail_rank, l.id)

    return MatchResult(l.id, tier, labels, checks, reasons, travel, dscore, sort_key)


def rank(q: StudentQuery, listings: list[ListingFacts], cfg: MatchConfig | None = None) -> list[MatchResult]:
    results = [evaluate(q, l, cfg) for l in listings]
    return sorted(results, key=lambda r: r.sort_key)
