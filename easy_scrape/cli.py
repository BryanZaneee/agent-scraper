"""Command-line parsing and mode runners for easy_scrape."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TextIO

import requests

from .constants import DEFAULT_BASE_URL, DEFAULT_OUTPUT_DIR
from .fetching import (
    discover_sidebar_categories,
    fetch_category_member_urls,
    fetch_sitemap_urls,
    make_session,
)
from .pipeline import ScrapeOptions, scrape_pages
from .stats import print_markdown_corpus_stats
from .tui.picker import InteractiveScrapeTui, display_path, normalize_base_url
from .tui.progress import ScrapeRainTui
from .tui.stats_browser import MarkdownStatsBrowserTui


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
        "--browse-stats",
        dest="browse_stats",
        action="store_true",
        default=None,
        help=(
            "After the token summary, open the interactive output folder stats "
            "browser."
        ),
    )
    p.add_argument(
        "--no-browse-stats",
        dest="browse_stats",
        action="store_false",
        help="Skip the post-run folder stats browser in interactive mode.",
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
        finish_with_markdown_stats(args)
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
    if args.browse_stats is None:
        args.browse_stats = True
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

        result = scrape_pages(
            session,
            [url],
            args.out,
            args.delay,
            ScrapeOptions(
                overwrite=args.overwrite,
                category=None,
                clean=args.clean,
                cache_dir=args.cache_dir,
                download_images=args.download_images,
                asset_root=args.out / "assets",
            ),
            tui=tui,
        )
        s, k, f = result.as_tuple()
        if tui.active:
            tui.update_stage("summarizing", str(args.out))

    print(f"\nDone. saved={s} skipped={k} failed={f}")
    finish_with_markdown_stats(args)


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

            result = scrape_pages(
                session,
                urls,
                cat_dir,
                args.delay,
                ScrapeOptions(
                    overwrite=args.overwrite,
                    category=cat_label,
                    clean=args.clean,
                    cache_dir=args.cache_dir,
                    download_images=args.download_images,
                    asset_root=args.out / "assets",
                ),
                label=f"  [{cat_label}] ",
                tui=tui,
            )
            total_saved += result.saved
            total_skipped += result.skipped
            total_failed += result.failed
        if tui.active:
            tui.update_stage("summarizing", str(args.out))

    if not args.list_only:
        print(
            f"\nDone. saved={total_saved} skipped={total_skipped} failed={total_failed}"
        )
        finish_with_markdown_stats(args)


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

        result = scrape_pages(
            session,
            urls,
            args.out,
            args.delay,
            ScrapeOptions(
                overwrite=args.overwrite,
                category=None,
                clean=args.clean,
                cache_dir=args.cache_dir,
                download_images=args.download_images,
                asset_root=args.out / "assets",
            ),
            tui=tui,
        )
        s, k, f = result.as_tuple()
        if tui.active:
            tui.update_stage("summarizing", str(args.out))
    print(f"\nDone. saved={s} skipped={k} failed={f}")
    finish_with_markdown_stats(args)


def finish_with_markdown_stats(args: argparse.Namespace) -> None:
    """Print final stats and optionally open the interactive folder browser."""
    print_markdown_corpus_stats(args.out)
    if not args.browse_stats:
        return

    browser = MarkdownStatsBrowserTui(args.out)
    if not browser.is_available():
        print("\nFolder stats browser requires an interactive terminal; skipping.")
        return

    with browser:
        browser.run()
