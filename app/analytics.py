"""Privacy-preserving analytics: daily aggregate counters only.

No IP addresses, cookies, user agents or free text are stored. Dimensions are
restricted to coarse, non-identifying values (domain slug, result bucket).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AnalyticsDaily

ALLOWED_EVENTS = {
    "search_started",
    "search_completed",
    "search_zero_results",
    "domain_selected",
    "results_bucket",
    "opportunity_view",
    "saved",
    "helper_started",
    "helper_completed",
    "contact_click",
    "report_submitted",
    "employer_submission",
}

# Events the browser may send via /api/event (others are server-side only).
CLIENT_EVENTS = {"saved", "helper_completed", "contact_click"}


def bucket(n: int) -> str:
    if n == 0:
        return "0"
    if n <= 5:
        return "1-5"
    if n <= 20:
        return "6-20"
    return "21+"


def track(db: Session, event: str, dimension: str = "", today: date | None = None) -> None:
    if event not in ALLOWED_EVENTS:
        return
    day = today or date.today()
    dimension = (dimension or "")[:80]
    row = db.scalar(
        select(AnalyticsDaily).where(
            AnalyticsDaily.day == day, AnalyticsDaily.event == event, AnalyticsDaily.dimension == dimension
        )
    )
    if row is None:
        row = AnalyticsDaily(day=day, event=event, dimension=dimension, count=0)
        db.add(row)
    row.count += 1
