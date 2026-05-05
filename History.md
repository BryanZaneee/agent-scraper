# easy_scrape — Change History & Architectural Decisions

A running log of meaningful changes to this scraper, the reasoning behind each
choice, and the alternatives considered. Entries are newest-first. Trivial fixes
(typos, formatting) skip the log.

Format per entry:

```
## YYYY-MM-DD — <Change Title>

**Context:** what prompted the change.
**Decision:** what we did.
**Rationale:** why this approach.
**Alternatives considered:** what we rejected and why.
**Trade-offs:** known limitations / open issues.
```

---

## 2026-05-05 — Visible all-categories selection and fuller totals

**Context:** The interactive category picker supported selecting every
discovered category with the `a` key, but users had to know the shortcut. The
ending report also surfaced files and estimated tokens but did not include the
full total set the user wanted for handoff and comparison.

**Decision:** Add a synthetic `All` row at the top of the category picker that
toggles every discovered category without adding `"All"` to the downstream
category scrape list. Extend Markdown corpus stats with character totals and
print a final report containing total files, total tokens, total words, and
total chars.

**Rationale:** Keeping `All` as UI-only preserves the existing category-mode
pipeline, output-folder layout, and per-category labels while making the common
"scrape everything discovered" path obvious. Character totals belong in the
same corpus aggregation that already computes bytes, words, and token
estimates, so stats-only and post-run reports stay consistent.

**Alternatives considered:** Treating `All` as a real category argument was
rejected because it would try to fetch a non-existent `/All` hub. Adding a
second prompt after category discovery was rejected because it would slow down
the normal picker flow.

**Trade-offs:** The `All` row reflects whether every concrete category is
selected; it is not written to frontmatter or output paths. Token totals remain
the scraper's stable estimate based on `~4 chars/token`.

## 2026-05-05 — TUI output folder browser

**Context:** The first interactive output step still required users to type a
custom path if they did not want the default Desktop folder. The user wanted a
cooler scraper-style folder selection flow that stays inside the terminal.

**Decision:** Replace the two-choice output menu with a dependency-free folder
browser in `InteractiveScrapeTui`. The browser starts on Desktop when
available, falls back to home, keeps `~/Desktop/easy_scrape_output` as the
first action, lets users choose the current folder, open child folders, jump to
home/Desktop, type an existing path, or name a new child folder without creating
it before the scrape pipeline runs.

**Rationale:** A state-machine-backed browser gives users the feel of a real
folder picker without adding a native dialog dependency or bypassing the
existing `args.out` plumbing. Returning a plain `Path` keeps category, sitemap,
single-URL, asset, dashboard, and stats behavior unchanged downstream.

**Alternatives considered:** A native OS folder dialog was rejected because it
would make the scraper less portable and harder to test in terminal-first
contexts. Keeping only the custom path prompt was rejected because it is clumsy
for users who want to browse to a destination.

**Trade-offs:** The browser lists visible child directories only. Hidden
folders and unusual paths remain reachable through the direct path entry.

---

## 2026-05-05 — Interactive output folder selection

**Context:** The interactive TUI let users choose a source and categories, but
the output location was still controlled only by `--out`. The requested flow is
to confirm where files should go before the scraper starts writing.

**Decision:** Add a pre-scrape output-folder chooser to `InteractiveScrapeTui`.
The default output directory is now `~/Desktop/easy_scrape_output`; the TUI
offers that location first and lets users enter a custom path. The chosen path
is assigned back to `args.out` before category, sitemap fallback, or single-URL
fallback scraping starts, so all existing output, asset, dashboard, and stats
paths continue to use one source of truth.

**Rationale:** Placing the prompt after target selection but before the scrape
keeps the flow explicit without changing the scraping pipeline. Reusing `Path`
objects and the existing `args.out` plumbing avoids a parallel output setting
that future agents would have to keep in sync.

**Alternatives considered:** Prompting for an output folder before category
discovery was rejected because the user may back out or change sources first.
Creating a separate interactive-only output setting was rejected because the
downstream runners already use `args.out` consistently.

