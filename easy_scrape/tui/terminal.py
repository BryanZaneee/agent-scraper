"""Shared terminal canvas, session, and key-reading primitives."""

from __future__ import annotations

import os
import re
import select
import termios
import tty
from typing import TextIO

from ..constants import (
    ANSI_ALT_SCREEN,
    ANSI_CLEAR,
    ANSI_COLORS,
    ANSI_DISABLE_MOUSE,
    ANSI_ENABLE_MOUSE,
    ANSI_HIDE_CURSOR,
    ANSI_HOME,
    ANSI_MAIN_SCREEN,
    ANSI_RESET,
    ANSI_SHOW_CURSOR,
    INTERACTIVE_KEY_POLL_SECONDS,
)

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



def fit_text(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = str(text)
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


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




class TerminalSession:
    """Own terminal mode, alternate screen, cursor, and mouse state."""

    def __init__(
        self,
        *,
        stream: TextIO,
        input_stream: TextIO,
        enable_input: bool = True,
        alternate_screen: bool = True,
        enable_mouse: bool = True,
    ) -> None:
        self.stream = stream
        self.input_stream = input_stream
        self.enable_input = enable_input
        self.alternate_screen = alternate_screen
        self.enable_mouse = enable_mouse
        self.stdin_fd: int | None = None
        self._stdin_attrs = None
        self.entered = False

    @property
    def has_input(self) -> bool:
        return self.stdin_fd is not None

    def enter(self) -> None:
        if self.enable_input:
            self._enable_input_controls()
        parts = []
        if self.alternate_screen:
            parts.append(ANSI_ALT_SCREEN)
        if self.enable_mouse:
            parts.append(ANSI_ENABLE_MOUSE)
        parts.extend([ANSI_HIDE_CURSOR, ANSI_CLEAR, ANSI_HOME])
        self.write("".join(parts))
        self.entered = True

    def restore(self) -> None:
        self._restore_input_controls()
        if not self.entered:
            return
        parts = [ANSI_RESET, ANSI_CLEAR, ANSI_HOME]
        if self.enable_mouse:
            parts.append(ANSI_DISABLE_MOUSE)
        parts.append(ANSI_SHOW_CURSOR)
        if self.alternate_screen:
            parts.append(ANSI_MAIN_SCREEN)
        self.write("".join(parts))
        self.entered = False

    def write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def read_char(self, timeout: float | None = None) -> str | None:
        if timeout is not None:
            target = self.stdin_fd if self.stdin_fd is not None else self.input_stream
            readable, _, _ = select.select([target], [], [], timeout)
            if not readable:
                return None

        if self.stdin_fd is not None:
            data = os.read(self.stdin_fd, 1)
            if not data:
                return None
            return data.decode(errors="ignore")

        ch = self.input_stream.read(1)
        return ch or None

    def _enable_input_controls(self) -> None:
        if not self.input_stream.isatty():
            return
        try:
            self.stdin_fd = self.input_stream.fileno()
            self._stdin_attrs = termios.tcgetattr(self.stdin_fd)
            tty.setcbreak(self.stdin_fd)
        except Exception:
            self.stdin_fd = None
            self._stdin_attrs = None

    def _restore_input_controls(self) -> None:
        if self.stdin_fd is None or self._stdin_attrs is None:
            return
        try:
            termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self._stdin_attrs)
        except Exception:
            pass
        self.stdin_fd = None
        self._stdin_attrs = None


class KeyReader:
    """Read semantic keys from a TerminalSession."""

    def __init__(
        self,
        session: TerminalSession,
        *,
        escape_poll_seconds: float = INTERACTIVE_KEY_POLL_SECONDS,
    ) -> None:
        self.session = session
        self.escape_poll_seconds = escape_poll_seconds

    def read_char(self, timeout: float | None = None) -> str | None:
        return self.session.read_char(timeout)

    def read_key(self, timeout: float | None = None) -> str | None:
        ch = self.read_char(timeout)
        if ch is None:
            return None
        if ch == "\x1b":
            return key_name_from_sequence(self.read_escape_sequence())
        if ch in ("\n", "\r"):
            return "enter"
        if ch in ("\x7f", "\b"):
            return "backspace"
        if ch == " ":
            return "space"
        return ch

    def read_escape_sequence(self) -> str:
        sequence = "\x1b"
        introducer = self.read_char(self.escape_poll_seconds)
        if introducer is None:
            return sequence

        sequence += introducer
        if introducer == "[":
            for _ in range(32):
                next_ch = self.read_char(self.escape_poll_seconds)
                if next_ch is None:
                    break
                sequence += next_ch
                if sequence == "\x1b[M":
                    for _ in range(3):
                        mouse_ch = self.read_char(self.escape_poll_seconds)
                        if mouse_ch is None:
                            break
                        sequence += mouse_ch
                    break
                if "@" <= next_ch <= "~":
                    break
        elif introducer == "O":
            next_ch = self.read_char(self.escape_poll_seconds)
            if next_ch is not None:
                sequence += next_ch

        return sequence
