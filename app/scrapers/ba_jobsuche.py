"""Bundesagentur für Arbeit – Jobsuche API.

Official public REST API behind arbeitsagentur.de/jobsuche (documented by the
bund.dev project: https://jobsuche.api.bund.dev/). It uses a publicly
documented client id sent as the X-API-Key header. Please review the BA's
terms of use before running this in production.

Search parameters used: was (keywords), wo (place), umkreis (km),
angebotsart (34 = Praktikum/Trainee), page, size.
"""
from __future__ import annotations

from datetime import date

from .base import ScrapedListing, Scraper

API_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
DETAIL_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
PUBLIC_CLIENT_ID = "jobboerse-jobsuche"

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
                   '"max_pages": 3, "required_keywords": ["schüler", "schulpraktikum"]}')

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
        return out

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
