"""Compatibility facade and executable entrypoint for easy_scrape.

The implementation lives in the ``easy_scrape`` package. This file stays tiny
so existing commands such as ``python scrape.py`` and imports from ``scrape``
continue to work while the internals are split by responsibility.
"""

from __future__ import annotations

from easy_scrape.cleanup import (
    CleanupPipelineContext,
    CleanupStep,
    ImageAssetContext,
    POST_LINK_CLEANUP_PIPELINE,
    PRE_LINK_CLEANUP_PIPELINE,
    _clean_label,
    asset_filename_from_url,
    download_image_asset,
    drop_banner_alt_rows,
    drop_empty_columns,
    drop_empty_headings,
    drop_empty_sections,
    drop_external_image_placeholders,
    drop_footer_nav_table,
    drop_fragment_anchors,
    drop_placeholder_sections,
    drop_stranded_category_links,
    expand_rowspans,
    extract_frontmatter,
    extract_main_element,
    format_frontmatter,
    html_to_markdown,
    normalize_heading_line,
    preserve_content_images,
    replace_images_with_alt,
    run_cleanup_pipeline,
    strip_inline_links,
    strip_wip_tokens,
)
from easy_scrape.cli import (
    dispatch_mode,
    finish_with_markdown_stats,
    main,
    parse_args,
    run_category_mode,
    run_discover_mode,
    run_interactive_mode,
    run_single_url_mode,
    run_sitemap_mode,
)
from easy_scrape.constants import (
    CATEGORY_PICKER_ALL_LABEL,
    CONTENT_SELECTOR,
    DEFAULT_BASE_URL,
    DEFAULT_OUTPUT_DIR,
    TOKEN_CHARS_PER_TOKEN,
)
from easy_scrape.fetching import (
    discover_sidebar_categories,
    fetch_category_member_urls,
    fetch_sitemap_urls,
    make_session,
    url_to_filename,
    url_to_title,
)
from easy_scrape.pipeline import (
    ScrapeBatchResult,
    ScrapeOptions,
    ScrapeResult,
    scrape_one,
    scrape_page,
    scrape_pages,
    scrape_url_list,
)
from easy_scrape.stats import (
    MarkdownCorpusStats,
    MarkdownFileStats,
    MarkdownFolderStats,
    collect_markdown_corpus_stats,
    collect_markdown_folder_stats,
    estimate_token_count,
    format_file_size,
    format_int,
    print_markdown_corpus_stats,
)
from easy_scrape.tui.effects import (
    TerminalCloud,
    TerminalCloudSystem,
    TerminalLightningBolt,
    TerminalRainDrop,
    TerminalRainSplash,
    TerminalRainSystem,
    TerminalStormSystem,
)
from easy_scrape.tui.picker import (
    CategoryPickerState,
    FolderBrowserState,
    InteractiveScrapeTui,
    display_path,
    list_browsable_dirs,
    normalize_base_url,
    normalize_existing_output_dir,
    normalize_new_output_dir,
    normalize_output_dir,
    output_browser_start_dir,
)
from easy_scrape.tui.progress import (
    ScrapeRainTui,
    ScrapeTuiState,
    format_duration,
    percent_done,
    progress_bar,
    url_host,
)
from easy_scrape.tui.stats_browser import (
    MarkdownStatsBrowserState,
    MarkdownStatsBrowserTui,
    reveal_folder,
)
from easy_scrape.tui.terminal import (
    KeyReader,
    TerminalCanvas,
    TerminalSession,
    fit_text,
    key_name_from_sequence,
    strip_ansi,
)

__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    main()
