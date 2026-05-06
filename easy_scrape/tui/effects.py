"""Weather effect primitives for the terminal UI."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..constants import MAX_RAIN_SPLASHES
from .terminal import TerminalCanvas

@dataclass
class TerminalRainDrop:
    x: float
    y: float
    speed_y: float
    speed_x: float
    character: str
    color: str
    z_index: int


@dataclass
class TerminalRainSplash:
    x: int
    y: int
    timer: int
    max_timer: int


@dataclass
class TerminalCloud:
    x: float
    y: int
    speed: float
    shape: list[str]
    color: str


@dataclass
class TerminalLightningBolt:
    segments: list[tuple[int, int, str]]
    age: int
    max_age: int



class TerminalRainSystem:
    """Port of weathr's raindrop/splash particle idea to this Python CLI.

    The important pieces borrowed from `weathr/src/animation/raindrops.rs` are
    width-scaled particle counts, wind-adjusted x velocity, and short-lived
    splash particles. Here, drops splash on the progress panel's top border so
    the animation visually hits the easy_scrape TUI instead of the terminal
    floor only.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self.drops: list[TerminalRainDrop] = []
        self.splashes: list[TerminalRainSplash] = []
        self.rng = rng or random.Random()
        self.wind_x = 0.08 if self.rng.random() > 0.5 else -0.08

    def _target_count(self, width: int) -> int:
        return max(18, int(width * 0.85))

    def _spawn_drop(self, width: int) -> None:
        x = self.rng.randrange(max(1, width * 2)) - (width * 0.5)
        z_index = 1 if self.rng.random() > 0.45 else 0
        if z_index == 1:
            chars = ["|", ":"]
            color = "white"
            speed_y = 0.85
        else:
            chars = [":", "."]
            color = "blue"
            speed_y = 0.52
        self.drops.append(
            TerminalRainDrop(
                x=x,
                y=0.0,
                speed_y=speed_y + self.rng.random() * 0.35,
                speed_x=self.wind_x + (self.rng.random() * 0.08 - 0.04),
                character=self.rng.choice(chars),
                color=color,
                z_index=z_index,
            )
        )

    def update(
        self,
        width: int,
        height: int,
        impact_rect: tuple[int, int, int] | None = None,
        *,
        speed: float = 1.0,
    ) -> None:
        if width <= 0 or height <= 1:
            self.drops.clear()
            self.splashes.clear()
            return

        target_count = self._target_count(width)
        spawn_rate = max(2, min(8, width // 16))
        for _ in range(spawn_rate):
            if len(self.drops) < target_count:
                self._spawn_drop(width)

        next_drops: list[TerminalRainDrop] = []
        left = right = impact_y = None
        if impact_rect is not None:
            left, right, impact_y = impact_rect

        for drop in self.drops:
            drop.y += drop.speed_y * speed
            drop.x += drop.speed_x * speed

            hit_panel = (
                left is not None
                and right is not None
                and impact_y is not None
                and left <= int(drop.x) <= right
                and drop.y >= impact_y
            )
            hit_floor = drop.y >= height - 1
            out_of_bounds = drop.x < -10 or drop.x > width + 10

            if hit_panel or hit_floor or out_of_bounds:
                if (hit_panel or hit_floor) and drop.z_index == 1 and self.rng.random() < 0.7:
                    splash_y = impact_y if hit_panel and impact_y is not None else height - 1
                    self.splashes.append(
                        TerminalRainSplash(
                            x=max(0, min(width - 1, int(drop.x))),
                            y=max(0, min(height - 1, int(splash_y))),
                            timer=0,
                            max_timer=7,
                        )
                    )
                continue
            next_drops.append(drop)

        self.drops = next_drops
        self.splashes = self.splashes[-MAX_RAIN_SPLASHES:]
        live_splashes: list[TerminalRainSplash] = []
        for splash in self.splashes:
            splash.timer += 1
            if splash.timer < splash.max_timer:
                live_splashes.append(splash)
        self.splashes = live_splashes

    def render_drops(self, canvas: TerminalCanvas) -> None:
        for drop in self.drops:
            x = int(drop.x)
            y = int(drop.y)
            if 0 <= x < canvas.width and 0 <= y < canvas.height:
                ch = (
                    "\\"
                    if drop.speed_x > 0.18
                    else "/"
                    if drop.speed_x < -0.18
                    else drop.character
                )
                canvas.set(x, y, ch, drop.color)

    def render_splashes(self, canvas: TerminalCanvas) -> None:
        for splash in self.splashes:
            ch = "." if splash.timer <= 2 else "o" if splash.timer <= 4 else "O"
            canvas.set(splash.x, splash.y, ch, "cyan")


class TerminalCloudSystem:
    """Small drifting background layer adapted from weathr's cloud system."""

    SHAPES = [
        ["   .--.   ", " .-(    ).", "(___.__)_)"],
        ["      _  _   ", "    ( `   )_ ", "   (    )   `)"],
        ["     .--.    ", "  .-(    ).  ", " (___.__)__) "],
    ]

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.clouds: list[TerminalCloud] = []

    def _spawn_cloud(self, width: int, height: int, *, random_x: bool = False) -> None:
        shape = [line.rstrip() for line in self.rng.choice(self.SHAPES)]
        x = self.rng.randrange(max(1, width)) if random_x else -max(len(shape[0]), 8)
        y_limit = max(2, min(6, height // 3))
        self.clouds.append(
            TerminalCloud(
                x=float(x),
                y=self.rng.randrange(0, y_limit),
                speed=0.035 + self.rng.random() * 0.05,
                shape=shape,
                color="dim",
            )
        )

    def update(self, width: int, height: int, *, speed: float = 1.0) -> None:
        if width <= 0 or height <= 0:
            self.clouds.clear()
            return

        if not self.clouds:
            for _ in range(max(1, width // 34)):
                self._spawn_cloud(width, height, random_x=True)

        for cloud in self.clouds:
            cloud.x += cloud.speed * speed

        self.clouds = [c for c in self.clouds if c.x < width + 4]
        max_clouds = max(1, width // 30)
        if len(self.clouds) < max_clouds and self.rng.random() < 0.035:
            self._spawn_cloud(width, height)

    def render(self, canvas: TerminalCanvas) -> None:
        for cloud in self.clouds:
            for row_offset, line in enumerate(cloud.shape):
                y = cloud.y + row_offset
                x = int(cloud.x)
                if y < 0 or y >= canvas.height:
                    continue
                for col_offset, ch in enumerate(line):
                    if ch != " ":
                        canvas.set(x + col_offset, y, ch, cloud.color)


class TerminalStormSystem:
    """Rare lightning effect, modeled after weathr's thunderstorm state machine."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.bolts: list[TerminalLightningBolt] = []
        self.timer = 0
        self.next_strike_in = 90 + self.rng.randrange(120)
        self.flash_timer = 0
        self.flash_active = False

    def _generate_bolt(self, width: int, height: int) -> None:
        if width < 12 or height < 8:
            return
        x = self.rng.randrange(5, max(6, width - 5))
        y = 1
        segments: list[tuple[int, int, str]] = [(x, y, "+")]
        max_y = max(4, min(height - 5, height // 2 + 3))

        while y < max_y:
            direction = self.rng.choice([-1, 0, 1])
            x = max(2, min(width - 3, x + direction))
            y += 1
            ch = "/" if direction < 0 else "\\" if direction > 0 else "|"
            segments.append((x, y, ch))

            if self.rng.random() < 0.18:
                branch_x = x
                branch_y = y
                branch_direction = -1 if direction >= 0 else 1
                for _ in range(2):
                    branch_x = max(1, min(width - 2, branch_x + branch_direction))
                    branch_y += 1
                    if branch_y < height - 2:
                        segments.append(
                            (
                                branch_x,
                                branch_y,
                                "/" if branch_direction < 0 else "\\",
                            )
                        )

        self.bolts.append(TerminalLightningBolt(segments=segments, age=0, max_age=14))
        self.bolts = self.bolts[-3:]
        self.flash_active = True
        self.flash_timer = 3

    def update(
        self,
        width: int,
        height: int,
        *,
        active_fetch: bool = False,
        failed_count: int = 0,
        speed: float = 1.0,
    ) -> None:
        if width <= 0 or height <= 0:
            self.bolts.clear()
            return

        if self.flash_timer > 0:
            self.flash_timer -= 1
            self.flash_active = True
        else:
            self.flash_active = False

        live_bolts: list[TerminalLightningBolt] = []
        for bolt in self.bolts:
            bolt.age += 1
            if bolt.age < bolt.max_age:
                live_bolts.append(bolt)
        self.bolts = live_bolts

        self.timer += max(1, int(speed))
        failure_pressure = min(40, failed_count * 8)
        active_bonus = 25 if active_fetch else 0
        if self.timer + failure_pressure + active_bonus >= self.next_strike_in:
            self._generate_bolt(width, height)
            self.timer = 0
            self.next_strike_in = 120 + self.rng.randrange(220)

    def render(self, canvas: TerminalCanvas) -> None:
        color = "white" if self.flash_active else "yellow"
        for bolt in self.bolts:
            for x, y, ch in bolt.segments:
                canvas.set(x, y, ch, color)

