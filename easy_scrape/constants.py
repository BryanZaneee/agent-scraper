"""Shared constants for easy_scrape."""

from __future__ import annotations

import re
from pathlib import Path

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "identity",
}

DEFAULT_BASE_URL = "https://darksouls.wiki.fextralife.com"
DEFAULT_OUTPUT_DIR = Path.home() / "Desktop" / "easy_scrape_output"
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
CONTENT_SELECTOR = "#wiki-content-block"
SIDEBAR_SELECTORS = (".wiki-menu-2-left", ".sidebar-nav")
FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ASSET_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
CONTENT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
CONTENT_IMAGE_MIN_DIMENSION = 80
PRESERVED_IMAGE_ATTR = "data-easy-scrape-preserved-image"
TOKEN_CHARS_PER_TOKEN = 4
TUI_FRAME_SECONDS = 1 / 18
TUI_MIN_WIDTH = 60
TUI_MIN_HEIGHT = 18
MAX_RAIN_SPLASHES = 100
MAX_RECENT_EVENTS = 4
INTERACTIVE_KEY_POLL_SECONDS = 0.08
CATEGORY_PICKER_ALL_LABEL = "All"

ANSI_RESET = "\x1b[0m"
ANSI_CLEAR = "\x1b[2J"
ANSI_HOME = "\x1b[H"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"
ANSI_ALT_SCREEN = "\x1b[?1049h"
ANSI_MAIN_SCREEN = "\x1b[?1049l"
ANSI_ENABLE_MOUSE = "\x1b[?1000h\x1b[?1006h"
ANSI_DISABLE_MOUSE = "\x1b[?1006l\x1b[?1000l"

ANSI_COLORS = {
    "reset": "\x1b[0m",
    "dim": "\x1b[2m",
    "cyan": "\x1b[36m",
    "blue": "\x1b[34m",
    "white": "\x1b[37m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "magenta": "\x1b[35m",
}

EASY_SCRAPE_BANNER = [
    "  ___  __ _ ___ _   _ ___  ___ _ __ __ _ _ __   ___",
    " / _ \\/ _` / __| | | / __|/ __| '__/ _` | '_ \\ / _ \\",
    "|  __/ (_| \\__ \\ |_| \\__ \\ (__| | | (_| | |_) |  __/",
    " \\___|\\__,_|___/\\__, |___/\\___|_|  \\__,_| .__/ \\___|",
    "                |___/                   |_|",
]

BONFIRE_ASCII = [
    ("        /\\", "yellow"),
    ("        ||", "yellow"),
    ("     .-'||'-.", "dim"),
    ("        ||", "yellow"),
    ("     \\  ||  /", "red"),
    ("    .-\\ || /-.", "yellow"),
    ("   /__/####\\__\\", "red"),
    ("     _/====\\_", "dim"),
]

# Paths inside category hub pages that are not member content (sidebar nav,
# meta-pages, etc.). Member URLs containing any of these are dropped.
HUB_LINK_BLOCKLIST = (
    "/forum/",
    "/search/",
    "/edit/",
    "/Tags/",
    "/Special:",
    "/Recent+Changes",
    "/To+Do+List",
    "/Help",
)

# Sidebar entries that look like categories but aren't real knowledge content
# (zero/single-page meta hubs, scraper staging areas). Filtered out at sidebar
# discovery so they never reach the picker. Synonym/subset duplicates
# (Bonfires, PSN/Player IDs, Sorceries) are NOT listed here — the URL dedup
# in run_category_mode plus the empty-category skip handles those for free.
CATEGORY_DISCOVERY_BLOCKLIST = (
    "Comedy",
    "Build Calculator",
    "Chatroom",
    "Fan Art",
    "Community Events",
    "todo",
)

