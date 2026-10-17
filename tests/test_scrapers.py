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


def ba_handler(request: httpx.Request):
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    assert request.headers["X-API-Key"] == "jobboerse-jobsuche"
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
