"""Scrape wikis and web content to Markdown files.

Currently optimized for Fextralife wikis (Dark Souls, Elden Ring, Bloodborne,
Sekiro, etc.). Extensible for other sites. Launch with no arguments for an
interactive category picker, or pass flags for scriptable modes:

- Interactive mode (no args or --interactive): choose the default Dark Souls
  wiki or enter a base URL, discover categories, multi-select categories, then
  scrape them.
- Sitemap mode (explicit flags without --category/--discover): reads
  /sitemap.xml and scrapes every page into a flat output directory.
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
import math
import os
import random
import re
import select
import signal
import shutil
import sys
import termios
import threading
import time
import tty
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TextIO
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

DEFAULT_BASE_URL = "https://darksouls.wiki.fextralife.com"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "easy_scrape_output"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
CONTENT_SELECTOR = "#wiki-content-block"
SIDEBAR_SELECTORS = (".wiki-menu-2-left", ".sidebar-nav")
FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ASSET_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
CONTENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
CONTENT_IMAGE_MIN_DIMENSION = 80
PRESERVED_IMAGE_ATTR = "data-easy-scrape-preserved-image"
TOKEN_CHARS_PER_TOKEN = 4
TUI_FRAME_SECONDS = 1 / 18
TUI_MIN_WIDTH = 60
TUI_MIN_HEIGHT = 18
MAX_RAIN_SPLASHES = 100
MAX_RECENT_EVENTS = 4
INTERACTIVE_KEY_POLL_SECONDS = 0.08

ANSI_RESET = "\x1b[0m"
ANSI_CLEAR = "\x1b[2J"
ANSI_HOME = "\x1b[H"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"
ANSI_ALT_SCREEN = "\x1b[?1049h"
ANSI_MAIN_SCREEN = "\x1b[?1049l"
ANSI_ENABLE_MOUSE = "\x1b[?1000h\x1b[?1006h"
ANSI_DISABLE_MOUSE = "\x1b[?1006l\x1b[?1000l"

ANSI_COLORS = {
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
    "cyan": "\x1b[36m",
    "blue": "\x1b[34m",
    "white": "\x1b[37m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "magenta": "\x1b[35m",
}

EASY_SCRAPE_BANNER = [
    "  ___  __ _ ___ _   _ ___  ___ _ __ __ _ _ __   ___",
    " / _ \\/ _` / __| | | / __|/ __| '__/ _` | '_ \\ / _ \\",
    "|  __/ (_| \\__ \\ |_| \\__ \\ (__| | | (_| | |_) |  __/",
    " \\___|\\__,_|___/\\__, |___/\\___|_|  \\__,_| .__/ \\___|",
    "                |___/                   |_|",
]

BONFIRE_ASCII = [
    ("        /\\", "yellow"),
    ("        ||", "yellow"),
    ("     .-'||'-.", "dim"),
    ("        ||", "yellow"),
    ("     \\  ||  /", "red"),
    ("    .-\\ || /-.", "yellow"),
    ("   /__/####\\__\\", "red"),
    ("     _/====\\_", "dim"),
]

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


def _parse_dimension(value) -> int | None:
    """Return the leading integer from an HTML dimension attribute."""
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", str(value))
    if not match:
        return None
    return int(match.group(1))


def _image_source_url(img, page_url: str) -> str | None:
    """Return the best source URL for an <img>, resolved against page_url."""
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        raw = (img.get(attr) or "").strip()
        if raw and not raw.startswith("data:"):
            return urljoin(page_url, raw)
    return None


def _url_has_content_image_extension(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() in CONTENT_IMAGE_EXTENSIONS


def asset_filename_from_url(source_url: str, seen: set[str] | None = None) -> str:
    """Return a stable, safe local filename for a downloaded image URL."""
    raw_name = Path(unquote(urlparse(source_url).path)).name or "image"
    stem, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    stem = ASSET_FILENAME_UNSAFE.sub("_", stem)
    stem = re.sub(r"_+", "_", stem).strip("._-") or "image"
    ext = ASSET_FILENAME_UNSAFE.sub("", ext)
    base = stem[:160]
    suffix = ext[:20]
    candidate = f"{base}{suffix}"

    if seen is None:
        return candidate

    counter = 2
    while candidate in seen:
        candidate = f"{base}_{counter}{suffix}"
        counter += 1
    seen.add(candidate)
    return candidate


def download_image_asset(
    session: requests.Session, source_url: str, dest_path: Path
) -> bool:
    """Download one image asset. Returns False instead of raising on failure."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return True
    try:
        r = session.get(source_url, timeout=30)
        if r.status_code == 404:
            print(f"  404 image {source_url}", file=sys.stderr)
            return False
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  image HTTP error {source_url}: {e}", file=sys.stderr)
        return False

    content_type = (r.headers.get("content-type") or "").split(";", 1)[0].lower()
    if content_type and not content_type.startswith("image/"):
        print(
            f"  skipping non-image response {source_url}: {content_type}",
            file=sys.stderr,
        )
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(r.content)
    return True


@dataclass
class ImageAssetContext:
    """State needed to download page images and rewrite them as local refs."""

    session: requests.Session
    asset_dir: Path
    markdown_dir: Path
    downloader: Callable[[requests.Session, str, Path], bool] = download_image_asset
    seen_filenames: set[str] = field(default_factory=set)


def _element_text(element) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _image_label(img, source_url: str) -> str:
    """Pick the least noisy image label available in nearby page markup."""
    parent_anchor = img.find_parent("a")
    if parent_anchor:
        text = _element_text(parent_anchor)
        if text:
            return text

    heading = img.find_previous(["h1", "h2", "h3", "h4"])
    if heading:
        text = _element_text(heading)
        if text:
            return text

    alt = (img.get("alt") or "").strip()
    if alt:
        return " ".join(alt.split())

    name = Path(unquote(urlparse(source_url).path)).stem
    name = re.sub(r"[_\-]+", " ", name).strip()
    return name or "Image"


def _is_meaningful_content_image(img, source_url: str, page_url: str) -> bool:
    """True for page-owned content images; false for stat/table icons."""
    if img.find_parent("table"):
        return False

    source_path = urlparse(source_url).path.lower()
    source_is_file_image = (
        "/file/" in source_path and _url_has_content_image_extension(source_url)
    )

    linked_file_image = False
    parent_anchor = img.find_parent("a", href=True)
    if parent_anchor:
        href_url = urljoin(page_url, parent_anchor["href"])
        href_path = urlparse(href_url).path.lower()
        linked_file_image = (
            "/file/" in href_path and _url_has_content_image_extension(href_url)
        )

    if not (source_is_file_image or linked_file_image):
        return False

    width = _parse_dimension(img.get("width"))
    height = _parse_dimension(img.get("height"))
    if width is not None and height is not None:
        return (
            width >= CONTENT_IMAGE_MIN_DIMENSION
            and height >= CONTENT_IMAGE_MIN_DIMENSION
        )
    if width is not None:
        return width >= CONTENT_IMAGE_MIN_DIMENSION
    if height is not None:
        return height >= CONTENT_IMAGE_MIN_DIMENSION
    return True


