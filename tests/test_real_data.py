"""Real-data setup: demo removal, Germany-wide postcodes, dead-link checker, text detection in admin."""
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.models import AuditLog, Employer, Opportunity, Postcode, utcnow
from app.scrapers import PoliteFetcher


def make_fetcher(handler):
    return PoliteFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), min_delay=0, sleep=lambda s: None)


def test_demo_data_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SEED_DEMO_DATA", raising=False)
    from app.config import Settings

    assert Settings().seed_demo_data is False


def test_remove_demo_from_dashboard(admin_client, db_session):
    c = admin_client
    assert "Demo-Angebote entfernen" in c.get("/admin").text
    r = c.post("/admin/demo/entfernen", data={"csrf_token": c.csrf}, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    assert db_session.scalar(select(func.count(Opportunity.id)).where(Opportunity.is_demo.is_(True))) == 0
    assert db_session.scalar(select(func.count(Employer.id)).where(Employer.description.like("Fiktiver%"))) == 0
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "demo.remove")).details["count"] == 12
    assert "Demo-Angebote entfernen" not in c.get("/admin").text


def test_remove_demo_requires_csrf(admin_client):
    r = admin_client.post("/admin/demo/entfernen", data={}, follow_redirects=False)
    assert r.status_code in (400, 403)


def test_bundled_postcodes_cover_germany(db_session):
    from app.geo import lookup_postcode
    from app.postcodes import load_bundled

    before = db_session.get(Postcode, db_session.scalar(select(Postcode.id).where(Postcode.code == "60311")))
    lat_before = before.latitude
    n = load_bundled(db_session)
    assert n > 8000
    for code in ("10115", "20095", "80331", "01067", "50667", "24103"):
        pt = lookup_postcode(db_session, code)
        assert pt is not None and pt.precision == "postcode", code
    munich = db_session.scalar(select(Postcode).where(Postcode.code == "80331"))
    assert munich.city.name == "München" and munich.city.state.code == "BY"
    # Existing (possibly admin-edited) postcodes are kept, and a second load does nothing.
    db_session.refresh(before)
    assert before.latitude == lat_before
    assert load_bundled(db_session) == 0


def test_import_postcodes_csv(db_session, tmp_path):
    from app.postcodes import import_file

    f = tmp_path / "plz.csv"
    f.write_text("plz;ort;bundesland;lat;lon\n99999;Testdorf;Thüringen;50.9;11.0\n1234;Kaputt;TH;1;1\n"
                 "60311;Frankfurt am Main;HE;50.2;8.7\n", encoding="utf-8")
    assert import_file(db_session, f) == 2
    pc = db_session.scalar(select(Postcode).where(Postcode.code == "99999"))
    assert pc.city.name == "Testdorf" and pc.city.state.code == "TH"
    assert db_session.scalar(select(Postcode).where(Postcode.code == "60311")).latitude == 50.2


def _link_handler(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /privat/\n")
    if request.url.path.startswith("/weg"):
        return httpx.Response(404)
    if request.url.path.startswith("/gesperrt"):
        return httpx.Response(403)
    if request.url.path == "/alt":
        return httpx.Response(301, headers={"location": "https://betrieb.example/neu"})
    return httpx.Response(200, text="ok")


def test_link_checker_classifies_conservatively(db_session):
    from app.linkcheck import check_links

    opps = db_session.scalars(select(Opportunity).where(Opportunity.status == "published").order_by(Opportunity.id)).all()
    urls = ["https://betrieb.example/ok", "https://betrieb.example/weg", "https://betrieb.example/gesperrt",
            "https://betrieb.example/privat/x", "https://betrieb.example/alt"]
    for opp, url in zip(opps, urls, strict=False):
        opp.official_url = url
    for opp in opps[len(urls):]:
        opp.official_url = None
        opp.source_url = None
    db_session.commit()
    result = check_links(db_session, make_fetcher(_link_handler))
    assert (result.checked, result.ok, result.broken, result.unknown) == (5, 2, 1, 2)
    status = {o.official_url: o.link_status for o in opps[:5]}
    assert status["https://betrieb.example/weg"] == "broken"
    assert status["https://betrieb.example/gesperrt"] == "unknown"   # bot blocking is not "broken"
    assert status["https://betrieb.example/privat/x"] == "unknown"   # robots.txt is respected
    assert status["https://betrieb.example/alt"] == "ok"             # redirects are followed
    # Checked links are not re-checked within a week.
    assert check_links(db_session, make_fetcher(_link_handler)).checked == 0
    opps[0].link_checked_at = utcnow() - timedelta(days=8)
    db_session.commit()
    assert check_links(db_session, make_fetcher(_link_handler)).checked == 1


def test_network_error_is_not_broken(db_session):
    from app.linkcheck import check_one

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("boom")

    opp = db_session.scalar(select(Opportunity).where(Opportunity.status == "published"))
    opp.official_url = "https://offline.example/"
    assert check_one(make_fetcher(handler), opp) == "unknown"


def test_broken_link_queue_and_detect_button(admin_client, db_session):
    c = admin_client
    opp = db_session.scalar(select(Opportunity).where(Opportunity.status == "published"))
    opp.link_status = "broken"
    opp.link_check_note = "Seite nicht gefunden (HTTP 404)"
    db_session.commit()
    page = c.get("/admin/pruefung?queue=broken_link").text
    assert "Link defekt" in page and opp.title in page
    assert "Seite nicht gefunden" in c.get(f"/admin/angebote/{opp.id}").text

    opp.min_age = opp.max_age = opp.min_grade = opp.max_grade = None
    opp.description = "Für Schülerinnen und Schüler ab 15 Jahren in der 9./10. Klasse."
    db_session.commit()
    r = c.post(f"/admin/angebote/{opp.id}/erkennen", data={"csrf_token": c.csrf}, follow_redirects=False)
    assert r.status_code == 303
    db_session.expire_all()
    opp = db_session.get(Opportunity, opp.id)
    assert (opp.min_age, opp.min_grade, opp.max_grade) == (15, 9, 10)
    assert "bitte prüfen" in opp.data_quality_notes


def test_source_config_rejects_translated_keys(admin_client, db_session):
    from app.models import ScrapeSource

    src = db_session.scalar(select(ScrapeSource).where(ScrapeSource.scraper == "ba_jobsuche"))
    before = dict(src.config)
    c = admin_client
    r = c.post("/admin/quellen", data={"csrf_token": c.csrf, "source_id": str(src.id), "scraper": "ba_jobsuche",
                                       "name": src.name, "config": '{"what": "student internship", "where": "Frankfurt am Main"}'},
               follow_redirects=True)
    assert "Unbekannte Einstellung" in r.text
    db_session.expire_all()
    assert db_session.get(ScrapeSource, src.id).config == before
    page = c.get("/admin/quellen").text
    assert 'translate="no"' in page and "Neue Quelle anlegen" in page
