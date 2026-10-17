import io

import pyotp
from sqlalchemy import select

from tests.conftest import csrf_from


def test_health_and_security_headers(app_client):
    r = app_client.get("/health")
    assert r.json() == {"status": "ok"}
    r = app_client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "<script src=\"/static/js/app.js\"" in r.text
    assert "onclick=" not in r.text  # no inline handlers (CSP)


def test_search_shows_explained_labels_and_hides_ineligible(app_client):
    r = app_client.get("/ergebnisse", params={"bereich": "gesundheit-soziales", "alter": "15", "klasse": "9",
                                              "schule_plz": "60311", "max_minuten": "45"})
    assert r.status_code == 200
    assert "Schülerpraktikum in der Pflege" in r.text
    assert "Passt gut" in r.text or "Könnte passen" in r.text
    assert "Praktikum IT-Support und Netzwerke" not in r.text  # other domain filtered


def test_search_too_young_hides_and_offers_show_all(app_client):
    params = {"bereich": "luftfahrt-verkehr", "alter": "14", "klasse": "9"}
    r = app_client.get("/ergebnisse", params=params)
    assert "Praktikum Flughafenbetrieb" not in r.text
    assert "ausgeblendet" in r.text
    r = app_client.get("/ergebnisse", params={**params, "alle": "1"})
    assert "Praktikum Flughafenbetrieb" in r.text
    assert "mindestens 16" in r.text


def test_search_validation_messages(app_client):
    r = app_client.get("/ergebnisse", params={"alter": "99", "von": "2026-12-10", "bis": "2026-12-01", "schule_plz": "123"})
    assert "zwischen 10 und 25" in r.text
    assert "vor dem Beginn" in r.text
    assert "5 Ziffern" in r.text


def test_zero_results_empty_state(app_client):
    r = app_client.get("/ergebnisse", params={"bereich": "bau-handwerk"})
    assert "Leider nichts Passendes gefunden" in r.text


def test_detail_page_never_invents_missing_data(app_client, db_session):
    from app.models import Opportunity
    vet = db_session.scalar(select(Opportunity).where(Opportunity.title == "Schnupperpraktikum Tiermedizin"))
    r = app_client.get(f"/angebote/{vet.id}")
    assert r.status_code == 200
    assert "Nicht angegeben" in r.text
    assert "Nicht bestätigt" in r.text
    assert "Deine Schule muss den Praktikumsplatz möglicherweise bestätigen" in r.text


def test_unpublished_listing_is_404(app_client, db_session):
    from app.models import Opportunity
    o = db_session.scalars(select(Opportunity)).first()
    o.status = "draft"
    db_session.commit()
    assert app_client.get(f"/angebote/{o.id}").status_code == 404
    assert app_client.get(f"/api/opportunities?ids={o.id}").json() == []


