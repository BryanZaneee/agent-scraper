"""Interactive pre-scrape source/category/output picker."""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from ..constants import (
    BONFIRE_ASCII,
    CATEGORY_PICKER_ALL_LABEL,
    DEFAULT_BASE_URL,
    DEFAULT_OUTPUT_DIR,
    EASY_SCRAPE_BANNER,
    TUI_FRAME_SECONDS,
)
from .effects import TerminalCloudSystem, TerminalRainSystem
from .terminal import ANSI_HOME, KeyReader, TerminalCanvas, TerminalSession, fit_text

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
        if self.option_count:
            self.cursor = max(0, min(self.cursor, self.option_count - 1))
        else:
            self.cursor = 0

    @property
    def option_count(self) -> int:
        return len(self.categories) + (1 if self.categories else 0)

    @property
    def all_selected(self) -> bool:
        return bool(self.categories) and set(self.categories) <= self.selected

    def option_label(self, index: int) -> str:
        if index == 0 and self.categories:
            return CATEGORY_PICKER_ALL_LABEL
        return self.categories[index - 1]

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
            self.cursor = min(self.option_count - 1, self.cursor + 1)
        elif key in (" ", "space"):
            if self.cursor == 0:
                if self.all_selected:
                    self.selected.clear()
                    self.message = "Selection cleared."
                else:
                    self.selected = set(self.categories)
                    self.message = "All categories selected."
            else:
                current = self.categories[self.cursor - 1]
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
        self._terminal: TerminalSession | None = None
        self._key_reader: KeyReader | None = None
        self._rain = TerminalRainSystem()
        self._clouds = TerminalCloudSystem()

    def is_available(self) -> bool:
        return bool(self.stream.isatty() and self.input_stream.isatty())

    def __enter__(self) -> "InteractiveScrapeTui":
        self._terminal = TerminalSession(
            stream=self.stream,
            input_stream=self.input_stream,
            enable_input=True,
        )
        self._terminal.enter()
        self._key_reader = KeyReader(self._terminal)
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._terminal is not None:
            self._terminal.restore()
        self._terminal = None
        self._key_reader = None

    def _write(self, text: str) -> None:
        if self._terminal is not None:
            self._terminal.write(text)
        else:
            self.stream.write(text)
            self.stream.flush()

    def _read_char(self, timeout: float | None = None) -> str | None:
        if self._key_reader is None:
            return None
        return self._key_reader.read_char(timeout)

    def _read_key(self, timeout: float | None = None) -> str | None:
        if self._key_reader is None:
            return None
        key = self._key_reader.read_key(timeout)
        if key == "\x03":
            raise KeyboardInterrupt
        return key

    def _read_escape_sequence(self) -> str:
        if self._key_reader is None:
            return "\x1b"
        return self._key_reader.read_escape_sequence()

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
        start = max(0, min(state.cursor - half, state.option_count - visible_count))
        end = min(state.option_count, start + visible_count)

        body = [
            f"Source: {base_url}",
            f"Selected: {len(state.selected)} / {len(state.categories)}",
            "",
        ]
        for idx in range(start, end):
            name = state.option_label(idx)
            cursor = ">" if idx == state.cursor else " "
            if idx == 0:
                mark = "x" if state.all_selected else " "
            else:
                mark = "x" if name in state.selected else " "
            body.append(f"{cursor} [{mark}] {name}")
        if state.message:
            body.extend(["", state.message])

        self._render(
            "Select categories",
            body,
            "space toggle | a all | n none | enter scrape | q quit",
        )