**Trade-offs:** Scripted modes also inherit the new desktop default when
`--out` is omitted. Users who want the old repo-local `output/` folder can pass
`--out output`.

---

## 2026-05-05 — Picker bonfire ASCII art

**Context:** The interactive source/category menu now has a branded animated
banner and rain layer, and the user wanted an ASCII version of a bonfire image
placed in the bottom-right of the menu.

**Decision:** Add a small ASCII-only bonfire/sword motif to the
`InteractiveScrapeTui` frame renderer. The art is anchored inside the lower
right of the picker panel and the body text dynamically shortens on rows where
the bonfire occupies space, so menu content does not paint through the art.

**Rationale:** Keeping the bonfire in the existing terminal canvas preserves
the project's dependency-free TUI architecture and lets the same frame renderer
handle source selection, custom URL prompts, status screens, errors, and
category selection consistently.

**Alternatives considered:** A larger full-screen illustration was rejected
because it would compete with category lists and URL prompts. Adding image or
Unicode rendering was rejected because the requested asset was ASCII and the
TUI already uses plain terminal cells.

**Trade-offs:** The bonfire is hidden on very narrow or short panels where it
would crowd the menu, so tiny terminals prioritize readable controls over the
decorative element.

---

## 2026-05-05 — TUI mouse-wheel guard and no-category fallback

**Context:** The animated picker/progress screens should not react to terminal
mouse-wheel input, and category discovery should not block users from scraping
sites that do not expose Fextralife-style sidebar categories. The final token
summary also needed a more obvious file-count line in the ending report.

**Decision:** Enable terminal mouse reporting while the TUI owns the alternate
screen, parse mouse escape sequences, and ignore them so the scroll wheel does
not move selection or scroll the weather frame. When interactive category
discovery returns no categories, offer fallback actions to scrape the site via
`/sitemap.xml`, scrape the entered URL directly, or choose another source. Add
a compact `Final report: <files> files, <tokens> estimated tokens` line to the
post-run token summary.

**Rationale:** Mouse reporting prevents terminal scrollback from interfering
with the animated UI and keeps wheel events out of the picker state machine.
The no-category fallback preserves the interactive flow for non-Fextralife or
less structured sites while continuing to reuse the existing sitemap and
single-URL scrape paths.

**Alternatives considered:** Disabling all mouse handling was rejected because
many terminals send wheel input as navigation or scrollback unless the app
claims mouse events. Treating no categories as a hard error was rejected
because the user may still want broad sitemap scraping or direct URL scraping.

**Trade-offs:** Sitemap fallback still depends on the target site exposing a
usable `/sitemap.xml`; when it does not, users can fall back to scraping the
entered URL directly.

---

## 2026-05-05 — Picker arrow-key fix and animated banner

**Context:** The new interactive picker accepted `j/k`, but arrow-key movement
could fail because terminals send arrows as multi-byte escape sequences and the
picker was reading `Esc` too eagerly. The picker also felt more utilitarian
than the scrape dashboard because it lacked a branded banner or weather layer.

**Decision:** Make the picker key reader poll briefly for complete escape
sequences and map common ANSI/xterm arrow forms before treating bare `Esc` as
cancel. Rework picker rendering onto the same lightweight terminal canvas
concept as the scrape dashboard, adding a centered `easyScrape` banner plus
rain/cloud effects that repaint while waiting for input.

**Rationale:** The input fix addresses the real terminal behavior without
adding dependencies or changing the category selection state machine. Reusing
the existing rain/cloud primitives keeps the visual language consistent across
the pre-scrape picker and the in-scrape dashboard.

**Alternatives considered:** Keeping the static picker and documenting `j/k`
as the reliable controls was rejected because arrow keys are expected in a TUI.
Switching to curses or another terminal framework was rejected because the
project intentionally remains dependency-free.

**Trade-offs:** The picker now repaints on a short timer while waiting for
input, which is slightly busier than a purely blocking prompt but still small
and local to interactive mode.

---

