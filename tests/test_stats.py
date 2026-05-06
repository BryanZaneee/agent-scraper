"""Tests for Markdown corpus statistics."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrape import (  # noqa: E402
    collect_markdown_folder_stats,
    collect_markdown_corpus_stats,
    estimate_token_count,
    format_file_size,
    print_markdown_corpus_stats,
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
    assert stats.chars == len("abcd efgh") + len("ijkl")
    assert stats.words == 3
    assert stats.estimated_tokens == estimate_token_count(
        "abcd efgh"
    ) + estimate_token_count("ijkl")
    assert [file.path.name for file in stats.files] == ["Armor.md", "Sword.md"]


def test_collect_markdown_folder_stats_groups_top_level_outputs(tmp_path):
    (tmp_path / "Weapons").mkdir()
    (tmp_path / "Armor").mkdir()
    (tmp_path / "assets").mkdir()
    (tmp_path / "Root.md").write_text("root file", encoding="utf-8")
    (tmp_path / "Weapons" / "Sword.md").write_text("abcd efgh", encoding="utf-8")
    (tmp_path / "Armor" / "Shield.md").write_text("ijkl", encoding="utf-8")
    (tmp_path / "assets" / "ignored.txt").write_text("ignored", encoding="utf-8")

    folders = collect_markdown_folder_stats(tmp_path)

    assert [folder.label for folder in folders] == ["Output root", "Armor", "Weapons"]
    assert [folder.stats.file_count for folder in folders] == [1, 1, 1]
    assert folders[0].path == tmp_path
    assert folders[1].path == tmp_path / "Armor"
    assert folders[2].path == tmp_path / "Weapons"


def test_format_file_size_uses_binary_units():
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KiB"


def test_token_summary_includes_final_total_report(tmp_path, capsys):
    (tmp_path / "Weapons").mkdir()
    (tmp_path / "Weapons" / "Sword.md").write_text("abcd efgh", encoding="utf-8")

    print_markdown_corpus_stats(tmp_path)

    output = capsys.readouterr().out
    assert "Markdown files: 1" in output
    assert "Characters: 9" in output
    assert (
        "Final report: "
        "1 total files, 3 total tokens, 2 total words, 9 total chars"
    ) in output
