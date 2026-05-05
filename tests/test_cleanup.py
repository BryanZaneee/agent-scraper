"""Fixture-based regression tests for scrape.py cleanup helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrape import (  # noqa: E402
    extract_frontmatter,
    extract_main_element,
    format_frontmatter,
    html_to_markdown,
    url_to_title,
)

FIXTURES = Path(__file__).parent / "fixtures"


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
