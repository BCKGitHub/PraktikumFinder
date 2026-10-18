import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.scrapers import PoliteFetcher
from app.scrapers.ba_jobsuche import BAJobsucheScraper
from app.scrapers.base import RobotsDisallowed, ScrapeError
from app.scrapers.jsonld_jobposting import JsonLdJobPostingScraper, extract_job_postings

FIX = Path(__file__).parent / "fixtures"


def make_fetcher(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteFetcher(client=client, min_delay=0, sleep=lambda s: None)


BA_DETAIL_TEXT = ("Für Schülerinnen und Schüler ab 14 Jahren aus den Klassen 8 bis 10. Praktikum vom 01.02.2028 bis "
                  "11.02.2028. Bewerbungsschluss: 10.12.2027. Bitte schicke Lebenslauf und Anschreiben an "
                  "praktikum@buero-beispiel.de.")


def ba_handler(request: httpx.Request):
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    assert request.headers["X-API-Key"] == "jobboerse-jobsuche"
    if "/jobdetails/" in request.url.path:
        refnr = base64.b64decode(request.url.path.rsplit("/", 1)[1]).decode()
        if refnr == "10000-1111111111-S":
            return httpx.Response(200, json={"refnr": refnr, "stellenangebotsBeschreibung": BA_DETAIL_TEXT})
        return httpx.Response(404)
    assert request.url.params["angebotsart"] == "34"
    assert "PraktikumFinderBot" in request.headers["User-Agent"]
    return httpx.Response(200, json=json.loads((FIX / "ba_jobs_page1.json").read_text()))


def test_ba_scraper_parses_and_filters_for_school_internships():
    items = BAJobsucheScraper(make_fetcher(ba_handler)).fetch({"wo": "Frankfurt am Main"})
    titles = [i.title for i in items]
    assert "Schülerpraktikum im Büro (m/w/d)" in titles
    assert "Schnupperpraktikum Kfz-Werkstatt" in titles
    assert "Praktikum für Schüler*innen – Softwareentwicklung" in titles
    assert "Praktikum Controlling (Studium)" not in titles  # university internship filtered out
    assert all(i.external_id for i in items)
    office = next(i for i in items if "Büro" in i.title)
    assert office.postcode == "60311" and office.latitude == 50.111
    assert office.url.endswith("10000-1111111111-S")


def test_robots_disallow_is_respected():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        pytest.fail("must not fetch disallowed URL")
    with pytest.raises(RobotsDisallowed):
        BAJobsucheScraper(make_fetcher(handler)).fetch({})


def test_unreachable_robots_aborts():
    def handler(request):
        return httpx.Response(503)
    with pytest.raises(ScrapeError):
        BAJobsucheScraper(make_fetcher(handler)).fetch({})


def test_retry_on_429_then_success():
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"stellenangebote": [], "maxErgebnisse": 0})
    assert BAJobsucheScraper(make_fetcher(handler)).fetch({}) == []
    assert calls["n"] == 2


def test_jsonld_extraction():
    html = (FIX / "career_page.html").read_text()
    postings = extract_job_postings(html)
    assert len(postings) == 2

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /intern/")
        return httpx.Response(200, text=html)
    items = JsonLdJobPostingScraper(make_fetcher(handler)).fetch({"urls": ["https://labor.example/karriere"]})
    assert len(items) == 1
    it = items[0]
    assert it.external_id == "SP-2027-01"
    assert it.employer_name == "Labor Beispiel GmbH"
    assert "<b>" not in it.description and "Versuchen" in it.description
    assert it.valid_through.isoformat() == "2027-01-31"