def test_report_requires_csrf_and_marks_listing(app_client, db_session):
    from app.models import Opportunity, Report
    o = db_session.scalars(select(Opportunity)).first()
    r = app_client.post(f"/angebote/{o.id}/melden", data={"reason": "falsche_angaben"})
    assert r.status_code == 400
    page = app_client.get(f"/angebote/{o.id}")
    r = app_client.post(f"/angebote/{o.id}/melden", data={"reason": "falsche_angaben", "message": "Alter falsch",
                                                          "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    assert db_session.scalar(select(Report).where(Report.opportunity_id == o.id)).message == "Alter falsch"
    assert db_session.get(Opportunity, o.id).verification_status == "student_reported"


def test_docx_download_requires_confirmation(app_client):
    page = app_client.get("/angebote/1/bewerbung")
    token = csrf_from(page.text)
    r = app_client.post("/bewerbung/docx", data={"body": "Hallo", "csrf_token": token})
    assert r.status_code == 400
    r = app_client.post("/bewerbung/docx", data={"body": "Sehr geehrte Damen und Herren,\nTest", "confirmed": "1",
                                                 "csrf_token": token, "title": "Anschreiben"})
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert r.headers["cache-control"] == "no-store"


def test_apply_page_has_templates_and_no_invention(app_client):
    r = app_client.get("/angebote/1/bewerbung")
    assert 'id="apply-templates"' in r.text
    assert "bitte ergänzen" in r.text
    assert "Entwurf" in r.text


def test_api_event_whitelist(app_client, db_session):
    from app.models import AnalyticsDaily
    app_client.post("/api/event", json={"event": "saved"})
    app_client.post("/api/event", json={"event": "drop table"})
    events = {a.event for a in db_session.scalars(select(AnalyticsDaily))}
    assert "saved" in events and "drop table" not in events


def test_admin_requires_login_and_mfa(app_client, db_session):
    from app.models import AdminUser
    from app.security import hash_password
    assert app_client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    db_session.add(AdminUser(email="new@example.org", password_hash=hash_password("a very long password")))
    db_session.commit()
    page = app_client.get("/admin/login")
    r = app_client.post("/admin/login", data={"email": "new@example.org", "password": "a very long password",
                                              "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert r.headers["location"] == "/admin/mfa/einrichten"
    # password alone does not grant access
    assert app_client.get("/admin", follow_redirects=False).status_code == 303
    page = app_client.get("/admin/mfa/einrichten")
    assert "<svg" in page.text
    import re
    secret = re.search(r"<code>([A-Z2-7]+)</code>", page.text).group(1)
    r = app_client.post("/admin/mfa/einrichten", data={"code": pyotp.TOTP(secret).now(),
                                                        "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert r.headers["location"] == "/admin"
    assert app_client.get("/admin").status_code == 200


def test_login_lockout_after_failures(app_client, db_session):
    from app.models import AdminUser
    from app.security import hash_password
    db_session.add(AdminUser(email="lock@example.org", password_hash=hash_password("a very long password")))
    db_session.commit()
    token = csrf_from(app_client.get("/admin/login").text)
    for _ in range(5):
        app_client.post("/admin/login", data={"email": "lock@example.org", "password": "wrong", "csrf_token": token})
    r = app_client.post("/admin/login", data={"email": "lock@example.org", "password": "a very long password",
                                              "csrf_token": token})
    assert r.status_code == 429


def test_employer_submission_review_publish_flow(admin_client, db_session):
    from app.models import AuditLog, EmployerSubmission, Opportunity
    c = admin_client
    page = c.get("/arbeitgeber/angebot-einreichen")
    token = csrf_from(page.text)
    bad = c.post("/arbeitgeber/angebot-einreichen", data={"csrf_token": token, "company_name": "X"})
    assert bad.status_code == 422 and "Bitte" in bad.text
    data = {
        "csrf_token": token, "company_name": "Tischlerei Holzwurm (Test)", "contact_email": "info@holzwurm.example",
        "title": "Schülerpraktikum Tischlerei", "postcode": "60385", "city": "Frankfurt am Main",
        "domains": ["bau-handwerk"], "min_age": "14", "max_age": "17", "min_grade": "8", "max_grade": "10",
        "start1": "2027-01-11", "end1": "2027-03-26", "application_email": "bewerbung@holzwurm.example",
        "documents": "Lebenslauf\nAnschreiben", "mandatory": "ja", "consent": "1",
    }
    r = c.post("/arbeitgeber/angebot-einreichen", data=data, follow_redirects=False)
    assert r.status_code == 303
    sub = db_session.scalar(select(EmployerSubmission))
    assert sub.status == "pending" and sub.consent_id
    # not public yet
    assert "Tischlerei" not in c.get("/ergebnisse", params={"bereich": "bau-handwerk"}).text
    r = c.post(f"/admin/einreichungen/{sub.id}", data={"decision": "approve", "csrf_token": c.csrf}, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    opp = db_session.scalar(select(Opportunity).where(Opportunity.title == "Schülerpraktikum Tischlerei"))
    assert opp.status == "awaiting_review" and opp.verification_status == "employer_confirmed"
    assert [d.name for d in opp.documents] == ["Lebenslauf", "Anschreiben"]
    r = c.post(f"/admin/angebote/{opp.id}/status", data={"status": "published", "csrf_token": c.csrf}, follow_redirects=False)
    assert r.status_code == 303
    res = c.get("/ergebnisse", params={"bereich": "bau-handwerk", "alter": "15", "klasse": "9",
                                       "von": "2027-02-01", "bis": "2027-02-12", "schule_plz": "60316"})
    assert "Schülerpraktikum Tischlerei" in res.text
    assert "Passt gut" in res.text
    actions = {a.action for a in db_session.scalars(select(AuditLog))}
    assert {"submission.approve", "opportunity.status", "admin.login"} <= actions


def test_publish_blocked_without_domain(admin_client, db_session):
    from app.models import Employer, Opportunity
    c = admin_client
    emp = db_session.scalars(select(Employer)).first()
    r = c.post("/admin/angebote/neu", data={"csrf_token": c.csrf, "employer_id": str(emp.id), "title": "Ohne Bereich",
                                            "postcode_code": "60311", "application_email": "a@b.example"},
               follow_redirects=False)
    assert r.status_code == 303
    opp = db_session.scalar(select(Opportunity).where(Opportunity.title == "Ohne Bereich"))
    c.post(f"/admin/angebote/{opp.id}/status", data={"status": "published", "csrf_token": c.csrf})
    db_session.expire_all()
    assert db_session.get(Opportunity, opp.id).status == "draft"


def test_admin_form_validation(admin_client, db_session):
    from app.models import Employer
    c = admin_client
    emp = db_session.scalars(select(Employer)).first()
    r = c.post("/admin/angebote/neu", data={"csrf_token": c.csrf, "employer_id": str(emp.id), "title": "X Test",
                                            "min_age": "16", "max_age": "14", "window_start_1": "2027-02-10",
                                            "window_end_1": "2027-02-01"})
    assert r.status_code == 422
    assert "Höchstalter" in r.text and "endet vor seinem Beginn" in r.text


def test_verification_requires_source_and_records_history(admin_client, db_session):
    from app.models import Opportunity
    c = admin_client
    o = db_session.scalars(select(Opportunity)).first()
    c.post(f"/admin/angebote/{o.id}/pruefen", data={"verification_status": "admin_verified", "source": "", "csrf_token": c.csrf})
    n_before = len(o.verifications)
    c.post(f"/admin/angebote/{o.id}/pruefen", data={"verification_status": "admin_verified",
                                                    "source": "Telefonat 17.10.", "csrf_token": c.csrf})
    db_session.expire_all()
    o = db_session.get(Opportunity, o.id)
    assert len(o.verifications) == n_before + 1
    assert o.verification_source == "Telefonat 17.10."


def test_csv_export_and_import(admin_client, db_session):
    from app.models import Opportunity
    c = admin_client
    r = c.get("/admin/export.csv")
    assert r.status_code == 200 and "Schülerpraktikum in der Pflege" in r.text
    csv_text = ("employer;title;domains;postcode;min_age;max_age;windows;application_email\n"
                "Import GmbH;Praktikum Import;it-software;60311;14;16;2027-01-04/2027-01-15;a@import.example\n"
                ";fehlt;;;;;;\n"
                "Import GmbH;Kaputt;it-software;60311;18;14;;\n")
    r = c.post("/admin/import", data={"csrf_token": c.csrf},
               files={"file": ("liste.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    assert "1 Angebote als Entwurf angelegt" in r.text
    assert "Zeile 3" in r.text and "Zeile 4" in r.text
    opp = db_session.scalar(select(Opportunity).where(Opportunity.title == "Praktikum Import"))
    assert opp.status == "draft" and opp.date_windows


def test_admin_pages_render(admin_client):
    for url in ["/admin", "/admin/angebote", "/admin/angebote/neu", "/admin/angebote/1", "/admin/pruefung",
                "/admin/pruefung?queue=stale", "/admin/pruefung?queue=broken_link", "/admin/einreichungen", "/admin/meldungen", "/admin/arbeitgeber",
                "/admin/arbeitgeber/1", "/admin/arbeitgeber/0", "/admin/bereiche", "/admin/bereiche?edit=1",
                "/admin/orte", "/admin/quellen", "/admin/import", "/admin/statistik", "/admin/protokoll"]:
        r = admin_client.get(url)
        assert r.status_code == 200, url
        assert r.headers["cache-control"] == "no-store"


def test_domain_management(admin_client, db_session):
    from app.models import Domain
    c = admin_client
    c.post("/admin/bereiche", data={"csrf_token": c.csrf, "name": "Sport", "slug": "sport", "position": "200",
                                    "active": "1", "keywords": "Sport, Verein"})
    d = db_session.scalar(select(Domain).where(Domain.slug == "sport"))
    assert d and d.keywords == ["sport", "verein"]
    assert "Sport" in c.get("/suche").text
    c.post("/admin/bereiche", data={"csrf_token": c.csrf, "domain_id": str(d.id), "name": "Sport", "slug": "sport",
                                    "position": "200"})
    db_session.expire_all()
    assert not db_session.get(Domain, d.id).active
    assert "Sport" not in c.get("/").text


def test_travel_rule_configuration_changes_labels(admin_client, db_session):
    from app.models import TravelRule
    c = admin_client
    he = db_session.scalar(select(TravelRule).where(TravelRule.name == "Hessen"))
    params = {"bereich": "fahrzeugtechnik", "alter": "15", "klasse": "9", "schule_plz": "60311", "max_minuten": "",
              "alle": "1"}
    c.post("/admin/orte/regeln", data={"csrf_token": c.csrf, "rule_id": str(he.id), "name": "Hessen",
                                       "state_id": str(he.state_id), "reference": "either", "max_travel_minutes": "20",
                                       "approval_required_beyond": "1", "active": "1"})
    assert "Schule muss zustimmen" in c.get("/ergebnisse", params=params).text
    c.post("/admin/orte/regeln", data={"csrf_token": c.csrf, "rule_id": str(he.id), "name": "Hessen",
                                       "state_id": str(he.state_id), "reference": "either", "max_travel_minutes": "120",
                                       "approval_required_beyond": "1", "active": "1"})
    assert "Schule muss zustimmen" not in c.get("/ergebnisse", params=params).text


def test_rate_limit_on_reports(app_client):
    page = app_client.get("/angebote/1")
    token = csrf_from(page.text)
    codes = [app_client.post("/angebote/1/melden", data={"reason": "sonstiges", "csrf_token": token},
                             follow_redirects=False).status_code for _ in range(12)]
    assert 429 in codes


def test_expire_outdated(db_session):
    from datetime import date, timedelta

    from app.listings import expire_outdated
    from app.models import Opportunity
    o = db_session.scalars(select(Opportunity).where(Opportunity.status == "published")).first()
    o.expires_at = date.today() - timedelta(days=1)
    db_session.commit()
    assert expire_outdated(db_session) >= 1
    db_session.commit()
    assert db_session.get(Opportunity, o.id).status == "expired"


def test_status_transition_rules():
    from app.listings import can_transition
    assert can_transition("draft", "published")
    assert can_transition("published", "closed")
    assert not can_transition("rejected", "published")
    assert not can_transition("expired", "closed")


def test_old_session_does_not_carry_over_to_a_new_account(admin_client, db_session):
    """A fresh database can give a new admin the same id; the old browser cookie must not log them in."""
    from app.models import AdminUser
    from app.security import hash_password

    assert admin_client.get("/admin", follow_redirects=False).status_code == 200
    old = db_session.scalar(select(AdminUser).where(AdminUser.email == "admin@example.org"))
    old_id = old.id
    db_session.delete(old)
    db_session.commit()
    db_session.add(AdminUser(id=old_id, email="fresh@example.org", password_hash=hash_password("another long password")))
    db_session.commit()
    assert admin_client.get("/admin", follow_redirects=False).headers["location"] == "/admin/login"
    # Nor may the old cookie set up two-factor login for the new account.
    assert admin_client.get("/admin/mfa/einrichten", follow_redirects=False).headers["location"] == "/admin/login"


def test_password_change_ends_existing_sessions(admin_client, db_session):
    from app.models import AdminUser
    from app.security import hash_password

    admin = db_session.scalar(select(AdminUser).where(AdminUser.email == "admin@example.org"))
    admin.password_hash = hash_password("a brand new long password")
    db_session.commit()
    assert admin_client.get("/admin", follow_redirects=False).status_code == 303


def test_admin_without_two_factor_cannot_use_admin_pages(admin_client, db_session):
    from app.models import AdminUser

    admin = db_session.scalar(select(AdminUser).where(AdminUser.email == "admin@example.org"))
    admin.totp_enabled = False
    db_session.commit()
    assert admin_client.get("/admin", follow_redirects=False).status_code == 303
