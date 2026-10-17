"""Postcode centroids: the bundled Germany-wide dataset and CSV imports.

The bundled file app/seed_data/postcodes_de.csv.gz is derived from GeoNames (www.geonames.org,
CC BY 4.0). Rebuild it with scripts/build_postcodes.py.
"""
from __future__ import annotations

import csv
import gzip
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import City, FederalState, Postcode

BUNDLED = Path(__file__).resolve().parent / "seed_data" / "postcodes_de.csv.gz"
ATTRIBUTION = "Postleitzahlen-Koordinaten: GeoNames (www.geonames.org), Lizenz CC BY 4.0"


def read_csv(fh) -> Iterable[dict]:
    sample = fh.read(2048)
    fh.seek(0)
    return csv.DictReader(fh, delimiter=";" if ";" in sample else ",")


def import_rows(db: Session, rows: Iterable[dict], overwrite: bool = True) -> int:
    """Insert or update postcodes from rows with keys plz, ort, bundesland (code or name), lat, lon.

    With overwrite=False existing postcodes are left as they are (so admin edits survive).
    """
    states = {s.code: s for s in db.scalars(select(FederalState))}
    states.update({s.name: s for s in list(states.values())})
    cities = {(c.name, c.state_id): c for c in db.scalars(select(City))}
    existing = {p.code: p for p in db.scalars(select(Postcode))}
    valid: list[tuple[str, str, FederalState, float, float]] = []
    for row in rows:
        code = (row.get("plz") or "").strip()
        st = states.get((row.get("bundesland") or "").strip())
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(code) != 5 or not code.isdigit() or st is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if code in existing and not overwrite:
            continue
        valid.append((code, (row.get("ort") or "").strip()[:120] or code, st, lat, lon))
    new_cities = []
    for _, name, st, _, _ in valid:
        if (name, st.id) not in cities:
            cities[(name, st.id)] = City(name=name, state_id=st.id)
            new_cities.append(cities[(name, st.id)])
    db.add_all(new_cities)
    db.flush()
    for code, name, st, lat, lon in valid:
        city = cities[(name, st.id)]
        pc = existing.get(code)
        if pc is None:
            existing[code] = Postcode(code=code, city_id=city.id, latitude=lat, longitude=lon)
            db.add(existing[code])
        else:
            pc.city_id, pc.latitude, pc.longitude = city.id, lat, lon
    n = len(valid)
    db.commit()
    return n


def import_file(db: Session, path: str | Path, overwrite: bool = True) -> int:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as fh:
        return import_rows(db, read_csv(fh), overwrite=overwrite)


def load_bundled(db: Session) -> int:
    """Add the Germany-wide postcodes once. Existing postcodes are kept."""
    if not BUNDLED.exists() or db.scalar(select(func.count(Postcode.id))) >= 5000:
        return 0
    return import_file(db, BUNDLED, overwrite=False)
