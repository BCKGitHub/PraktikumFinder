"""Bundesagentur für Arbeit – Jobsuche API.

Official public REST API behind arbeitsagentur.de/jobsuche (documented by the
bund.dev project: https://jobsuche.api.bund.dev/). It uses a publicly
documented client id sent as the X-API-Key header. Please review the BA's
terms of use before running this in production.

Search parameters used: was (keywords), wo (place), umkreis (km),
angebotsart (34 = Praktikum/Trainee), page, size.

Search results carry no description text. With "fetch_details" (default on) the
scraper also asks the job-details endpoint for each kept listing, so age, grade
and dates can be read from the ad text. That endpoint is less well documented;
if it fails twice in a row, the run carries on without details.
"""
from __future__ import annotations

import base64
import logging
from datetime import date

from .base import RobotsDisallowed, ScrapedListing, ScrapeError, Scraper

log = logging.getLogger(__name__)

API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
DETAILS_API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobdetails/{key}"
PUBLIC_CLIENT_ID = "jobboerse-jobsuche"
DESCRIPTION_KEYS = ("stellenangebotsBeschreibung", "stellenbeschreibung", "beschreibung")

# Only listings mentioning one of these are kept (case-insensitive), so general
# university internships do not end up in a tool for 14-year-olds.
DEFAULT_REQUIRED_KEYWORDS = ["schüler", "schulpraktikum", "schnupperpraktikum", "berufsorientierung", "betriebspraktikum"]


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


class BAJobsucheScraper(Scraper):
    key = "ba_jobsuche"
    label = "Bundesagentur für Arbeit – Jobsuche (offizielle API)"
    description = "Sucht Praktikumsangebote in der Jobbörse der Bundesagentur für Arbeit."
    config_help = ('{"was": "Schülerpraktikum", "wo": "Darmstadt", "umkreis": 15, "angebotsart": 34, '
                   '"max_pages": 3, "fetch_details": true, "required_keywords": ["schüler", "schulpraktikum"]}')

    def fetch(self, config: dict) -> list[ScrapedListing]:
        required = [k.lower() for k in config.get("required_keywords", DEFAULT_REQUIRED_KEYWORDS)]
        size = min(int(config.get("size", 50)), 100)
        max_pages = min(int(config.get("max_pages", 3)), 10)
        out: list[ScrapedListing] = []
        for page in range(1, max_pages + 1):
            params = {
                "was": config.get("was", "Schülerpraktikum"),
                "wo": config.get("wo", ""),
                "umkreis": int(config.get("umkreis", 15)),
                "angebotsart": int(config.get("angebotsart", 34)),
                "page": page,
                "size": size,
            }
            r = self.fetcher.get(API_URL, params=params, headers={"X-API-Key": PUBLIC_CLIENT_ID,
                                                                  "Accept": "application/json"})
            data = r.json()
            items = data.get("stellenangebote") or []
            for item in items:
                listing = self.parse_item(item)
                if listing is None:
                    continue
                hay = f"{listing.title} {listing.extra_text}".lower()
                if required and not any(k in hay for k in required):
                    continue
                out.append(listing)
            total = int(data.get("maxErgebnisse") or 0)
            if not items or page * size >= total:
                break
        if config.get("fetch_details", True):
            self.add_details(out)
        return out

    def add_details(self, listings: list[ScrapedListing]) -> None:
        failures = 0
        for listing in listings:
            if failures >= 2:
                log.info("BA job details unavailable, continuing without description text")
                return
            key = base64.b64encode(listing.external_id.encode()).decode()
            try:
                data = self.fetcher.get(DETAILS_API_URL.format(key=key),
                                        headers={"X-API-Key": PUBLIC_CLIENT_ID, "Accept": "application/json"}).json()
            except (ScrapeError, RobotsDisallowed, ValueError):
                failures += 1
                continue
            failures = 0
            text = next((data.get(k) for k in DESCRIPTION_KEYS if isinstance(data.get(k), str) and data.get(k).strip()), None)
            if text:
                listing.description = text.strip()[:20000]
            email = (data.get("arbeitgeberdarstellung") or {}).get("email") if isinstance(data.get("arbeitgeberdarstellung"), dict) else None
            if email and not listing.application_email:
                listing.application_email = email

    @staticmethod
    def parse_item(item: dict) -> ScrapedListing | None:
        refnr = item.get("refnr")
        title = (item.get("titel") or item.get("beruf") or "").strip()
        employer = (item.get("arbeitgeber") or "").strip()
        if not refnr or not title or not employer:
            return None
        ort = item.get("arbeitsort") or {}
        coords = ort.get("koordinaten") or {}
        return ScrapedListing(
            external_id=str(refnr),
            title=title[:250],
            employer_name=employer[:200],
            source_label="Bundesagentur für Arbeit – Jobsuche",
            postcode=(ort.get("plz") or None),
            city=ort.get("ort"),
            street=ort.get("strasse"),
            latitude=coords.get("lat"),
            longitude=coords.get("lon"),
            url=item.get("externeUrl") or DETAIL_URL.format(refnr=refnr),
            published=_date(item.get("aktuelleVeroeffentlichungsdatum")),
            start_date=_date(item.get("eintrittsdatum")),
            extra_text=f"{item.get('beruf') or ''}",
            raw={k: item.get(k) for k in ("refnr", "beruf", "titel", "arbeitgeber", "eintrittsdatum",
                                         "aktuelleVeroeffentlichungsdatum", "modifikationsTimestamp")},
        )
