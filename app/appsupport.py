"""Template-based German application drafts and checklists (no AI, no external calls).

Drafts are filled in the student's browser; personal data never reaches the
server unless the student explicitly downloads a .docx (processed in memory,
not stored or logged). Missing information is shown as a visible placeholder
instead of being invented.
"""
from __future__ import annotations

from .models import Opportunity

EMAIL_SUBJECT = "Bewerbung um ein Schülerpraktikum vom {start} bis {end}"

EMAIL_BODY = """{salutation}

mein Name ist {name}, ich bin {age} Jahre alt und besuche die {grade}. Klasse der {school}. Vom {start} bis zum {end} absolviere ich ein {practicum_kind} und interessiere mich sehr für den Bereich {domain}.

{motivation}

Deshalb bewerbe ich mich bei {employer} um einen Praktikumsplatz („{title}“). Meine Bewerbungsunterlagen finden Sie im Anhang.

Über eine Rückmeldung freue ich mich sehr. Bei Fragen erreichen Sie mich unter {email}{phone_part}.

Mit freundlichen Grüßen
{name}"""

COVER_LETTER = """{name}
{contact_line}

{employer}
{employer_place}

{city_date}

Bewerbung um einen Platz für ein Schülerpraktikum vom {start} bis {end}

{salutation}

ich besuche zurzeit die {grade}. Klasse der {school}. Im Rahmen meiner Schulzeit absolviere ich vom {start} bis zum {end} ein {practicum_kind}. Ich möchte in dieser Zeit den Bereich {domain} näher kennenlernen und bewerbe mich daher bei Ihnen um einen Platz als Praktikant/in („{title}“).

{motivation}

{strengths}

Ich freue mich darauf, Ihren Betrieb kennenzulernen und im Praktikum mitzuarbeiten. Über eine Einladung zu einem Gespräch würde ich mich sehr freuen.

Mit freundlichen Grüßen

{name}

Anlagen: {attachments}"""

PLACEHOLDERS = {
    "name": "Dein Name",
    "age": "Dein Alter",
    "school": "Name deiner Schule",
    "grade": "Klassenstufe",
    "start": "Beginn des Praktikums",
    "end": "Ende des Praktikums",
    "domain": "Bereich",
    "motivation": "Warum interessiert dich dieser Bereich? (2–3 Sätze, in deinen eigenen Worten)",
    "strengths": "Was kannst du gut? (z. B. Hobbys, Schulfächer – nur was stimmt)",
    "email": "Deine E-Mail-Adresse",
    "phone": "Telefonnummer (optional)",
    "contact_line": "Deine Adresse oder E-Mail",
    "city_date": "Ort, Datum",
    "contact_person": "Ansprechperson (falls bekannt)",
}


def application_templates(opp: Opportunity) -> dict:
    contact_name = opp.contact.name if opp.contact and opp.contact.publish_allowed and opp.contact.name else ""
    docs = [d.name for d in opp.documents] or ["Lebenslauf", "Letztes Zeugnis"]
    domain = opp.domains[0].name if opp.domains else ""
    return {
        "email_subject": EMAIL_SUBJECT,
        "email_body": EMAIL_BODY,
        "cover_letter": COVER_LETTER,
        "placeholders": PLACEHOLDERS,
        "fixed": {
            "employer": opp.employer.name,
            "title": opp.title,
            "employer_place": " ".join(x for x in (opp.postcode_code, opp.city_name) if x) if opp.address_public else (opp.city_name or ""),
            "domain_default": domain,
            "contact_person": contact_name,
            "attachments": ", ".join(docs),
            "application_email": opp.application_email or "",
        },
    }


BASE_CHECKLIST = {
    "Über dich (für Anschreiben und Lebenslauf)": [
        "Vollständiger Name und Kontakt (E-Mail, ggf. Telefon)",
        "Name deiner Schule und Klassenstufe",
        "Genaue Praktikumsdaten von deiner Schule",
        "2–3 Sätze, warum dich der Bereich interessiert",
    ],
    "Lebenslauf (max. 1 Seite)": [
        "Persönliche Daten (Name, Kontakt – ein Foto ist freiwillig)",
        "Schulbildung (aktuelle Schule, Klasse, angestrebter Abschluss)",
        "Lieblingsfächer und Interessen / Hobbys",
        "Kenntnisse: Sprachen, Computer – nur was wirklich stimmt",
        "Ort, Datum (Unterschrift, falls verlangt)",
    ],
    "Bevor du abschickst": [
        "Deine Schule hat das Praktikum bzw. den Betrieb bestätigt (falls nötig)",
        "Deine Eltern/Erziehungsberechtigten wissen Bescheid und sind einverstanden",
        "Eine seriöse E-Mail-Adresse (z. B. vorname.nachname@…)",
        "Anhänge als PDF, sinnvoll benannt (z. B. Lebenslauf_Vorname_Nachname.pdf)",
        "Text noch einmal gelesen oder vorlesen lassen",
    ],
}


def checklist_for(opp: Opportunity | None) -> dict[str, list[str]]:
    data = {k: list(v) for k, v in BASE_CHECKLIST.items()}
    if opp is not None:
        docs = [d.name for d in opp.documents]
        data = {"Vom Betrieb verlangte Unterlagen": docs or ["Nicht angegeben – frag beim Betrieb nach, was du schicken sollst."], **data}
        if opp.school_agreement_required:
            data["Bevor du abschickst"].insert(0, "Der Betrieb verlangt eine Vereinbarung mit deiner Schule – frag deine Lehrkraft nach dem Formular.")
        if opp.clothing_safety:
            data["Bevor du abschickst"].append("Hinweise zu Kleidung/Sicherheit gelesen: " + opp.clothing_safety)
    return data