## 2026-05-05 — Interactive category picker TUI

**Context:** The scraper had strong scripted modes and an animated progress
dashboard, but the launch flow still required users to know the right
`--discover` and `--category` sequence before scraping. The desired workflow is
to launch the app, choose a source, discover broad categories such as Weapons
and Armor, select the wanted categories, and then start the scrape from inside
the terminal UI.

**Decision:** Add a dependency-free pre-scrape interactive TUI. No-argument
launch now opens a source picker for the default Dark Souls Fextralife wiki or a
custom base URL, discovers categories through the existing sidebar parser, lets
the user multi-select categories with keyboard controls, and then hands those
categories to the existing category scrape runner. Add `--interactive` so the
same picker can be forced while supplying defaults such as `--base`, `--out`,
`--delay`, or `--download-images`.

**Rationale:** Keeping the picker separate from `scrape_one`,
`fetch_category_member_urls`, and `scrape_url_list` preserves the cleanup,
caching, image, output, and token-summary behavior already covered by tests.
The selector is just a launch-time coordinator; the progress dashboard remains
the observer for the scrape itself.

**Alternatives considered:** Replacing the CLI with a full-screen app was
rejected because the existing scripted modes are useful for automation. Adding
a terminal UI dependency was rejected to keep installation small. Treating a
custom URL as a single page was rejected for this pass because the requested
flow is category discovery from a site/wiki base.

**Trade-offs:** Interactive mode requires both stdin and stdout to be TTYs and
exits with a clear error otherwise. Explicit flags still preserve the old
scriptable sitemap/category/discover behavior, so users who want sitemap mode
now pass an explicit flag such as `--base`.

---

## 2026-05-05 — Balanced scrape dashboard TUI

**Context:** The first rain TUI made long runs feel better, but the centered
box still hid the scrape's actual workflow. It was hard to tell which mode was
running, where files were going, what stage the current page was in, or how to
leave the animation without canceling the scrape.

**Decision:** Overhaul the TUI into a dependency-free dashboard. It now tracks
explicit scrape state (mode, stage, output path, current URL/slug, counters,
elapsed time, rate, and recent events), adds `weathr`-style cloud and lightning
effects alongside rain and splashes, and supports basic visual controls:
`?`/`h` for help, `p` to pause effects only, and `q` to exit the TUI while the
scrape continues with plain logs.

**Rationale:** Treating the TUI as an observer around the existing scrape flow
keeps generated Markdown, cache behavior, image downloads, and token summaries
unchanged. The added stage hooks report what the scraper is already doing
(fetching, reading cache, cleaning, converting, writing, waiting, summarizing)
without changing the underlying work.

**Alternatives considered:** A full terminal UI framework was rejected to keep
the scraper easy to install and dependency-free. Turning `q` into a process
quit was rejected because `Ctrl-C` already owns cancellation; `q` is safer as a
visual fallback to plain logs. Making the scene cinematic-first was rejected in
favor of a balanced dashboard where progress remains readable.

**Trade-offs:** Keyboard controls require an interactive stdin; when unavailable
the dashboard still renders but controls are inactive. Plain logs resume only
for pages completed after `q` is pressed, while final counts and token summaries
still print after the TUI exits.

---

## 2026-05-05 — Animated rain scrape TUI

**Context:** The command-line scraper worked, but long interactive runs were
plain line-by-line logs. The user wanted the terminal UI improved with a cool
rain animation, borrowing the proven raindrop/splash behavior from the sibling
`weathr` repo, while preserving scraper functionality.

**Decision:** Add a dependency-free animated progress UI for interactive scrape
runs. The new `ScrapeRainTui` uses an ANSI alternate screen, a central
`easy_scrape` progress panel, saved/skipped/failed counters, current-page
status, and a Python port of `weathr`'s rain particle ideas: width-scaled drop
density, wind-influenced horizontal motion, and short-lived splash particles
when drops hit the panel.

