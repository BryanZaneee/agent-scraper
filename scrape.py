"""Scrape wikis and web content to Markdown files.

Currently optimized for Fextralife wikis (Dark Souls, Elden Ring, Bloodborne,
Sekiro, etc.). Extensible for other sites. Pick a source with --base, then
choose a mode:

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


def extract_main_element(html: str, page_url: str, *, clean: bool = True):
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
        replace_images_with_alt(el)
        drop_banner_alt_rows(el)
        drop_footer_nav_table(el)
        drop_stranded_category_links(el)

    for tag in el.find_all(["img", "script", "style", "noscript", "iframe"]):
        tag.decompose()
    for a in el.find_all("a", href=True):
        a["href"] = urljoin(page_url, a["href"])

    if clean:
        drop_fragment_anchors(el, page_url)
        strip_inline_links(el)
        drop_empty_columns(el)
    return el


def html_to_markdown(html_fragment: str, *, title: str = "", clean: bool = True) -> str:
    md = markdownify(html_fragment, heading_style="ATX", strip=["img"])
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
) -> str:
    """Return one of: 'saved', 'skipped', 'failed'."""
    if out_path.exists() and not overwrite:
        return "skipped"

    cache_path = (
        cache_dir / f"{url_to_filename(url)}.html" if cache_dir is not None else None
    )

    html_text: str | None = None
    if cache_path is not None and cache_path.exists() and cache_path.stat().st_size > 0:
        html_text = cache_path.read_text(encoding="utf-8")

    if html_text is None:
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
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(html_text, encoding="utf-8")

    el = extract_main_element(html_text, url, clean=clean)
    if el is None:
        print(f"  no #wiki-content-block at {url}", file=sys.stderr)
        return "failed"

    title = url_to_title(url)
    md = html_to_markdown(str(el), title=title, clean=clean)
    if not md:
        print(f"  empty content at {url}", file=sys.stderr)
        return "failed"

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
) -> tuple[int, int, int]:
    """Save each URL into out_dir/<slug>.md. Returns (saved, skipped, failed)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = skipped = failed = 0

    for i, url in enumerate(urls, 1):
        slug = url_to_filename(url)
        path = out_dir / f"{slug}.md"
        prefix = f"{label}[{i}/{len(urls)}]" if label else f"[{i}/{len(urls)}]"

        result = scrape_one(
            session,
            url,
            path,
            overwrite,
            category=category,
            clean=clean,
            cache_dir=cache_dir,
        )
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
        "--no-clean",
        dest="clean",
        action="store_false",
        help=(
            "Skip cleanup pass (keep raw markdownify output, no YAML frontmatter, "
            "no footer-nav stripping, no heading normalization)."
        ),
    )
    p.set_defaults(clean=True)
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
            category=cat_label,
            clean=args.clean,
            label=f"  [{cat_label}] ",
            cache_dir=args.cache_dir,
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
        category=None,
        clean=args.clean,
        cache_dir=args.cache_dir,
    )
    print(f"\nDone. saved={s} skipped={k} failed={f}")


if __name__ == "__main__":
    main()
