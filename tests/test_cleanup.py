"""Fixture-based regression tests for scrape.py cleanup helpers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scrape as scrape_module  # noqa: E402

from scrape import (  # noqa: E402
    CategoryPickerState,
    DEFAULT_OUTPUT_DIR,
    FolderBrowserState,
    ImageAssetContext,
    InteractiveScrapeTui,
    ScrapeRainTui,
    ScrapeTuiState,
    TerminalRainDrop,
    TerminalRainSystem,
    TerminalLightningBolt,
    asset_filename_from_url,
    collect_markdown_corpus_stats,
    display_path,
    dispatch_mode,
    estimate_token_count,
    extract_frontmatter,
    extract_main_element,
    format_frontmatter,
    html_to_markdown,
    key_name_from_sequence,
    normalize_base_url,
    normalize_existing_output_dir,
    normalize_new_output_dir,
    normalize_output_dir,
    parse_args,
    print_markdown_corpus_stats,
    progress_bar,
    run_interactive_mode,
    scrape_one,
    strip_ansi,
    url_to_title,
)

FIXTURES = Path(__file__).parent / "fixtures"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def render(fixture: str, url: str, category: str | None = None) -> tuple[str, dict]:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    el = extract_main_element(html, url, clean=True)
    assert el is not None, f"no #wiki-content-block in {fixture}"
    title = url_to_title(url)
    md = html_to_markdown(str(el), title=title, clean=True)
    fm = extract_frontmatter(el, url=url, title=title, category=category)
    body = f"{format_frontmatter(fm)}\n\n# {title}\n\n{md}\n"
    return body, fm


DRAKE = ("drake-sword.html", "https://darksouls.wiki.fextralife.com/Drake+Sword", "Weapons")
BELL = ("bell-gargoyle.html", "https://darksouls.wiki.fextralife.com/Bell+Gargoyle", "Bosses")
EMBERS = ("embers.html", "https://darksouls.wiki.fextralife.com/Embers", "Items")
ASYLUM = ("asylum-demon.html", "https://darksouls.wiki.fextralife.com/Asylum+Demon", "Bosses")
MAPS_URL = "https://darksouls.wiki.fextralife.com/Maps"


def test_drake_sword_no_footer_nav():
    body, _ = render(*DRAKE)
    assert "♦" not in body


def test_drake_sword_section_headings_normalized():
    body, _ = render(*DRAKE)
    assert "### Dark Souls Drake Sword" not in body
    assert "### How to Get" in body or "### Hints and Tips" in body


def test_drake_sword_frontmatter_has_weapon_stats():
    _, fm = render(*DRAKE)
    assert fm["title"] == "Drake Sword"
    assert fm.get("category") == "Weapons"
    assert any(k in fm for k in ("weapon_type", "attack_type", "enchantable", "special")), fm


def test_bell_gargoyle_no_footer_nav():
    body, _ = render(*BELL)
    assert "Asylum Demon" not in body
    assert "Capra Demon" not in body


def test_bell_gargoyle_inline_links_stripped():
    body, fm = render(*BELL)
    # No markdown link syntax `[text](url)` should remain in the body — only
    # the source URL in YAML frontmatter is allowed to contain a URL.
    body_after_fm = body.split("---\n", 2)[-1]
    assert "](http" not in body_after_fm, "inline markdown links should be unwrapped"
    assert "darksouls.wiki.fextralife.com/home" not in body_after_fm
    assert "darksouls.wiki.fextralife.com/items" not in body_after_fm
    # Frontmatter still has the canonical URL for provenance
    assert fm["url"].startswith("https://")


def test_embers_mid_article_stranded_links_dropped():
    body, _ = render(*EMBERS)
    assert "[Ammunition](" not in body


def test_embers_intro_preserved():
    body, _ = render(*EMBERS)
    assert "Embers" in body
    assert "blacksmiths" in body.lower()


def test_asylum_demon_banner_row_dropped():
    body, _ = render(*ASYLUM)
    assert "Boss 0036 Asylum Demon" not in body


def test_asylum_demon_fragment_anchor_dropped():
    body, _ = render(*ASYLUM)
    assert "Jump to Strategies" not in body


def test_asylum_demon_frontmatter_shape():
    body, fm = render(*ASYLUM)
    assert body.startswith("---\n")
    assert fm["title"] == "Asylum Demon"
    assert fm.get("category") == "Bosses"


def test_asylum_demon_rowspan_captures_ng_and_ng_plus():
    _, fm = render(*ASYLUM)
    health = str(fm.get("health", ""))
    assert "NG" in health, health
    assert "NG+" in health, health


def test_asylum_demon_empty_icon_column_dropped():
    """Boss stat table has an empty middle icon column; drop_empty_columns should collapse it."""
    body, _ = render(*ASYLUM)
    # Expect Location/Health/Souls rows to be 2-cell after collapse, not 3-cell.
    location_lines = [
        l for l in body.splitlines()
        if l.startswith("|") and "Location" in l and "Undead" in l
    ]
    assert location_lines, "could not find Location row in body"
    # `| **Location** | [Undead Asylum]... |` → exactly 2 separators inside; or 3 pipes total
    assert location_lines[0].count("|") == 3, (
        f"Location row should be 2-cell after empty-icon-col drop: {location_lines[0]!r}"
    )


def test_maps_download_images_as_local_markdown_refs(tmp_path):
    html = (FIXTURES / "maps.html").read_text(encoding="utf-8")
    downloads = []

    def fake_downloader(_session, source_url, dest_path):
        downloads.append((source_url, dest_path))
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"fake image")
        return True

    ctx = ImageAssetContext(
        session=object(),
        asset_dir=tmp_path / "assets" / "Maps",
        markdown_dir=tmp_path / "Maps",
        downloader=fake_downloader,
    )
    el = extract_main_element(html, MAPS_URL, clean=True, image_assets=ctx)
    assert el is not None
    md = html_to_markdown(str(el), title="Maps", clean=True, preserve_images=True)

    assert "![Northern Undead Asylum](../assets/Maps/Northern_AsylumMapV1.jpg)" in md
    assert "![Sen's Fortress](../assets/Maps/Sen_s_Fortress.png)" in md
    assert "![World Map](../assets/Maps/dark_souls_entire_map_bosses.png)" in md
    assert "### Northern Undead Asylum Northern Undead Asylum" not in md
    assert len(downloads) == 3
    assert (tmp_path / "assets" / "Maps" / "Northern_AsylumMapV1.jpg").exists()


def test_image_download_does_not_capture_stat_icons(tmp_path):
    html = (FIXTURES / "asylum-demon.html").read_text(encoding="utf-8")
    downloads = []

    def fake_downloader(_session, source_url, dest_path):
        downloads.append((source_url, dest_path))
        return True

    ctx = ImageAssetContext(
        session=object(),
        asset_dir=tmp_path / "assets" / "Asylum_Demon",
        markdown_dir=tmp_path,
        downloader=fake_downloader,
    )
    el = extract_main_element(html, ASYLUM[1], clean=True, image_assets=ctx)
    assert el is not None
    md = html_to_markdown(str(el), title="Asylum Demon", clean=True, preserve_images=True)

    assert downloads == []
    assert "![" not in md
    assert "Boss 0036 Asylum Demon" not in md


def test_asset_filename_from_url_sanitizes_and_deduplicates():
    seen: set[str] = set()

    assert (
        asset_filename_from_url(
            "https://darksouls.wiki.fextralife.com/file/Dark-Souls/"
            "dark%20souls%20entire%20map%20bosses.png?v=1518927256403",
            seen,
        )
        == "dark_souls_entire_map_bosses.png"
    )
    assert (
        asset_filename_from_url(
            "https://darksouls.wiki.fextralife.com/file/Dark-Souls/"
            "Sen's_Fortress.png",
            seen,
        )
        == "Sen_s_Fortress.png"
    )
    assert (
        asset_filename_from_url(
            "https://darksouls.wiki.fextralife.com/file/Dark-Souls/"
            "Sen's_Fortress.png?download=1",
            seen,
        )
        == "Sen_s_Fortress_2.png"
    )


def test_collect_markdown_corpus_stats_counts_nested_markdown(tmp_path):
    (tmp_path / "Weapons").mkdir()
    (tmp_path / "Weapons" / "Sword.md").write_text("abcd efgh", encoding="utf-8")
    (tmp_path / "Armor.md").write_text("ijkl", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    stats = collect_markdown_corpus_stats(tmp_path)

    assert stats.file_count == 2
    assert stats.bytes == len("abcd efgh".encode("utf-8")) + len(
        "ijkl".encode("utf-8")
    )
    assert stats.words == 3
    assert stats.estimated_tokens == estimate_token_count(
        "abcd efgh"
    ) + estimate_token_count("ijkl")
    assert [file.path.name for file in stats.files] == ["Armor.md", "Sword.md"]


def test_token_summary_includes_final_file_count_report(tmp_path, capsys):
    (tmp_path / "Weapons").mkdir()
    (tmp_path / "Weapons" / "Sword.md").write_text("abcd efgh", encoding="utf-8")

    print_markdown_corpus_stats(tmp_path)

    output = capsys.readouterr().out
    assert "Markdown files: 1" in output
    assert "Final report: 1 files, 3 estimated tokens" in output


def test_progress_bar_formats_completed_work():
    assert progress_bar(2, 4, 10) == "[####....]"
    assert progress_bar(0, 0, 6) == "[....]"


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


def test_normalize_base_url_accepts_custom_base_urls():
    assert normalize_base_url("") == "https://darksouls.wiki.fextralife.com"
    assert (
        normalize_base_url("darksouls.wiki.fextralife.com/")
        == "https://darksouls.wiki.fextralife.com"
    )
    assert (
        normalize_base_url("HTTP://Example.COM/wiki/?ignored=yes#section")
        == "http://example.com/wiki"
    )
    with pytest.raises(ValueError):
        normalize_base_url("ftp://example.com")


def test_parse_args_defaults_to_desktop_output_dir():
    args = parse_args([])

    assert args.out == DEFAULT_OUTPUT_DIR
    assert display_path(args.out) == "~/Desktop/easy_scrape_output"


def test_normalize_output_dir_accepts_default_and_rejects_files(tmp_path):
    default_path = tmp_path / "default"
    custom_path = tmp_path / "custom"
    existing_file = tmp_path / "not-a-dir"
    existing_file.write_text("not a directory", encoding="utf-8")

    assert normalize_output_dir("", default_path) == default_path
    assert normalize_output_dir(str(custom_path), default_path) == custom_path
    with pytest.raises(ValueError):
        normalize_output_dir(str(existing_file), default_path)


def test_folder_browser_default_action_returns_default_output(tmp_path):
    state = FolderBrowserState(DEFAULT_OUTPUT_DIR, tmp_path)
    state.refresh()

    state.handle_key("enter")

    assert state.submitted == DEFAULT_OUTPUT_DIR


def test_folder_browser_opens_child_directory(tmp_path):
    child = tmp_path / "Child"
    child.mkdir()
    state = FolderBrowserState(DEFAULT_OUTPUT_DIR, tmp_path)
    state.refresh()
    state.cursor = 2

    state.handle_key("enter")

    assert state.current_dir == child
    assert state.cursor == 1
    assert state.submitted is None


def test_folder_browser_parent_home_and_desktop_shortcuts(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    nested = desktop / "Nested"
    nested.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    state = FolderBrowserState(DEFAULT_OUTPUT_DIR, nested)
    state.refresh()
    state.handle_key("backspace")
    assert state.current_dir == desktop

    state.handle_key("~")
    assert state.current_dir == tmp_path

    state.handle_key("d")
    assert state.current_dir == desktop


def test_folder_browser_direct_path_validates_existing_directories(tmp_path):
    existing_dir = tmp_path / "existing"
    existing_dir.mkdir()
    existing_file = tmp_path / "not-a-dir"
    existing_file.write_text("not a directory", encoding="utf-8")
    state = FolderBrowserState(DEFAULT_OUTPUT_DIR, tmp_path)

    state.submit_direct_path("existing")
    assert state.submitted == existing_dir

    with pytest.raises(ValueError):
        normalize_existing_output_dir(str(existing_file), tmp_path)
    with pytest.raises(ValueError):
        normalize_existing_output_dir(str(tmp_path / "missing"), tmp_path)


def test_folder_browser_new_folder_returns_path_without_creating(tmp_path):
    state = FolderBrowserState(DEFAULT_OUTPUT_DIR, tmp_path)

    state.submit_new_folder("New Scrape")

    assert state.submitted == tmp_path / "New Scrape"
    assert not state.submitted.exists()

    with pytest.raises(ValueError):
        normalize_new_output_dir("nested/path", tmp_path)


def test_category_picker_state_toggles_and_submits_selection():
    state = CategoryPickerState(["Weapons", "Armor", "Bosses"])

    state.handle_key("down")
    state.handle_key("space")
    assert state.cursor == 1
    assert state.selected_categories() == ["Armor"]

    state.handle_key("a")
    assert state.selected_categories() == ["Weapons", "Armor", "Bosses"]

    state.handle_key("n")
    assert state.selected_categories() == []
    state.handle_key("enter")
    assert not state.submitted
    assert "Select at least one" in state.message

    state.handle_key("space")
    state.handle_key("enter")
    assert state.submitted
    assert state.selected_categories() == ["Armor"]


def test_category_picker_state_q_cancels():
    state = CategoryPickerState(["Weapons"])

    state.handle_key("q")

    assert state.cancelled


def test_terminal_escape_sequences_map_to_arrow_keys():
    assert key_name_from_sequence("\x1b[A") == "up"
    assert key_name_from_sequence("\x1b[B") == "down"
    assert key_name_from_sequence("\x1bOA") == "up"
    assert key_name_from_sequence("\x1b[1;5B") == "down"
    assert key_name_from_sequence("\x1b[<64;12;8M") == "mouse"
    assert key_name_from_sequence("\x1b[M`!!") == "mouse"
    assert key_name_from_sequence("\x1b") == "escape"


def test_interactive_picker_frame_has_banner_and_rain_state():
    class FakeStream:
        def __init__(self):
            self.writes = []

        def isatty(self):
            return True

        def write(self, text):
            self.writes.append(text)

        def flush(self):
            pass

    stream = FakeStream()
    tui = InteractiveScrapeTui(stream=stream)

    frame = strip_ansi(
        tui._draw_frame(80, 24, "Choose a source", ["> Dark Souls"], "q quit")
    )

    assert "easyScrape" in frame or "___  __" in frame
    assert "Choose a source" in frame
    assert "####" in frame
    assert "_/====\\_" in frame
    assert tui._rain.drops


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

    monkeypatch.setattr(scrape_module, "InteractiveScrapeTui", FakeTui)
    monkeypatch.setattr(scrape_module, "discover_sidebar_categories", fake_discover)
    monkeypatch.setattr(scrape_module, "run_single_url_mode", fake_single)

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

    monkeypatch.setattr(scrape_module, "InteractiveScrapeTui", FakeTui)
    monkeypatch.setattr(scrape_module, "discover_sidebar_categories", fake_discover)
    monkeypatch.setattr(scrape_module, "run_category_mode", fake_category)

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


def test_rain_system_splashes_on_tui_panel():
    class AlwaysSplash:
        def random(self):
            return 0.0

    rain = TerminalRainSystem()
    rain._target_count = lambda _width: 0
    rain.rng = AlwaysSplash()
    rain.drops = [
        TerminalRainDrop(
            x=10,
            y=4.5,
            speed_y=1.0,
            speed_x=0.0,
            character="|",
            color="white",
            z_index=1,
        )
    ]

    rain.update(40, 20, (5, 20, 5), speed=1.0)

    assert rain.drops == []
    assert [(s.x, s.y) for s in rain.splashes] == [(10, 5)]


def test_rain_tui_is_inert_for_non_tty_stream():
    class FakeStream:
        def __init__(self):
            self.writes = []

        def isatty(self):
            return False

        def write(self, text):
            self.writes.append(text)

        def flush(self):
            pass

    stream = FakeStream()
    with ScrapeRainTui(enabled=True, stream=stream) as tui:
        tui.start_batch("Title", "Subtitle", 3)
        tui.start_page(
            1,
            3,
            "https://example.test",
            "Example",
            saved=0,
            skipped=0,
            failed=0,
        )
        tui.finish_page("saved", "Example", saved=1, skipped=0, failed=0)

    assert not tui.active
    assert stream.writes == []


def test_tui_q_switches_to_plain_logs_without_failure():
    class FakeTty:
        def isatty(self):
            return True

        def write(self, _text):
            pass

        def flush(self):
            pass

    tui = ScrapeRainTui(enabled=True, stream=FakeTty())
    tui.start_batch("Title", "Subtitle", 3)

    tui.handle_key("q")

    state = tui.snapshot()
    assert not tui.active
    assert tui.plain_requested
    assert state.failed == 0


def test_tui_pause_does_not_block_state_updates():
    class FakeTty:
        def isatty(self):
            return True

        def write(self, _text):
            pass

        def flush(self):
            pass

    tui = ScrapeRainTui(enabled=True, stream=FakeTty())
    tui.start_batch("Title", "Subtitle", 2)

    tui.handle_key("p")
    tui.start_page(1, 2, "https://example.test/a", "a", saved=0, skipped=0, failed=0)
    tui.finish_page("saved", "a", saved=1, skipped=0, failed=0)

    state = tui.snapshot()
    assert tui.paused
    assert state.saved == 1
    assert state.index == 1
    assert state.stage == "saved"


def test_tui_help_frame_fits_supported_narrow_terminal():
    tui = ScrapeRainTui(enabled=False)
    tui.handle_key("?")
    state = ScrapeTuiState(
        title="Category: Maps",
        mode="category",
        stage="fetching page",
        output_path="/tmp/out",
        current_url="https://example.test/maps",
        current_slug="Maps",
        total=3,
        index=1,
        saved=0,
        skipped=0,
        failed=0,
    )

    frame = tui._draw_frame(60, 18, state)
    plain_lines = [ANSI_RE.sub("", line) for line in frame.splitlines()]

    assert len(plain_lines) == 18
    assert all(len(line) == 60 for line in plain_lines)
    assert "easy_scrape controls" in "\n".join(plain_lines)


def test_tui_redraws_dashboard_text_over_storm_effects():
    tui = ScrapeRainTui(enabled=False)
    tui._paused = True
    tui._storm.bolts = [
        TerminalLightningBolt(
            segments=[(30, 8, "|"), (31, 9, "\\"), (32, 10, "|")],
            age=0,
            max_age=10,
        )
    ]
    tui._storm.flash_active = True
    state = ScrapeTuiState(
        title="Sitemap scrape",
        mode="sitemap",
        stage="fetching page",
        output_path="/tmp/out",
        detail="https://example.test/current",
        current_url="https://example.test/current",
        current_slug="Current Page",
        total=10,
        index=4,
        saved=3,
        skipped=0,
        failed=0,
    )

    frame = ANSI_RE.sub("", tui._draw_frame(80, 24, state))

    assert "Sitemap scrape" in frame
    assert "current: Current Page" in frame
    assert "FETCHING PAGE" in frame


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