**Rationale:** Keeping the animation as a wrapper around existing progress
reporting avoids touching fetch, cache, cleanup, image-download, or stats
behavior. The TUI only activates for real interactive terminals and falls back
to the original plain logs for redirected output, tiny terminals, `--list-only`,
`--stats-only`, and `--no-tui`.

**Alternatives considered:** Adding a terminal UI dependency was rejected
because this project is a single-file scraper with a small requirements list.
Using curses was avoided for the same reason and because ANSI alternate-screen
rendering is enough for this progress view. Running the animation for every
mode was rejected because list/discover/stats output should remain scriptable.

**Trade-offs:** The animation is intentionally visual-only and does not persist
per-page logs while active; final done counts and token summaries still print
after the TUI exits. Stderr errors may still appear over the animation if the
network or image downloader reports a problem.

---

## 2026-05-05 — Post-run Markdown token summaries

**Context:** The scraper output is being used as an AI agent knowledge base,
and the user wants to compare how much context different scrape collections
consume across cleanup iterations, categories, and future software versions.

**Decision:** Add a dependency-free Markdown corpus counter. Normal scrape
runs now print a post-run token summary for the final `--out` directory,
including Markdown file count, bytes, words, estimated tokens, and the largest
files by estimated tokens. Add `--stats-only` so existing output folders can be
compared without fetching or rewriting pages.

**Rationale:** Counting the final Markdown corpus makes the metric useful even
when pages were skipped because files already existed. A stable `~4 chars/token`
estimate is not model-perfect, but it is deterministic, cheap, and good enough
for comparing relative savings between scraper versions and collections.

**Alternatives considered:** Adding `tiktoken` or another model-specific
tokenizer was deferred to avoid a new dependency and provider-specific counts.
Counting only newly saved pages was rejected because resumed runs and skipped
files would under-report the real KB size.

**Trade-offs:** Token counts are estimates, not exact model tokenizer counts.
They should be used for collection-to-collection comparisons and rough context
budgeting, not for exact billing or provider limit enforcement.

---

## 2026-05-05 — Opt-in content image asset scraping

**Context:** The Dark Souls Maps page exposes useful map files in static HTML
under `/file/Dark-Souls/...`, but the scraper's clean mode intentionally
converted all images to alt text or removed them. The user wants those maps
available as local image assets for AI model workflows while preserving the
existing text-only default.

**Decision:** Add `--download-images` for clean-mode scrapes. Meaningful
article images outside tables are downloaded into `<out>/assets/<page-slug>/`,
rewritten as relative Markdown image references, and labeled from nearby anchor
or heading text before falling back to `alt`. Table/stat icons continue through
the existing alt-text cleanup path and are not downloaded.

**Rationale:** Keeping image capture opt-in avoids surprise binary downloads
for normal KB text scrapes. Storing assets under the scrape output root keeps
category pages portable (`../assets/...` from category folders) while avoiding
per-category duplication. The classifier is deliberately conservative: linked
Fextralife `/file/` images with real dimensions are content, table images are
labels/icons.

**Alternatives considered:** Downloading every `<img>` was rejected because
Fextralife uses many small icons as semantic table labels. Keeping only remote
URLs was rejected because the requested AI workflow needs local image files.
Adding OCR/caption generation was deferred; the first pass preserves original
assets and human-readable Markdown references only.

**Trade-offs:** The content-image classifier may miss unusual useful images
embedded inside tables or served without file extensions. That is safer than
polluting outputs with icons and can be extended if a real page needs it.

---

## 2026-05-05 — Documentation aligned with clean-mode output

**Context:** The scraper now produces substantially cleaner AI-KB-oriented
Markdown than the README described. The repo instruction also requires
`History.md` to be updated whenever codebase changes are made.

**Decision:** Rename the change log to `History.md` to match the repo
instruction, and update the README setup, workflow, flags, internals, tests,
and quirks sections around clean-mode output, YAML frontmatter, inline-link
stripping, HTML caching, and the fixture-based pytest suite.

**Rationale:** Future users and agents need the documented workflow to match
the current default behavior. Keeping the architecture log in the instructed
filename makes it easier for future agents to find the reasoning behind the
cleanup pipeline.