def preserve_content_images(
    element, page_url: str, image_assets: ImageAssetContext
) -> None:
    """Download meaningful content images and rewrite <img> tags to local paths."""
    for img in list(element.find_all("img")):
        source_url = _image_source_url(img, page_url)
        if not source_url:
            continue
        if not _is_meaningful_content_image(img, source_url, page_url):
            continue

        filename = asset_filename_from_url(source_url, image_assets.seen_filenames)
        asset_path = image_assets.asset_dir / filename
        if not image_assets.downloader(image_assets.session, source_url, asset_path):
            continue

        rel_path = os.path.relpath(asset_path, start=image_assets.markdown_dir)
        img["src"] = rel_path.replace(os.sep, "/")
        img["alt"] = _image_label(img, source_url)
        img[PRESERVED_IMAGE_ATTR] = "1"
        for attr in (
            "data-src",
            "data-original",
            "data-lazy-src",
            "srcset",
            "style",
            "title",
            "width",
            "height",
        ):
            if img.has_attr(attr):
                del img[attr]

        heading = img.find_parent(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading is not None:
            img.extract()
            heading.insert_after(img)


def expand_rowspans(element) -> None:
    """In-place: clone <td rowspan=N> cells into the next N-1 rows.

    Fextralife stacks NG / NG+ values under a single Health (or Souls) label
    using rowspan. After markdownify, those secondary rows lose the label and
    extract_frontmatter only sees NG. Pre-expanding rectangularizes the table
    so every row carries every label and frontmatter can capture both values.
    """
    import copy as _copy

    for table in element.find_all("table"):
        rows = table.find_all("tr")
        for r_idx, row in enumerate(rows):
            for cell in list(row.find_all(["td", "th"], recursive=False)):
                span_str = cell.get("rowspan")
                if not span_str:
                    continue
                try:
                    span = int(span_str)
                except (ValueError, TypeError):
                    del cell["rowspan"]
                    continue
                del cell["rowspan"]
                if span <= 1:
                    continue
                row_cells = row.find_all(["td", "th"], recursive=False)
                col_idx = row_cells.index(cell)
                for offset in range(1, span):
                    target_idx = r_idx + offset
                    if target_idx >= len(rows):
                        break
                    target_row = rows[target_idx]
                    target_cells = target_row.find_all(["td", "th"], recursive=False)
                    clone = _copy.deepcopy(cell)
                    if col_idx < len(target_cells):
                        target_cells[col_idx].insert_before(clone)
                    else:
                        target_row.append(clone)


def replace_images_with_alt(element) -> None:
    """In-place: replace <img alt='X'> with cleaned alt text; drop empty alts.

    Preserves stat-icon labels in tables (Fextralife uses icons for many column
    headers — without alt-text fallback, those columns become unlabeled values).
    The alt text is cleaned of game-name and 'icon' noise before insertion so
    the rendered markdown table is readable.
    """
    for img in list(element.find_all("img")):
        if img.get(PRESERVED_IMAGE_ATTR) == "1":
            continue
        alt = (img.get("alt") or "").strip()
        if not alt:
            img.decompose()
            continue
        cleaned = _clean_label(alt)
        if cleaned:
            img.replace_with(cleaned)
        else:
            img.decompose()


_BANNER_ROW_RE = re.compile(r"^(?:Boss|NPC|Item|Image)\s+\d+\b")


def drop_banner_alt_rows(element) -> None:
    """In-place: drop <tr> rows that are just the page-banner image alt-text.

    Fextralife item/boss pages have a banner image with alt text like
    'Boss 0036 Asylum Demon'. After replace_images_with_alt promotes the alt
    text to plain text, that image becomes a one-cell row inside the stat
    table. Drop these — they're decorative and duplicate the page title.
    """
    for table in element.find_all("table"):
        for row in list(table.find_all("tr")):
            non_empty = [
                _cell_text(c)
                for c in row.find_all(["td", "th"])
                if _cell_text(c)
            ]
            if len(non_empty) == 1 and _BANNER_ROW_RE.match(non_empty[0]):
                row.decompose()


def drop_footer_nav_table(element) -> None:
    """In-place: drop the trailing single-column ♦-list navigation table.

    Every Fextralife item page ends with a nav table listing every other item
    in the category. It's huge and adds no value to a per-page file.
    """
    tables = element.find_all("table")
    if not tables:
        return
    last = tables[-1]
    first_row = last.find("tr")
    if not first_row:
        return
    cells = first_row.find_all(["td", "th"])
    if len(cells) == 1:
        last.decompose()


def drop_stranded_category_links(element) -> None:
    """In-place: drop top-level <p> tags that are bare sidebar-leak category links.

    Predicate: paragraph contains exactly one anchor, the anchor's visible text
    equals the paragraph's full text, and the anchor's href is a single-segment
    path (e.g. 'Ammunition' linking to /Ammunition). Walks every top-level <p>
    child — handles both the leading sidebar leak (Drake Sword) and the
    mid-article variant where the leak appears between the intro and the main
    content table (Embers).
    """
    for p in list(element.find_all("p", recursive=False)):
        text_content = p.get_text(strip=True)
        if not text_content:
            continue
        anchors = p.find_all("a")
        if len(anchors) != 1:
            continue
        anchor_text = anchors[0].get_text(strip=True)
        if anchor_text != text_content:
            continue
        href = anchors[0].get("href") or ""
        path = urlparse(href).path.strip("/")
        if not path or "/" in path:
            continue
        p.decompose()


def drop_fragment_anchors(element, page_url: str) -> None:
    """In-place: decompose <a> tags pointing to a fragment within the same page.

    Pages prepend a 'Jump to Strategies ↓' anchor link to #strategies and
    similar within-page skip-links. They're decorative, not informational.
    Detection compares the resolved URL's path to the current page's path —
    handles both `#strategies`, `Asylum+Demon#strategies`, and the absolutized
    `https://.../Asylum+Demon#strategies` forms.
    """
    page_path = urlparse(page_url).path
    for a in list(element.find_all("a", href=True)):
        href = a["href"]
        if "#" not in href:
            continue
        resolved = urljoin(page_url, href)
        parsed = urlparse(resolved)
        if parsed.fragment and parsed.path == page_path:
            a.decompose()


def strip_inline_links(element) -> None:
    """In-place: unwrap every <a> tag, replacing it with its inner text/elements.

    Inline links bloat AI-KB token budgets without adding semantic value —
    'Lautrec' carries the same meaning as
    '[Lautrec](https://.../Knight+Lautrec "Dark Souls Knight Lautrec")'.
    Cross-page references belong in structured fields (frontmatter `url`),
    not as URL-laden body prose. The page's own URL is still recorded in
    YAML frontmatter, so provenance isn't lost.
    """
    for a in list(element.find_all("a")):
        a.unwrap()


def drop_empty_columns(element) -> None:
    """In-place: per <table>, drop columns where every body cell is empty.

    Fextralife uses icon images for column headers in stat tables; after
    image-alt cleanup, many columns are entirely empty. Operates on the
    rows whose cell count matches the predominant width — outlier rows
    (typically full-width title/section headers via colspan) are left alone.
    """
    for table in element.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        cell_counts = [len(r.find_all(["td", "th"])) for r in rows]
        if not cell_counts:
            continue
        n_cols = max(set(cell_counts), key=cell_counts.count)
        if n_cols <= 1:
            continue
        matching_rows = [r for r in rows if len(r.find_all(["td", "th"])) == n_cols]
        if len(matching_rows) < 2:
            continue
        keep = [False] * n_cols
        for row in matching_rows:
            for i, c in enumerate(row.find_all(["td", "th"])):
                if _cell_text(c):
                    keep[i] = True
        if all(keep):
            continue
        for row in matching_rows:
            cells = row.find_all(["td", "th"])
            for i in range(n_cols - 1, -1, -1):
                if not keep[i]:
                    cells[i].decompose()


def _cell_text(cell) -> str:
    """Extract and collapse cell text (whitespace-normalized, no HTML)."""
    return " ".join(cell.get_text(" ", strip=True).split())


_LABEL_NOISE_RE = re.compile(
    r"\b(?:dark\s+souls(?:\s+remastered)?|icon|ds)\b",
    flags=re.IGNORECASE,
)


def _clean_label(s: str) -> str:
    """Strip game-name and 'icon' prefix noise from a stat label."""
    cleaned = _LABEL_NOISE_RE.sub(" ", s)
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).strip()
    return cleaned


def _to_snake(label: str) -> str:
    """Convert a label to snake_case key (after noise cleanup)."""
    cleaned = _clean_label(label)
    s = re.sub(r"[^a-z0-9]+", "_", cleaned.lower()).strip("_")
    return s


def _coerce_value(val: str):
    """Coerce a string value to int/float/bool when it looks like one."""
    val = val.strip()
    if not val:
        return None
    low = val.lower()
    if low in ("yes", "true"):
        return True
    if low in ("no", "false"):
        return False
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if re.fullmatch(r"-?\d+\.\d+", val):
        return float(val)
    return val


def _is_label_text(s: str) -> bool:
    """True if s looks like a stat label (mostly alpha, short, after noise strip)."""
    if not s or len(s) > 60:
        return False
    cleaned = _clean_label(s)
    if not cleaned:
        return False
    alpha = sum(c.isalpha() for c in cleaned)
    return alpha >= 3 and alpha >= len(cleaned) * 0.5


_FIXED_VALUE_TOKENS = {"yes", "no", "true", "false", "n/a", "-", "–"}


def _is_strict_value(s: str) -> bool:
    """Strict value: numeric, fixed token, has-digit, or multi-word capitalized."""
    if not s:
        return False
    s = s.strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return True
    if s.lower() in _FIXED_VALUE_TOKENS:
        return True
    if len(s) <= 40 and re.search(r"\d", s):
        return True
    if (
        2 <= len(s) <= 40
        and re.fullmatch(r"[A-Z][a-z]+(?:[\s\-'][A-Z][a-z]+)+", s)
    ):
        return True
    return False


def _is_short_enum(s: str) -> bool:
    """Single capitalized word — used as a fallback in 2-cell rows."""
    if not s:
        return False
    s = s.strip()
    return bool(re.fullmatch(r"[A-Z][a-z]+", s))


def _label_value_redundant(key: str, value) -> bool:
    """True if the snake key and the (string) value are essentially the same.

    Catches list-row false positives like 'wpn_parrying_dagger: Parrying Dagger'
    on category hub pages, where the alt-text icon and the link text describe
    the same thing rather than a label/value pair.
    """
    if not isinstance(value, str):
        return False
    if not key:
        return False

    def _norm(s: str) -> str:
        s = re.sub(r"\d+", "", s.lower())
        s = re.sub(r"[^a-z]+", "_", s).strip("_")
        return s

    nk = _norm(key)
    nv = _norm(value)
    if not nk or not nv:
        return False
    if min(len(nk), len(nv)) < 4:
        return False
    return nv in nk or nk in nv


def extract_frontmatter(
    element, *, url: str, title: str, category: str | None
) -> dict:
    """Pull universal + best-effort stats from the first table in the content.

    Returns a dict with always-present (title, url) and (when applicable)
    category + extracted stats. Never raises — failures fall back to the
    universal fields.
    """
    fm: dict = {"title": title, "url": url}
    if category:
        fm["category"] = category

    try:
        table = element.find("table")
        if table is None:
            return fm

        # Guard: only extract from the first table when it appears to be the
        # page's own stat table (title shows up in the first ~3 rows).
        # On catalog/hub pages, the first table lists OTHER items rather
        # than this page's stats, and the labels there are item names —
        # treating those as stats produces nonsense like
        # "soul_of_quelaag: Quelaag's Furysword".
        rows = table.find_all("tr")
        title_lower = title.lower().strip()
        if title_lower:
            preview = " ".join(
                _cell_text(c)
                for row in rows[:3]
                for c in row.find_all(["td", "th"])
            ).lower()
            if title_lower not in preview:
                return fm

        stats: dict = {}

        def _try_emit(label: str, value: str) -> bool:
            if not _is_label_text(label):
                return False
            key = _to_snake(label)
            if not key or key in fm:
                return False
            if len(stats) >= 20 and key not in stats:
                return False
            coerced = _coerce_value(value)
            if coerced is None or coerced == "":
                return False
            if _label_value_redundant(key, coerced):
                return False
            if key in stats:
                existing = stats[key]
                if (
                    isinstance(existing, str)
                    and isinstance(coerced, str)
                    and existing != coerced
                ):
                    stats[key] = f"{existing} / {coerced}"
                    return True
                return False
            stats[key] = coerced
            return True

        skip_next = False
        for idx, row in enumerate(rows):
            if skip_next:
                skip_next = False
                continue
            cells = row.find_all(["td", "th"])
            if cells and all(c.name == "th" for c in cells):
                continue
            non_empty = [_cell_text(c) for c in cells if _cell_text(c)]
            if len(non_empty) < 2:
                continue

            # Cross-row pairing: a label-only row followed by a value-only row.
            if (
                idx + 1 < len(rows)
                and all(_is_label_text(t) and not _is_strict_value(t) for t in non_empty)
            ):
                next_cells = rows[idx + 1].find_all(["td", "th"])
                next_non_empty = [_cell_text(c) for c in next_cells if _cell_text(c)]
                if (
                    len(next_non_empty) == len(non_empty)
                    and all(_is_strict_value(t) for t in next_non_empty)
                ):
                    for label, val in zip(non_empty, next_non_empty):
                        _try_emit(label, val)
                    skip_next = True
                    continue

            # Within-row alternating label/value pairs.
            emitted_any = False
            for i in range(0, len(non_empty) - 1, 2):
                label = non_empty[i]
                value = non_empty[i + 1]
                if _is_label_text(label) and _is_strict_value(value):
                    if _try_emit(label, value):
                        emitted_any = True
            if emitted_any:
                continue

            # 2-cell relaxed: a single label/value pair (boss-style or trailing
            # weapon attribute like "Attack Type | Regular").
            if len(non_empty) == 2:
                label, value = non_empty
                if _is_label_text(label) and (
                    _is_strict_value(value) or _is_short_enum(value)
                ):
                    _try_emit(label, value)

            if len(stats) >= 20:
                break

        fm.update(stats)
    except Exception as e:
        print(f"  frontmatter extraction failed for {url}: {e}", file=sys.stderr)

    return fm


_YAML_NEEDS_QUOTE = re.compile(r'[:#\n"\']|^[\-?|>!@&*]')


