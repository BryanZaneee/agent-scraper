"""Tests for CLI dispatch, runners, pipeline wrappers, and facade imports."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import easy_scrape.cli as cli_module  # noqa: E402
import scrape  # noqa: E402
from scrape import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    ScrapeRainTui,
    dispatch_mode,
    parse_args,
    run_interactive_mode,
    scrape_one,
    scrape_url_list,
)

FIXTURES = Path(__file__).parent / "fixtures"
ASYLUM = ("asylum-demon.html", "https://darksouls.wiki.fextralife.com/Asylum+Demon", "Bosses")

def test_dispatch_mode_defaults_to_interactive_without_args():
    args = parse_args([])

    assert dispatch_mode(args, []) == "interactive"

def test_dispatch_mode_preserves_explicit_scripted_modes():
    def mode(argv: list[str]) -> str:
        return dispatch_mode(parse_args(argv), argv)

    assert mode(["--discover"]) == "discover"
    assert mode(["--category", "Weapons"]) == "category"
    assert mode(["--stats-only"]) == "stats"
    assert mode(["--base", "https://eldenring.wiki.fextralife.com"]) == "sitemap"
    assert (
        mode(["--interactive", "--base", "https://bloodborne.wiki.fextralife.com"])
        == "interactive"
    )


def test_parse_args_supports_post_run_stats_browser_toggle():
    assert parse_args([]).browse_stats is None
    assert parse_args(["--browse-stats"]).browse_stats is True
    assert parse_args(["--no-browse-stats"]).browse_stats is False


def test_interactive_mode_requires_tty_streams():
    class FakeStream:
        def isatty(self):
            return False

    args = parse_args(["--interactive"])

    with pytest.raises(SystemExit) as exc:
        run_interactive_mode(
            session=object(),
            args=args,
            stream=FakeStream(),
            input_stream=FakeStream(),
        )

    assert exc.value.code == 1

def test_interactive_no_categories_can_fall_back_to_single_url(monkeypatch):
    calls = {}

    class FakeTui:
        def __init__(self, **_kwargs):
            pass

        def is_available(self):
            return True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def choose_source(self, _default_base_url):
            return "default"

        def show_status(self, _title, _detail):
            return None

        def choose_no_category_fallback(self, base_url):
            calls["fallback_base"] = base_url
            return "single"

        def choose_output_path(self, default_output_path):
            calls["default_output"] = default_output_path
            return Path("/tmp/easy-scrape-custom")

    def fake_discover(_session, _base_url):
        return []

    def fake_single(_session, args, url):
        calls["single"] = (args.base, args.out, url)

    monkeypatch.setattr(cli_module, "InteractiveScrapeTui", FakeTui)
    monkeypatch.setattr(cli_module, "discover_sidebar_categories", fake_discover)
    monkeypatch.setattr(cli_module, "run_single_url_mode", fake_single)

    args = parse_args(["--interactive", "--base", "example.com/docs"])
    run_interactive_mode(object(), args)

    assert calls["fallback_base"] == "https://example.com/docs"
    assert calls["default_output"] == DEFAULT_OUTPUT_DIR
    assert calls["single"] == (
        "https://example.com/docs",
        Path("/tmp/easy-scrape-custom"),
        "https://example.com/docs",
    )

def test_interactive_category_selection_sets_output_before_scrape(monkeypatch):
    calls = {}

    class FakeTui:
        def __init__(self, **_kwargs):
            pass

        def is_available(self):
            return True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def choose_source(self, _default_base_url):
            return "default"

        def show_status(self, _title, _detail):
            return None

        def choose_categories(self, categories, *, base_url):
            calls["category_prompt"] = (categories, base_url)
            return ["Weapons"]

        def choose_output_path(self, default_output_path):
            calls["default_output"] = default_output_path
            return Path("/tmp/easy-scrape-categories")

    def fake_discover(_session, _base_url):
        return ["Weapons", "Armor"]

    def fake_category(_session, args):
        calls["category_run"] = (args.base, args.out, args.category)

    monkeypatch.setattr(cli_module, "InteractiveScrapeTui", FakeTui)
    monkeypatch.setattr(cli_module, "discover_sidebar_categories", fake_discover)
    monkeypatch.setattr(cli_module, "run_category_mode", fake_category)

    args = parse_args(["--interactive"])
    run_interactive_mode(object(), args)

    assert calls["category_prompt"] == (
        ["Weapons", "Armor"],
        "https://darksouls.wiki.fextralife.com",
    )
    assert calls["default_output"] == DEFAULT_OUTPUT_DIR
    assert calls["category_run"] == (
        "https://darksouls.wiki.fextralife.com",
        Path("/tmp/easy-scrape-categories"),
        ["Weapons"],
    )

def test_tui_hooks_do_not_change_scraped_output(tmp_path):
    html = (FIXTURES / "asylum-demon.html").read_text(encoding="utf-8")

    class FakeResponse:
        status_code = 200
        text = html
        headers = {}
        content = html.encode("utf-8")

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    class FakeTty:
        def isatty(self):
            return True

        def write(self, _text):
            pass

        def flush(self):
            pass

    plain_path = tmp_path / "plain" / "Asylum Demon.md"
    tui_path = tmp_path / "tui" / "Asylum Demon.md"
    url = ASYLUM[1]
    session = FakeSession()

    plain_result = scrape_one(session, url, plain_path, overwrite=True, category="Bosses")
    tui = ScrapeRainTui(enabled=True, stream=FakeTty())
    tui.start_batch("Category: Bosses", "1 page", 1)
    tui.start_page(1, 1, url, "Asylum Demon", saved=0, skipped=0, failed=0)
    tui_result = scrape_one(
        session,
        url,
        tui_path,
        overwrite=True,
        category="Bosses",
        tui=tui,
    )

    assert plain_result == tui_result == "saved"
    assert plain_path.read_text(encoding="utf-8") == tui_path.read_text(encoding="utf-8")


def test_scrape_url_list_legacy_wrapper_returns_tuple_for_skips(tmp_path):
    existing = tmp_path / "Example.md"
    existing.write_text("already here", encoding="utf-8")

    class FakeSession:
        def get(self, *_args, **_kwargs):  # pragma: no cover - skip path should not fetch
            raise AssertionError("skip path should not fetch")

    assert scrape_url_list(
        FakeSession(),
        ["https://example.test/Example"],
        tmp_path,
        delay=0,
        overwrite=False,
    ) == (0, 1, 0)


def test_scrape_facade_exports_legacy_public_symbols():
    for name in (
        "extract_main_element",
        "html_to_markdown",
        "scrape_one",
        "scrape_url_list",
        "InteractiveScrapeTui",
        "ScrapeRainTui",
        "parse_args",
        "main",
    ):
        assert hasattr(scrape, name)


def test_discover_sidebar_categories_drops_curated_junk(monkeypatch):
    sample_html = (
        '<div class="wiki-menu-2-left">'
        '<a href="/Weapons">Weapons</a>'
        '<a href="/Armor">Armor</a>'
        '<a href="/Comedy">Comedy</a>'
        '<a href="/Chatroom">Chatroom</a>'
        '<a href="/Build+Calculator">Build Calculator</a>'
        '<a href="/Fan+Art">Fan Art</a>'
        '<a href="/Community+Events">Community Events</a>'
        '<a href="/todo">todo</a>'
        "</div>"
    )

    class FakeResponse:
        text = sample_html
        status_code = 200

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    from easy_scrape.fetching import discover_sidebar_categories

    names = discover_sidebar_categories(FakeSession(), "https://x.test")
    assert "Weapons" in names
    assert "Armor" in names
    for blocked in ("Comedy", "Chatroom", "Build Calculator", "Fan Art", "Community Events", "todo"):
        assert blocked not in names


def test_run_category_mode_skips_categories_with_no_members(monkeypatch, tmp_path):
    fetched = []
    scraped = []

    def fake_fetch(_session, _base, cat):
        fetched.append(cat)
        if cat == "Weapons":
            return (
                f"https://x.test/{cat}",
                ["https://x.test/Drake+Sword"],
            )
        return (f"https://x.test/{cat}", [])

    def fake_scrape_pages(*_args, **kwargs):
        scraped.append(kwargs.get("label", "").strip())
        from easy_scrape.pipeline import ScrapeBatchResult

        return ScrapeBatchResult()

    monkeypatch.setattr(cli_module, "fetch_category_member_urls", fake_fetch)
    monkeypatch.setattr(cli_module, "scrape_pages", fake_scrape_pages)
    monkeypatch.setattr(
        cli_module,
        "ScrapeRainTui",
        lambda **_kw: _NullTui(),
    )
    monkeypatch.setattr(cli_module, "finish_with_markdown_stats", lambda _args: None)

    args = parse_args(["--category", "Weapons", "--category", "Comedy", "--no-tui"])
    args.out = tmp_path
    args.delay = 0
    cli_module.run_category_mode(object(), args)

    assert fetched == ["Weapons", "Comedy"]
    # Comedy returned no members → scrape_pages must not be called for it.
    assert scraped == ["[Weapons]"]


def test_run_category_mode_dedups_urls_across_categories(monkeypatch, tmp_path):
    captured = []

    def fake_fetch(_session, _base, cat):
        # Drake Sword belongs to both Weapons (first) and Items.
        return (
            f"https://x.test/{cat}",
            [
                "https://x.test/Drake+Sword",
                f"https://x.test/{cat}-only",
            ],
        )

    def fake_scrape_pages(_session, urls, *_args, **kwargs):
        captured.append((kwargs.get("label", "").strip(), list(urls)))
        seen = kwargs.get("seen_urls")
        from easy_scrape.pipeline import ScrapeBatchResult

        if seen is not None:
            for u in urls:
                seen.add(u)
        return ScrapeBatchResult()

    monkeypatch.setattr(cli_module, "fetch_category_member_urls", fake_fetch)
    monkeypatch.setattr(cli_module, "scrape_pages", fake_scrape_pages)
    monkeypatch.setattr(
        cli_module,
        "ScrapeRainTui",
        lambda **_kw: _NullTui(),
    )
    monkeypatch.setattr(cli_module, "finish_with_markdown_stats", lambda _args: None)

    args = parse_args(["--category", "Weapons", "--category", "Items", "--no-tui"])
    args.out = tmp_path
    args.delay = 0
    cli_module.run_category_mode(object(), args)

    weapons_urls = next(u for lbl, u in captured if lbl == "[Weapons]")
    items_urls = next(u for lbl, u in captured if lbl == "[Items]")

    assert "https://x.test/Drake+Sword" in weapons_urls
    # Drake Sword was claimed by Weapons; Items must not see it again.
    assert "https://x.test/Drake+Sword" not in items_urls
    # Each cat still gets its own unique URL.
    assert "https://x.test/Weapons-only" in weapons_urls
    assert "https://x.test/Items-only" in items_urls


def test_scrape_page_skips_stub_below_min_body(monkeypatch, tmp_path):
    from bs4 import BeautifulSoup

    from easy_scrape import pipeline

    monkeypatch.setattr(pipeline, "html_to_markdown", lambda *_a, **_k: "tiny.")
    monkeypatch.setattr(
        pipeline,
        "extract_main_element",
        lambda *_a, **_k: BeautifulSoup("<div/>", "lxml").div,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_frontmatter",
        lambda *_a, **_k: {"title": "x", "url": "x"},
    )

    class FakeResponse:
        status_code = 200
        text = "<html></html>"
        headers = {}
        content = b"<html></html>"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    out = tmp_path / "Stub.md"
    result = pipeline.scrape_page(
        FakeSession(),
        "https://x.test/Stub",
        out,
        pipeline.ScrapeOptions(overwrite=True, category="Items"),
    )

    assert result.status == "skipped"
    assert not out.exists()


class _NullTui:
    """Minimal context-manager stand-in for ScrapeRainTui in unit tests."""

    active = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def set_stage(self, *_args, **_kwargs):
        return None

    def start_batch(self, *_args, **_kwargs):
        return None

    def update_stage(self, *_args, **_kwargs):
        return None
