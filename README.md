# Easy Scrape

A flexible wiki scraper that converts web content into clean, AI-friendly
Markdown files. Currently supports [Fextralife](https://fextralife.com) wiki
subdomains, with extensibility for other sites. Reads only the article body,
normalizes noisy Fextralife markup, extracts useful stats into YAML
frontmatter, and writes one `.md` per page.

Tested on Dark Souls, Elden Ring, and Bloodborne — works on any wiki on
the `*.wiki.fextralife.com` infrastructure.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Pick your source

Use `--base` to point at any wiki or website to scrape. Currently optimized for
Fextralife subdomains — some examples:

```
https://darksouls.wiki.fextralife.com           (Dark Souls — default)
https://darksouls2.wiki.fextralife.com          (Dark Souls 2)
https://darksouls3.wiki.fextralife.com          (Dark Souls 3)
https://eldenring.wiki.fextralife.com           (Elden Ring)
https://bloodborne.wiki.fextralife.com          (Bloodborne)
https://sekiroshadowsdietwice.wiki.fextralife.com   (Sekiro)
https://nioh2.wiki.fextralife.com               (Nioh 2)
https://lordsofthefallen.wiki.fextralife.com    (Lords of the Fallen)
```

If the wiki you want isn't listed: open it in a browser, copy the base URL
(everything before the first `/<page>` segment), and pass it via `--base`.

## Workflow

### Interactive category scrape

Run the script with no flags to open the interactive picker:

```bash
.venv/bin/python scrape.py
```

The TUI opens with an `easyScrape` banner, light rain/cloud effects, and a
source picker. It lets you choose the default Dark Souls Fextralife wiki or
enter a custom base URL, discovers the wiki sidebar categories, and gives you a
multi-select category list. Before scraping starts, it asks where to write the
files with an in-terminal folder browser. The default action writes to
`~/Desktop/easy_scrape_output`; you can also browse folders, use the current
folder, create a named child folder, or type an existing path directly. Use
`up/down` or `j/k` to move, `space` to toggle categories, the `All` row or `a`
to select all, `n` to clear categories or name a new output folder, `/` to type
an output path, `backspace` to browse to a parent folder, `~` for home, `d` for
Desktop, `enter` to continue, and `q` to quit.
Mouse-wheel events are ignored by the TUI so scrolling does not disturb the
weather effects or selection.

If the target site has no discoverable sidebar categories, the TUI offers
fallback scrape choices: scrape via `/sitemap.xml` or scrape only the entered
URL.

You can also force the picker while still supplying defaults:

```bash
.venv/bin/python scrape.py \
  --interactive \
  --base https://eldenring.wiki.fextralife.com \
  --out elden-ring
```

After category and output-folder selection, the scrape uses the same organized
category folders and animated progress dashboard as scripted category mode.

### Scripted workflow

#### 1. Discover what categories the wiki has

```bash
.venv/bin/python scrape.py --base <wiki-url> --discover
```

This prints the wiki's sidebar nav as a flat list of category names —
typically 60-100 entries like `Weapons`, `Armor`, `Bosses`, `Spells`,
`Rings`, etc. Pick the ones you want.

#### 2. Preview a category before committing

```bash
.venv/bin/python scrape.py --base <wiki-url> --category Weapons --list-only
```

Prints the URLs that would be downloaded — sanity-check that the hub gives
clean results (some hubs are sub-indexes that link to other hubs rather
than to individual pages).

#### 3. Scrape into organized folders

```bash
.venv/bin/python scrape.py \
  --base <wiki-url> \
  --category Weapons \
  --category Armor \
  --category Bosses
```

Each category becomes its own subfolder under the chosen output root. By
default, that root is `~/Desktop/easy_scrape_output`:

```
~/Desktop/easy_scrape_output/
├── Weapons/
├── Armor/
└── Bosses/
```

Clean-mode output is the default. It adds YAML frontmatter with the page title,
source URL, category, and best-effort table stats; removes inline links while
preserving their visible text; strips footer navigation tables and sidebar leak
links; promotes useful image alt text into table labels; normalizes repeated
game-name headings; and drops placeholder sections such as `N/A` notes.

Every scrape run ends with a token summary for the final Markdown collection:
Markdown file count, bytes, characters, words, estimated tokens, a compact
final report with total files/tokens/words/chars, and the largest files. The
token estimate is intentionally stable and dependency-free (`~4 chars/token`),
so you can compare different scrape versions and category collections over time.

Interactive terminal scrapes render a structured dashboard with weather effects
inspired by the sibling `weathr` app: drifting clouds, rain, splashes, and rare
lightning. The dashboard shows the active mode, current stage, output path,
page progress, current URL, saved/skipped/failed counts, elapsed time, and
recent page results. It automatically falls back to plain line-by-line logs
when stdout is redirected, when the terminal is too small, or when you pass
`--no-tui`.

TUI controls are intentionally visual-only: `?` or `h` toggles help, `p`
pauses/resumes the weather effects while scraping continues, `q` leaves the TUI
and continues the scrape with plain logs, and `Ctrl-C` still cancels the scrape.

### Scrape maps or other page images

By default, the scraper stays text-only. Add `--download-images` when you want
meaningful article images downloaded for AI workflows:

```bash
.venv/bin/python scrape.py \
  --base https://darksouls.wiki.fextralife.com \
  --category Maps \
  --download-images
```

This keeps stat/table icons as text labels, but downloads real content images
from the page into `<out>/assets/<page-slug>/` and writes relative image
references into the Markdown, such as:

```md
![Northern Undead Asylum](../assets/Maps/Northern_AsylumMapV1.jpg)
```

### Or scrape everything (sitemap mode)

If you want every page in the wiki, pass an explicit flag such as `--base` and
drop `--category`:

```bash
.venv/bin/python scrape.py --base <wiki-url>
```

This reads `/sitemap.xml` and saves every page into a single flat folder.
Optionally narrow with `--filter '<regex>'`.

### Compare token size for an existing collection

Use `--stats-only` to inspect an existing output folder without fetching pages:

```bash
.venv/bin/python scrape.py --out output --stats-only
```

This is useful after cleanup iterations or when comparing category-specific
collections for an AI agent knowledge base.

## Iterating on cleanup

Use `--cache-dir` while improving cleanup rules or testing a scrape shape. The
first run stores raw HTML; later runs replay from disk and avoid refetching the
same URLs.

```bash
.venv/bin/python scrape.py \
  --base https://darksouls.wiki.fextralife.com \
  --category Bosses \
  --cache-dir .cache/html \
  --overwrite
```

Use `--no-clean` when you need the older raw-ish markdownify output with a
simple title/source header and no YAML frontmatter.

## Worked example: Elden Ring

```bash
# 1. See what categories exist
.venv/bin/python scrape.py \
  --base https://eldenring.wiki.fextralife.com --discover

# 2. Pick categories from the printed list, then scrape
.venv/bin/python scrape.py \
  --base https://eldenring.wiki.fextralife.com \
  --out elden-ring \
  --category Weapons \
  --category Armor \
  --category Bosses \
  --category Sorceries \
  --category Incantations \
  --category Ashes+of+War
```

## All flags

| flag           | default                                 | purpose                                  |
| -------------- | --------------------------------------- | ---------------------------------------- |
| `--base`       | `https://darksouls.wiki.fextralife.com` | wiki subdomain                           |
| `--out`        | `~/Desktop/easy_scrape_output`          | output directory                         |
| `--category`   | none                                    | hub name; repeat to scrape several       |
| `--discover`   | off                                     | print sidebar categories, do nothing else |
| `--interactive` | off                                    | open source/category picker              |
| `--filter`     | none                                    | regex over URL (sitemap mode only)       |
| `--limit`      | none                                    | stop after N URLs                        |
| `--delay`      | `1.0`                                   | seconds between requests                 |
| `--overwrite`  | off                                     | re-download files that already exist     |
| `--list-only`  | off                                     | print URLs only, don't download anything |
| `--stats-only` | off                                     | only count existing Markdown under `--out` |
| `--cache-dir`  | none                                    | cache raw HTML and replay from disk      |
| `--download-images` | off                                | download meaningful article images in clean mode |
| `--no-tui`     | off                                     | disable animated rain progress UI        |
| `--no-clean`   | off                                     | skip cleanup/YAML frontmatter pass       |

Category names with spaces use the wiki's URL form: `Ashes+of+War`,
`Boss+Souls`, etc. Use the names exactly as shown by `--discover`.

## How it works

1. **Choose URL source** — the no-arg interactive picker discovers categories
   first; scripted modes use `/sitemap.xml` for the whole wiki, or a hub page
   (e.g. `/Weapons`) plus its in-content links for category mode.
2. **Fetch the page** with browser-like headers; auto-retry on 429/5xx.
3. **Fetch or replay** the raw HTML, optionally using `--cache-dir`.
4. **Extract** only `<div id="wiki-content-block">` (the article body).
5. **Clean** Fextralife noise: expand rowspans, preserve useful image alt text,
   optionally download content images, drop banner/footer/sidebar clutter,
   unwrap inline links, collapse empty table columns, normalize headings, and
   remove placeholder sections.
6. **Extract frontmatter** from the first page-owned stat table when possible.
7. **Convert** HTML to Markdown via `markdownify`.
8. **Save** as `<slug>.md` with YAML frontmatter in clean mode.
9. **Render progress** through the optional interactive dashboard TUI when
   stdout is a real terminal; otherwise preserve plain logs.
10. **Summarize tokens** for the final Markdown collection using a stable
   `~4 chars/token` estimate.
11. **Politeness:** 1s default delay, retry/backoff, skip files that
   already exist (so you can interrupt and resume safely).

## Tests

Fixture-based regression tests cover the cleanup behavior for representative
Dark Souls pages:

```bash
.venv/bin/pytest
```

## Quirks to know

- Some hubs are **meta-indexes** that link to other hubs rather than to
  individual pages. For example, on Dark Souls 1, `/Magic` links to
  `Pyromancies`, `Sorceries`, `Miracles` instead of listing spells
  directly. Use `--list-only` to spot this, then point at the leaf hubs.
- Hub pages can still include some "noise" links (related categories, helper
  pages). Clean mode removes the common sidebar/footer patterns, but use
  `--list-only` before large runs when a category may be a meta-index.
- The `#wiki-content-block` selector is shared across all Fextralife
  wikis, so the scraper itself doesn't need per-wiki tweaks.
