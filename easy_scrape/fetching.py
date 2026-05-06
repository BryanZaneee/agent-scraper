"""Network/session helpers and URL discovery utilities."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import (
    BROWSER_HEADERS,
    CATEGORY_DISCOVERY_BLOCKLIST,
    CONTENT_SELECTOR,
    FILENAME_FORBIDDEN,
    HUB_LINK_BLOCKLIST,
    SIDEBAR_SELECTORS,
    SITEMAP_NS,
)

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def fetch_sitemap_urls(session: requests.Session, sitemap_url: str) -> list[str]:
    r = session.get(sitemap_url, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    return [loc.text.strip() for loc in root.findall(".//sm:loc", SITEMAP_NS) if loc.text]


def discover_sidebar_categories(
    session: requests.Session, base_url: str
) -> list[str]:
    """Return single-segment category names from the wiki's sidebar nav.

    Tries SIDEBAR_SELECTORS in order; returns the first one that matches.
    """
    r = session.get(base_url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    base_host = urlparse(base_url).netloc
    for sel in SIDEBAR_SELECTORS:
        nav = soup.select_one(sel)
        if not nav:
            continue
        names: set[str] = set()
        for a in nav.find_all("a", href=True):
            absolute = urljoin(base_url, a["href"]).split("#")[0].split("?")[0]
            parsed = urlparse(absolute)
            if parsed.netloc != base_host:
                continue
            path = parsed.path.strip("/")
            if not path or "/" in path:
                continue
            if any(b.strip("/") == path for b in HUB_LINK_BLOCKLIST):
                continue
            unquoted = unquote(path)
            if any(b.lower() == unquoted.lower() for b in CATEGORY_DISCOVERY_BLOCKLIST):
                continue
            names.add(unquoted)
        if names:
            return sorted(names, key=str.lower)
    return []


def fetch_category_member_urls(
    session: requests.Session, base_url: str, category: str
) -> tuple[str, list[str]]:
    """Return (hub_url, [member_urls]) for a category like 'Weapons'.

    Pulls every internal link from the hub page's #wiki-content-block.
    """
    hub_url = f"{base_url.rstrip('/')}/{category.lstrip('/')}"
    r = session.get(hub_url, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    el = soup.select_one(CONTENT_SELECTOR)
    if not el:
        return hub_url, []

    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    members: list[str] = []
    for a in el.find_all("a", href=True):
        absolute = urljoin(hub_url, a["href"]).split("#")[0]
        parsed = urlparse(absolute)
        if parsed.netloc != base_host:
            continue
        if not parsed.path or parsed.path == "/":
            continue
        if any(b in parsed.path for b in HUB_LINK_BLOCKLIST):
            continue
        if absolute == hub_url or absolute in seen:
            continue
        seen.add(absolute)
        members.append(absolute)
    return hub_url, members


def url_to_filename(url: str) -> str:
    path = urlparse(url).path.lstrip("/")
    name = unquote(path).replace("+", " ").replace("/", "_").strip() or "index"
    name = FILENAME_FORBIDDEN.sub("_", name)
    return name[:200]


def url_to_title(url: str) -> str:
    path = urlparse(url).path.lstrip("/")
    return unquote(path).replace("+", " ").replace("_", " ") or "Index"


