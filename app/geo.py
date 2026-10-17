"""Approximate distance and travel-time estimation.

The MVP does not call an external routing service (no student location leaves
the server). Travel time is estimated from straight-line distance between
postcode centroids using configurable per-mode factors, and is always shown to
the student as an approximation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Postcode

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    precision: str = "postcode"  # postcode | prefix | exact


# Default estimation profiles. Overridable via the "travel_profiles" setting.
# minutes = overhead + distance_km * detour / speed_kmh * 60
DEFAULT_TRAVEL_PROFILES: dict[str, dict[str, float | str]] = {
    "oepnv": {"label": "Bus & Bahn", "speed_kmh": 22.0, "detour": 1.35, "overhead_min": 12.0},
    "fahrrad": {"label": "Fahrrad", "speed_kmh": 15.0, "detour": 1.25, "overhead_min": 3.0},
    "zu_fuss": {"label": "Zu Fuß", "speed_kmh": 4.5, "detour": 1.25, "overhead_min": 0.0},
    "auto": {"label": "Auto (wird gebracht)", "speed_kmh": 35.0, "detour": 1.3, "overhead_min": 5.0},
}


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def estimate_minutes(distance_km: float, mode: str, profiles: dict | None = None) -> int:
    profiles = profiles or DEFAULT_TRAVEL_PROFILES
    p = profiles.get(mode) or profiles["oepnv"]
    minutes = float(p["overhead_min"]) + distance_km * float(p["detour"]) / float(p["speed_kmh"]) * 60
    return max(1, round(minutes))


def normalize_postcode(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits if len(digits) == 5 else None


def lookup_postcode(db: Session, code: str | None) -> Point | None:
    """Exact postcode centroid, else the mean of known postcodes with the same 3-digit prefix."""
    code = normalize_postcode(code)
    if not code:
        return None
    pc = db.scalar(select(Postcode).where(Postcode.code == code))
    if pc:
        return Point(pc.latitude, pc.longitude, "postcode")
    near = db.scalars(select(Postcode).where(Postcode.code.like(code[:3] + "%"))).all()
    if near:
        return Point(
            sum(p.latitude for p in near) / len(near),
            sum(p.longitude for p in near) / len(near),
            "prefix",
        )
    return None