**Alternatives considered:** Leaving the old README wording in place was
rejected because it still described stripping images and preserving absolute
links, which is no longer the clean-mode behavior.

**Trade-offs:** The README documents the Fextralife-oriented cleanup pipeline
at a high level rather than exhaustively listing every helper. Detailed
implementation rationale remains in this file.

---

## 2026-05-05 — Strip all inline links from body

**Context:** After the morning's hygiene pass, body prose still contained
heavy inline link soup. Example from the Bell Gargoyle strategy section: a
single paragraph contained nine `[Lautrec](https://...)` and `[Solaire](https://...)`
markdown links plus title attributes, contributing nothing semantically and
inflating token cost for AI-KB consumption.

**Decision:** Add `strip_inline_links(element)` — a one-line BeautifulSoup
helper that unwraps every `<a>` tag (replacing it with its inner text /
elements). Wired into the post-urljoin DOM cleanup pass, between
`drop_fragment_anchors` and `drop_empty_columns`. Removed
`unwrap_decorative_links` (and `_DECORATIVE_LINK_PATHS`) — subsumed.

**Rationale:** Cross-page references in a knowledge base belong in
structured fields, not URL-laden body prose. The page's own URL is still
recorded in YAML frontmatter, so provenance survives. Soup-level unwrap is
safer than markdown post-process regex (no nested-bracket false-positives).

**Alternatives considered:**

- **Markdown regex post-process** (`re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)`) —
  rejected. Risk of mis-matching nested brackets in code blocks or escaped
  text. Soup-level unwrap operates on structured DOM, no parsing pitfalls.
- **markdownify `strip=["a"]`** — rejected. That option strips both the URL
  AND the anchor's text content, losing the very text we want to keep.
- **Keep `unwrap_decorative_links` for /home and /items only, leave other
  links as is** — rejected. The user explicitly asked for ALL links removed.
  Two competing helpers would also be confusing.
- **Add `--keep-links` flag for opt-out** — rejected (Karpathy guideline:
  no features beyond what was asked). The existing `--no-clean` flag already
  provides "raw output" mode.

**Trade-offs:**

- Information about which entities have their own wiki pages is lost (e.g.
  "Lautrec" used to be a link, telling the LLM Lautrec has more content
  available). For the AI-KB use case this is fine — the agent can search the
  KB for "Lautrec" to find the page directly.
- The frontmatter `url` field is the only remaining URL in clean-mode
  output. If callers want raw-with-links output they pass `--no-clean`.

---

## 2026-05-05 — Data hygiene pass for AI-KB consumption

**Context:** Output is being fed into an AI knowledge base. Live-fetched audits
of Drake Sword, Bell Gargoyle, Embers, and Asylum Demon revealed seven residual
noise patterns leaking through after the initial cleanup helpers: orphaned
rowspan rows on boss stats, banner image alt-text rows, within-page fragment
anchors, decorative SEO cross-links to `/home` and `/items`, mid-article
stranded category-link paragraphs, sparse stat tables with all-empty columns,
and empty heading lines from over-aggressive heading normalization. The user
also raised a list of general scraping tips (Selenium, embedded JSON, demjson,
pandas, retry decorators, response caching) for evaluation.

**Decision:** Surgical additions to `scrape.py` only — no architectural
overhaul. Six new helpers (`expand_rowspans`, `drop_banner_alt_rows`,
`drop_fragment_anchors`, `unwrap_decorative_links`, `drop_empty_columns`,
`drop_empty_headings`) and one extension (`drop_stranded_category_links` now
walks all `<p>` children, not just leading). New `--cache-dir` flag for fixture
caching during dev iteration. Fixture-based pytest suite (4 fixtures, 11
assertions) for regression safety.

