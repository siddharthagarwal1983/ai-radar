"""Stage 1-2: fetch feeds, normalise, drop duplicates and anything stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx

from .config import Source, SourceSet
from .models import RawItem

USER_AGENT = "ai-radar/0.1 (+personal news digest; contact via repo)"
TRACKING_PREFIXES = ("utm_", "ref_", "mc_")


def canonicalise(url: str) -> str:
    """Strip tracking noise and fragments so the same story from two feeds collides."""
    parsed = urlparse(url)
    kept = [
        pair
        for pair in parsed.query.split("&")
        if pair and not pair.split("=")[0].startswith(TRACKING_PREFIXES)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "&".join(kept), ""))


def _published(entry: object) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, field, None)
        if parsed:
            year, month, day, hour, minute, second = parsed[:6]
            return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    return None


def fetch_source(source: Source, timeout: int = 20) -> tuple[list[RawItem], str | None]:
    """Return (items, error). An empty list with no error is a valid, quiet feed."""
    try:
        response = httpx.get(
            source.url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"

    parsed = feedparser.parse(response.content)
    items = []
    for entry in parsed.entries:
        link = getattr(entry, "link", "") or ""
        title = _strip_html(getattr(entry, "title", "") or "")
        if not link or not title:
            continue
        summary = getattr(entry, "summary", "") or ""
        items.append(
            RawItem(
                source_id=source.id,
                source_name=source.name,
                title=title,
                url=link,
                canonical_url=canonicalise(link),
                summary=_strip_html(summary),
                published_at=_published(entry),
            )
        )
    return items, None


def _strip_html(text: str) -> str:
    import re
    from html import unescape

    plain = unescape(re.sub(r"<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", plain).strip()


def interleave(by_source: list[list[RawItem]]) -> list[RawItem]:
    """Round-robin across sources, newest first within each.

    Downstream stages cap the candidate pool, and a flat concatenation makes that
    cap a function of config order: arXiv alone supplies ~120 items, so a limit of
    60 over a concatenated list would never reach the news feeds. Interleaving
    makes every source represented at any cutoff.
    """
    merged: list[RawItem] = []
    for rank in range(max((len(items) for items in by_source), default=0)):
        for items in by_source:
            if rank < len(items):
                merged.append(items[rank])
    return merged


def ingest(source_set: SourceSet) -> tuple[list[RawItem], dict[str, str]]:
    """Fetch every enabled source. Returns deduped items plus per-source errors."""
    by_source: list[list[RawItem]] = []
    errors: dict[str, str] = {}

    for source in source_set.enabled:
        items, error = fetch_source(source)
        if error:
            errors[source.id] = error
            continue
        items.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        by_source.append(items[: source_set.max_items_per_source])

    return dedupe(interleave(by_source), source_set.max_age_days), errors


def dedupe(items: list[RawItem], max_age_days: int) -> list[RawItem]:
    """Drop exact URL repeats and anything older than the window.

    Items with no date are kept: several vendor feeds omit it, and dropping them
    would silently lose the sources closest to the beat.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    seen: set[str] = set()
    kept: list[RawItem] = []

    for item in items:
        if item.canonical_url in seen:
            continue
        if item.published_at and item.published_at < cutoff:
            continue
        seen.add(item.canonical_url)
        kept.append(item)

    return kept
