"""Scraper for arnoldspumpclub.com (Beehiiv-hosted newsletter)."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BASE_URL = "https://arnoldspumpclub.com"
LISTING_URL = "https://arnoldspumpclub.com/blogs/newsletter"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GarminBot/1.0; +https://github.com/garminbot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
REQUEST_DELAY = 2.5  # polite delay between requests (seconds)
MAX_PAGES = 100


@dataclass
class PostMeta:
    url: str
    title: str
    published_date: date | None


def _is_allowed_url(url: str) -> bool:
    """Return True only if url has scheme http/https and netloc arnoldspumpclub.com."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.netloc == "arnoldspumpclub.com"


def _is_post_url(href: str) -> bool:
    """Return True if href looks like a newsletter post (not a tag/collection page)."""
    # Must contain /blogs/newsletter/ and have a slug after it
    parts = href.rstrip("/").split("/blogs/newsletter/")
    return len(parts) == 2 and bool(parts[1]) and "?" not in parts[1]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15), reraise=True)
def _get(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _parse_date(text: str) -> date | None:
    """Parse a date string from Beehiiv post cards into a date object."""
    text = text.strip()
    # ISO datetime attribute (e.g. <time datetime="2024-03-15T...">)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    # Human-readable formats: "March 15, 2024", "Mar 15, 2024"
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    # Extract "Month DD, YYYY" substring
    m = re.search(r"([A-Za-z]+ \d{1,2},?\s*\d{4})", text)
    if m:
        clean = re.sub(r",\s*", " ", m.group(1))
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(clean, fmt).date()
            except ValueError:
                pass
    return None


def _extract_posts_from_page(soup: BeautifulSoup) -> list[PostMeta]:
    """Extract post metadata from the /blogs/newsletter listing page (Shopify)."""
    posts: list[PostMeta] = []
    seen: set[str] = set()

    # Shopify blog cards: <a href="/blogs/newsletter/<slug>">
    links = soup.find_all("a", href=lambda h: h and "/blogs/newsletter/" in h)

    for link in links:
        href: str = link.get("href", "")
        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        full_url = full_url[:490]

        if not _is_allowed_url(full_url) or not _is_post_url(full_url):
            continue

        if full_url in seen:
            continue
        seen.add(full_url)

        # Title: prefer heading inside the card, fall back to link text
        card = link.find_parent(["article", "li", "div"])
        title_el = card.find(["h1", "h2", "h3"]) if card else None
        title = (title_el or link).get_text(separator=" ", strip=True)
        if not title:
            continue

        # Date: <time> tag first, then any element with "date" in class/id
        date_el = None
        if card:
            date_el = card.find("time") or card.find(
                class_=lambda c: c and "date" in " ".join(c).lower()
            )
        pub_date: date | None = None
        if date_el:
            raw = date_el.get("datetime", "") or date_el.get_text(strip=True)
            pub_date = _parse_date(raw)

        posts.append(PostMeta(url=full_url, title=title, published_date=pub_date))

    return posts


def scrape_post_list() -> list[PostMeta]:
    """Scrape all available post metadata from arnoldspumpclub.com.

    Handles pagination automatically. Returns posts ordered by date ascending
    (oldest first) to allow incremental processing.
    """
    posts: list[PostMeta] = []
    seen_urls: set[str] = set()
    page = 1

    while True:
        if page > MAX_PAGES:
            logger.warning("Newsletter scraper: reached MAX_PAGES (%d), stopping pagination", MAX_PAGES)
            break
        url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
        logger.info("Newsletter scraper: fetching post list page %d", page)

        try:
            soup = _get(url)
        except Exception as exc:
            logger.warning("Newsletter scraper: failed to fetch page %d: %s", page, exc)
            break

        found = _extract_posts_from_page(soup)
        new = [p for p in found if p.url not in seen_urls]
        if not new:
            break

        for p in new:
            seen_urls.add(p.url)
            posts.append(p)

        # Check for a "next page" link
        has_next = bool(
            soup.find("a", string=re.compile(r"next|older|mais", re.I))
            or soup.find("a", rel=lambda r: r and "next" in r)
        )
        if not has_next:
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    # Sort oldest-first so bulk processing is chronological
    posts.sort(key=lambda p: p.published_date or date.min)
    logger.info("Newsletter scraper: found %d posts total", len(posts))
    return posts


def scrape_post_content(url: str) -> str:
    """Fetch a post and return its main text content (noise-stripped)."""
    if not _is_allowed_url(url):
        raise ValueError(f"URL not allowed: {url!r}")
    logger.info("Newsletter scraper: fetching content for %s", url)
    soup = _get(url)

    # Remove structural noise
    for tag in soup.find_all(["nav", "footer", "header", "script", "style", "aside", "form"]):
        tag.decompose()

    # Shopify blog post content selectors (most specific first)
    content = (
        soup.find(class_=re.compile(r"article[-_]?(body|content|template|rte)", re.I))
        or soup.find(class_=re.compile(r"rte|blog[-_]?post[-_]?content|post[-_]?content", re.I))
        or soup.find("article")
        or soup.find("main")
    )

    if content:
        return content.get_text(separator="\n", strip=True)

    # Last-resort: full body
    body = soup.find("body")
    return body.get_text(separator="\n", strip=True) if body else ""


def scrape_latest_post() -> PostMeta | None:
    """Return the most recent post from the first listing page, or None."""
    try:
        soup = _get(LISTING_URL)
    except Exception as exc:
        logger.error("Newsletter scraper: failed to fetch listing page: %s", exc)
        return None
    posts = _extract_posts_from_page(soup)
    # First card in the listing is the newest post
    return posts[0] if posts else None


def iter_new_posts(known_urls: set[str]) -> Iterator[PostMeta]:
    """Yield PostMeta for any post not in known_urls (newest-first check)."""
    all_posts = scrape_post_list()
    for post in reversed(all_posts):  # newest first
        if post.url not in known_urls:
            yield post
