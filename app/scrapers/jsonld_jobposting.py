"""Employer career pages that publish schema.org JobPosting data (JSON-LD).

Only URLs an administrator explicitly adds are fetched (an allow-list), and
robots.txt is respected. Structured JobPosting markup is published by
employers precisely so that job search services can read it.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date
from html.parser import HTMLParser

from .base import ScrapedListing, Scraper

DEFAULT_KEYWORDS = ["schülerpraktikum", "schulpraktikum", "schüler", "schnupperpraktikum", "praktikum"]


class _LdJsonExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in = False
        self._buf: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._in = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in:
            self.blocks.append("".join(self._buf))
            self._in = False

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:5000] or None


def _date(raw) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _iter_postings(node):
    if isinstance(node, list):
        for x in node:
            yield from _iter_postings(x)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "JobPosting" in types:
            yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _iter_postings(node[key])


def extract_job_postings(page_html: str) -> list[dict]:
    p = _LdJsonExtractor()
    p.feed(page_html)
    postings = []
    for block in p.blocks:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        postings.extend(_iter_postings(data))
    return postings


def posting_to_listing(posting: dict, page_url: str) -> ScrapedListing | None:
    title = (posting.get("title") or "").strip()
    org = posting.get("hiringOrganization") or {}
    employer = (org.get("name") if isinstance(org, dict) else str(org or "")).strip()
    if not title or not employer:
        return None
    loc = posting.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address") or {}
    geo = loc.get("geo") or {}
    ident = posting.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value")
    url = posting.get("url") or page_url
    ext_id = str(ident) if ident else hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:24]

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return ScrapedListing(
        external_id=ext_id,
        title=title[:250],
        employer_name=employer[:200],
        source_label=f"Karriereseite {employer}",
        description=_strip_html(posting.get("description")),
        postcode=(addr.get("postalCode") or None) if isinstance(addr, dict) else None,
        city=addr.get("addressLocality") if isinstance(addr, dict) else None,
        street=addr.get("streetAddress") if isinstance(addr, dict) else None,
        latitude=_f(geo.get("latitude")),
        longitude=_f(geo.get("longitude")),
        url=url,
        published=_date(posting.get("datePosted")),
        valid_through=_date(posting.get("validThrough")),
        extra_text=" ".join(str(posting.get(k) or "") for k in ("occupationalCategory", "industry", "employmentType")),
    )


class JsonLdJobPostingScraper(Scraper):
    key = "jsonld_jobposting"
    label = "Karriereseiten mit JobPosting-Daten (schema.org)"
    description = "Liest strukturierte Stellenangebote (JSON-LD) von freigegebenen Arbeitgeber-Seiten."
    config_help = '{"urls": ["https://arbeitgeber.example/karriere/praktikum"], "keywords": ["schülerpraktikum", "praktikum"]}'
    config_keys = frozenset({"urls", "keywords"})

    def fetch(self, config: dict) -> list[ScrapedListing]:
        keywords = [k.lower() for k in config.get("keywords", DEFAULT_KEYWORDS)]
        out: list[ScrapedListing] = []
        for url in config.get("urls", [])[:50]:
            if not str(url).startswith("https://"):
                continue
            r = self.fetcher.get(url)
            for posting in extract_job_postings(r.text):
                listing = posting_to_listing(posting, url)
                if listing is None:
                    continue
                hay = f"{listing.title} {listing.description or ''}".lower()
                if keywords and not any(k in hay for k in keywords):
                    continue
                out.append(listing)
        return out
