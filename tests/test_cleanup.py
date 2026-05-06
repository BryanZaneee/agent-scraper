"""Fixture-based regression tests for cleanup and frontmatter behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrape import (  # noqa: E402
    ImageAssetContext,
    _clean_label,
    asset_filename_from_url,
    drop_empty_sections,
    drop_external_image_placeholders,
    extract_frontmatter,
    extract_main_element,
    format_frontmatter,
    html_to_markdown,
    strip_wip_tokens,
    url_to_title,
)

FIXTURES = Path(__file__).parent / "fixtures"
DRAKE = ("drake-sword.html", "https://darksouls.wiki.fextralife.com/Drake+Sword", "Weapons")
BELL = ("bell-gargoyle.html", "https://darksouls.wiki.fextralife.com/Bell+Gargoyle", "Bosses")
EMBERS = ("embers.html", "https://darksouls.wiki.fextralife.com/Embers", "Items")
ASYLUM = ("asylum-demon.html", "https://darksouls.wiki.fextralife.com/Asylum+Demon", "Bosses")
MAPS_URL = "https://darksouls.wiki.fextralife.com/Maps"

def render(fixture: str, url: str, category: str | None = None) -> tuple[str, dict]:
    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    el = extract_main_element(html, url, clean=True)
    assert el is not None, f"no #wiki-content-block in {fixture}"
    title = url_to_title(url)
    md = html_to_markdown(str(el), title=title, clean=True)
    fm = extract_frontmatter(el, url=url, title=title, category=category)
    body = f"{format_frontmatter(fm)}\n\n# {title}\n\n{md}\n"
    return body, fm

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

def test_clean_label_strips_alt_text_leak_words():
    # Banner-style alt-text like "Sens Fortress walkthrough wiki guide"
    # collapses to just the page name, killing the H1 alt-text leak pattern.
    assert _clean_label("Sens Fortress walkthrough wiki guide") == "Sens Fortress"
    # File-extension residue from icon images is stripped.
    cleaned = _clean_label("prot phy png")
    assert "png" not in cleaned.lower()
    # Legitimate stat-icon labels are untouched.
    assert _clean_label("Physical Defense") == "Physical Defense"
    assert _clean_label("Strength") == "Strength"
    assert _clean_label("Stability") == "Stability"


def test_replace_images_with_alt_dedupes_stuttered_heading():
    # Catalog/hub page context: an NPC sub-section heading has both an alt
    # text image and the rendered name. We must not stutter the heading.
    html = (
        '<div id="wiki-content-block">'
        "<h1>Merchants</h1>"
        '<h2><img alt="griggs of vinheim"/>Griggs of Vinheim</h2>'
        "<p>Sells sorceries.</p>"
        "</div>"
    )
    el = extract_main_element(html, "https://x.test/Merchants", clean=True)
    assert el is not None
    md = html_to_markdown(str(el), title="Merchants", clean=True)
    assert "## Griggs of Vinheim" in md
    assert "griggs of vinheim Griggs of Vinheim" not in md.lower()


def test_drop_empty_sections_removes_stub_walkthrough_header():
    md = (
        "## Strategy\n\n"
        "Roll behind him.\n\n"
        "### Video Walkthrough\n\n"
        "### Notes\n\n"
        "Has health.\n"
    )
    out = drop_empty_sections(md)
    assert "### Video Walkthrough" not in out
    assert "### Notes" in out
    assert "Roll behind him." in out


def test_drop_empty_sections_collapses_stacked_empties_and_trailing_eof():
    md = "## Foo\n\nBody\n\n### A\n\n### B\n\n### C\n"
    out = drop_empty_sections(md)
    assert "### A" not in out and "### B" not in out and "### C" not in out
    assert "## Foo" in out and "Body" in out


def test_drop_empty_sections_preserves_section_with_inline_body():
    md = "## Foo\n\nReal body\n\n### Bar\n\nReal body 2\n"
    out = drop_empty_sections(md)
    assert "## Foo" in out and "### Bar" in out
    assert "Real body 2" in out


def test_strip_wip_tokens_removes_inline_and_standalone():
    assert strip_wip_tokens("- Damage 100 (WIP)") == "- Damage 100"
    assert strip_wip_tokens("(Work in progress)") == ""
    assert strip_wip_tokens("Notes (WIP) and tips") == "Notes and tips"
    # Pre-existing blank line preserved (paragraph spacing intact).
    assert "\n\n" in strip_wip_tokens("Para 1\n\nPara 2 (WIP)\n")


def test_drop_external_image_placeholders_strips_broken_image_lines():
    md = "Para A\n\nexternal image abc123def456 jpg\n\nPara B"
    out = drop_external_image_placeholders(md)
    assert "external image" not in out
    assert "Para A" in out and "Para B" in out


def test_html_to_markdown_pipeline_strips_wip_and_external_image_residue():
    # End-to-end: empty header + WIP marker + broken image line all clear in
    # one html_to_markdown pass.
    html = (
        '<div id="wiki-content-block">'
        "<h2>Strategy</h2><p>Real body.</p>"
        "<h3>Video Walkthrough</h3>"
        "<h3>Notes</h3>"
        "<p>Damage 100 (WIP)</p>"
        "<p>external image abc123def456 jpg</p>"
        "</div>"
    )
    el = extract_main_element(html, "https://x.test/Foo", clean=True)
    assert el is not None
    md = html_to_markdown(str(el), title="Foo", clean=True)
    assert "Video Walkthrough" not in md
    assert "(WIP)" not in md
    assert "external image" not in md
    assert "Strategy" in md and "Damage 100" in md


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
