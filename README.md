# Easy Scrape

A flexible wiki scraper that converts web content into clean Markdown files.
Currently supports [Fextralife](https://fextralife.com) wiki subdomains, with
extensibility for other sites. Reads only the article body, strips images and
scripts, rewrites internal links to absolute URLs, and writes one `.md` per page.

Tested on Dark Souls, Elden Ring, and Bloodborne — works on any wiki on
the `*.wiki.fextralife.com` infrastructure.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install requests beautifulsoup4 markdownify lxml
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

### 1. Discover what categories the wiki has

```bash
.venv/bin/python scrape.py --base <wiki-url> --discover
```

This prints the wiki's sidebar nav as a flat list of category names —
typically 60-100 entries like `Weapons`, `Armor`, `Bosses`, `Spells`,
`Rings`, etc. Pick the ones you want.

### 2. Preview a category before committing

```bash
.venv/bin/python scrape.py --base <wiki-url> --category Weapons --list-only
```

Prints the URLs that would be downloaded — sanity-check that the hub gives
clean results (some hubs are sub-indexes that link to other hubs rather
than to individual pages).

### 3. Scrape into organized folders

```bash
.venv/bin/python scrape.py \
  --base <wiki-url> \
  --category Weapons \
  --category Armor \
  --category Bosses
```

Each category becomes its own subfolder under `output/`:

```
output/
├── Weapons/
├── Armor/
└── Bosses/
```

### Or scrape everything (sitemap mode)

If you want every page in the wiki, drop `--category`:

```bash
.venv/bin/python scrape.py --base <wiki-url>
```

This reads `/sitemap.xml` and saves every page into a single flat folder.
Optionally narrow with `--filter '<regex>'`.

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
| `--out`        | `output`                                | output directory                         |
| `--category`   | none                                    | hub name; repeat to scrape several       |
| `--discover`   | off                                     | print sidebar categories, do nothing else |
| `--filter`     | none                                    | regex over URL (sitemap mode only)       |
| `--limit`      | none                                    | stop after N URLs                        |
| `--delay`      | `1.0`                                   | seconds between requests                 |
| `--overwrite`  | off                                     | re-download files that already exist     |
| `--list-only`  | off                                     | print URLs only, don't download anything |

Category names with spaces use the wiki's URL form: `Ashes+of+War`,
`Boss+Souls`, etc. Use the names exactly as shown by `--discover`.

## How it works

1. **Choose URL source** — `/sitemap.xml` for the whole wiki, or a hub
   page (e.g. `/Weapons`) plus its in-content links for category mode.
2. **Fetch the page** with browser-like headers; auto-retry on 429/5xx.
3. **Extract** only `<div id="wiki-content-block">` (the article body).
4. **Strip** `<img>`, `<script>`, `<style>`, `<noscript>`, `<iframe>`.
   Rewrite all `<a href>` to absolute URLs.
5. **Convert** HTML to Markdown via `markdownify`.
6. **Save** as `<slug>.md` with title and source URL header.
7. **Politeness:** 1s default delay, retry/backoff, skip files that
   already exist (so you can interrupt and resume safely).

## Quirks to know

- Some hubs are **meta-indexes** that link to other hubs rather than to
  individual pages. For example, on Dark Souls 1, `/Magic` links to
  `Pyromancies`, `Sorceries`, `Miracles` instead of listing spells
  directly. Use `--list-only` to spot this, then point at the leaf hubs.
- Hub pages include some "noise" links (related categories, helper
  pages). The bulk of what you get is the actual content; you can prune
  the few extras manually.
- The `#wiki-content-block` selector is shared across all Fextralife
  wikis, so the scraper itself doesn't need per-wiki tweaks.
