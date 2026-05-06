"""Interactive post-run browser for output-folder Markdown stats."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from ..constants import TUI_FRAME_SECONDS
from ..stats import (
    MarkdownFileStats,
    MarkdownFolderStats,
    collect_markdown_folder_stats,
    format_file_size,
    format_int,
)
from .picker import display_path
from .terminal import ANSI_HOME, KeyReader, TerminalCanvas, TerminalSession, fit_text


def _visible_range(cursor: int, row_count: int, visible_count: int) -> range:
    visible_count = max(1, visible_count)
    half = visible_count // 2
    start = max(0, min(cursor - half, row_count - visible_count))
    end = min(row_count, start + visible_count)
    return range(start, end)


def _relative_file_label(
    file_stats: MarkdownFileStats, folder: MarkdownFolderStats
) -> str:
    try:
        return str(file_stats.path.resolve().relative_to(folder.path.resolve()))
    except ValueError:
        return str(file_stats.path)


def reveal_folder(path: Path) -> tuple[bool, str]:
    """Ask the OS to open a folder in the user's file manager."""
    if not path.exists():
        return False, "Folder no longer exists."

    if sys.platform == "darwin":
        command = ["open", str(path)]
    elif os.name == "nt":
        command = ["cmd", "/c", "start", "", str(path)]
    else:
        command = ["xdg-open", str(path)]

    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return False, str(exc)
    return True, f"Opened {display_path(path)}."


@dataclass
class MarkdownStatsBrowserState:
    """Navigation state for the post-run folder stats browser."""

    root: Path
    folders: list[MarkdownFolderStats] = field(default_factory=list)
    folder_cursor: int = 0
    file_cursor: int = 0
    view: str = "folders"
    cancelled: bool = False
    message: str = ""

    def refresh(self) -> None:
        previous_path = self.selected_folder.path if self.selected_folder else None
        self.folders = collect_markdown_folder_stats(self.root)
        if previous_path is not None:
            for index, folder in enumerate(self.folders):
                if folder.path == previous_path:
                    self.folder_cursor = index
                    break
        self.folder_cursor = max(0, min(self.folder_cursor, len(self.folders) - 1))
        self.file_cursor = max(0, min(self.file_cursor, len(self.selected_files) - 1))
        if not self.folders:
            self.view = "folders"
            self.file_cursor = 0

    @property
    def selected_folder(self) -> MarkdownFolderStats | None:
        if not self.folders:
            return None
        return self.folders[self.folder_cursor]

    @property
    def selected_files(self) -> list[MarkdownFileStats]:
        folder = self.selected_folder
        return folder.stats.files if folder is not None else []

    @property
    def selected_file(self) -> MarkdownFileStats | None:
        files = self.selected_files
        if not files:
            return None
        return files[self.file_cursor]

    def handle_key(self, key: str) -> None:
        if key in ("q", "Q", "escape"):
            self.cancelled = True
            return
        if key in ("r", "R"):
            self.refresh()
            self.message = "Stats refreshed."
            return
        if self.view == "files":
            self._handle_file_key(key)
        else:
            self._handle_folder_key(key)

    def _handle_folder_key(self, key: str) -> None:
        if not self.folders:
            return
        if key in ("up", "k", "K"):
            self.folder_cursor = max(0, self.folder_cursor - 1)
            self.file_cursor = 0
            self.message = ""
        elif key in ("down", "j", "J"):
            self.folder_cursor = min(len(self.folders) - 1, self.folder_cursor + 1)
            self.file_cursor = 0
            self.message = ""
        elif key in ("enter", "right", "l", "L"):
            if self.selected_folder is not None:
                self.view = "files"
                self.file_cursor = 0
                self.message = ""

    def _handle_file_key(self, key: str) -> None:
        if key in ("left", "backspace", "h", "H"):
            self.view = "folders"
            self.message = ""
        elif key in ("up", "k", "K"):
            self.file_cursor = max(0, self.file_cursor - 1)
            self.message = ""
        elif key in ("down", "j", "J"):
            self.file_cursor = min(len(self.selected_files) - 1, self.file_cursor + 1)
            self.message = ""