def format_frontmatter(fm: dict) -> str:
    """Hand-rolled YAML emit (avoids a PyYAML dep). Quotes only when needed."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            s = str(v)
            if _YAML_NEEDS_QUOTE.search(s):
                escaped = s.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{k}: "{escaped}"')
            else:
                lines.append(f"{k}: {s}")
    lines.append("---")
    return "\n".join(lines)


_GAME_NAME_RE = re.compile(
    r"\s*(?:and\s+Dark\s+Souls\s+Remastered|Dark\s+Souls\s+Remastered|Dark\s+Souls)\b",
    flags=re.IGNORECASE,
)
_TRAILING_NOISE_RE = re.compile(
    r"\s+\b(?:in|of|for|on|at|to|from|the|a|an|and|with)\b\s*$",
    flags=re.IGNORECASE,
)


def normalize_heading_line(line: str, title: str) -> str:
    """Strip 'Dark Souls' and the page title from a markdown heading."""
    if not line.startswith("#"):
        return line
    cleaned = _GAME_NAME_RE.sub("", line)
    if title:
        cleaned = re.sub(
            rf"\s+{re.escape(title)}\b", "", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(
            rf"\b{re.escape(title)}\s+", "", cleaned, flags=re.IGNORECASE
        )
    cleaned = re.sub(r":\s*$", "", cleaned)
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = _TRAILING_NOISE_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).rstrip()
    return cleaned


_PLACEHOLDER_BODY_RE = re.compile(
    r"^(#{2,6})[ \t]+[^\n]+\n+"
    r"(?:[*\-]\s*(?:N/A|Notes\s+and\s+Tips\s+go\s+here[\.,;:]*|TBD|None)\s*\n+)+"
    r"(?=#{1,6}[ \t]|\Z)",
    flags=re.MULTILINE,
)


def drop_placeholder_sections(md: str) -> str:
    """Drop heading + body for sections whose only content is N/A / TBD."""
    return _PLACEHOLDER_BODY_RE.sub("", md)


_EMPTY_HEADING_RE = re.compile(r"^#{1,6}[ \t]*$\n?", flags=re.MULTILINE)


def drop_empty_headings(md: str) -> str:
    """Remove heading lines that are just hashes after normalization stripped the text."""
    return _EMPTY_HEADING_RE.sub("", md)


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


@dataclass
class MarkdownFileStats:
    """Token estimate details for one generated Markdown file."""

    path: Path
    bytes: int
    words: int
    estimated_tokens: int


@dataclass
class MarkdownCorpusStats:
    """Aggregate token estimate details for a Markdown output collection."""

    root: Path
    file_count: int
    bytes: int
    words: int
    estimated_tokens: int
    files: list[MarkdownFileStats]


def estimate_token_count(text: str) -> int:
    """Return a stable rough token estimate for comparing Markdown corpora."""
    if not text:
        return 0
    return math.ceil(len(text) / TOKEN_CHARS_PER_TOKEN)


def collect_markdown_corpus_stats(root: Path) -> MarkdownCorpusStats:
    """Count Markdown files under root and estimate their combined token size."""
    files: list[MarkdownFileStats] = []
    total_bytes = total_words = total_tokens = 0

    if root.exists():
        for path in sorted(root.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            byte_count = len(text.encode("utf-8"))
            word_count = len(re.findall(r"\S+", text))
            token_count = estimate_token_count(text)
            files.append(
                MarkdownFileStats(
                    path=path,
                    bytes=byte_count,
                    words=word_count,
                    estimated_tokens=token_count,
                )
            )
            total_bytes += byte_count
            total_words += word_count
            total_tokens += token_count

    return MarkdownCorpusStats(
        root=root,
        file_count=len(files),
        bytes=total_bytes,
        words=total_words,
        estimated_tokens=total_tokens,
        files=files,
    )


def format_int(value: int) -> str:
    return f"{value:,}"


def print_markdown_corpus_stats(root: Path, *, top_n: int = 5) -> None:
    """Print a Repomix-style post-run summary for generated Markdown."""
    stats = collect_markdown_corpus_stats(root)
    print("\nToken summary")
    print(f"  Collection: {stats.root}")
    print(f"  Markdown files: {format_int(stats.file_count)}")
    print(f"  Bytes: {format_int(stats.bytes)}")
    print(f"  Words: {format_int(stats.words)}")
    print(
        "  Estimated tokens: "
        f"{format_int(stats.estimated_tokens)} "
        f"(~{TOKEN_CHARS_PER_TOKEN} chars/token)"
    )
    print(
        "  Final report: "
        f"{format_int(stats.file_count)} files, "
        f"{format_int(stats.estimated_tokens)} estimated tokens"
    )

    if not stats.files:
        return

    print(f"  Largest files:")
    root_resolved = root.resolve()
    largest = sorted(stats.files, key=lambda f: f.estimated_tokens, reverse=True)[:top_n]
    for file_stats in largest:
        try:
            label = file_stats.path.resolve().relative_to(root_resolved)
        except ValueError:
            label = file_stats.path
        print(
            "    "
            f"{format_int(file_stats.estimated_tokens)} tokens  "
            f"{label}"
        )


@dataclass
class TerminalRainDrop:
    x: float
    y: float
    speed_y: float
    speed_x: float
    character: str
    color: str
    z_index: int


@dataclass
class TerminalRainSplash:
    x: int
    y: int
    timer: int
    max_timer: int


@dataclass
class TerminalCloud:
    x: float
    y: int
    speed: float
    shape: list[str]
    color: str


@dataclass
class TerminalLightningBolt:
    segments: list[tuple[int, int, str]]
    age: int
    max_age: int


@dataclass
class ScrapeTuiState:
    title: str = "easy_scrape"
    mode: str = "starting"
    stage: str = "warming up"
    output_path: str = ""
    detail: str = ""
    current_url: str = ""
    current_slug: str = ""
    last_result: str = ""
    total: int = 0
    index: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.monotonic)
    recent_events: list[str] = field(default_factory=list)


class TerminalCanvas:
    """Tiny ANSI canvas used by the optional scrape TUI."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.cells: list[list[tuple[str, str | None]]] = [
            [(" ", None) for _ in range(width)] for _ in range(height)
        ]

    def set(self, x: int, y: int, ch: str, color: str | None = None) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.cells[y][x] = (ch[:1] or " ", color)

    def text(self, x: int, y: int, text: str, color: str | None = None) -> None:
        if y < 0 or y >= self.height:
            return
        for offset, ch in enumerate(text):
            self.set(x + offset, y, ch, color)

    def hline(self, x: int, y: int, width: int, ch: str, color: str | None = None) -> None:
        for offset in range(max(0, width)):
            self.set(x + offset, y, ch, color)

    def vline(self, x: int, y: int, height: int, ch: str, color: str | None = None) -> None:
        for offset in range(max(0, height)):
            self.set(x, y + offset, ch, color)

    def fill_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        ch: str = " ",
        color: str | None = None,
    ) -> None:
        for row in range(max(0, height)):
            for col in range(max(0, width)):
                self.set(x + col, y + row, ch, color)

    def box(self, x: int, y: int, width: int, height: int, color: str | None = None) -> None:
        if width < 2 or height < 2:
            return
        self.hline(x + 1, y, width - 2, "-", color)
        self.hline(x + 1, y + height - 1, width - 2, "-", color)
        self.vline(x, y + 1, height - 2, "|", color)
        self.vline(x + width - 1, y + 1, height - 2, "|", color)
        self.set(x, y, "+", color)
        self.set(x + width - 1, y, "+", color)
        self.set(x, y + height - 1, "+", color)
        self.set(x + width - 1, y + height - 1, "+", color)

    def render(self) -> str:
        lines: list[str] = []
        current_color: str | None = None
        for row in self.cells:
            parts: list[str] = []
            for ch, color in row:
                if color != current_color:
                    parts.append(ANSI_COLORS.get(color or "reset", ANSI_RESET))
                    current_color = color
                parts.append(ch)
            if current_color is not None:
                parts.append(ANSI_RESET)
                current_color = None
            lines.append("".join(parts))
        return "\n".join(lines)