**Rationale:** The live audits established that all seven noise patterns are
real and stable across pages — fixing each at the source (BeautifulSoup or
Markdown post-process) is the smallest change that solves the actual problem.
Pre-expanding rowspans rectangularizes tables before any other transform, which
both fixes orphaned NG+ rows in the body and lets `extract_frontmatter` capture
NG+ stats in YAML. Fixture tests pin behavior so future cleanup tweaks can't
silently break existing fixes.

**Alternatives considered:**

- **Selenium / headless browser** — rejected. Fextralife serves the article
  body in initial HTML; no JS gating means `requests` + `BeautifulSoup` are
  sufficient. Selenium would add ~100MB of deps and 10x runtime for zero gain.
- **Extract JSON from page source** — rejected. The wiki-content-block is
  rendered HTML, not data-embedded JSON. OpenGraph tags exist but carry no stat
  data. There's nothing to parse.
- **pandas / DataFrame integration** — rejected. Output target is per-page
  Markdown for an LLM to read; tabular intermediate representations add no
  value and a heavy dep.
- **demjson lenient JSON loader** — rejected. No JSON parsing in pipeline.
- **Custom @retry decorator** — rejected. `urllib3.util.retry.Retry` is already
  mounted on the requests session adapter with exponential backoff
  (`backoff_factor=1.5`). Re-implementing would be duplicative.
- **Drop the entire first stat table when frontmatter has captured stats** —
  rejected (more aggressive option presented to user). Risk of losing
  information that the frontmatter heuristics didn't capture; column-collapse
  is lossless and equally readable.
- **Skip rowspan handling, leave NG+ rows orphaned** — rejected. The user
  explicitly chose the more thorough path: pre-expand rowspans in soup so both
  body table and frontmatter benefit. Trade-off accepted: slightly more code
  (~20 lines) for richer YAML output.
- **No tests, just visual spot-check** — rejected. Cleanup logic is exactly the
  kind of thing that silently regresses when one helper is tweaked and another
  breaks. Fixture tests are cheap (~30 LoC) and catch this.
- **Defer HTTP cache to a later PR** — rejected (user wanted it now). The
  `--cache-dir` flag is ~15 LoC and doesn't change default behavior.

**Trade-offs:**

- `drop_empty_columns` operates on the predominant cell-count rows in each
  table; outlier rows (typically full-width title/section headers via colspan)
  are left alone. This handles the common boss-stat-table case (3-cell rows
  with a 1-cell title row) but does not collapse Drake Sword's first stat
  table (nested colspans + section headers spanning all columns). The Drake
  Sword body retains a 20-column sparse stat block; the AI-KB consumer should
  read its YAML frontmatter (which captures all stats cleanly) instead of the
  body table.
- `unwrap_decorative_links` only matches paths `/home` and `/items` — does not
  touch `/bosses`, `/weapons`, etc. because those are sometimes legitimate hub
  references. False negatives possible (a `/bosses` link with text "help" would
  slip through), but false positives would be worse.
- `drop_empty_headings` only matches truly-empty heading lines (`### `), not
  headings whose only body is another heading at the same depth. Letting that
  pattern through is intentional — distinguishing "parent heading with subhead
  body" from "stray empty section" is heuristic-heavy and risky.
- Banner-row regex (`^(Boss|NPC|Item|Image)\s+\d+\b`) is title-agnostic. New
  Fextralife banner formats would need the regex extended.

**Implementation deviations from the original plan:**

