"""Animated progress dashboard for scrape runs."""

from __future__ import annotations

import os
import signal
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from ..constants import (
    MAX_RECENT_EVENTS,
    TUI_FRAME_SECONDS,
    TUI_MIN_HEIGHT,
    TUI_MIN_WIDTH,
)
from .effects import TerminalCloudSystem, TerminalLightningBolt, TerminalRainSystem, TerminalStormSystem
from .terminal import ANSI_CLEAR, ANSI_HOME, KeyReader, TerminalCanvas, TerminalSession, fit_text

@dataclass
class ScrapeTuiState:
    title: str = "easy_scrape"
    mode: str = "starting"
    stage: str = "warming up"
    output_path: str = ""
    detail: str = ""
    current_url: str = ""
    current_slug: str = ""
    last_result: str = ""
    total: int = 0
    index: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: float = field(default_factory=time.monotonic)
    recent_events: list[str] = field(default_factory=list)


def progress_bar(done: int, total: int, width: int) -> str:
    if width <= 2:
        return ""
    if total <= 0:
        return "[" + "." * (width - 2) + "]"
    ratio = max(0.0, min(1.0, done / total))
    fill = int((width - 2) * ratio)
    return "[" + "#" * fill + "." * (width - 2 - fill) + "]"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}m"
    return f"{minutes:02d}:{secs:02d}"


def percent_done(done: int, total: int) -> str:
    if total <= 0:
        return "--%"
    return f"{min(100.0, max(0.0, done / total * 100)):5.1f}%"


def url_host(url: str) -> str:
    if not url:
        return ""
    return urlparse(url).netloc or url


