"""Command-line tasks: python -m app.cli <command>

  create-admin EMAIL           create an administrator (prompts for password)
  import-postcodes FILE.csv    import postcode centroids (columns: plz;ort;bundesland;lat;lon)
  run-scrapers                 run all enabled scrape sources once
  expire                       mark outdated listings as expired
  seed-demo                    insert fictional demo listings
"""
from __future__ import annotations

import csv
import getpass
import sys

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import AdminUser, City, FederalState, Postcode
from .seed import seed_demo, seed_reference


def create_admin(email: str) -> None:
    pw = getpass.getpass("Passwort (mind. 12 Zeichen): ")
    if pw != getpass.getpass("Passwort wiederholen: "):
        sys.exit("Passwörter stimmen nicht überein.")
    from .security import hash_password

    db = SessionLocal()
    try:
        if db.scalar(select(AdminUser).where(AdminUser.email == email.lower())):
            sys.exit("Diesen Admin gibt es schon.")
        db.add(AdminUser(email=email.lower(), password_hash=hash_password(pw)))
        db.commit()
        print(f"Admin {email} angelegt. Beim ersten Login wird die Zwei-Faktor-Anmeldung eingerichtet.")
    finally:
        db.close()


def import_postcodes(path: str) -> None:
    db = SessionLocal()
    states = {s.code: s for s in db.scalars(select(FederalState))}
    states.update({s.name: s for s in states.values()})
    n = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter=";" if ";" in sample else ",")
        for row in reader:
            code = (row.get("plz") or "").strip()
            st = states.get((row.get("bundesland") or "").strip())
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (KeyError, ValueError):
                continue
            if len(code) != 5 or not code.isdigit() or st is None:
                continue
            name = (row.get("ort") or "").strip()[:120]
            city = db.scalar(select(City).where(City.name == name, City.state_id == st.id))
            if city is None:
                city = City(name=name, state_id=st.id)
                db.add(city)
                db.flush()
            pc = db.scalar(select(Postcode).where(Postcode.code == code))
            if pc is None:
                db.add(Postcode(code=code, city_id=city.id, latitude=lat, longitude=lon))
            else:
                pc.city_id, pc.latitude, pc.longitude = city.id, lat, lon
            n += 1
    db.commit()
    db.close()
    print(f"{n} Postleitzahlen importiert/aktualisiert.")


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    init_db()
    db = SessionLocal()
    seed_reference(db)
    db.close()
    cmd, args = argv[0], argv[1:]
    if cmd == "create-admin" and args:
        create_admin(args[0])
    elif cmd == "import-postcodes" and args:
        import_postcodes(args[0])
    elif cmd == "run-scrapers":
        from .scrapers.runner import run_all_enabled

        db = SessionLocal()
        for run in run_all_enabled(db, actor="cli"):
            print(("OK  " if run.ok else "ERR ") + (run.message or ""))
        db.close()
    elif cmd == "expire":
        from .listings import expire_outdated

        db = SessionLocal()
        print(f"{expire_outdated(db, actor='cli')} Angebote abgelaufen.")
        db.commit()
        db.close()
    elif cmd == "seed-demo":
        db = SessionLocal()
        print(f"{seed_demo(db)} Demo-Angebote angelegt.")
        db.close()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