class MarkdownStatsBrowserTui:
    """Blocking terminal UI for browsing generated Markdown folder stats."""

    def __init__(
        self,
        root: Path,
        *,
        stream: TextIO | None = None,
        input_stream: TextIO | None = None,
    ) -> None:
        self.root = root
        self.stream = stream or sys.stdout
        self.input_stream = input_stream or sys.stdin
        self.state = MarkdownStatsBrowserState(root=root)
        self._terminal: TerminalSession | None = None
        self._key_reader: KeyReader | None = None

    def is_available(self) -> bool:
        return bool(self.stream.isatty() and self.input_stream.isatty())

    def __enter__(self) -> "MarkdownStatsBrowserTui":
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

    def run(self) -> None:
        self.state.refresh()
        while not self.state.cancelled:
            self._render()
            key = self._read_key(TUI_FRAME_SECONDS)
            if key is None:
                continue
            if key in ("o", "O"):
                self._reveal_selected_folder()
            else:
                self.state.handle_key(key)

    def _write(self, text: str) -> None:
        if self._terminal is not None:
            self._terminal.write(text)
        else:
            self.stream.write(text)
            self.stream.flush()

    def _read_key(self, timeout: float | None = None) -> str | None:
        if self._key_reader is None:
            return None
        key = self._key_reader.read_key(timeout)
        if key == "\x03":
            raise KeyboardInterrupt
        return key

    def _reveal_selected_folder(self) -> None:
        folder = self.state.selected_folder
        if folder is None:
            self.state.message = "No output folder selected."
            return
        _success, message = reveal_folder(folder.path)
        self.state.message = message

    def _render(self) -> None:
        size = shutil.get_terminal_size((90, 28))
        width = max(60, size.columns)
        height = max(18, size.lines)
        frame = self._draw_frame(width, height, self.state)
        self._write(ANSI_HOME + frame)

    def _draw_frame(
        self,
        width: int,
        height: int,
        state: MarkdownStatsBrowserState,
    ) -> str:
        canvas = TerminalCanvas(width, height)
        canvas.text(2, 0, "easy_scrape folder stats", "cyan")
        canvas.text(
            2,
            1,
            fit_text(f"out: {display_path(state.root)}", width - 4),
            "dim",
        )

        top = 3
        bottom = height - 3
        panel_height = max(8, bottom - top + 1)
        left_width = min(max(28, width // 3), 42)
        right_left = left_width + 3
        right_width = max(24, width - right_left - 2)

        self._draw_folder_panel(canvas, state, 1, top, left_width, panel_height)
        self._draw_detail_panel(
            canvas,
            state,
            right_left,
            top,
            right_width,
            panel_height,
        )

        if state.message:
            canvas.text(2, height - 2, fit_text(state.message, width - 4), "yellow")
        footer = (
            "up/down folders | enter/right contents | o reveal folder | r refresh | q done"
            if state.view == "folders"
            else "up/down files | left/backspace folders | o reveal folder | r refresh | q done"
        )
        canvas.text(2, height - 1, fit_text(footer, width - 4), "dim")
        return canvas.render()

    def _draw_folder_panel(
        self,
        canvas: TerminalCanvas,
        state: MarkdownStatsBrowserState,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        canvas.box(left, top, width, height, "cyan")
        canvas.text(left + 2, top, " Folders ", "white")
        if not state.folders:
            canvas.text(
                left + 2,
                top + 2,
                fit_text("No Markdown folders found.", width - 4),
                "dim",
            )
            return

        visible_count = max(1, height - 3)
        for row, index in enumerate(
            _visible_range(state.folder_cursor, len(state.folders), visible_count)
        ):
            folder = state.folders[index]
            cursor = ">" if index == state.folder_cursor else " "
            color = "white" if index == state.folder_cursor else "dim"
            label = (
                f"{cursor} {folder.label}  "
                f"{format_int(folder.stats.estimated_tokens)} tok"
            )
            canvas.text(left + 2, top + 2 + row, fit_text(label, width - 4), color)

    def _draw_detail_panel(
        self,
        canvas: TerminalCanvas,
        state: MarkdownStatsBrowserState,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        canvas.box(left, top, width, height, "cyan")
        title = " Contents " if state.view == "files" else " Folder Stats "
        canvas.text(left + 2, top, title, "white")
        folder = state.selected_folder
        if folder is None:
            lines = [
                "No Markdown files were found under this output directory.",
                "",
                "Run a scrape first, or choose a different --out path.",
            ]
            self._draw_lines(canvas, left, top, width, height, lines)
            return

        if state.view == "files":
            self._draw_file_view(canvas, state, left, top, width, height, folder)
        else:
            self._draw_folder_view(canvas, left, top, width, height, folder)

    def _draw_folder_view(
        self,
        canvas: TerminalCanvas,
        left: int,
        top: int,
        width: int,
        height: int,
        folder: MarkdownFolderStats,
    ) -> None:
        stats = folder.stats
        lines = [
            f"Folder: {folder.label}",
            f"Path: {display_path(folder.path)}",
            "",
            f"Markdown files: {format_int(stats.file_count)}",
            (
                "File size: "
                f"{format_file_size(stats.bytes)} ({format_int(stats.bytes)} bytes)"
            ),
            f"Characters: {format_int(stats.chars)}",
            f"Words: {format_int(stats.words)}",
            f"Estimated tokens: {format_int(stats.estimated_tokens)}",
            "",
            "Largest files:",
        ]
        largest = sorted(
            stats.files,
            key=lambda file_stats: file_stats.estimated_tokens,
            reverse=True,
        )[: max(0, height - len(lines) - 4)]
        for file_stats in largest:
            label = _relative_file_label(file_stats, folder)
            lines.append(
                f"{format_int(file_stats.estimated_tokens)} tok  "
                f"{format_file_size(file_stats.bytes)}  {label}"
            )
        self._draw_lines(canvas, left, top, width, height, lines)

    def _draw_file_view(
        self,
        canvas: TerminalCanvas,
        state: MarkdownStatsBrowserState,
        left: int,
        top: int,
        width: int,
        height: int,
        folder: MarkdownFolderStats,
    ) -> None:
        files = state.selected_files
        selected = state.selected_file
        lines = [
            f"Folder: {folder.label}",
            f"Files: {format_int(len(files))}",
        ]
        if selected is not None:
            lines.extend(
                [
                    f"Selected: {_relative_file_label(selected, folder)}",
                    f"File size: {format_file_size(selected.bytes)}",
                    f"Tokens: {format_int(selected.estimated_tokens)}",
                    "",
                ]
            )
        else:
            lines.append("")

        body_start = top + 2 + len(lines)
        max_rows = max(1, top + height - 1 - body_start)
        self._draw_lines(canvas, left, top, width, height, lines)
        for row, index in enumerate(
            _visible_range(state.file_cursor, len(files), max_rows)
        ):
            file_stats = files[index]
            cursor = ">" if index == state.file_cursor else " "
            color = "white" if index == state.file_cursor else "dim"
            label = (
                f"{cursor} {_relative_file_label(file_stats, folder)}  "
                f"{format_int(file_stats.estimated_tokens)} tok  "
                f"{format_file_size(file_stats.bytes)}"
            )
            canvas.text(left + 2, body_start + row, fit_text(label, width - 4), color)

    def _draw_lines(
        self,
        canvas: TerminalCanvas,
        left: int,
        top: int,
        width: int,
        height: int,
        lines: list[str],
    ) -> None:
        max_lines = max(1, height - 3)
        for offset, line in enumerate(lines[:max_lines]):
            color = "white" if offset == 0 else "dim"
            canvas.text(left + 2, top + 2 + offset, fit_text(line, width - 4), color)
