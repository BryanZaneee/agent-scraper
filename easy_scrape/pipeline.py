"""Scrape pipeline data objects and behavior-preserving legacy wrappers."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from .cleanup import (
    ImageAssetContext,
    extract_frontmatter,
    extract_main_element,
    format_frontmatter,
    html_to_markdown,
)
from .fetching import url_to_filename, url_to_title

if TYPE_CHECKING:
    from .tui.progress import ScrapeRainTui


@dataclass(frozen=True)
class ScrapeOptions:
    """Behavior flags for one scrape run."""

    overwrite: bool
    category: str | None = None
    clean: bool = True
    cache_dir: Path | None = None
    download_images: bool = False
    asset_root: Path | None = None


@dataclass(frozen=True)
class ScrapeResult:
    """Result for one page in the scrape pipeline."""

    status: str
    url: str
    out_path: Path
    slug: str


@dataclass
class ScrapeBatchResult:
    """Aggregated results for a URL list scrape."""

    saved: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[ScrapeResult] = field(default_factory=list)

    def record(self, result: ScrapeResult) -> None:
        self.results.append(result)
        if result.status == "saved":
            self.saved += 1
        elif result.status == "skipped":
            self.skipped += 1
        else:
            self.failed += 1

    def as_tuple(self) -> tuple[int, int, int]:
        return self.saved, self.skipped, self.failed


def scrape_page(
    session: requests.Session,
    url: str,
    out_path: Path,
    options: ScrapeOptions,
    *,
    tui: "ScrapeRainTui | None" = None,
) -> ScrapeResult:
    """Scrape one URL and return a structured result."""
    slug = out_path.stem
    if out_path.exists() and not options.overwrite:
        return ScrapeResult("skipped", url, out_path, slug)

    cache_path = (
        options.cache_dir / f"{url_to_filename(url)}.html"
        if options.cache_dir is not None
        else None
    )

    html_text: str | None = None
    if (
        cache_path is not None
        and cache_path.exists()
        and cache_path.stat().st_size > 0
    ):
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
                return ScrapeResult("failed", url, out_path, slug)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  HTTP error {url}: {e}", file=sys.stderr)
            return ScrapeResult("failed", url, out_path, slug)
        html_text = r.text
        if cache_path is not None:
            if tui is not None:
                tui.update_stage("writing cache", str(cache_path))
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(html_text, encoding="utf-8")

    preserve_images = (
        options.clean
        and options.download_images
        and options.asset_root is not None
    )
    image_assets = None
    if preserve_images:
        image_assets = ImageAssetContext(
            session=session,
            asset_dir=options.asset_root / out_path.stem,
            markdown_dir=out_path.parent,
        )

    if tui is not None:
        tui.update_stage("cleaning", url)
    el = extract_main_element(
        html_text,
        url,
        clean=options.clean,
        image_assets=image_assets,
    )
    if el is None:
        print(f"  no #wiki-content-block at {url}", file=sys.stderr)
        return ScrapeResult("failed", url, out_path, slug)

    title = url_to_title(url)
    if tui is not None:
        tui.update_stage("converting", title)
    md = html_to_markdown(
        str(el),
        title=title,
        clean=options.clean,
        preserve_images=preserve_images,
    )
    MIN_BODY_CHARS = 200
    if not md or (options.clean and len(md) < MIN_BODY_CHARS):
        reason = "empty" if not md else f"stub ({len(md)} chars)"
        print(f"  {reason} content at {url}", file=sys.stderr)
        return ScrapeResult("skipped", url, out_path, slug)

    if tui is not None:
        tui.update_stage("writing", str(out_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if options.clean:
        fm = extract_frontmatter(
            el,
            url=url,
            title=title,
            category=options.category,
        )
        body = f"{format_frontmatter(fm)}\n\n# {title}\n\n{md}\n"
    else:
        body = f"# {title}\n\nSource: {url}\n\n{md}\n"
    out_path.write_text(body, encoding="utf-8")
    return ScrapeResult("saved", url, out_path, slug)


def scrape_pages(
    session: requests.Session,
    urls: list[str],
    out_dir: Path,
    delay: float,
    options: ScrapeOptions,
    *,
    label: str = "",
    tui: "ScrapeRainTui | None" = None,
    seen_urls: set[str] | None = None,
) -> ScrapeBatchResult:
    """Save each URL into out_dir/<slug>.md and return structured totals."""
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = ScrapeBatchResult()

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
                saved=batch.saved,
                skipped=batch.skipped,
                failed=batch.failed,
            )

        result = scrape_page(session, url, path, options, tui=tui)
        batch.record(result)
        if seen_urls is not None and result.status in ("saved", "skipped"):
            seen_urls.add(url)

        if result.status == "saved":
            if not (tui is not None and tui.active):
                print(f"{prefix} saved {slug}.md")
        elif result.status == "skipped":
            if not (tui is not None and tui.active):
                print(f"{prefix} skip {slug}")

        if tui is not None and tui.active:
            tui.finish_page(
                result.status,
                slug,
                saved=batch.saved,
                skipped=batch.skipped,
                failed=batch.failed,
            )

        if i < len(urls):
            if tui is not None:
                tui.update_stage("waiting", f"{delay:g}s politeness delay")
            time.sleep(delay)

    return batch


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
    tui: "ScrapeRainTui | None" = None,
) -> str:
    """Legacy wrapper returning one of: 'saved', 'skipped', 'failed'."""
    options = ScrapeOptions(
        overwrite=overwrite,
        category=category,
        clean=clean,
        cache_dir=cache_dir,
        download_images=download_images,
        asset_root=asset_root,
    )
    return scrape_page(session, url, out_path, options, tui=tui).status


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
    tui: "ScrapeRainTui | None" = None,
) -> tuple[int, int, int]:
    """Legacy wrapper returning (saved, skipped, failed)."""
    options = ScrapeOptions(
        overwrite=overwrite,
        category=category,
        clean=clean,
        cache_dir=cache_dir,
        download_images=download_images,
        asset_root=asset_root,
    )
    return scrape_pages(
        session,
        urls,
        out_dir,
        delay,
        options,
        label=label,
        tui=tui,
    ).as_tuple()
