"""Command-line tasks: python -m app.cli <command>

  create-admin EMAIL           create an administrator (prompts for password)
  import-postcodes FILE.csv    import postcode centroids (columns: plz;ort;bundesland;lat;lon), .csv or .csv.gz
  import-postcodes --bundled   re-import the bundled Germany-wide postcodes, overwriting existing ones
  run-scrapers                 run all enabled scrape sources once
  expire                       mark outdated listings as expired
  seed-demo                    insert fictional demo listings
  remove-demo                  delete all fictional demo listings
  check-links                  check whether the links of published listings still work
"""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import AdminUser
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
    from .postcodes import BUNDLED, import_file

    db = SessionLocal()
    try:
        n = import_file(db, BUNDLED if path == "--bundled" else path)
    finally:
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
    elif cmd == "remove-demo":
        from .listings import remove_demo

        db = SessionLocal()
        print(f"{remove_demo(db, actor='cli')} Demo-Angebote entfernt.")
        db.close()
    elif cmd == "check-links":
        from .linkcheck import check_links

        db = SessionLocal()
        result = check_links(db)
        print(f"{result.checked} Links geprüft: {result.ok} ok, {result.broken} defekt, {result.unknown} unklar.")
        db.close()
    elif cmd == "seed-demo":
        db = SessionLocal()
        print(f"{seed_demo(db)} Demo-Angebote angelegt.")
        db.close()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
