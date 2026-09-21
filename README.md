# ostensive

*Ostensive: showing by pointing.* Two Claude skills that answer and review
using only other people's words. The model chooses which passages to show; a script fetches the sources, cuts
the passages out and builds the page. No text written by the model appears
on it.

- **quote-pack** answers a request for advice with a briefing pack: one HTML
  page that makes the argument from verbatim source passages, in the order
  the model chose.
- **quote-review** reviews a draft: the document is shown as written, with
  highlighted phrases set beside verbatim passages from a style guide, or
  from a web source when the point is factual. No comments, no rewrites.
  The draft can be a text, Word or RTF file or a live web page; the guide can
  be a website, a PDF or a DRM-free EPUB, so craft books work as well as
  house styles. A recurring habit is marked in every place it occurs beside a
  single passage.

Both enforce the same rules in code. The model's spec can hold only URLs and
locators (plus, for a review, phrases that already exist in the document);
unknown fields are rejected. Quotes are whole sentences in one contiguous
run, with the surrounding text shown in grey. A list item always comes with
the line that introduces its list, and a list stem or heading can't be
quoted alone. Each passage links back to the same words on the live page.

## Install

As a Claude Code plugin:

```
/plugin marketplace add dannyboland/ostensive-skills
/plugin install ostensive@dannyboland
```

The skills are then available as `/ostensive:quote-pack` and
`/ostensive:quote-review`, and trigger by themselves on requests such as
"show me the evidence, not your opinion" or "review this against the style
guide".

As standalone skills, copy either folder under `plugins/ostensive/skills/`
into `~/.claude/skills/`, or zip it for upload to claude.ai. Each folder is
self-contained.

Requires Python 3.9+, standard library only. PDF sources need `pypdf` or the
`pdftotext` binary; RTF, DOC and ODT drafts need macOS `textutil` or `pandoc`.

Books you use as guides stay on your machine. `test-guides/` is ignored by
git, as are all `.pdf` and `.epub` files; review pages that quote a
copyrighted book are for private use.

## Layout

```
.claude-plugin/marketplace.json
plugins/ostensive/
  .claude-plugin/plugin.json
  skills/
    quote-pack/    SKILL.md, scripts/quotepack.py, tests, evals
    quote-review/  SKILL.md, scripts/quotereview.py (+ a copy of quotepack.py), tests, evals
tools/sync-shared.sh
```

`quotepack.py` is shared. Edit the copy in `quote-pack/scripts/`, then run
`tools/sync-shared.sh`; a test fails if the two copies drift.

## Develop

```
cd plugins/ostensive/skills/quote-pack/scripts && python3 test_quotepack.py
cd plugins/ostensive/skills/quote-review/scripts && python3 test_quotereview.py
claude plugin validate . --strict
uvx ruff check
```

Each test suite includes a check of the central claim: every word on a built
page is source text, document text, or one of the scripts' fixed strings.
