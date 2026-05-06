"""Markdown corpus statistics and token estimates."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .constants import TOKEN_CHARS_PER_TOKEN


@dataclass
class MarkdownFileStats:
    """Token estimate details for one generated Markdown file."""

    path: Path
    bytes: int
    chars: int
    words: int
    estimated_tokens: int


@dataclass
class MarkdownCorpusStats:
    """Aggregate token estimate details for a Markdown output collection."""

    root: Path
    file_count: int
    bytes: int
    chars: int
    words: int
    estimated_tokens: int
    files: list[MarkdownFileStats]


@dataclass
class MarkdownFolderStats:
    """Stats for one browsable folder inside an output collection."""

    label: str
    path: Path
    stats: MarkdownCorpusStats


def estimate_token_count(text: str) -> int:
    """Return a stable rough token estimate for comparing Markdown corpora."""
    if not text:
        return 0
    return math.ceil(len(text) / TOKEN_CHARS_PER_TOKEN)


def _collect_markdown_paths_stats(
    root: Path, paths: list[Path]
) -> MarkdownCorpusStats:
    files: list[MarkdownFileStats] = []
    total_bytes = total_chars = total_words = total_tokens = 0

    for path in paths:
        text = path.read_text(encoding="utf-8")
        byte_count = len(text.encode("utf-8"))
        char_count = len(text)
        word_count = len(re.findall(r"\S+", text))
        token_count = estimate_token_count(text)
        files.append(
            MarkdownFileStats(
                path=path,
                bytes=byte_count,
                chars=char_count,
                words=word_count,
                estimated_tokens=token_count,
            )
        )
        total_bytes += byte_count
        total_chars += char_count
        total_words += word_count
        total_tokens += token_count

    return MarkdownCorpusStats(
        root=root,
        file_count=len(files),
        bytes=total_bytes,
        chars=total_chars,
        words=total_words,
        estimated_tokens=total_tokens,
        files=files,
    )


def collect_markdown_corpus_stats(root: Path) -> MarkdownCorpusStats:
    """Count Markdown files under root and estimate their combined token size."""
    if root.exists():
        paths = sorted(path for path in root.rglob("*.md") if path.is_file())
    else:
        paths = []
    return _collect_markdown_paths_stats(root, paths)


def collect_markdown_folder_stats(root: Path) -> list[MarkdownFolderStats]:
    """Return per-folder stats for the top-level output groups under root."""
    if not root.exists():
        return []

    folders: list[MarkdownFolderStats] = []
    direct_files = sorted(path for path in root.glob("*.md") if path.is_file())
    if direct_files:
        folders.append(
            MarkdownFolderStats(
                label="Output root",
                path=root,
                stats=_collect_markdown_paths_stats(root, direct_files),
            )
        )

    child_dirs = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: path.name.casefold(),
    )
    for child_dir in child_dirs:
        stats = collect_markdown_corpus_stats(child_dir)
        if stats.file_count:
            folders.append(
                MarkdownFolderStats(
                    label=child_dir.name,
                    path=child_dir,
                    stats=stats,
                )
            )

    return folders


def format_int(value: int) -> str:
    return f"{value:,}"


def format_file_size(value: int) -> str:
    """Return a compact binary file-size label."""
    if value < 1024:
        return f"{format_int(value)} B"

    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:0.1f} {unit}"
        amount /= 1024

    return f"{format_int(value)} B"


def print_markdown_corpus_stats(root: Path, *, top_n: int = 5) -> None:
    """Print a Repomix-style post-run summary for generated Markdown."""
    stats = collect_markdown_corpus_stats(root)
    print("\nToken summary")
    print(f"  Collection: {stats.root}")
    print(f"  Markdown files: {format_int(stats.file_count)}")
    print(f"  Bytes: {format_int(stats.bytes)}")
    print(f"  Characters: {format_int(stats.chars)}")
    print(f"  Words: {format_int(stats.words)}")
    print(
        "  Estimated tokens: "
        f"{format_int(stats.estimated_tokens)} "
        f"(~{TOKEN_CHARS_PER_TOKEN} chars/token)"
    )
    print(
        "  Final report: "
        f"{format_int(stats.file_count)} total files, "
        f"{format_int(stats.estimated_tokens)} total tokens, "
        f"{format_int(stats.words)} total words, "
        f"{format_int(stats.chars)} total chars"
    )

    if not stats.files:
        return

    print(f"  Largest files:")
    root_resolved = root.resolve()
    largest = sorted(stats.files, key=lambda f: f.estimated_tokens, reverse=True)[:top_n]
    for file_stats in largest:
        try:
            label = file_stats.path.resolve().relative_to(root_resolved)
        except ValueError:
            label = file_stats.path
        print(
            "    "
            f"{format_int(file_stats.estimated_tokens)} tokens  "
            f"{label}"
        )
