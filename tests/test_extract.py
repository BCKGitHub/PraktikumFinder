from datetime import date

import pytest

from app.extract import extract

TODAY = date(2026, 10, 17)


@pytest.mark.parametrize("text, ages", [
    ("Für Schüler ab 14 Jahren.", (14, None)),
    ("Du bist mindestens 15 Jahre alt.", (15, None)),
    ("Geeignet für 14-16 Jahre alte Schülerinnen.", (14, 16)),
    ("Alter: 14 bis 17 Jahre", (14, 17)),
    ("Mindestalter: 16", (16, None)),
    ("Wir sind seit 25 Jahren im Geschäft und haben 120 Mitarbeitende.", (None, None)),
])
def test_ages(text, ages):
    r = extract(text, TODAY)
    assert (r.min_age, r.max_age) == ages


@pytest.mark.parametrize("text, grades", [
    ("Praktikum ab Klasse 8", (8, None)),
    ("ab der 9. Klasse", (9, None)),
    ("für die 8. Klasse", (8, 8)),
    ("Jahrgangsstufe 9", (9, 9)),
    ("Schüler der Klassen 8 bis 10", (8, 10)),
    ("Schülerinnen der 9./10. Klasse", (9, 10)),
    ("in der 9. und 10. Klasse", (9, 10)),
    ("Ein Team der Extraklasse mit 3 Standorten.", (None, None)),
    ("Klasse 2 der Grundschule", (None, None)),
])
def test_grades(text, grades):
    r = extract(text, TODAY)
    assert (r.min_grade, r.max_grade) == grades


def test_date_ranges_and_deadline():
    r = extract("Praktikum vom 02.02.2027 bis 13.02.2027 oder 1. März - 12. März 2027. "
                "Bewerbungsschluss: 15.12.2026.", TODAY)
    assert r.windows == [(date(2027, 2, 2), date(2027, 2, 13)), (date(2027, 3, 1), date(2027, 3, 12))]
    assert r.deadline == date(2026, 12, 15)


def test_date_range_without_year_on_start():
    r = extract("Zeitraum: 08.06.-19.06.2027", TODAY)
    assert r.windows == [(date(2027, 6, 8), date(2027, 6, 19))]


@pytest.mark.parametrize("text, deadline", [
    ("Bewirb dich bis zum 31. Januar 2027!", date(2027, 1, 31)),
    ("Bewerbungen bitte bis 01.04.2027 per E-Mail.", date(2027, 4, 1)),
    ("Wir freuen uns über deine Bewerbung bis spätestens 15.01.27.", date(2027, 1, 15)),
    ("Bitte bis zum 20.11.2026 bewerben.", date(2026, 11, 20)),
])
def test_deadlines(text, deadline):
    assert extract(text, TODAY).deadline == deadline


def test_application_period_is_not_a_practicum_window():
    r = extract("Bewerbungszeitraum 01.10.2026 bis 30.11.2026. Praktikum 18.01.2027 - 29.01.2027", TODAY)
    assert r.windows == [(date(2027, 1, 18), date(2027, 1, 29))]


def test_long_periods_and_old_dates_are_ignored():
    r = extract("Gegründet am 01.01.1990. Ausbildung vom 01.08.2027 bis 31.07.2030.", TODAY)
    assert r.windows == [] and r.deadline is None


def test_email_and_documents():
    r = extract("Schick Lebenslauf, Anschreiben und dein letztes Zeugnis an Praktikum@Beispiel-Firma.de. "
                "Nachweis über Masernschutz nötig. Antworten nicht an noreply@beispiel-firma.de.", TODAY)
    assert r.emails == ["praktikum@beispiel-firma.de"]
    assert r.documents == ["Anschreiben", "Lebenslauf", "Letztes Zeugnis", "Nachweis Masernschutz"]


def test_nothing_found_means_nothing_filled():
    r = extract("Spannendes Praktikum in unserem Büro.", TODAY)
    assert r.summary() == []