class TerminalRainSystem:
    """Port of weathr's raindrop/splash particle idea to this Python CLI.

    The important pieces borrowed from `weathr/src/animation/raindrops.rs` are
    width-scaled particle counts, wind-adjusted x velocity, and short-lived
    splash particles. Here, drops splash on the progress panel's top border so
    the animation visually hits the easy_scrape TUI instead of the terminal
    floor only.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.drops: list[TerminalRainDrop] = []
        self.splashes: list[TerminalRainSplash] = []
        self.rng = rng or random.Random()
        self.wind_x = 0.08 if self.rng.random() > 0.5 else -0.08

    def _target_count(self, width: int) -> int:
        return max(18, int(width * 0.85))

    def _spawn_drop(self, width: int) -> None:
        x = self.rng.randrange(max(1, width * 2)) - (width * 0.5)
        z_index = 1 if self.rng.random() > 0.45 else 0
        if z_index == 1:
            chars = ["|", ":"]
            color = "white"
            speed_y = 0.85
        else:
            chars = [":", "."]
            color = "blue"
            speed_y = 0.52
        self.drops.append(
            TerminalRainDrop(
                x=x,
                y=0.0,
                speed_y=speed_y + self.rng.random() * 0.35,
                speed_x=self.wind_x + (self.rng.random() * 0.08 - 0.04),
                character=self.rng.choice(chars),
                color=color,
                z_index=z_index,
            )
        )

    def update(
        self,
        width: int,
        height: int,
        impact_rect: tuple[int, int, int] | None = None,
        *,
        speed: float = 1.0,
    ) -> None:
        if width <= 0 or height <= 1:
            self.drops.clear()
            self.splashes.clear()
            return

        target_count = self._target_count(width)
        spawn_rate = max(2, min(8, width // 16))
        for _ in range(spawn_rate):
            if len(self.drops) < target_count:
                self._spawn_drop(width)

        next_drops: list[TerminalRainDrop] = []
        left = right = impact_y = None
        if impact_rect is not None:
            left, right, impact_y = impact_rect

        for drop in self.drops:
            drop.y += drop.speed_y * speed
            drop.x += drop.speed_x * speed

            hit_panel = (
                left is not None
                and right is not None
                and impact_y is not None
                and left <= int(drop.x) <= right
                and drop.y >= impact_y
            )
            hit_floor = drop.y >= height - 1
            out_of_bounds = drop.x < -10 or drop.x > width + 10

            if hit_panel or hit_floor or out_of_bounds:
                if (hit_panel or hit_floor) and drop.z_index == 1 and self.rng.random() < 0.7:
                    splash_y = impact_y if hit_panel and impact_y is not None else height - 1
                    self.splashes.append(
                        TerminalRainSplash(
                            x=max(0, min(width - 1, int(drop.x))),
                            y=max(0, min(height - 1, int(splash_y))),
                            timer=0,
                            max_timer=7,
                        )
                    )
                continue
            next_drops.append(drop)

        self.drops = next_drops
        self.splashes = self.splashes[-MAX_RAIN_SPLASHES:]
        live_splashes: list[TerminalRainSplash] = []
        for splash in self.splashes:
            splash.timer += 1
            if splash.timer < splash.max_timer:
                live_splashes.append(splash)
        self.splashes = live_splashes

    def render_drops(self, canvas: TerminalCanvas) -> None:
        for drop in self.drops:
            x = int(drop.x)
            y = int(drop.y)
            if 0 <= x < canvas.width and 0 <= y < canvas.height:
                ch = (
                    "\\"
                    if drop.speed_x > 0.18
                    else "/"
                    if drop.speed_x < -0.18
                    else drop.character
                )
                canvas.set(x, y, ch, drop.color)

    def render_splashes(self, canvas: TerminalCanvas) -> None:
        for splash in self.splashes:
            ch = "." if splash.timer <= 2 else "o" if splash.timer <= 4 else "O"
            canvas.set(splash.x, splash.y, ch, "cyan")


class TerminalCloudSystem:
    """Small drifting background layer adapted from weathr's cloud system."""

    SHAPES = [
        ["   .--.   ", " .-(    ).", "(___.__)_)"],
        ["      _  _   ", "    ( `   )_ ", "   (    )   `)"],
        ["     .--.    ", "  .-(    ).  ", " (___.__)__) "],
    ]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.clouds: list[TerminalCloud] = []

    def _spawn_cloud(self, width: int, height: int, *, random_x: bool = False) -> None:
        shape = [line.rstrip() for line in self.rng.choice(self.SHAPES)]
        x = self.rng.randrange(max(1, width)) if random_x else -max(len(shape[0]), 8)
        y_limit = max(2, min(6, height // 3))
        self.clouds.append(
            TerminalCloud(
                x=float(x),
                y=self.rng.randrange(0, y_limit),
                speed=0.035 + self.rng.random() * 0.05,
                shape=shape,
                color="dim",
            )
        )

    def update(self, width: int, height: int, *, speed: float = 1.0) -> None:
        if width <= 0 or height <= 0:
            self.clouds.clear()
            return

        if not self.clouds:
            for _ in range(max(1, width // 34)):
                self._spawn_cloud(width, height, random_x=True)

        for cloud in self.clouds:
            cloud.x += cloud.speed * speed

        self.clouds = [c for c in self.clouds if c.x < width + 4]
        max_clouds = max(1, width // 30)
        if len(self.clouds) < max_clouds and self.rng.random() < 0.035:
            self._spawn_cloud(width, height)

    def render(self, canvas: TerminalCanvas) -> None:
        for cloud in self.clouds:
            for row_offset, line in enumerate(cloud.shape):
                y = cloud.y + row_offset
                x = int(cloud.x)
                if y < 0 or y >= canvas.height:
                    continue
                for col_offset, ch in enumerate(line):
                    if ch != " ":
                        canvas.set(x + col_offset, y, ch, cloud.color)


class TerminalStormSystem:
    """Rare lightning effect, modeled after weathr's thunderstorm state machine."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.bolts: list[TerminalLightningBolt] = []
        self.timer = 0
        self.next_strike_in = 90 + self.rng.randrange(120)
        self.flash_timer = 0
        self.flash_active = False

    def _generate_bolt(self, width: int, height: int) -> None:
        if width < 12 or height < 8:
            return
        x = self.rng.randrange(5, max(6, width - 5))
        y = 1
        segments: list[tuple[int, int, str]] = [(x, y, "+")]
        max_y = max(4, min(height - 5, height // 2 + 3))

        while y < max_y:
            direction = self.rng.choice([-1, 0, 1])
            x = max(2, min(width - 3, x + direction))
            y += 1
            ch = "/" if direction < 0 else "\\" if direction > 0 else "|"
            segments.append((x, y, ch))

            if self.rng.random() < 0.18:
                branch_x = x
                branch_y = y
                branch_direction = -1 if direction >= 0 else 1
                for _ in range(2):
                    branch_x = max(1, min(width - 2, branch_x + branch_direction))
                    branch_y += 1
                    if branch_y < height - 2:
                        segments.append(
                            (
                                branch_x,
                                branch_y,
                                "/" if branch_direction < 0 else "\\",
                            )
                        )

        self.bolts.append(TerminalLightningBolt(segments=segments, age=0, max_age=14))
        self.bolts = self.bolts[-3:]
        self.flash_active = True
        self.flash_timer = 3

    def update(
        self,
        width: int,
        height: int,
        *,
        active_fetch: bool = False,
        failed_count: int = 0,
        speed: float = 1.0,
    ) -> None:
        if width <= 0 or height <= 0:
            self.bolts.clear()
            return

        if self.flash_timer > 0:
            self.flash_timer -= 1
            self.flash_active = True
        else:
            self.flash_active = False

        live_bolts: list[TerminalLightningBolt] = []
        for bolt in self.bolts:
            bolt.age += 1
            if bolt.age < bolt.max_age:
                live_bolts.append(bolt)
        self.bolts = live_bolts

        self.timer += max(1, int(speed))
        failure_pressure = min(40, failed_count * 8)
        active_bonus = 25 if active_fetch else 0
        if self.timer + failure_pressure + active_bonus >= self.next_strike_in:
            self._generate_bolt(width, height)
            self.timer = 0
            self.next_strike_in = 120 + self.rng.randrange(220)

    def render(self, canvas: TerminalCanvas) -> None:
        color = "white" if self.flash_active else "yellow"
        for bolt in self.bolts:
            for x, y, ch in bolt.segments:
                canvas.set(x, y, ch, color)


def fit_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def progress_bar(done: int, total: int, width: int) -> str:
    if width <= 2:
        return ""
    if total <= 0:
        return "[" + "." * (width - 2) + "]"
    ratio = max(0.0, min(1.0, done / total))
    fill = int((width - 2) * ratio)
    return "[" + "#" * fill + "." * (width - 2 - fill) + "]"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    return f"{minutes:02d}:{secs:02d}"


def percent_done(done: int, total: int) -> str:
    if total <= 0:
        return "--%"
    return f"{min(100.0, max(0.0, done / total * 100)):5.1f}%"


def url_host(url: str) -> str:
    if not url:
        return ""
    return urlparse(url).netloc or url


def normalize_base_url(raw_url: str) -> str:
    """Normalize user-entered base URLs for interactive mode."""
    value = raw_url.strip()
    if not value:
        return DEFAULT_BASE_URL
    has_scheme = re.match(r"^[a-z][a-z0-9+.-]*://", value, flags=re.IGNORECASE)
    has_http_scheme = re.match(r"^https?://", value, flags=re.IGNORECASE)
    if has_scheme and not has_http_scheme:
        raise ValueError("Enter a valid http(s) URL.")
    if not has_http_scheme:
        value = f"https://{value}"

    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError("Enter a valid http(s) URL.")

    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/"),
        params="",
        query="",
        fragment="",
    ).geturl()
    return normalized.rstrip("/")


def normalize_output_dir(raw_path: str, default_path: Path) -> Path:
    """Normalize a user-entered output directory without creating it yet."""
    value = raw_path.strip()
    if not value:
        path = default_path
    else:
        path = Path(os.path.expandvars(value)).expanduser()
    if path.exists() and not path.is_dir():
        raise ValueError("Output path exists but is not a directory.")
    return path


def normalize_existing_output_dir(raw_path: str, current_dir: Path) -> Path:
    """Return an existing output directory entered from the folder browser."""
    value = raw_path.strip()
    if not value:
        path = current_dir
    else:
        path = Path(os.path.expandvars(value)).expanduser()
        if not path.is_absolute():
            path = current_dir / path
    if not path.exists():
        raise ValueError("Path does not exist. Use n to create a new folder.")
    if not path.is_dir():
        raise ValueError("Output path exists but is not a directory.")
    return path


def normalize_new_output_dir(folder_name: str, current_dir: Path) -> Path:
    """Return a child output folder path without creating it yet."""
    name = folder_name.strip()
    if not name:
        raise ValueError("Enter a folder name.")
    candidate = Path(name)
    if candidate.is_absolute() or candidate.name != name or name in (".", ".."):
        raise ValueError("Enter a folder name, not a path.")
    path = current_dir / name
    if path.exists() and not path.is_dir():
        raise ValueError("Output path exists but is not a directory.")
    return path


def display_path(path: Path) -> str:
    """Return a compact path label for terminal screens and help text."""
    expanded = path.expanduser()
    try:
        home = Path.home()
        relative = expanded.relative_to(home)
        return f"~/{relative}" if str(relative) != "." else "~"
    except ValueError:
        return str(path)


def output_browser_start_dir() -> Path:
    """Start interactive output browsing on Desktop, falling back to home."""
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop
    return Path.home()


def list_browsable_dirs(path: Path) -> list[Path]:
    """Return visible child directories for the output folder browser."""
    try:
        children = [
            child
            for child in path.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        ]
    except OSError:
        return []
    return sorted(children, key=lambda child: child.name.casefold())


@dataclass
class FolderBrowserState:
    """State machine for the interactive output folder browser."""

    default_output: Path
    current_dir: Path
    folders: list[Path] = field(default_factory=list)
    cursor: int = 0
    submitted: Path | None = None
    cancelled: bool = False
    message: str = ""

    @property
    def row_count(self) -> int:
        return 2 + len(self.folders)

    def refresh(self) -> None:
        self.folders = list_browsable_dirs(self.current_dir)
        self.cursor = max(0, min(self.cursor, self.row_count - 1))

    def handle_key(self, key: str) -> None:
        if key in ("q", "Q", "escape"):
            self.cancelled = True
            return
        if key in ("up", "k", "K"):
            self.cursor = max(0, self.cursor - 1)
        elif key in ("down", "j", "J"):
            self.cursor = min(self.row_count - 1, self.cursor + 1)
        elif key in ("backspace", "h", "H"):
            self.go_parent()
        elif key == "~":
            self.go_home()
        elif key in ("d", "D"):
            self.go_desktop()
        elif key == "enter":
            self.submit_or_open()

    def submit_or_open(self) -> None:
        if self.cursor == 0:
            self.submitted = self.default_output
            return
        if self.cursor == 1:
            self.submitted = self.current_dir
            return
        folder = self.folders[self.cursor - 2]
        self.current_dir = folder
        self.cursor = 1
        self.message = ""
        self.refresh()

    def go_parent(self) -> None:
        parent = self.current_dir.parent
        if parent != self.current_dir and parent.is_dir():
            self.current_dir = parent
            self.cursor = 1
            self.message = ""
            self.refresh()

    def go_home(self) -> None:
        self.current_dir = Path.home()
        self.cursor = 1
        self.message = ""
        self.refresh()

    def go_desktop(self) -> None:
        self.current_dir = output_browser_start_dir()
        self.cursor = 1
        self.message = ""
        self.refresh()

    def submit_direct_path(self, raw_path: str) -> None:
        self.submitted = normalize_existing_output_dir(raw_path, self.current_dir)

    def submit_new_folder(self, folder_name: str) -> None:
        self.submitted = normalize_new_output_dir(folder_name, self.current_dir)


@dataclass
class CategoryPickerState:
    """State machine for the interactive multi-select category picker."""

    categories: list[str]
    cursor: int = 0
    selected: set[str] = field(default_factory=set)
    submitted: bool = False
    cancelled: bool = False
    message: str = ""

    def __post_init__(self) -> None:
        if self.categories:
            self.cursor = max(0, min(self.cursor, len(self.categories) - 1))
        else:
            self.cursor = 0

    def selected_categories(self) -> list[str]:
        return [name for name in self.categories if name in self.selected]

    def handle_key(self, key: str) -> None:
        if key in ("q", "Q", "escape"):
            self.cancelled = True
            return
        if not self.categories:
            return

        if key in ("up", "k", "K"):
            self.cursor = max(0, self.cursor - 1)
        elif key in ("down", "j", "J"):
            self.cursor = min(len(self.categories) - 1, self.cursor + 1)
        elif key in (" ", "space"):
            current = self.categories[self.cursor]
            if current in self.selected:
                self.selected.remove(current)
            else:
                self.selected.add(current)
            self.message = ""
        elif key in ("a", "A"):
            self.selected = set(self.categories)
            self.message = "All categories selected."
        elif key in ("n", "N"):
            self.selected.clear()
            self.message = "Selection cleared."
        elif key in ("enter", "\n", "\r"):
            if self.selected:
                self.submitted = True
            else:
                self.message = "Select at least one category before continuing."


class InteractiveScrapeTui:
    """Blocking pre-scrape terminal UI for picking source and categories."""

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        input_stream: TextIO | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.input_stream = input_stream or sys.stdin
        self._stdin_fd: int | None = None
        self._stdin_attrs = None
        self._entered = False
        self._rain = TerminalRainSystem()
        self._clouds = TerminalCloudSystem()

    def is_available(self) -> bool:
        return bool(self.stream.isatty() and self.input_stream.isatty())

    def __enter__(self) -> "InteractiveScrapeTui":
        self._enable_input()
        self._write(
            ANSI_ALT_SCREEN
            + ANSI_ENABLE_MOUSE
            + ANSI_HIDE_CURSOR
            + ANSI_CLEAR
            + ANSI_HOME
        )
        self._entered = True
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self._restore_input()
        if self._entered:
            self._write(
                ANSI_RESET
                + ANSI_CLEAR
                + ANSI_HOME
                + ANSI_DISABLE_MOUSE
                + ANSI_SHOW_CURSOR
                + ANSI_MAIN_SCREEN
            )
        self._entered = False

    def _enable_input(self) -> None:
        self._stdin_fd = self.input_stream.fileno()
        self._stdin_attrs = termios.tcgetattr(self._stdin_fd)
        tty.setcbreak(self._stdin_fd)

    def _restore_input(self) -> None:
        if self._stdin_fd is None or self._stdin_attrs is None:
            return
        try:
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._stdin_attrs)
        finally:
            self._stdin_fd = None
            self._stdin_attrs = None

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def _read_char(self, timeout: float | None = None) -> str | None:
        if timeout is not None:
            target = self._stdin_fd if self._stdin_fd is not None else self.input_stream
            readable, _, _ = select.select([target], [], [], timeout)
            if not readable:
                return None

        if self._stdin_fd is not None:
            data = os.read(self._stdin_fd, 1)
            if not data:
                return None
            return data.decode(errors="ignore")

        ch = self.input_stream.read(1)
        return ch or None

    def _read_key(self, timeout: float | None = None) -> str | None:
        ch = self._read_char(timeout)
        if ch is None:
            return None
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            return key_name_from_sequence(self._read_escape_sequence())
        if ch in ("\n", "\r"):
            return "enter"
        if ch in ("\x7f", "\b"):
            return "backspace"
        if ch == " ":
            return "space"
        return ch

    def _read_escape_sequence(self) -> str:
        sequence = "\x1b"
        introducer = self._read_char(INTERACTIVE_KEY_POLL_SECONDS)
        if introducer is None:
            return sequence

        sequence += introducer
        if introducer == "[":
            for _ in range(32):
                next_ch = self._read_char(INTERACTIVE_KEY_POLL_SECONDS)
                if next_ch is None:
                    break
                sequence += next_ch
                if sequence == "\x1b[M":
                    for _ in range(3):
                        mouse_ch = self._read_char(INTERACTIVE_KEY_POLL_SECONDS)
                        if mouse_ch is None:
                            break
                        sequence += mouse_ch
                    break
                if "@" <= next_ch <= "~":
                    break
        elif introducer == "O":
            next_ch = self._read_char(INTERACTIVE_KEY_POLL_SECONDS)
            if next_ch is not None:
                sequence += next_ch

        return sequence

    def _render(self, title: str, body: list[str], footer: str = "") -> None:
        size = shutil.get_terminal_size((80, 24))
        width = max(40, size.columns)
        height = max(12, size.lines)
        frame = self._draw_frame(width, height, title, body, footer)
        self._write(ANSI_HOME + frame)

    def _banner_lines(self, width: int) -> list[str]:
        if width < 64:
            return ["easyScrape"]
        return EASY_SCRAPE_BANNER

    def _bonfire_layout(
        self, panel_left: int, panel_top: int, panel_width: int, panel_height: int
    ) -> tuple[int, int] | None:
        art_width = max(len(line) for line, _color in BONFIRE_ASCII)
        art_height = len(BONFIRE_ASCII)
        if panel_width < art_width + 36 or panel_height < art_height + 3:
            return None

        art_x = panel_left + panel_width - art_width - 3
        art_y = panel_top + panel_height - art_height - 2
        return art_x, art_y

    def _draw_bonfire(self, canvas: TerminalCanvas, x: int, y: int) -> None:
        for offset, (line, color) in enumerate(BONFIRE_ASCII):
            canvas.text(x, y + offset, line, color)

    def _draw_frame(
        self,
        width: int,
        height: int,
        title: str,
        body: list[str],
        footer: str = "",
    ) -> str:
        canvas = TerminalCanvas(width, height)
        banner = self._banner_lines(width)
        banner_top = 1
        panel_top = min(height - 8, banner_top + len(banner) + 2)
        panel_top = max(4, panel_top)
        panel_height = max(7, height - panel_top - 2)
        panel_width = min(max(40, width - 8), 100)
        panel_left = max(2, (width - panel_width) // 2)
        panel_right = panel_left + panel_width - 1
        bonfire_layout = self._bonfire_layout(
            panel_left, panel_top, panel_width, panel_height
        )

        self._clouds.update(width, height, speed=0.8)
        self._rain.update(
            width,
            height,
            (panel_left, panel_right, panel_top),
            speed=1.1,
        )
        self._clouds.render(canvas)
        self._rain.render_drops(canvas)

        for offset, line in enumerate(banner):
            color = "cyan" if offset % 2 == 0 else "white"
            canvas.text(
                max(0, (width - len(line)) // 2),
                banner_top + offset,
                line,
                color,
            )

        canvas.fill_rect(
            panel_left + 1,
            panel_top + 1,
            panel_width - 2,
            panel_height - 2,
        )
        canvas.box(panel_left, panel_top, panel_width, panel_height, "cyan")
        title_label = f" {title} "
        canvas.text(
            panel_left + max(2, (panel_width - len(title_label)) // 2),
            panel_top,
            title_label,
            "white",
        )

        inner_x = panel_left + 3
        inner_width = max(1, panel_width - 6)
        max_body_lines = max(1, panel_height - 4)
        for offset, line in enumerate(body[:max_body_lines]):
            line_y = panel_top + 2 + offset
            line_width = inner_width
            if bonfire_layout is not None:
                art_x, art_y = bonfire_layout
                if art_y <= line_y < art_y + len(BONFIRE_ASCII):
                    line_width = max(1, art_x - inner_x - 2)
            canvas.text(
                inner_x,
                line_y,
                fit_text(line, line_width),
                "white" if line.startswith(">") else "dim",
            )

        if bonfire_layout is not None:
            self._draw_bonfire(canvas, *bonfire_layout)

        self._rain.render_splashes(canvas)
        footer_text = footer or "up/down or j/k move | enter choose | q quit"
        canvas.text(2, height - 1, fit_text(footer_text, width - 4), "dim")
        return canvas.render()

    def choose_source(self, default_base_url: str) -> str | None:
        default_label = (
            "Dark Souls Fextra"
            if default_base_url == DEFAULT_BASE_URL
            else "Configured source"
        )
        options = [
            ("default", f"{default_label} ({default_base_url})"),
            ("custom", "Enter URL"),
        ]
        selected = self._run_menu(
            "Choose a source",
            options,
            footer="up/down or j/k move | enter choose | q quit",
        )
        return selected

    def _run_menu(
        self,
        title: str,
        options: list[tuple[str, str]],
        *,
        footer: str,
    ) -> str | None:
        cursor = 0
        while True:
            body = []
            for idx, (_value, label) in enumerate(options):
                prefix = ">" if idx == cursor else " "
                body.append(f"{prefix} {label}")
            self._render(title, body, footer)

            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key in ("q", "Q", "escape"):
                return None
            if key in ("up", "k", "K"):
                cursor = max(0, cursor - 1)
            elif key in ("down", "j", "J"):
                cursor = min(len(options) - 1, cursor + 1)
            elif key == "enter":
                return options[cursor][0]

    def prompt_url(self, default_base_url: str) -> str | None:
        value = ""
        error = ""
        while True:
            body = [
                "Enter a site or wiki base URL.",
                "",
                f"Default: {default_base_url}",
                "",
                f"URL: {value}",
            ]
            if error:
                body.extend(["", error])
            self._render(
                "Custom source URL",
                body,
                "enter continue | esc cancel | backspace edit",
            )

            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key == "escape":
                return None
            if key == "enter":
                try:
                    return normalize_base_url(value or default_base_url)
                except ValueError as exc:
                    error = str(exc)
            elif key == "backspace":
                value = value[:-1]
                error = ""
            elif len(key) == 1 and key.isprintable():
                value += key
                error = ""

    def choose_output_path(self, default_output_path: Path) -> Path | None:
        state = FolderBrowserState(
            default_output=default_output_path,
            current_dir=output_browser_start_dir(),
        )
        state.refresh()
        while state.submitted is None and not state.cancelled:
            self._draw_output_folder_browser(state)
            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key == "/":
                path = self.prompt_direct_output_path(state.current_dir)
                if path is not None:
                    state.submitted = path
            elif key in ("n", "N"):
                path = self.prompt_new_output_folder(state.current_dir)
                if path is not None:
                    state.submitted = path
            else:
                try:
                    state.handle_key(key)
                except ValueError as exc:
                    state.message = str(exc)
        if state.cancelled:
            return None
        return state.submitted

    def prompt_direct_output_path(
        self, current_dir: Path, initial_error: str = ""
    ) -> Path | None:
        value = ""
        error = initial_error
        while True:
            body = [
                "Enter an existing output folder.",
                "",
                f"Current: {display_path(current_dir)}",
                "",
                f"Path: {value}",
            ]
            if error:
                body.extend(["", error])
            self._render(
                "Direct output path",
                body,
                "enter continue | esc cancel | backspace edit",
            )

            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key == "escape":
                return None
            if key == "enter":
                try:
                    return normalize_existing_output_dir(value, current_dir)
                except ValueError as exc:
                    error = str(exc)
            elif key == "backspace":
                value = value[:-1]
                error = ""
            elif key == "space":
                value += " "
                error = ""
            elif len(key) == 1 and key.isprintable():
                value += key
                error = ""

    def prompt_new_output_folder(self, current_dir: Path) -> Path | None:
        value = ""
        error = ""
        while True:
            body = [
                "Name a new output folder.",
                "",
                f"Inside: {display_path(current_dir)}",
                "",
                f"Folder: {value}",
            ]
            if error:
                body.extend(["", error])
            self._render(
                "New output folder",
                body,
                "enter choose | esc cancel | backspace edit",
            )

            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key == "escape":
                return None
            if key == "enter":
                try:
                    return normalize_new_output_dir(value, current_dir)
                except ValueError as exc:
                    error = str(exc)
            elif key == "backspace":
                value = value[:-1]
                error = ""
            elif key == "space":
                value += " "
                error = ""
            elif len(key) == 1 and key.isprintable():
                value += key
                error = ""

    def _draw_output_folder_browser(self, state: FolderBrowserState) -> None:
        size = shutil.get_terminal_size((80, 24))
        rows: list[tuple[str, str]] = [
            ("default", f"Use {display_path(state.default_output)}"),
            ("current", f"Use current folder ({display_path(state.current_dir)})"),
        ]
        rows.extend(("folder", folder.name) for folder in state.folders)

        visible_count = max(5, size.lines - 12)
        half = visible_count // 2
        start = max(0, min(state.cursor - half, len(rows) - visible_count))
        end = min(len(rows), start + visible_count)

        body = [
            f"Browsing: {display_path(state.current_dir)}",
            f"Folders: {len(state.folders)}",
            "",
        ]
        for idx in range(start, end):
            kind, label = rows[idx]
            cursor = ">" if idx == state.cursor else " "
            if kind == "folder":
                label = f"[dir] {label}"
            body.append(f"{cursor} {label}")
        if not state.folders:
            body.append("  No visible folders here.")
        if state.message:
            body.extend(["", state.message])

        self._render(
            "Choose output folder",
            body,
            "enter use/open | backspace parent | ~ home | d desktop | n new | / path | q quit",
        )

    def show_status(self, title: str, detail: str) -> None:
        self._render(title, [detail, "", "Please wait..."])

    def show_error(self, title: str, message: str) -> str | None:
        while True:
            self._render(
                title,
                [message, "", "Press enter to choose another source."],
                "enter back | q quit",
            )
            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key in ("q", "Q", "escape"):
                return None
            if key == "enter":
                return "back"

    def choose_no_category_fallback(self, base_url: str) -> str | None:
        return self._run_menu(
            "No categories found",
            [
                ("sitemap", f"Scrape site via sitemap ({base_url}/sitemap.xml)"),
                ("single", f"Scrape this URL only ({base_url})"),
                ("back", "Choose another source"),
            ],
            footer="up/down or j/k move | enter choose | q quit",
        )

    def choose_categories(
        self, categories: list[str], *, base_url: str
    ) -> list[str] | None:
        state = CategoryPickerState(categories)
        while not state.submitted and not state.cancelled:
            self._draw_category_picker(state, base_url=base_url)
            key = self._read_key(TUI_FRAME_SECONDS)
            if key is not None:
                state.handle_key(key)
        if state.cancelled:
            return None
        return state.selected_categories()

    def _draw_category_picker(
        self, state: CategoryPickerState, *, base_url: str
    ) -> None:
        size = shutil.get_terminal_size((80, 24))
        visible_count = max(5, size.lines - 10)
        half = visible_count // 2
        start = max(0, min(state.cursor - half, len(state.categories) - visible_count))
        end = min(len(state.categories), start + visible_count)

        body = [
            f"Source: {base_url}",
            f"Selected: {len(state.selected)} / {len(state.categories)}",
            "",
        ]
        for idx in range(start, end):
            name = state.categories[idx]
            cursor = ">" if idx == state.cursor else " "
            mark = "x" if name in state.selected else " "
            body.append(f"{cursor} [{mark}] {name}")
        if state.message:
            body.extend(["", state.message])

        self._render(
            "Select categories",
            body,
            "space toggle | a all | n none | enter scrape | q quit",
        )


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def key_name_from_sequence(sequence: str) -> str:
    """Return a semantic key name for terminal escape sequences."""
    if re.fullmatch(r"\x1b\[<[0-9;]+[mM]", sequence):
        return "mouse"
    if re.fullmatch(r"\x1b\[M...", sequence, flags=re.DOTALL):
        return "mouse"
    if re.fullmatch(r"\x1b(?:\[|O)[0-9;]*A", sequence):
        return "up"
    if re.fullmatch(r"\x1b(?:\[|O)[0-9;]*B", sequence):
        return "down"
    if re.fullmatch(r"\x1b(?:\[|O)[0-9;]*C", sequence):
        return "right"
    if re.fullmatch(r"\x1b(?:\[|O)[0-9;]*D", sequence):
        return "left"
    if sequence == "\x1b":
        return "escape"
    return "escape"


class ScrapeRainTui:
    """Optional animated dashboard. Inert when not attached to a TTY."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        stream: TextIO | None = None,
        input_stream: TextIO | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.input_stream = input_stream or sys.stdin
        self.active = bool(enabled and self.stream.isatty())
        self._state = ScrapeTuiState()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._rain = TerminalRainSystem()
        self._clouds = TerminalCloudSystem()
        self._storm = TerminalStormSystem()
        self._last_size: tuple[int, int] | None = None
        self._entered = False
        self._paused = False
        self._show_help = False
        self._plain_requested = False
        self._render_thread: threading.Thread | None = None
        self._input_thread: threading.Thread | None = None
        self._stdin_fd: int | None = None
        self._stdin_attrs = None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def show_help(self) -> bool:
        return self._show_help

    @property
    def plain_requested(self) -> bool:
        return self._plain_requested

    def __enter__(self) -> "ScrapeRainTui":
        if not self.active:
            return self
        size = shutil.get_terminal_size((80, 24))
        if size.columns < TUI_MIN_WIDTH or size.lines < TUI_MIN_HEIGHT:
            self.active = False
            return self

        try:
            self._enable_input_controls()
            with self._io_lock:
                self.stream.write(
                    ANSI_ALT_SCREEN
                    + ANSI_ENABLE_MOUSE
                    + ANSI_HIDE_CURSOR
                    + ANSI_CLEAR
                    + ANSI_HOME
                )
                self.stream.flush()
            self._entered = True
            self._render_thread = threading.Thread(
                target=self._render_loop,
                daemon=True,
                name="easy-scrape-tui-render",
            )
            self._render_thread.start()
            if self._stdin_fd is not None:
                self._input_thread = threading.Thread(
                    target=self._input_loop,
                    daemon=True,
                    name="easy-scrape-tui-input",
                )
                self._input_thread.start()
        except Exception:
            self._disable_to_plain_logs(cleanup=True)
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self._stop.set()
        self._join_threads()
        self._restore_input_controls()
        if self._entered:
            with self._io_lock:
                self.stream.write(
                    ANSI_RESET
                    + ANSI_CLEAR
                    + ANSI_HOME
                    + ANSI_DISABLE_MOUSE
                    + ANSI_SHOW_CURSOR
                    + ANSI_MAIN_SCREEN
                )
                self.stream.flush()
        self._entered = False
        self.active = False

    def _enable_input_controls(self) -> None:
        if not self.input_stream.isatty():
            return
        try:
            self._stdin_fd = self.input_stream.fileno()
            self._stdin_attrs = termios.tcgetattr(self._stdin_fd)
            tty.setcbreak(self._stdin_fd)
        except Exception:
            self._stdin_fd = None
            self._stdin_attrs = None

    def _restore_input_controls(self) -> None:
        if self._stdin_fd is None or self._stdin_attrs is None:
            return
        try:
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._stdin_attrs)
        except Exception:
            pass
        self._stdin_fd = None
        self._stdin_attrs = None

    def _join_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._input_thread, self._render_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)

    def _disable_to_plain_logs(self, *, cleanup: bool) -> None:
        self._plain_requested = True
        self.active = False
        self._stop.set()
        if cleanup and self._entered:
            self._restore_input_controls()
            with self._io_lock:
                self.stream.write(
                    ANSI_RESET
                    + ANSI_CLEAR
                    + ANSI_HOME
                    + ANSI_DISABLE_MOUSE
                    + ANSI_SHOW_CURSOR
                    + ANSI_MAIN_SCREEN
                )
                self.stream.flush()
            self._entered = False

    def handle_key(self, key: str) -> None:
        """Handle one keypress; public for focused unit tests."""
        if key in ("?", "h", "H"):
            self._show_help = not self._show_help
        elif key in ("p", "P"):
            self._paused = not self._paused
        elif key in ("q", "Q"):
            self._disable_to_plain_logs(cleanup=True)
        elif key == "\x03":
            os.kill(os.getpid(), signal.SIGINT)

    def _input_loop(self) -> None:
        while not self._stop.is_set() and self._stdin_fd is not None:
            try:
                readable, _, _ = select.select([self.input_stream], [], [], 0.05)
                if readable:
                    ch = self.input_stream.read(1)
                    if ch:
                        self.handle_key(ch)
            except Exception:
                return

    def set_stage(
        self,
        title: str | None = None,
        detail: str = "",
        stage: str = "",
        *,
        mode: str | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            if title is not None:
                self._state.title = title or "easy_scrape"
            if mode is not None:
                self._state.mode = mode
            if output_path is not None:
                self._state.output_path = str(output_path)
            if detail:
                self._state.detail = detail
            if stage:
                self._state.stage = stage

    def update_stage(self, stage: str, detail: str = "") -> None:
        if not self.active:
            return
        with self._lock:
            self._state.stage = stage
            if detail:
                self._state.detail = detail

    def start_batch(
        self,
        title: str,
        subtitle: str,
        total: int,
        *,
        mode: str | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.title = title
            self._state.detail = subtitle
            self._state.stage = "ready"
            self._state.mode = mode or self._state.mode
            if output_path is not None:
                self._state.output_path = str(output_path)
            self._state.current_url = ""
            self._state.current_slug = ""
            self._state.last_result = ""
            self._state.total = total
            self._state.index = 0
            self._state.saved = 0
            self._state.skipped = 0
            self._state.failed = 0
            self._state.started_at = time.monotonic()
            self._state.recent_events = []

    def start_page(
        self,
        index: int,
        total: int,
        url: str,
        slug: str,
        *,
        saved: int,
        skipped: int,
        failed: int,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.index = index
            self._state.total = total
            self._state.current_url = url
            self._state.current_slug = slug
            self._state.stage = "fetching page"
            self._state.detail = url
            self._state.saved = saved
            self._state.skipped = skipped
            self._state.failed = failed

    def finish_page(
        self,
        result: str,
        slug: str,
        *,
        saved: int,
        skipped: int,
        failed: int,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.stage = result
            self._state.last_result = result
            self._state.saved = saved
            self._state.skipped = skipped
            self._state.failed = failed
            event = f"{result.upper():7} {slug}"
            self._state.recent_events.append(event)
            self._state.recent_events = self._state.recent_events[-MAX_RECENT_EVENTS:]

    def snapshot(self) -> ScrapeTuiState:
        with self._lock:
            return ScrapeTuiState(
                title=self._state.title,
                mode=self._state.mode,
                stage=self._state.stage,
                output_path=self._state.output_path,
                detail=self._state.detail,
                current_url=self._state.current_url,
                current_slug=self._state.current_slug,
                last_result=self._state.last_result,
                total=self._state.total,
                index=self._state.index,
                saved=self._state.saved,
                skipped=self._state.skipped,
                failed=self._state.failed,
                started_at=self._state.started_at,
                recent_events=list(self._state.recent_events),
            )

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            try:
                width, height = shutil.get_terminal_size((80, 24))
                frame = self._draw_frame(width, height, self.snapshot())
                prefix = ANSI_HOME
                if self._last_size != (width, height):
                    prefix += ANSI_CLEAR
                    self._last_size = (width, height)
                with self._io_lock:
                    self.stream.write(prefix + frame)
                    self.stream.flush()
            except Exception:
                self._disable_to_plain_logs(cleanup=True)
                break
            time.sleep(TUI_FRAME_SECONDS)

    def _draw_frame(self, width: int, height: int, state: ScrapeTuiState) -> str:
        canvas = TerminalCanvas(width, height)
        speed = 0.0 if self._paused else 1.0
        active_fetch = "fetch" in state.stage or "discover" in state.stage

        panel_width = min(max(TUI_MIN_WIDTH - 4, width - 6), 100)
        panel_height = min(max(12, height - 7), 16)
        left = max(2, (width - panel_width) // 2)
        top = max(3, (height - panel_height) // 2)
        right = left + panel_width - 1
        bottom = top + panel_height - 1
        border_color = "white" if self._storm.flash_active else "cyan"

        if not self._paused:
            self._clouds.update(width, height, speed=0.9)
            self._rain.update(width, height, (left, right, top), speed=1.25)
            self._storm.update(
                width,
                height,
                active_fetch=active_fetch,
                failed_count=state.failed,
                speed=1.0,
            )

        self._clouds.render(canvas)
        self._storm.render(canvas)
        self._rain.render_drops(canvas)
        self._draw_ground(canvas, width, height)
        self._draw_shell(canvas, width, height, state, left, top, panel_width, panel_height, border_color)
        self._rain.render_splashes(canvas)
        self._draw_dashboard_text(canvas, state, left, top, panel_width, panel_height)
        self._draw_footer(canvas, width, height)
        if self._show_help:
            self._draw_help(canvas, width, height)
        return canvas.render()

    def _draw_ground(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        if height < 3:
            return
        ground_y = height - 2
        for x in range(width):
            canvas.set(x, ground_y, "~" if x % 2 else "_", "dim")

    def _draw_shell(
        self,
        canvas: TerminalCanvas,
        width: int,
        height: int,
        state: ScrapeTuiState,
        left: int,
        top: int,
        panel_width: int,
        panel_height: int,
        border_color: str,
    ) -> None:
        canvas.text(2, 0, "easy_scrape", "cyan")
        header = f"mode: {state.mode} | stage: {state.stage}"
        canvas.text(max(14, width - len(header) - 2), 0, fit_text(header, width - 16), "white")
        if state.output_path:
            canvas.text(2, 1, fit_text(f"out: {state.output_path}", width - 4), "dim")
        canvas.fill_rect(left + 1, top + 1, panel_width - 2, panel_height - 2)
        canvas.box(left, top, panel_width, panel_height, border_color)

    def _draw_dashboard_text(
        self,
        canvas: TerminalCanvas,
        state: ScrapeTuiState,
        left: int,
        top: int,
        panel_width: int,
        panel_height: int,
    ) -> None:
        inner_x = left + 3
        inner_width = max(1, panel_width - 6)
        done = state.saved + state.skipped + state.failed
        elapsed = time.monotonic() - state.started_at
        rate = done / elapsed * 60 if elapsed > 0 and done > 0 else 0.0
        host = url_host(state.current_url)

        title = f" {state.title} "
        canvas.text(left + max(2, (panel_width - len(title)) // 2), top, title, "white")
        y = top + 2
        canvas.text(
            inner_x,
            y,
            fit_text(
                f"{state.stage.upper()}  {state.index}/{state.total}  {percent_done(done, state.total)}",
                inner_width,
            ),
            "white",
        )
        y += 1
        canvas.text(inner_x, y, progress_bar(done, state.total, inner_width), "green")
        y += 2
        canvas.text(
            inner_x,
            y,
            fit_text(
                f"saved {state.saved}  skipped {state.skipped}  failed {state.failed}",
                inner_width,
            ),
            "yellow" if state.failed == 0 else "red",
        )
        y += 1
        timing = f"elapsed {format_duration(elapsed)}  rate {rate:0.1f} pages/min"
        canvas.text(inner_x, y, fit_text(timing, inner_width), "dim")
        y += 2
        current = state.current_slug or "waiting for first page"
        canvas.text(inner_x, y, fit_text(f"current: {current}", inner_width), "cyan")
        y += 1
        if host:
            canvas.text(inner_x, y, fit_text(f"host: {host}", inner_width), "dim")
            y += 1
        if state.detail and y < top + panel_height - 2:
            canvas.text(inner_x, y, fit_text(state.detail, inner_width), "dim")
            y += 1

        if state.recent_events and y < top + panel_height - 2:
            recent = "recent: " + " | ".join(state.recent_events[-2:])
            canvas.text(inner_x, y, fit_text(recent, inner_width), "dim")

    def _draw_footer(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        if height <= 0:
            return
        status = "animation paused" if self._paused else "rain/storm effects active"
        if self._plain_requested:
            status = "plain logs requested"
        footer = f"?/h help  p pause effects  q plain logs  Ctrl-C cancel | {status}"
        canvas.text(2, height - 1, fit_text(footer, width - 4), "dim")

    def _draw_help(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        help_width = min(72, max(36, width - 4))
        help_height = 8
        left = max(2, (width - help_width) // 2)
        top = max(2, (height - help_height) // 2)
        canvas.fill_rect(left, top, help_width, help_height)
        canvas.box(left, top, help_width, help_height, "yellow")
        lines = [
            " easy_scrape controls ",
            "? or h  toggle this help",
            "p       pause/resume weather effects only",
            "q       leave TUI and continue with plain logs",
            "Ctrl-C  cancel the scrape",
            "Generated files and scraper behavior are unchanged.",
        ]
        for offset, line in enumerate(lines):
            color = "white" if offset == 0 else "dim"
            canvas.text(left + 2, top + 1 + offset, fit_text(line, help_width - 4), color)


def extract_main_element(
    html: str,
    page_url: str,
    *,
    clean: bool = True,
    image_assets: ImageAssetContext | None = None,
):
    """Return the cleaned <#wiki-content-block> element, or None if absent.

    With clean=True (default): replaces <img alt> with the alt text (so stat
    icons survive as labels), drops the trailing footer nav table, drops
    leading sidebar-leak link paragraphs.
    """
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(CONTENT_SELECTOR)
    if not el:
        return None

    if clean:
        expand_rowspans(el)
        if image_assets is not None:
            preserve_content_images(el, page_url, image_assets)
        replace_images_with_alt(el)
        drop_banner_alt_rows(el)
        drop_footer_nav_table(el)
        drop_stranded_category_links(el)

    for tag in el.find_all(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    for img in list(el.find_all("img")):
        if img.get(PRESERVED_IMAGE_ATTR) == "1":
            del img[PRESERVED_IMAGE_ATTR]
            continue
        img.decompose()
    for a in el.find_all("a", href=True):
        a["href"] = urljoin(page_url, a["href"])

    if clean:
        drop_fragment_anchors(el, page_url)
        strip_inline_links(el)
        drop_empty_columns(el)
    return el


def html_to_markdown(
    html_fragment: str,
    *,
    title: str = "",
    clean: bool = True,
    preserve_images: bool = False,
) -> str:
    kwargs = {"heading_style": "ATX"}
    if not preserve_images:
        kwargs["strip"] = ["img"]
    md = markdownify(html_fragment, **kwargs)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    if clean:
        md = "\n".join(
            normalize_heading_line(line, title) for line in md.splitlines()
        )
        md = drop_empty_headings(md)
        md = drop_placeholder_sections(md)
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def scrape_one(
    session: requests.Session,
    url: str,
    out_path: Path,
    overwrite: bool,
    *,
    category: str | None = None,
    clean: bool = True,
    cache_dir: Path | None = None,
    download_images: bool = False,
    asset_root: Path | None = None,
    tui: ScrapeRainTui | None = None,
) -> str:
    """Return one of: 'saved', 'skipped', 'failed'."""
    if out_path.exists() and not overwrite:
        return "skipped"

    cache_path = (
        cache_dir / f"{url_to_filename(url)}.html" if cache_dir is not None else None
    )

    html_text: str | None = None
    if cache_path is not None and cache_path.exists() and cache_path.stat().st_size > 0:
        if tui is not None:
            tui.update_stage("reading cache", str(cache_path))
        html_text = cache_path.read_text(encoding="utf-8")

    if html_text is None:
        if tui is not None:
            tui.update_stage("fetching page", url)
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:
                print(f"  404 {url}", file=sys.stderr)
                return "failed"
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  HTTP error {url}: {e}", file=sys.stderr)
            return "failed"
        html_text = r.text
        if cache_path is not None:
            if tui is not None:
                tui.update_stage("writing cache", str(cache_path))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(html_text, encoding="utf-8")

    preserve_images = clean and download_images and asset_root is not None
    image_assets = None
    if preserve_images:
        image_assets = ImageAssetContext(
            session=session,
            asset_dir=asset_root / out_path.stem,
            markdown_dir=out_path.parent,
        )

    if tui is not None:
        tui.update_stage("cleaning", url)
    el = extract_main_element(html_text, url, clean=clean, image_assets=image_assets)
    if el is None:
        print(f"  no #wiki-content-block at {url}", file=sys.stderr)
        return "failed"

    title = url_to_title(url)
    if tui is not None:
        tui.update_stage("converting", title)
    md = html_to_markdown(
        str(el), title=title, clean=clean, preserve_images=preserve_images
    )
    if not md:
        print(f"  empty content at {url}", file=sys.stderr)
        return "failed"

    if tui is not None:
        tui.update_stage("writing", str(out_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if clean:
        fm = extract_frontmatter(el, url=url, title=title, category=category)
        body = f"{format_frontmatter(fm)}\n\n# {title}\n\n{md}\n"
    else:
        body = f"# {title}\n\nSource: {url}\n\n{md}\n"
    out_path.write_text(body, encoding="utf-8")
    return "saved"


def scrape_url_list(
    session: requests.Session,
    urls: list[str],
    out_dir: Path,
    delay: float,
    overwrite: bool,
    *,
    category: str | None = None,
    clean: bool = True,
    label: str = "",
    cache_dir: Path | None = None,
    download_images: bool = False,
    asset_root: Path | None = None,
    tui: ScrapeRainTui | None = None,
) -> tuple[int, int, int]:
    """Save each URL into out_dir/<slug>.md. Returns (saved, skipped, failed)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = skipped = failed = 0

    for i, url in enumerate(urls, 1):
        slug = url_to_filename(url)
        path = out_dir / f"{slug}.md"
        prefix = f"{label}[{i}/{len(urls)}]" if label else f"[{i}/{len(urls)}]"

        if tui is not None and tui.active:
            tui.start_page(
                i,
                len(urls),
                url,
                slug,
                saved=saved,
                skipped=skipped,
                failed=failed,
            )

        result = scrape_one(
            session,
            url,
            path,
            overwrite,
            category=category,
            clean=clean,
            cache_dir=cache_dir,
            download_images=download_images,
            asset_root=asset_root,
            tui=tui,
        )
        if result == "saved":
            saved += 1
            if not (tui is not None and tui.active):
                print(f"{prefix} saved {slug}.md")
        elif result == "skipped":
            skipped += 1
            if not (tui is not None and tui.active):
                print(f"{prefix} skip {slug}")
        else:
            failed += 1

        if tui is not None and tui.active:
            tui.finish_page(
                result,
                slug,
                saved=saved,
                skipped=skipped,
                failed=failed,
            )

        if i < len(urls):
            if tui is not None:
                tui.update_stage("waiting", f"{delay:g}s politeness delay")
            time.sleep(delay)

    return saved, skipped, failed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--base",
        default=DEFAULT_BASE_URL,
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
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {display_path(DEFAULT_OUTPUT_DIR)})",
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
        "--stats-only",
        action="store_true",
        help=(
            "Do not fetch pages. Count existing Markdown files under --out and "
            "print the token summary."
        ),
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Open the interactive source/category picker before scraping. "
            "With no arguments this is the default."
        ),
    )
    p.add_argument(
        "--discover",
        action="store_true",
        help=(
            "Print the wiki's sidebar category names. "
            "Use the output to pick values for --category."
        ),
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=(
            "Cache raw HTML in this directory. On subsequent runs, replays from "
            "disk instead of refetching. Useful when iterating cleanup logic."
        ),
    )
    p.add_argument(
        "--download-images",
        action="store_true",
        help=(
            "Clean mode only: download meaningful article images into "
            "<out>/assets/<page-slug>/ and keep local Markdown image refs."
        ),
    )
    p.add_argument(
        "--no-tui",
        dest="tui",
        action="store_false",
        help="Disable the animated rain progress UI and print plain logs.",
    )
    p.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help=(
            "Skip cleanup pass (keep raw markdownify output, no YAML frontmatter, "
            "no footer-nav stripping, no heading normalization)."
        ),
    )
    p.set_defaults(clean=True, tui=True)
    return p.parse_args(argv)


def dispatch_mode(args: argparse.Namespace, argv: list[str]) -> str:
    """Return the top-level mode selected by argv without creating side effects."""
    if args.stats_only:
        return "stats"
    if args.interactive or not argv:
        return "interactive"
    if args.discover:
        return "discover"
    if args.category:
        return "category"
    return "sitemap"


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)
    mode = dispatch_mode(args, argv)

    if mode == "stats":
        print_markdown_corpus_stats(args.out)
        return

    session = make_session()

    if mode == "interactive":
        run_interactive_mode(session, args)
    elif mode == "discover":
        run_discover_mode(session, args)
    elif mode == "category":
        run_category_mode(session, args)
    else:
        run_sitemap_mode(session, args)


def run_interactive_mode(
    session: requests.Session,
    args: argparse.Namespace,
    *,
    stream: TextIO | None = None,
    input_stream: TextIO | None = None,
) -> None:
    tui = InteractiveScrapeTui(stream=stream, input_stream=input_stream)
    if not tui.is_available():
        print(
            "Interactive mode requires a terminal for both stdin and stdout. "
            "Pass --category, --discover, or another explicit mode for "
            "scripted use.",
            file=sys.stderr,
        )
        sys.exit(1)

    selected_categories: list[str] | None = None
    fallback_mode: str | None = None
    configured_base = normalize_base_url(args.base)
    selected_base = configured_base
    selected_output = args.out

    with tui:
        while selected_categories is None and fallback_mode is None:
            source = tui.choose_source(configured_base)
            if source is None:
                return

            if source == "custom":
                custom_base = tui.prompt_url(configured_base)
                if custom_base is None:
                    continue
                selected_base = custom_base
            else:
                selected_base = configured_base

            tui.show_status("Discovering categories", selected_base)
            try:
                categories = discover_sidebar_categories(session, selected_base)
            except requests.RequestException as exc:
                action = tui.show_error(
                    "Could not discover categories",
                    f"{selected_base}: {exc}",
                )
                if action is None:
                    return
                continue

            if not categories:
                fallback = tui.choose_no_category_fallback(selected_base)
                if fallback is None:
                    return
                if fallback == "back":
                    continue
                fallback_mode = fallback
                continue

            selected_categories = tui.choose_categories(
                categories, base_url=selected_base
            )
            if selected_categories is None:
                return

        output_path = tui.choose_output_path(args.out)
        if output_path is None:
            return
        selected_output = output_path

    args.base = selected_base
    args.out = selected_output
    if fallback_mode == "sitemap":
        run_sitemap_mode(session, args)
        return
    if fallback_mode == "single":
        run_single_url_mode(session, args, selected_base)
        return

    args.category = selected_categories
    run_category_mode(session, args)


def run_discover_mode(session: requests.Session, args: argparse.Namespace) -> None:
    print(f"Fetching sidebar from {args.base} ...")
    names = discover_sidebar_categories(session, args.base)
    if not names:
        print("No sidebar categories found. Try --base with a different URL.", file=sys.stderr)
        sys.exit(1)
    print(f"\n{len(names)} category candidates (pass any to --category):\n")
    for name in names:
        print(f"  {name}")


def run_single_url_mode(
    session: requests.Session,
    args: argparse.Namespace,
    url: str,
) -> None:
    if args.list_only:
        print(url)
        return

    with ScrapeRainTui(enabled=args.tui) as tui:
        if tui.active:
            tui.start_batch(
                "Single URL scrape",
                f"1 page -> {args.out}",
                1,
                mode="single-url",
                output_path=args.out,
            )
        else:
            print(f"\n=== Single URL scrape: {url} -> {args.out} ===")

        s, k, f = scrape_url_list(
            session=session,
            urls=[url],
            out_dir=args.out,
            delay=args.delay,
            overwrite=args.overwrite,
            category=None,
            clean=args.clean,
            cache_dir=args.cache_dir,
            download_images=args.download_images,
            asset_root=args.out / "assets",
            tui=tui,
        )
        if tui.active:
            tui.update_stage("summarizing", str(args.out))

    print(f"\nDone. saved={s} skipped={k} failed={f}")
    print_markdown_corpus_stats(args.out)


def run_category_mode(session: requests.Session, args: argparse.Namespace) -> None:
    total_saved = total_skipped = total_failed = 0
    with ScrapeRainTui(enabled=args.tui and not args.list_only) as tui:
        for cat in args.category:
            cat_label = cat.strip("/").replace("+", " ")
            cat_dir = args.out / cat_label

            if tui.active:
                tui.set_stage(
                    f"Category: {cat_label}",
                    f"Fetching hub /{cat} -> {cat_dir}",
                    "discovering URLs",
                    mode="category",
                    output_path=cat_dir,
                )
            else:
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

            if tui.active:
                tui.start_batch(
                    f"Category: {cat_label}",
                    f"{len(urls)} pages (1 hub + {len(urls) - 1} members)",
                    len(urls),
                    mode="category",
                    output_path=cat_dir,
                )
            else:
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
                category=cat_label,
                clean=args.clean,
                label=f"  [{cat_label}] ",
                cache_dir=args.cache_dir,
                download_images=args.download_images,
                asset_root=args.out / "assets",
                tui=tui,
            )
            total_saved += s
            total_skipped += k
            total_failed += f
        if tui.active:
            tui.update_stage("summarizing", str(args.out))

    if not args.list_only:
        print(
            f"\nDone. saved={total_saved} skipped={total_skipped} failed={total_failed}"
        )
        print_markdown_corpus_stats(args.out)


def run_sitemap_mode(session: requests.Session, args: argparse.Namespace) -> None:
    sitemap_url = f"{args.base.rstrip('/')}/sitemap.xml"
    with ScrapeRainTui(enabled=args.tui and not args.list_only) as tui:
        if tui.active:
            tui.set_stage(
                "Sitemap scrape",
                sitemap_url,
                "fetching sitemap",
                mode="sitemap",
                output_path=args.out,
            )
        else:
            print(f"Fetching sitemap: {sitemap_url}")

        urls = fetch_sitemap_urls(session, sitemap_url)
        if tui.active:
            tui.set_stage(
                "Sitemap scrape",
                f"{len(urls)} URLs in sitemap",
                "preparing",
                mode="sitemap",
                output_path=args.out,
            )
        else:
            print(f"  {len(urls)} URLs in sitemap")

        if args.filter:
            pattern = re.compile(args.filter)
            urls = [u for u in urls if pattern.search(u)]
            if tui.active:
                tui.set_stage(
                    "Sitemap scrape",
                    f"{len(urls)} URLs after filter {args.filter!r}",
                    "filtering",
                )
            else:
                print(f"  {len(urls)} after filter {args.filter!r}")
        if args.limit:
            urls = urls[: args.limit]
            if tui.active:
                tui.set_stage("Sitemap scrape", f"{len(urls)} URLs after limit", "limiting")
            else:
                print(f"  {len(urls)} after limit")

        if args.list_only:
            for u in urls:
                print(u)
            return

        if tui.active:
            tui.start_batch(
                "Sitemap scrape",
                f"{len(urls)} pages -> {args.out}",
                len(urls),
                mode="sitemap",
                output_path=args.out,
            )

        s, k, f = scrape_url_list(
            session=session,
            urls=urls,
            out_dir=args.out,
            delay=args.delay,
            overwrite=args.overwrite,
            category=None,
            clean=args.clean,
            cache_dir=args.cache_dir,
            download_images=args.download_images,
            asset_root=args.out / "assets",
            tui=tui,
        )
        if tui.active:
            tui.update_stage("summarizing", str(args.out))
    print(f"\nDone. saved={s} skipped={k} failed={f}")
    print_markdown_corpus_stats(args.out)


if __name__ == "__main__":
    main()