def test_runner_imports_as_awaiting_review_and_is_idempotent(db_session):
    from app.models import Opportunity, ScrapeSource
    from app.scrapers.runner import run_source

    src = ScrapeSource(scraper="ba_jobsuche", name="Test", config={"wo": "Frankfurt"}, enabled=True)
    db_session.add(src)
    db_session.commit()
    run = run_source(db_session, src, make_fetcher(ba_handler))
    assert run.ok, run.message
    assert run.created == 3
    opps = db_session.scalars(select(Opportunity).where(Opportunity.source == "scraper:ba_jobsuche")).all()
    assert {o.status for o in opps} == {"awaiting_review"}
    assert {o.verification_status for o in opps} == {"unverified"}
    kfz = next(o for o in opps if "Kfz" in o.title)
    assert any(d.slug == "kfz-werkstatt" for d in kfz.domains)
    it = next(o for o in opps if "Software" in o.title)
    assert it.latitude is not None  # filled from postcode table (no coords in source)
    run2 = run_source(db_session, src, make_fetcher(ba_handler))
    assert run2.created == 0 and run2.updated == 3


def test_runner_auto_publish_only_with_domain_and_contact(db_session):
    from app.models import Opportunity, ScrapeSource
    from app.scrapers.runner import run_source

    src = ScrapeSource(scraper="ba_jobsuche", name="Auto", config={}, enabled=True, auto_publish_unverified=True)
    db_session.add(src)
    db_session.commit()
    run_source(db_session, src, make_fetcher(ba_handler))
    opps = db_session.scalars(select(Opportunity).where(Opportunity.source == "scraper:ba_jobsuche")).all()
    for o in opps:
        if o.status == "published":
            assert o.domains and o.official_url and o.verification_status == "unverified"


def test_ba_details_fill_age_grade_dates_and_documents(db_session):
    from app.models import Opportunity, ScrapeSource
    from app.scrapers.runner import DETECTED_NOTE, run_source

    src = ScrapeSource(scraper="ba_jobsuche", name="Details", config={}, enabled=True)
    db_session.add(src)
    db_session.commit()
    run = run_source(db_session, src, make_fetcher(ba_handler))
    assert run.ok, run.message
    office = db_session.scalar(select(Opportunity).where(Opportunity.external_id == "10000-1111111111-S"))
    assert office.status == "awaiting_review"
    assert (office.min_age, office.max_age, office.min_grade, office.max_grade) == (14, None, 8, 10)
    assert [(w.start_date.isoformat(), w.end_date.isoformat()) for w in office.date_windows] == [("2028-02-01", "2028-02-11")]
    assert office.flexible_dates is False
    assert office.application_deadline.isoformat() == "2027-12-10"
    assert office.application_email == "praktikum@buero-beispiel.de"
    assert {d.name for d in office.documents} == {"Lebenslauf", "Anschreiben"}
    assert DETECTED_NOTE in office.data_quality_notes
    # Listings without detail text keep their fields empty instead of guessed values.
    other = db_session.scalar(select(Opportunity).where(Opportunity.external_id == "10000-4444444444-S"))
    assert other.min_age is None and other.min_grade is None and not other.date_windows
    assert DETECTED_NOTE not in (other.data_quality_notes or "")


def test_ba_details_can_be_switched_off():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return ba_handler(request)

    BAJobsucheScraper(make_fetcher(handler)).fetch({"fetch_details": False})
    assert not any("/jobdetails/" in p for p in seen)


def test_ba_scraper_reads_api_v6():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if "/jobdetails/" in request.url.path:
            return httpx.Response(404)
        assert request.url.path.endswith("/pc/v6/jobs")
        return httpx.Response(200, json=json.loads((FIX / "ba_jobs_v6_page1.json").read_text()))

    items = BAJobsucheScraper(make_fetcher(handler)).fetch({"wo": "Wiesbaden"})
    assert [i.title for i in items] == ["Schülerpraktikum in der Tischlerei (m/w/d)"]  # university internship filtered
    it = items[0]
    assert it.external_id == "12336-a26f539j0448996-S"
    assert it.employer_name == "Holzwerkstatt Beispiel GmbH"
    assert (it.postcode, it.city, it.latitude, it.longitude) == ("65183", "Wiesbaden", 50.082, 8.24)
    assert it.published.isoformat() == "2026-10-02"
    assert it.url == "https://www.arbeitsagentur.de/jobsuche/jobdetail/12336-a26f539j0448996-S"


def test_ba_v6_zero_hits_has_no_list():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"maxErgebnisse": 0, "facetten": {}})

    assert BAJobsucheScraper(make_fetcher(handler)).fetch({}) == []
