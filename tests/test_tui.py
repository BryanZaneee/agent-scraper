"""Tests for terminal picker, rendering, and progress helpers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrape import (  # noqa: E402
    CategoryPickerState,
    DEFAULT_OUTPUT_DIR,
    FolderBrowserState,
    InteractiveScrapeTui,
    MarkdownStatsBrowserState,
    ScrapeRainTui,
    ScrapeTuiState,
    TerminalLightningBolt,
    TerminalRainDrop,
    TerminalRainSystem,
    display_path,
    key_name_from_sequence,
    normalize_base_url,
    normalize_existing_output_dir,
    normalize_new_output_dir,
    normalize_output_dir,
    parse_args,
    progress_bar,
    strip_ansi,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

def test_progress_bar_formats_completed_work():
    assert progress_bar(2, 4, 10) == "[####....]"
    assert progress_bar(0, 0, 6) == "[....]"

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


def test_markdown_stats_browser_state_opens_folder_contents(tmp_path):
    weapons = tmp_path / "Weapons"
    armor = tmp_path / "Armor"
    weapons.mkdir()
    armor.mkdir()
    (weapons / "Sword.md").write_text("abcd efgh", encoding="utf-8")
    (weapons / "Axe.md").write_text("ijkl", encoding="utf-8")
    (armor / "Shield.md").write_text("mnop", encoding="utf-8")

    state = MarkdownStatsBrowserState(root=tmp_path)
    state.refresh()

    assert [folder.label for folder in state.folders] == ["Armor", "Weapons"]
    state.handle_key("down")
    assert state.selected_folder.path == weapons
    state.handle_key("enter")
    assert state.view == "files"
    assert state.selected_file.path.name == "Axe.md"
    state.handle_key("down")
    assert state.selected_file.path.name == "Sword.md"
    state.handle_key("left")
    assert state.view == "folders"


def test_category_picker_state_toggles_and_submits_selection():
    state = CategoryPickerState(["Weapons", "Armor", "Bosses"])

    assert state.option_label(0) == "All"
    state.handle_key("space")
    assert state.selected_categories() == ["Weapons", "Armor", "Bosses"]

    state.handle_key("space")
    assert state.selected_categories() == []

    state.handle_key("down")
    state.handle_key("down")
    state.handle_key("space")
    assert state.cursor == 2
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
