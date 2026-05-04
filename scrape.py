"""Scrape any Fextralife wiki to Markdown files.

Works for any Fextralife subdomain (Dark Souls, Elden Ring, Bloodborne,
Sekiro, etc.). Pick a wiki with --base, then choose a mode:

- Sitemap mode (default): reads /sitemap.xml and scrapes every page into
  a flat output directory.
- Category mode (--category Weapons --category Armor ...): fetches each
  category hub page (e.g. /Weapons), extracts member links, and saves
  each category into its own subfolder.
- Discover mode (--discover): prints the wiki's sidebar category names
  so you know what to pass to --category.

In all modes the scraper extracts only the article body
(#wiki-content-block), strips images, and converts to Markdown.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "identity",
}

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
CONTENT_SELECTOR = "#wiki-content-block"
SIDEBAR_SELECTORS = (".wiki-menu-2-left", ".sidebar-nav")
FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Paths inside category hub pages that are not member content (sidebar nav,
# meta-pages, etc.). Member URLs containing any of these are dropped.
HUB_LINK_BLOCKLIST = (
    "/forum/",
    "/search/",
    "/edit/",
    "/Tags/",
    "/Special:",
    "/Recent+Changes",
    "/To+Do+List",
    "/Help",
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
            names.add(unquote(path))
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


def extract_main_html(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(CONTENT_SELECTOR)
    if not el:
        return None
    for tag in el.find_all(["img", "script", "style", "noscript", "iframe"]):
        tag.decompose()
    for a in el.find_all("a", href=True):
        a["href"] = urljoin(page_url, a["href"])
    return str(el)


def html_to_markdown(html_fragment: str) -> str:
    md = markdownify(html_fragment, heading_style="ATX", strip=["img"])
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def scrape_one(
    session: requests.Session, url: str, out_path: Path, overwrite: bool
) -> str:
    """Return one of: 'saved', 'skipped', 'failed'."""
    if out_path.exists() and not overwrite:
        return "skipped"

    try:
        r = session.get(url, timeout=30)
        if r.status_code == 404:
            print(f"  404 {url}", file=sys.stderr)
            return "failed"
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  HTTP error {url}: {e}", file=sys.stderr)
        return "failed"

    content_html = extract_main_html(r.text, url)
    if not content_html:
        print(f"  no #wiki-content-block at {url}", file=sys.stderr)
        return "failed"

    md = html_to_markdown(content_html)
    if not md:
        print(f"  empty content at {url}", file=sys.stderr)
        return "failed"

    title = url_to_title(url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"# {title}\n\nSource: {url}\n\n{md}\n",
        encoding="utf-8",
    )
    return "saved"


def scrape_url_list(
    session: requests.Session,
    urls: list[str],
    out_dir: Path,
    delay: float,
    overwrite: bool,
    label: str = "",
) -> tuple[int, int, int]:
    """Save each URL into out_dir/<slug>.md. Returns (saved, skipped, failed)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = skipped = failed = 0

    for i, url in enumerate(urls, 1):
        slug = url_to_filename(url)
        path = out_dir / f"{slug}.md"
        prefix = f"{label}[{i}/{len(urls)}]" if label else f"[{i}/{len(urls)}]"

        result = scrape_one(session, url, path, overwrite)
        if result == "saved":
            saved += 1
            print(f"{prefix} saved {slug}.md")
        elif result == "skipped":
            skipped += 1
            print(f"{prefix} skip {slug}")
        else:
            failed += 1

        if i < len(urls):
            time.sleep(delay)

    return saved, skipped, failed


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--base",
        default="https://darksouls.wiki.fextralife.com",
        help=(
            "Fextralife wiki subdomain base URL. Examples: "
            "https://darksouls.wiki.fextralife.com (default), "
            "https://eldenring.wiki.fextralife.com, "
            "https://bloodborne.wiki.fextralife.com, "
            "https://sekiroshadowsdietwice.wiki.fextralife.com"
        ),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("output"),
        help="Output directory (default: output)",
    )
    p.add_argument(
        "--category",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Category hub to scrape into its own subfolder. Repeatable. "
            "E.g. --category Weapons --category Armor --category Magic"
        ),
    )
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Seconds between requests (default: 1.0)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many URLs (useful for testing)",
    )
    p.add_argument(
        "--filter",
        default=None,
        help="Only scrape URLs matching this regex (sitemap mode only)",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .md files (default: skip)",
    )
    p.add_argument(
        "--list-only",
        action="store_true",
        help="Print URLs that would be scraped, don't download anything",
    )
    p.add_argument(
        "--discover",
        action="store_true",
        help=(
            "Print the wiki's sidebar category names. "
            "Use the output to pick values for --category."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    session = make_session()

    if args.discover:
        run_discover_mode(session, args)
    elif args.category:
        run_category_mode(session, args)
    else:
        run_sitemap_mode(session, args)


def run_discover_mode(session: requests.Session, args: argparse.Namespace) -> None:
    print(f"Fetching sidebar from {args.base} ...")
    names = discover_sidebar_categories(session, args.base)
    if not names:
        print("No sidebar categories found. Try --base with a different URL.", file=sys.stderr)
        sys.exit(1)
    print(f"\n{len(names)} category candidates (pass any to --category):\n")
    for name in names:
        print(f"  {name}")


def run_category_mode(session: requests.Session, args: argparse.Namespace) -> None:
    total_saved = total_skipped = total_failed = 0
    for cat in args.category:
        cat_label = cat.strip("/").replace("+", " ")
        cat_dir = args.out / cat_label
        print(f"\n=== Category: {cat_label} → {cat_dir} ===")

        try:
            hub_url, members = fetch_category_member_urls(session, args.base, cat)
        except requests.RequestException as e:
            print(f"  failed to fetch hub /{cat}: {e}", file=sys.stderr)
            total_failed += 1
            continue

        urls = [hub_url] + members
        if args.limit:
            urls = urls[: args.limit]
        print(f"  {len(urls)} pages (1 hub + {len(urls) - 1} members)")

        if args.list_only:
            for u in urls:
                print(f"  {u}")
            continue

        s, k, f = scrape_url_list(
            session=session,
            urls=urls,
            out_dir=cat_dir,
            delay=args.delay,
            overwrite=args.overwrite,
            label=f"  [{cat_label}] ",
        )
        total_saved += s
        total_skipped += k
        total_failed += f

    if not args.list_only:
        print(
            f"\nDone. saved={total_saved} skipped={total_skipped} failed={total_failed}"
        )


def run_sitemap_mode(session: requests.Session, args: argparse.Namespace) -> None:
    sitemap_url = f"{args.base.rstrip('/')}/sitemap.xml"
    print(f"Fetching sitemap: {sitemap_url}")
    urls = fetch_sitemap_urls(session, sitemap_url)
    print(f"  {len(urls)} URLs in sitemap")

    if args.filter:
        pattern = re.compile(args.filter)
        urls = [u for u in urls if pattern.search(u)]
        print(f"  {len(urls)} after filter {args.filter!r}")
    if args.limit:
        urls = urls[: args.limit]
        print(f"  {len(urls)} after limit")

    if args.list_only:
        for u in urls:
            print(u)
        return

    s, k, f = scrape_url_list(
        session=session,
        urls=urls,
        out_dir=args.out,
        delay=args.delay,
        overwrite=args.overwrite,
    )
    print(f"\nDone. saved={s} skipped={k} failed={f}")


if __name__ == "__main__":
    main()
