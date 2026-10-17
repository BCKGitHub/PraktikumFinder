"""Relational data model.

Students are deliberately NOT stored server-side in the MVP: search is anonymous
and saved listings / the application tracker live in the student's own browser
(see docs/PLAN.md, "Privacy by design"). Student accounts, guardian consent and
notifications are modelled in the plan for a later milestone.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Locations (configurable data, nothing Frankfurt-specific in code)
# --------------------------------------------------------------------------


class FederalState(Base):
    __tablename__ = "federal_states"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(5), unique=True)  # e.g. "HE"
    name: Mapped[str] = mapped_column(String(100))
    cities: Mapped[list[City]] = relationship(back_populates="state")


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (UniqueConstraint("name", "state_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    state_id: Mapped[int] = mapped_column(ForeignKey("federal_states.id"))
    in_launch_region: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[FederalState] = relationship(back_populates="cities")
    postcodes: Mapped[list[Postcode]] = relationship(back_populates="city")


class Postcode(Base):
    __tablename__ = "postcodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(5), unique=True, index=True)
    city_id: Mapped[int] = mapped_column(ForeignKey("cities.id"))
    # Approximate centroid. Never a student's exact address.
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    city: Mapped[City] = relationship(back_populates="postcodes")


class School(Base):
    __tablename__ = "schools"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    school_type: Mapped[str | None] = mapped_column(String(60))
    postcode_id: Mapped[int | None] = mapped_column(ForeignKey("postcodes.id"))
    postcode: Mapped[Postcode | None] = relationship()
    periods: Mapped[list[SchoolPracticumPeriod]] = relationship(
        back_populates="school", cascade="all, delete-orphan"
    )


class SchoolPracticumPeriod(Base):
    __tablename__ = "school_practicum_periods"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_period_dates"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"))
    grade: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    school: Mapped[School] = relationship(back_populates="periods")


class TravelReference(str, enum.Enum):
    school = "school"
    home = "home"
    either = "either"


class TravelRule(Base):
    """Configurable reachability rule. state_id NULL = default for all states."""

    __tablename__ = "travel_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    state_id: Mapped[int | None] = mapped_column(ForeignKey("federal_states.id"))
    reference: Mapped[str] = mapped_column(String(10), default=TravelReference.either.value)
    max_travel_minutes: Mapped[int | None] = mapped_column(Integer)
    max_distance_km: Mapped[float | None] = mapped_column(Float)
    # When exceeded the listing is still shown, flagged "Schule muss zustimmen".
    approval_required_beyond: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[FederalState | None] = relationship()


# --------------------------------------------------------------------------
# Domains / taxonomy
# --------------------------------------------------------------------------

opportunity_domains = Table(
    "opportunity_domains",
    Base.metadata,
    Column("opportunity_id", ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True),
    Column("domain_id", ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True),
)

opportunity_tags = Table(
    "opportunity_tags",
    Base.metadata,
    Column("opportunity_id", ForeignKey("opportunities.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("activity_tags.id", ondelete="CASCADE"), primary_key=True),
)


class Domain(Base):
    """A domain (Bereich) or, when parent_id is set, a subdomain."""

    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(8))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("domains.id"))
    position: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    # Keywords used to suggest domains for imported listings (data-driven).
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    # Related job categories (e.g. "Pflegefachkraft", "Fachinformatiker/in").
    related_jobs: Mapped[list] = mapped_column(JSON, default=list)

    parent: Mapped[Domain | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Domain]] = relationship(back_populates="parent", order_by="Domain.position")


class ActivityTag(Base):
    __tablename__ = "activity_tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


# --------------------------------------------------------------------------
# Employers
# --------------------------------------------------------------------------


class Employer(Base):
    __tablename__ = "employers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    employer_type: Mapped[str | None] = mapped_column(String(60))  # Unternehmen, Klinik, Behörde ...
    website: Mapped[str | None] = mapped_column(String(500))
    address_line: Mapped[str | None] = mapped_column(String(200))
    postcode_code: Mapped[str | None] = mapped_column(String(5))
    city_name: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    contacts: Mapped[list[EmployerContact]] = relationship(
        back_populates="employer", cascade="all, delete-orphan"
    )
    opportunities: Mapped[list[Opportunity]] = relationship(back_populates="employer")


class EmployerContact(Base):
    __tablename__ = "employer_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(60))
    # Only shown publicly when the employer allowed it.
    publish_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    employer: Mapped[Employer] = relationship(back_populates="contacts")


# --------------------------------------------------------------------------
# Opportunities
# --------------------------------------------------------------------------


class ListingStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    awaiting_review = "awaiting_review"
    published = "published"
    expired = "expired"
    closed = "closed"
    rejected = "rejected"


class VerificationStatus(str, enum.Enum):
    unverified = "unverified"
    employer_confirmed = "employer_confirmed"
    official_source_verified = "official_source_verified"
    admin_verified = "admin_verified"
    student_reported = "student_reported"


class Availability(str, enum.Enum):
    unknown = "unknown"
    open = "open"
    few_places = "few_places"
    full = "full"


class Opportunity(Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("min_age IS NULL OR (min_age >= 10 AND min_age <= 25)", name="ck_min_age"),
        CheckConstraint("max_age IS NULL OR (max_age >= 10 AND max_age <= 25)", name="ck_max_age"),
        CheckConstraint(
            "min_age IS NULL OR max_age IS NULL OR max_age >= min_age", name="ck_age_range"
        ),
        CheckConstraint(
            "min_grade IS NULL OR max_grade IS NULL OR max_grade >= min_grade", name="ck_grade_range"
        ),
        CheckConstraint("number_of_places IS NULL OR number_of_places >= 0", name="ck_places"),
        UniqueConstraint("source", "external_id", name="uq_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employer_id: Mapped[int] = mapped_column(ForeignKey("employers.id"))
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text)

    # Location
    location_label: Mapped[str | None] = mapped_column(String(250))  # e.g. "Frankfurt-Sachsenhausen"
    address_public: Mapped[bool] = mapped_column(Boolean, default=True)
    street: Mapped[str | None] = mapped_column(String(200))
    postcode_code: Mapped[str | None] = mapped_column(String(5), index=True)
    city_name: Mapped[str | None] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    travel_information: Mapped[str | None] = mapped_column(Text)  # ÖPNV-Hinweise

    # Eligibility
    min_age: Mapped[int | None] = mapped_column(Integer)
    max_age: Mapped[int | None] = mapped_column(Integer)
    min_grade: Mapped[int | None] = mapped_column(Integer)
    max_grade: Mapped[int | None] = mapped_column(Integer)
    accepted_school_types: Mapped[list] = mapped_column(JSON, default=list)
    mandatory_practicum_supported: Mapped[bool | None] = mapped_column(Boolean)
    language_requirements: Mapped[str | None] = mapped_column(String(250))
    clothing_safety: Mapped[str | None] = mapped_column(Text)
    school_agreement_required: Mapped[bool | None] = mapped_column(Boolean)
    school_approval_note: Mapped[str | None] = mapped_column(Text)

    # Dates & availability
    flexible_dates: Mapped[bool] = mapped_column(Boolean, default=False)
    flexible_dates_note: Mapped[str | None] = mapped_column(String(250))
    application_deadline: Mapped[date | None] = mapped_column(Date)
    number_of_places: Mapped[int | None] = mapped_column(Integer)
    availability: Mapped[str] = mapped_column(String(20), default=Availability.unknown.value)

    # Application
    application_method: Mapped[str | None] = mapped_column(String(60))  # E-Mail, Online-Formular, Post
    application_email: Mapped[str | None] = mapped_column(String(200))
    official_url: Mapped[str | None] = mapped_column(String(800))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("employer_contacts.id"))

    # Lifecycle & verification
    status: Mapped[str] = mapped_column(String(20), default=ListingStatus.draft.value, index=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), default=VerificationStatus.unverified.value
    )
    last_verified_at: Mapped[date | None] = mapped_column(Date)
    verification_source: Mapped[str | None] = mapped_column(String(300))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    next_review_at: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date | None] = mapped_column(Date)
    data_quality_notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    # Provenance (manual | employer_submission | scraper key)
    source: Mapped[str] = mapped_column(String(60), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(800))
    last_seen_in_source_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Result of the last dead-link check of official_url/source_url: ok | broken | unknown
    link_status: Mapped[str | None] = mapped_column(String(20))
    link_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    link_check_note: Mapped[str | None] = mapped_column(String(300))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    employer: Mapped[Employer] = relationship(back_populates="opportunities")
    contact: Mapped[EmployerContact | None] = relationship()
    domains: Mapped[list[Domain]] = relationship(secondary=opportunity_domains)
    tags: Mapped[list[ActivityTag]] = relationship(secondary=opportunity_tags)
    date_windows: Mapped[list[OpportunityDateWindow]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", order_by="OpportunityDateWindow.start_date"
    )
    documents: Mapped[list[OpportunityDocument]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    verifications: Mapped[list[VerificationRecord]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan", order_by="VerificationRecord.verified_at.desc()"
    )
    reports: Mapped[list[Report]] = relationship(back_populates="opportunity", cascade="all, delete-orphan")


class OpportunityDateWindow(Base):
    """A period in which the employer accepts practicum students."""

    __tablename__ = "opportunity_date_windows"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_window_dates"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    opportunity: Mapped[Opportunity] = relationship(back_populates="date_windows")


class OpportunityDocument(Base):
    __tablename__ = "opportunity_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    opportunity: Mapped[Opportunity] = relationship(back_populates="documents")


class VerificationRecord(Base):
    __tablename__ = "verification_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    verification_status: Mapped[str] = mapped_column(String(30))
    source: Mapped[str | None] = mapped_column(String(300))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    opportunity: Mapped[Opportunity] = relationship(back_populates="verifications")


class Report(Base):
    """A student/parent report that a listing is inaccurate. No personal data stored."""

    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(60))
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution: Mapped[str | None] = mapped_column(Text)
    opportunity: Mapped[Opportunity] = relationship(back_populates="reports")


# --------------------------------------------------------------------------
# Employer submissions
# --------------------------------------------------------------------------


class EmployerSubmission(Base):
    """Raw employer submission. Becomes an Opportunity only after admin review."""

    __tablename__ = "employer_submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="new")  # new | update
    target_opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    consent_id: Mapped[int | None] = mapped_column(ForeignKey("consent_records.id"))


class ConsentRecord(Base):
    """Records that a person agreed to a specific version of a notice."""

    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(30))  # employer_contact, guardian, student
    purpose: Mapped[str] = mapped_column(String(120))
    notice_version: Mapped[str] = mapped_column(String(30))
    given_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime)


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(20), default="admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)


class AnalyticsDaily(Base):
    """Privacy-preserving counters: event + optional dimension per day. No identifiers."""

    __tablename__ = "analytics_daily"
    __table_args__ = (UniqueConstraint("day", "event", "dimension"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    event: Mapped[str] = mapped_column(String(60))
    dimension: Mapped[str] = mapped_column(String(80), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------


class ScrapeSource(Base):
    __tablename__ = "scrape_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    scraper: Mapped[str] = mapped_column(String(60))  # registry key, e.g. "ba_jobsuche"
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Even when true, imported listings are published only as "Unverified".
    auto_publish_unverified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(300))
    runs: Mapped[list[ScrapeRun]] = relationship(
        back_populates="source", cascade="all, delete-orphan", order_by="ScrapeRun.started_at.desc()"
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("scrape_sources.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    found: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[ScrapeSource] = relationship(back_populates="runs")