- `_try_emit` (inside `extract_frontmatter`) was extended to concatenate
  dup-key string values rather than skip them. Without this, rowspan expansion
  alone would still leave frontmatter capturing only NG (since the second
  Health row's key already exists in the stats dict and would be skipped). The
  concat path triggers only when both old and new values are strings and they
  differ — so `health: "NG: 813 / NG+: 2,195"` rather than `health: "NG: 813"`.
- `drop_empty_columns` was relaxed from strict cell-count match (every row
  must have the same count) to predominant cell-count (process the matching
  majority, leave outlier rows untouched). The strict version skipped the
  entire boss stat table because the title row uses colspan and has 1 cell
  while data rows have 3.
- One Drake Sword test expectation was replaced. The original plan asserted
  the first stat table would collapse to <8 columns; in practice, Drake's
  first table is built from nested colspans (the title row spans all 20
  columns; data rows use colspan=5 inside that) which `drop_empty_columns`
  cannot meaningfully reduce without losing the section-header structure.
  Replaced with a section-heading normalization assertion. Net test count: 12
  instead of 11.

---

## 2026-05-04 — Initial cleanup helpers (uncommitted on `main`)

**Context:** Raw markdownify output retained large amounts of noise that hurt
AI-KB readability: stat icons rendered as nothing (alt text dropped), trailing
single-column nav tables listing every other item in the category, leading
sidebar-leak link paragraphs on hub-style pages, "Dark Souls X" prefix in every
heading, and N/A placeholder sections.

**Decision:** Add six post-processing passes wired into `extract_main_element`
and `html_to_markdown`: `replace_images_with_alt`, `drop_footer_nav_table`,
`drop_stranded_category_links`, `drop_placeholder_sections`,
`normalize_heading_line`. Plus `extract_frontmatter` to surface stats as YAML
for downstream tooling.

**Rationale:** Each transform targets a specific structural pattern observed
across many pages. Doing them at the soup level (vs. markdown regex) preserves
table cell alignment and lets us reason about element shape (cell counts,
single-anchor paragraphs, etc.).

**Alternatives considered:**

- **Drop images entirely (no alt promotion)** — rejected. Fextralife uses stat
  icons as table column headers; without alt-text fallback, those columns
  become unlabeled values.
- **Strip the entire first table** — rejected. The first table is the
  authoritative stat block on item pages.

**Trade-offs:**

- `drop_stranded_category_links` only catches LEADING bare-anchor paragraphs;
  mid-article variants (Embers) still leak. Addressed by 2026-05-05 entry.
- `drop_footer_nav_table` only checks single-cell first-row tables; some
  Fextralife pages with multi-column footer navs may slip through.

---

## (commit `e286c5f`) — Rename to easy_scrape + multi-site extensibility

**Context:** Original scraper was hard-coded for Dark Souls. User wanted to use
the same code for other Fextralife wikis (Elden Ring, Bloodborne, Sekiro, etc.).

**Decision:** Add `--base` flag to point at any wiki subdomain. Add three modes:
sitemap (default), category (fetch hub page + members), and discover (print
sidebar categories). Keep `#wiki-content-block` as the single content selector.

**Rationale:** All Fextralife wikis run on the same infrastructure, so the
content selector is stable across subdomains. Sitemap and category modes cover
the two natural ways users want to scrape (everything or specific categories).

**Alternatives considered:**

- **Per-wiki config file** — rejected. The existing selector works universally;
  config would be over-engineering.

**Trade-offs:**

- Some category hubs are meta-indexes that link to other hubs rather than
  members (e.g., Dark Souls 1 `/Magic` → `/Pyromancies` etc.). User must use
  `--list-only` to spot this and point at leaf hubs.

---

## (commit `5494f43`) — Initial Fextralife → Markdown scraper

**Context:** Need a way to convert Fextralife wiki content into clean Markdown
files for LLM consumption.

**Decision:** Single-file Python script. `requests` for HTTP (with urllib3
Retry mounted for backoff), `BeautifulSoup` for HTML parsing,
`markdownify` for conversion. Sitemap-driven URL list. Politeness: 1s delay
between requests, skip-if-exists for safe interruption/resume.

**Rationale:** Fextralife serves static HTML; no JS rendering needed. Single
file keeps deployment trivial. Skip-if-exists makes the run idempotent and
resumable.

**Alternatives considered:**

- **Selenium / Playwright** — rejected. Static HTML means no need for a browser
  engine. Would 10x the runtime and add heavy deps.
- **Scrapy framework** — rejected. Single-domain, single-shape scraper doesn't
  need a framework's middleware/pipeline machinery.

**Trade-offs:**

- No test suite. Addressed by 2026-05-05 entry.
- Default 1s delay is conservative; large category scrapes are slow. User can
  override via `--delay`.
