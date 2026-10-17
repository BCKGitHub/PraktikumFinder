"""Seed reference data (locations, domains, rules, scrape sources) and synthetic demo listings.

All demo employers and listings are fictional and marked with is_demo=True.
No real student data is ever seeded.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .listings import S
from .models import (
    City,
    Domain,
    Employer,
    EmployerContact,
    FederalState,
    Opportunity,
    OpportunityDateWindow,
    OpportunityDocument,
    Postcode,
    ScrapeSource,
    TravelRule,
    VerificationRecord,
)

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"


def seed_reference(db: Session) -> None:
    if db.scalar(select(func.count(FederalState.id))):
        return
    loc = json.loads((SEED_DIR / "locations.json").read_text(encoding="utf-8"))
    states = {}
    for s in loc["states"]:
        st = FederalState(code=s["code"], name=s["name"])
        db.add(st)
        states[s["code"]] = st
    db.flush()
    for c in loc["cities"]:
        city = City(name=c["name"], state=states[c["state"]], in_launch_region=c.get("launch", False))
        db.add(city)
        db.flush()
        for code, lat, lon in c["postcodes"]:
            db.add(Postcode(code=code, city_id=city.id, latitude=lat, longitude=lon))
    for r in loc["travel_rules"]:
        db.add(TravelRule(
            name=r["name"], state=states.get(r["state"]) if r["state"] else None, reference=r["reference"],
            max_travel_minutes=r["max_travel_minutes"], max_distance_km=r["max_distance_km"],
            approval_required_beyond=r["approval_required_beyond"], note=r["note"],
        ))

    domains = json.loads((SEED_DIR / "domains.json").read_text(encoding="utf-8"))
    for i, d in enumerate(domains):
        parent = Domain(
            slug=d["slug"], name=d["name"], icon=d.get("icon"), description=d.get("description"),
            position=(i + 1) * 10, featured=d.get("featured", False), keywords=d.get("keywords", []),
            related_jobs=d.get("related_jobs", []),
        )
        db.add(parent)
        db.flush()
        for j, c in enumerate(d.get("children", [])):
            db.add(Domain(slug=c["slug"], name=c["name"], parent_id=parent.id, position=(j + 1) * 10,
                          keywords=c.get("keywords", [])))

    # Scrape sources: an official public API, disabled until an admin reviews and enables them.
    for city in ("Frankfurt am Main", "Offenbach am Main", "Darmstadt", "Mainz", "Wiesbaden"):
        db.add(ScrapeSource(
            scraper="ba_jobsuche", name=f"BA Jobsuche – Schülerpraktikum {city}", enabled=False,
            config={"was": "Schülerpraktikum", "wo": city, "umkreis": 15, "angebotsart": 34, "max_pages": 3},
        ))
    db.commit()


def _d(today: date, days: int) -> date:
    return today + timedelta(days=days)


DEMO_LISTINGS = [
    dict(employer="Beispiel-Klinikum Mainufer (fiktiv)", etype="Krankenhaus", pc="60596", city="Frankfurt am Main",
         label="Frankfurt-Sachsenhausen", title="Schülerpraktikum in der Pflege", domains=["gesundheit-soziales", "pflege"],
         desc="Du begleitest Pflegekräfte auf einer Station, lernst Abläufe im Krankenhaus kennen und hilfst bei einfachen Aufgaben wie Essen verteilen oder Betten vorbereiten.",
         min_age=15, max_age=17, min_grade=9, max_grade=10, mandatory=True, windows=[(30, 130)], deadline=25,
         places=4, availability="open", method="E-Mail", email="praktikum@klinikum-mainufer.example",
         docs=["Kurzes Anschreiben", "Lebenslauf", "Letztes Zeugnis", "Nachweis Masernschutz"],
         clothing="Dienstkleidung wird gestellt. Bitte geschlossene, helle Schuhe mitbringen.",
         transit="Ca. 5 Minuten zu Fuß von der Haltestelle „Beispielplatz“ (Tram/Bus).",
         verification="admin_verified", verified_days_ago=10, school_agreement=True),
    dict(employer="Praxis Dr. Muster (fiktiv)", etype="Arztpraxis", pc="63065", city="Offenbach am Main",
         label="Offenbach-Innenstadt", title="Praktikum Medizinische Fachangestellte", domains=["arztpraxis"],
         desc="Einblick in Empfang, Terminvergabe und einfache Tätigkeiten in einer Hausarztpraxis.",
         min_age=14, max_age=None, min_grade=8, max_grade=10, mandatory=True, windows=[], flexible=True,
         flex_note="Termine nach Absprache, nicht in den Sommerferien.", deadline=None, places=1,
         availability="unknown", method="Telefon oder E-Mail", email="praxis@dr-muster.example",
         docs=["Lebenslauf", "Kurzes Anschreiben"], verification="unverified", verified_days_ago=None),
    dict(employer="Pixelwerk Software GmbH (fiktiv)", etype="Unternehmen", pc="60316", city="Frankfurt am Main",
         label="Frankfurt-Nordend", title="Schülerpraktikum Softwareentwicklung", domains=["it-software", "softwareentwicklung"],
         desc="Du lernst, wie ein Team eine App baut: Planung, Programmieren, Testen. Du schreibst ein kleines eigenes Programm mit Unterstützung.",
         min_age=14, max_age=17, min_grade=8, max_grade=10, mandatory=True, windows=[(20, 60), (90, 160)], deadline=40,
         places=2, availability="few_places", method="Online-Formular", email=None,
         url="https://pixelwerk.example/karriere/schuelerpraktikum",
         docs=["Lebenslauf", "Kurze Motivation (3–5 Sätze)"], languages="Deutsch",
         transit="U-Bahn-Station in der Nähe (ca. 3 Minuten).", verification="employer_confirmed", verified_days_ago=20),
    dict(employer="Netz & Service IT (fiktiv)", etype="Unternehmen", pc="64283", city="Darmstadt",
         label="Darmstadt-Mitte", title="Praktikum IT-Support und Netzwerke", domains=["it-support"],
         desc="Rechner einrichten, Netzwerkkabel testen, Kund*innen-Anfragen im IT-Support mitverfolgen.",
         min_age=15, max_age=None, min_grade=9, max_grade=10, mandatory=True, windows=[(15, 120)], deadline=7,
         places=1, availability="open", method="E-Mail", email="ausbildung@netz-service.example",
         docs=["Lebenslauf", "Anschreiben", "Letztes Zeugnis"], verification="admin_verified", verified_days_ago=30),
    dict(employer="Autohaus Rhein-Main Beispiel (fiktiv)", etype="Kfz-Betrieb", pc="63067", city="Offenbach am Main",
         label="Offenbach", title="Schülerpraktikum Kfz-Mechatronik", domains=["auto-mobilitaet", "kfz-werkstatt"],
         desc="Reifenwechsel, Inspektion und Fehlersuche am Fahrzeug – immer zusammen mit einem Gesellen oder einer Gesellin.",
         min_age=14, max_age=17, min_grade=8, max_grade=10, mandatory=True, windows=[(10, 200)], deadline=60,
         places=3, availability="open", method="E-Mail", email="werkstatt@autohaus-beispiel.example",
         docs=["Lebenslauf", "Kurzes Anschreiben"],
         clothing="Arbeitskleidung und Sicherheitsschuhe werden gestellt (Schuhgröße bitte vorher angeben).",
         verification="employer_confirmed", verified_days_ago=5),
    dict(employer="Mobility Lab Beispiel (fiktiv)", etype="Unternehmen", pc="65428", city="Rüsselsheim am Main",
         label="Rüsselsheim", title="Praktikum Fahrzeugtechnik und E-Mobilität", domains=["fahrzeugtechnik"],
         desc="Einblicke in Prüfstände, Elektroantriebe und Konstruktion am Computer.",
         min_age=15, max_age=17, min_grade=9, max_grade=10, mandatory=True, windows=[(30, 150)], deadline=50,
         places=2, availability="open", method="Online-Formular", url="https://mobility-lab.example/praktikum",
         docs=["Lebenslauf", "Letztes Zeugnis"], verification="admin_verified", verified_days_ago=40),
    dict(employer="Bürgerservice Beispielstadt (fiktiv)", etype="Behörde", pc="65183", city="Wiesbaden",
         label="Wiesbaden-Mitte", title="Praktikum in der Stadtverwaltung", domains=["oeffentlicher-dienst"],
         desc="Du lernst verschiedene Ämter kennen: Bürgerbüro, Standesamt und Öffentlichkeitsarbeit.",
         min_age=14, max_age=None, min_grade=8, max_grade=10, mandatory=True, windows=[(45, 75)], deadline=30,
         places=5, availability="open", method="E-Mail", email="praktikum@beispielstadt.example",
         docs=["Anschreiben", "Lebenslauf", "Letztes Zeugnis", "Einverständniserklärung der Eltern"],
         verification="official_source_verified", verified_days_ago=15),
    dict(employer="Labor Nordend Beispiel (fiktiv)", etype="Labor", pc="60318", city="Frankfurt am Main",
         label="Frankfurt-Nordend", title="Praktikum im Chemielabor", domains=["naturwissenschaft-labor"],
         desc="Proben vorbereiten, Messungen begleiten, Sicherheit im Labor kennenlernen.",
         min_age=15, max_age=None, min_grade=9, max_grade=10, mandatory=True, windows=[(20, 90)], deadline=None,
         places=1, availability="open", method="E-Mail", email="labor@nordend-beispiel.example",
         docs=["Lebenslauf", "Anschreiben"], clothing="Laborkittel und Schutzbrille werden gestellt.",
         verification="admin_verified", verified_days_ago=120),
    dict(employer="Kita Sonnenschein Beispiel (fiktiv)", etype="Kita", pc="55116", city="Mainz",
         label="Mainz-Altstadt", title="Praktikum in der Kita", domains=["bildung-kinder"],
         desc="Spielen, Basteln und Vorlesen mit Kindern zwischen 3 und 6 Jahren.",
         min_age=14, max_age=None, min_grade=8, max_grade=10, mandatory=True, windows=[(10, 180)], deadline=None,
         places=2, availability="open", method="Persönlich oder E-Mail", email="kita@sonnenschein-beispiel.example",
         docs=["Lebenslauf", "Nachweis Masernschutz"], verification="employer_confirmed", verified_days_ago=25),
    dict(employer="Tierarztpraxis am Kurpark (fiktiv)", etype="Tierarztpraxis", pc="61348", city="Bad Homburg v. d. Höhe",
         label="Bad Homburg", title="Schnupperpraktikum Tiermedizin", domains=["umwelt-tiere"],
         desc="Tiere versorgen, Behandlungen beobachten und Praxisräume vorbereiten.",
         min_age=None, max_age=None, min_grade=None, max_grade=None, mandatory=None, windows=[], flexible=True,
         deadline=None, places=None, availability="unknown", method="E-Mail", email="team@tierarzt-kurpark.example",
         docs=[], verification="unverified", verified_days_ago=None),
    dict(employer="Medienhaus Beispiel (fiktiv)", etype="Unternehmen", pc="60311", city="Frankfurt am Main",
         label="Frankfurt-Innenstadt", title="Praktikum Redaktion & Social Media", domains=["medien-design"],
         desc="Mitarbeit in der Online-Redaktion: Themen recherchieren, Fotos auswählen, kurze Texte schreiben.",
         min_age=15, max_age=17, min_grade=9, max_grade=10, mandatory=True, windows=[(20, 80)], deadline=15,
         places=0, availability="full", method="E-Mail", email="redaktion@medienhaus-beispiel.example",
         docs=["Anschreiben", "Lebenslauf", "Schreibprobe (optional)"], verification="employer_confirmed", verified_days_ago=8),
    dict(employer="Airport Service Beispiel (fiktiv)", etype="Unternehmen", pc="60549", city="Frankfurt am Main",
         label="Frankfurt-Flughafen", title="Praktikum Flughafenbetrieb", domains=["luftfahrt-verkehr"],
         desc="Blick hinter die Kulissen: Gepäcklogistik, Passagierservice und Vorfeld (begleitet).",
         min_age=16, max_age=None, min_grade=9, max_grade=10, mandatory=True, windows=[(60, 100)], deadline=35,
         places=6, availability="open", method="Online-Formular", url="https://airport-service.example/schuelerpraktikum",
         docs=["Lebenslauf", "Letztes Zeugnis", "Kopie Personalausweis (für Sicherheitsüberprüfung)"],
         clothing="Sicherheitsüberprüfung erforderlich, Bearbeitungszeit ca. 4 Wochen.",
         verification="admin_verified", verified_days_ago=12),
]


def seed_demo(db: Session, today: date | None = None) -> int:
    if db.scalar(select(func.count(Opportunity.id)).where(Opportunity.is_demo.is_(True))):
        return 0
    today = today or date.today()
    domains = {d.slug: d for d in db.scalars(select(Domain))}
    postcodes = {p.code: p for p in db.scalars(select(Postcode))}
    n = 0
    for spec in DEMO_LISTINGS:
        emp = Employer(name=spec["employer"], employer_type=spec["etype"], postcode_code=spec["pc"],
                       city_name=spec["city"], description="Fiktiver Beispielbetrieb für Test- und Demozwecke.")
        if spec.get("email"):
            emp.contacts.append(EmployerContact(name=None, role="Praktikumsteam", email=spec["email"], publish_allowed=True))
        db.add(emp)
        db.flush()
        pc = postcodes.get(spec["pc"])
        vda = spec.get("verified_days_ago")
        opp = Opportunity(
            employer_id=emp.id, title=spec["title"], description=spec["desc"], location_label=spec["label"],
            postcode_code=spec["pc"], city_name=spec["city"],
            latitude=pc.latitude if pc else None, longitude=pc.longitude if pc else None,
            travel_information=spec.get("transit"),
            min_age=spec["min_age"], max_age=spec["max_age"], min_grade=spec["min_grade"], max_grade=spec["max_grade"],
            mandatory_practicum_supported=spec["mandatory"], flexible_dates=spec.get("flexible", False),
            flexible_dates_note=spec.get("flex_note"),
            application_deadline=_d(today, spec["deadline"]) if spec.get("deadline") is not None else None,
            number_of_places=spec["places"], availability=spec["availability"],
            application_method=spec["method"], application_email=spec.get("email"), official_url=spec.get("url"),
            language_requirements=spec.get("languages"), clothing_safety=spec.get("clothing"),
            school_agreement_required=spec.get("school_agreement"),
            status=S.published, verification_status=spec["verification"],
            last_verified_at=_d(today, -vda) if vda is not None else None,
            verification_source="Fiktive Beispieldaten (Demo)", verified_by="seed" if vda is not None else None,
            next_review_at=_d(today, 90 - vda) if vda is not None else None,
            expires_at=_d(today, 240), is_demo=True, source="demo", published_at=datetime.now(),
        )
        opp.contact = emp.contacts[0] if emp.contacts else None
        opp.domains = [domains[s] for s in spec["domains"]]
        opp.date_windows = [OpportunityDateWindow(start_date=_d(today, a), end_date=_d(today, b))
                            for a, b in spec["windows"]]
        opp.documents = [OpportunityDocument(name=x) for x in spec["docs"]]
        if vda is not None:
            opp.verifications.append(VerificationRecord(verification_status=spec["verification"],
                                                        source="Fiktive Beispieldaten (Demo)", verified_by="seed"))
        db.add(opp)
        n += 1
    db.commit()
    return n
