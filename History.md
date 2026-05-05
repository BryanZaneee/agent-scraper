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
