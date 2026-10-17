"""Dead-link checker for listing links (official_url, else source_url).

Polite like the scrapers: robots.txt, per-host delay, identifying user agent. A link only counts
as broken on 404 or 410. Sites that block bots (401, 403, 429), robots.txt refusals, network
errors and server errors count as "unknown", never as broken. Broken links land
in an admin queue; nothing is unpublished automatically.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .audit import audit
from .listings import S
from .models import Opportunity, utcnow
from .scrapers.base import PoliteFetcher, RobotsDisallowed, ScrapeError

log = logging.getLogger(__name__)
_lock = threading.Lock()

LINK_LABELS = {"ok": "Link funktioniert", "broken": "Link defekt", "unknown": "Link nicht prüfbar"}
RECHECK_AFTER = timedelta(days=7)


@dataclass
class LinkCheckResult:
    checked: int = 0
    ok: int = 0
    broken: int = 0
    unknown: int = 0


def classify(code: int | None, reason: str) -> tuple[str, str]:
    if code is None:
        # Could be the site, or our own network: never call it broken on that alone.
        return "unknown", f"Keine Antwort ({reason})"
    if code < 400:
        return "ok", f"HTTP {code}"
    if code in (404, 410):
        return "broken", f"Seite nicht gefunden (HTTP {code})"
    return "unknown", f"HTTP {code} – Seite blockiert automatische Abrufe oder hat gerade Probleme"


def link_for(opp: Opportunity) -> str | None:
    url = opp.official_url or opp.source_url
    return url if url and url.startswith(("http://", "https://")) else None


def check_one(fetcher: PoliteFetcher, opp: Opportunity) -> str:
    url = link_for(opp)
    if url is None:
        return "skipped"
    try:
        code, reason = fetcher.status(url)
        status, note = classify(code, reason)
    except RobotsDisallowed:
        status, note = "unknown", "robots.txt der Seite erlaubt keine automatische Prüfung"
    except ScrapeError as exc:
        status, note = "unknown", str(exc)[:300]
    opp.link_status, opp.link_check_note, opp.link_checked_at = status, note[:300], utcnow()
    return status


def check_links(db: Session, fetcher: PoliteFetcher | None = None, limit: int = 200, force: bool = False,
                actor: str = "system") -> LinkCheckResult:
    """Check the links of published and to-be-reviewed listings that were not checked in the last week."""
    result = LinkCheckResult()
    if not _lock.acquire(blocking=False):
        return result
    try:
        fetcher = fetcher or PoliteFetcher()
        q = select(Opportunity).where(
            Opportunity.status.in_([S.published, S.awaiting_review]),
            or_(Opportunity.official_url.is_not(None), Opportunity.source_url.is_not(None)),
        )
        if not force:
            q = q.where(or_(Opportunity.link_checked_at.is_(None), Opportunity.link_checked_at < utcnow() - RECHECK_AFTER))
        q = q.order_by(Opportunity.link_checked_at.is_not(None), Opportunity.link_checked_at).limit(limit)
        for opp in db.scalars(q).all():
            status = check_one(fetcher, opp)
            if status == "skipped":
                continue
            result.checked += 1
            setattr(result, status, getattr(result, status) + 1)
            db.commit()
        audit(db, actor, "links.check", None, None, checked=result.checked, ok=result.ok, broken=result.broken,
              unknown=result.unknown)
        db.commit()
        log.info("link check: %s", result)
        return result
    finally:
        _lock.release()
