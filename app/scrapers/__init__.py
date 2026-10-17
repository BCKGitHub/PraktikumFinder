"""Pluggable scrapers. Register new ones in REGISTRY."""
from .ba_jobsuche import BAJobsucheScraper
from .base import PoliteFetcher, ScrapedListing, ScrapeError, Scraper
from .jsonld_jobposting import JsonLdJobPostingScraper

REGISTRY: dict[str, type[Scraper]] = {
    BAJobsucheScraper.key: BAJobsucheScraper,
    JsonLdJobPostingScraper.key: JsonLdJobPostingScraper,
}

__all__ = ["REGISTRY", "PoliteFetcher", "ScrapedListing", "Scraper", "ScrapeError"]
