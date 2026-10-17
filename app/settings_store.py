"""Runtime-configurable settings stored in the database (editable by admins)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .geo import DEFAULT_TRAVEL_PROFILES
from .models import Setting

DEFAULTS: dict = {
    "verification_period_days": 90,
    "deadline_warning_days": 14,
    "default_max_travel_minutes": 60,
    "scraper_not_seen_days": 14,
    "travel_profiles": DEFAULT_TRAVEL_PROFILES,
}


def get_setting(db: Session, key: str):
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
