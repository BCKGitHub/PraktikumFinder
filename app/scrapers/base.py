"""Polite HTTP fetching and the scraper interface.

Rules enforced for every scraper:
* robots.txt is checked before every request (cached per host); a missing
  robots.txt (4xx) allows access, an unreachable one (5xx/network) blocks it.
* At least SCRAPER_MIN_DELAY_SECONDS between requests to the same host.
* An identifying User-Agent with a contact address.
* Retries with backoff on 429/5xx, honouring Retry-After.
* Response size limit.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit

import httpx

from ..config import get_settings

MAX_BYTES = 3 * 1024 * 1024


class ScrapeError(Exception):
    pass


class RobotsDisallowed(ScrapeError):
    pass


@dataclass
class ScrapedListing:
    external_id: str
    title: str
    employer_name: str
    source_label: str
    description: str | None = None
    postcode: str | None = None
    city: str | None = None
    street: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    url: str | None = None
    application_email: str | None = None
    published: date | None = None
    start_date: date | None = None
    valid_through: date | None = None
    extra_text: str = ""  # used for domain keyword suggestion only
    raw: dict = field(default_factory=dict)


class PoliteFetcher:
    def __init__(self, client: httpx.Client | None = None, min_delay: float | None = None,
                 user_agent: str | None = None, sleep=time.sleep) -> None:
        s = get_settings()
        self.user_agent = user_agent or s.scraper_user_agent
        self.min_delay = s.scraper_min_delay_seconds if min_delay is None else min_delay
        self.client = client or httpx.Client(timeout=20.0, follow_redirects=True)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._sleep = sleep

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            delta = time.monotonic() - last
            if delta < self.min_delay:
                self._sleep(self.min_delay - delta)
        self._last[host] = time.monotonic()

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        self._wait(parts.netloc)
        try:
            r = self.client.get(origin + "/robots.txt", headers={"User-Agent": self.user_agent})
        except httpx.HTTPError as exc:
            raise ScrapeError(f"robots.txt von {origin} nicht erreichbar ({exc.__class__.__name__}) – Abbruch.") from exc
        if 400 <= r.status_code < 500:
            parser = None  # no robots.txt => allowed
        elif r.status_code >= 500:
            raise ScrapeError(f"robots.txt von {origin} liefert {r.status_code} – Abbruch.")
        else:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(r.text.splitlines())
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return parser is None or parser.can_fetch(self.user_agent, url)

    def status(self, url: str) -> tuple[int | None, str]:
        """HTTP status of url after redirects, without reading the body. (None, reason) on network errors.

        Raises RobotsDisallowed when robots.txt forbids the URL.
        """
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt verbietet den Abruf von {url}")
        self._wait(urlsplit(url).netloc)
        try:
            with self.client.stream("GET", url, headers={"User-Agent": self.user_agent,
                                                         "Accept-Language": "de-DE,de;q=0.9"}) as r:
                return r.status_code, str(r.url)
        except httpx.HTTPError as exc:
            return None, exc.__class__.__name__

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            retries: int = 3) -> httpx.Response:
        full = str(httpx.URL(url, params=params)) if params else url
        if not self.allowed(full):
            raise RobotsDisallowed(f"robots.txt verbietet den Abruf von {full}")
        host = urlsplit(full).netloc
        hdrs = {"User-Agent": self.user_agent, "Accept-Language": "de-DE,de;q=0.9"}
        hdrs.update(headers or {})
        backoff = 2.0
        for attempt in range(retries + 1):
            self._wait(host)
            try:
                r = self.client.get(full, headers=hdrs)
            except httpx.HTTPError as exc:
                if attempt == retries:
                    raise ScrapeError(f"Netzwerkfehler bei {host}: {exc.__class__.__name__}") from exc
                self._sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    raise ScrapeError(f"{host} antwortet mit {r.status_code}")
                retry_after = r.headers.get("retry-after", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else backoff)
                backoff *= 2
                continue
            if r.status_code >= 400:
                raise ScrapeError(f"{host} antwortet mit {r.status_code}")
            if len(r.content) > MAX_BYTES:
                raise ScrapeError(f"Antwort von {host} ist zu groß")
            return r
        raise ScrapeError("unreachable")  # pragma: no cover


class Scraper:
    """Interface: subclasses yield ScrapedListing objects for one configured source."""

    key: str = ""
    label: str = ""
    description: str = ""
    config_help: str = ""
    # Configuration keys this scraper understands; others are rejected when an admin saves a source.
    config_keys: frozenset[str] = frozenset()

    def __init__(self, fetcher: PoliteFetcher) -> None:
        self.fetcher = fetcher

    def fetch(self, config: dict) -> list[ScrapedListing]:  # pragma: no cover - interface
        raise NotImplementedError