class ScrapeRainTui:
    """Optional animated dashboard. Inert when not attached to a TTY."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        stream: TextIO | None = None,
        input_stream: TextIO | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.input_stream = input_stream or sys.stdin
        self.active = bool(enabled and self.stream.isatty())
        self._state = ScrapeTuiState()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._rain = TerminalRainSystem()
        self._clouds = TerminalCloudSystem()
        self._storm = TerminalStormSystem()
        self._last_size: tuple[int, int] | None = None
        self._entered = False
        self._paused = False
        self._show_help = False
        self._plain_requested = False
        self._render_thread: threading.Thread | None = None
        self._input_thread: threading.Thread | None = None
        self._terminal: TerminalSession | None = None
        self._key_reader: KeyReader | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def show_help(self) -> bool:
        return self._show_help

    @property
    def plain_requested(self) -> bool:
        return self._plain_requested

    def __enter__(self) -> "ScrapeRainTui":
        if not self.active:
            return self
        size = shutil.get_terminal_size((80, 24))
        if size.columns < TUI_MIN_WIDTH or size.lines < TUI_MIN_HEIGHT:
            self.active = False
            return self

        try:
            self._terminal = TerminalSession(
                stream=self.stream,
                input_stream=self.input_stream,
                enable_input=self.input_stream.isatty(),
            )
            with self._io_lock:
                self._terminal.enter()
            self._key_reader = KeyReader(self._terminal)
            self._entered = True
            self._render_thread = threading.Thread(
                target=self._render_loop,
                daemon=True,
                name="easy-scrape-tui-render",
            )
            self._render_thread.start()
            if self._terminal.has_input:
                self._input_thread = threading.Thread(
                    target=self._input_loop,
                    daemon=True,
                    name="easy-scrape-tui-input",
                )
                self._input_thread.start()
        except Exception:
            self._disable_to_plain_logs(cleanup=True)
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self._stop.set()
        self._join_threads()
        if self._terminal is not None:
            with self._io_lock:
                self._terminal.restore()
        self._terminal = None
        self._key_reader = None
        self._entered = False
        self.active = False

    def _join_threads(self) -> None:
        current = threading.current_thread()
        for thread in (self._input_thread, self._render_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)

    def _disable_to_plain_logs(self, *, cleanup: bool) -> None:
        self._plain_requested = True
        self.active = False
        self._stop.set()
        if cleanup and self._entered and self._terminal is not None:
            with self._io_lock:
                self._terminal.restore()
            self._entered = False

    def handle_key(self, key: str) -> None:
        """Handle one keypress; public for focused unit tests."""
        if key in ("?", "h", "H"):
            self._show_help = not self._show_help
        elif key in ("p", "P"):
            self._paused = not self._paused
        elif key in ("q", "Q"):
            self._disable_to_plain_logs(cleanup=True)
        elif key == "\x03":
            os.kill(os.getpid(), signal.SIGINT)

    def _input_loop(self) -> None:
        while (
            not self._stop.is_set()
            and self._key_reader is not None
            and self._terminal is not None
            and self._terminal.has_input
        ):
            try:
                key = self._key_reader.read_key(0.05)
                if key:
                    self.handle_key(key)
            except Exception:
                return

    def set_stage(
        self,
        title: str | None = None,
        detail: str = "",
        stage: str = "",
        *,
        mode: str | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            if title is not None:
                self._state.title = title or "easy_scrape"
            if mode is not None:
                self._state.mode = mode
            if output_path is not None:
                self._state.output_path = str(output_path)
            if detail:
                self._state.detail = detail
            if stage:
                self._state.stage = stage

    def update_stage(self, stage: str, detail: str = "") -> None:
        if not self.active:
            return
        with self._lock:
            self._state.stage = stage
            if detail:
                self._state.detail = detail

    def start_batch(
        self,
        title: str,
        subtitle: str,
        total: int,
        *,
        mode: str | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.title = title
            self._state.detail = subtitle
            self._state.stage = "ready"
            self._state.mode = mode or self._state.mode
            if output_path is not None:
                self._state.output_path = str(output_path)
            self._state.current_url = ""
            self._state.current_slug = ""
            self._state.last_result = ""
            self._state.total = total
            self._state.index = 0
            self._state.saved = 0
            self._state.skipped = 0
            self._state.failed = 0
            self._state.started_at = time.monotonic()
            self._state.recent_events = []

    def start_page(
        self,
        index: int,
        total: int,
        url: str,
        slug: str,
        *,
        saved: int,
        skipped: int,
        failed: int,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.index = index
            self._state.total = total
            self._state.current_url = url
            self._state.current_slug = slug
            self._state.stage = "fetching page"
            self._state.detail = url
            self._state.saved = saved
            self._state.skipped = skipped
            self._state.failed = failed

    def finish_page(
        self,
        result: str,
        slug: str,
        *,
        saved: int,
        skipped: int,
        failed: int,
    ) -> None:
        if not self.active:
            return
        with self._lock:
            self._state.stage = result
            self._state.last_result = result
            self._state.saved = saved
            self._state.skipped = skipped
            self._state.failed = failed
            event = f"{result.upper():7} {slug}"
            self._state.recent_events.append(event)
            self._state.recent_events = self._state.recent_events[-MAX_RECENT_EVENTS:]

    def snapshot(self) -> ScrapeTuiState:
        with self._lock:
            return ScrapeTuiState(
                title=self._state.title,
                mode=self._state.mode,
                stage=self._state.stage,
                output_path=self._state.output_path,
                detail=self._state.detail,
                current_url=self._state.current_url,
                current_slug=self._state.current_slug,
                last_result=self._state.last_result,
                total=self._state.total,
                index=self._state.index,
                saved=self._state.saved,
                skipped=self._state.skipped,
                failed=self._state.failed,
                started_at=self._state.started_at,
                recent_events=list(self._state.recent_events),
            )

    def _render_loop(self) -> None:
        while not self._stop.is_set():
            try:
                width, height = shutil.get_terminal_size((80, 24))
                frame = self._draw_frame(width, height, self.snapshot())
                prefix = ANSI_HOME
                if self._last_size != (width, height):
                    prefix += ANSI_CLEAR
                    self._last_size = (width, height)
                with self._io_lock:
                    self.stream.write(prefix + frame)
                    self.stream.flush()
            except Exception:
                self._disable_to_plain_logs(cleanup=True)
                break
            time.sleep(TUI_FRAME_SECONDS)

    def _draw_frame(self, width: int, height: int, state: ScrapeTuiState) -> str:
        canvas = TerminalCanvas(width, height)
        speed = 0.0 if self._paused else 1.0
        active_fetch = "fetch" in state.stage or "discover" in state.stage

        panel_width = min(max(TUI_MIN_WIDTH - 4, width - 6), 100)
        panel_height = min(max(12, height - 7), 16)
        left = max(2, (width - panel_width) // 2)
        top = max(3, (height - panel_height) // 2)
        right = left + panel_width - 1
        bottom = top + panel_height - 1
        border_color = "white" if self._storm.flash_active else "cyan"

        if not self._paused:
            self._clouds.update(width, height, speed=0.9)
            self._rain.update(width, height, (left, right, top), speed=1.25)
            self._storm.update(
                width,
                height,
                active_fetch=active_fetch,
                failed_count=state.failed,
                speed=1.0,
            )

        self._clouds.render(canvas)
        self._storm.render(canvas)
        self._rain.render_drops(canvas)
        self._draw_ground(canvas, width, height)
        self._draw_shell(canvas, width, height, state, left, top, panel_width, panel_height, border_color)
        self._rain.render_splashes(canvas)
        self._draw_dashboard_text(canvas, state, left, top, panel_width, panel_height)
        self._draw_footer(canvas, width, height)
        if self._show_help:
            self._draw_help(canvas, width, height)
        return canvas.render()

    def _draw_ground(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        if height < 3:
            return
        ground_y = height - 2
        for x in range(width):
            canvas.set(x, ground_y, "~" if x % 2 else "_", "dim")

    def _draw_shell(
        self,
        canvas: TerminalCanvas,
        width: int,
        height: int,
        state: ScrapeTuiState,
        left: int,
        top: int,
        panel_width: int,
        panel_height: int,
        border_color: str,
    ) -> None:
        canvas.text(2, 0, "easy_scrape", "cyan")
        header = f"mode: {state.mode} | stage: {state.stage}"
        canvas.text(max(14, width - len(header) - 2), 0, fit_text(header, width - 16), "white")
        if state.output_path:
            canvas.text(2, 1, fit_text(f"out: {state.output_path}", width - 4), "dim")
        canvas.fill_rect(left + 1, top + 1, panel_width - 2, panel_height - 2)
        canvas.box(left, top, panel_width, panel_height, border_color)

    def _draw_dashboard_text(
        self,
        canvas: TerminalCanvas,
        state: ScrapeTuiState,
        left: int,
        top: int,
        panel_width: int,
        panel_height: int,
    ) -> None:
        inner_x = left + 3
        inner_width = max(1, panel_width - 6)
        done = state.saved + state.skipped + state.failed
        elapsed = time.monotonic() - state.started_at
        rate = done / elapsed * 60 if elapsed > 0 and done > 0 else 0.0
        host = url_host(state.current_url)

        title = f" {state.title} "
        canvas.text(left + max(2, (panel_width - len(title)) // 2), top, title, "white")
        y = top + 2
        canvas.text(
            inner_x,
            y,
            fit_text(
                f"{state.stage.upper()}  {state.index}/{state.total}  {percent_done(done, state.total)}",
                inner_width,
            ),
            "white",
        )
        y += 1
        canvas.text(inner_x, y, progress_bar(done, state.total, inner_width), "green")
        y += 2
        canvas.text(
            inner_x,
            y,
            fit_text(
                f"saved {state.saved}  skipped {state.skipped}  failed {state.failed}",
                inner_width,
            ),
            "yellow" if state.failed == 0 else "red",
        )
        y += 1
        timing = f"elapsed {format_duration(elapsed)}  rate {rate:0.1f} pages/min"
        canvas.text(inner_x, y, fit_text(timing, inner_width), "dim")
        y += 2
        current = state.current_slug or "waiting for first page"
        canvas.text(inner_x, y, fit_text(f"current: {current}", inner_width), "cyan")
        y += 1
        if host:
            canvas.text(inner_x, y, fit_text(f"host: {host}", inner_width), "dim")
            y += 1
        if state.detail and y < top + panel_height - 2:
            canvas.text(inner_x, y, fit_text(state.detail, inner_width), "dim")
            y += 1

        if state.recent_events and y < top + panel_height - 2:
            recent = "recent: " + " | ".join(state.recent_events[-2:])
            canvas.text(inner_x, y, fit_text(recent, inner_width), "dim")

    def _draw_footer(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        if height <= 0:
            return
        status = "animation paused" if self._paused else "rain/storm effects active"
        if self._plain_requested:
            status = "plain logs requested"
        footer = f"?/h help  p pause effects  q plain logs  Ctrl-C cancel | {status}"
        canvas.text(2, height - 1, fit_text(footer, width - 4), "dim")

    def _draw_help(self, canvas: TerminalCanvas, width: int, height: int) -> None:
        help_width = min(72, max(36, width - 4))
        help_height = 8
        left = max(2, (width - help_width) // 2)
        top = max(2, (height - help_height) // 2)
        canvas.fill_rect(left, top, help_width, help_height)
        canvas.box(left, top, help_width, help_height, "yellow")
        lines = [
            " easy_scrape controls ",
            "? or h  toggle this help",
            "p       pause/resume weather effects only",
            "q       leave TUI and continue with plain logs",
            "Ctrl-C  cancel the scrape",
            "Generated files and scraper behavior are unchanged.",
        ]
        for offset, line in enumerate(lines):
            color = "white" if offset == 0 else "dim"
            canvas.text(left + 2, top + 1 + offset, fit_text(line, help_width - 4), color)

