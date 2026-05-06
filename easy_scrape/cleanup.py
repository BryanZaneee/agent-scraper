"""HTML cleanup, image preservation, Markdown conversion, and frontmatter."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify

from .constants import (
    ASSET_FILENAME_UNSAFE,
    CONTENT_IMAGE_EXTENSIONS,
    CONTENT_IMAGE_MIN_DIMENSION,
    CONTENT_SELECTOR,
    PRESERVED_IMAGE_ATTR,
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


@dataclass(frozen=True)
class CleanupPipelineContext:
    """Context shared by ordered HTML cleanup steps."""

    page_url: str
    image_assets: ImageAssetContext | None = None


@dataclass(frozen=True)
class CleanupStep:
    """Named cleanup step for preserving the order-sensitive pipeline."""

    name: str
    apply: Callable[[object, CleanupPipelineContext], None]


def _preserve_content_images_step(
    element,
    context: CleanupPipelineContext,
) -> None:
    if context.image_assets is not None:
        preserve_content_images(element, context.page_url, context.image_assets)


def _drop_fragment_anchors_step(
    element,
    context: CleanupPipelineContext,
) -> None:
    drop_fragment_anchors(element, context.page_url)


PRE_LINK_CLEANUP_PIPELINE = (
    CleanupStep("expand_rowspans", lambda element, _context: expand_rowspans(element)),
    CleanupStep("preserve_content_images", _preserve_content_images_step),
    CleanupStep(
        "replace_images_with_alt",
        lambda element, _context: replace_images_with_alt(element),
    ),
    CleanupStep(
        "drop_banner_alt_rows",
        lambda element, _context: drop_banner_alt_rows(element),
    ),
    CleanupStep(
        "drop_footer_nav_table",
        lambda element, _context: drop_footer_nav_table(element),
    ),
    CleanupStep(
        "drop_stranded_category_links",
        lambda element, _context: drop_stranded_category_links(element),
    ),
)

POST_LINK_CLEANUP_PIPELINE = (
    CleanupStep("drop_fragment_anchors", _drop_fragment_anchors_step),
    CleanupStep("strip_inline_links", lambda element, _context: strip_inline_links(element)),
    CleanupStep("drop_empty_columns", lambda element, _context: drop_empty_columns(element)),
)


def run_cleanup_pipeline(
    element,
    steps: tuple[CleanupStep, ...],
    context: CleanupPipelineContext,
) -> None:
    """Apply named cleanup steps in their declared order."""
    for step in steps:
        step.apply(element, context)



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

    context = CleanupPipelineContext(page_url=page_url, image_assets=image_assets)
    if clean:
        run_cleanup_pipeline(el, PRE_LINK_CLEANUP_PIPELINE, context)

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
        run_cleanup_pipeline(el, POST_LINK_CLEANUP_PIPELINE, context)
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

