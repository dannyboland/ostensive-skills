---
name: quote-pack
description: Answer a request for advice or a position with a quote-only briefing pack - one HTML page that makes the argument using only verbatim passages from sources, assembled by a script so that no AI-written prose appears on it. Use whenever the user asks for a quote pack, briefing pack, evidence pack, an "ostensive" answer or summary, or a "sources only" / "source-only" answer or summary, says they don't want to take the AI's word for something, asks to see the evidence rather than a summary, or wants advice they can verify themselves (health, money, legal, technical choices, contested facts). Also use when the user asks you to back up a position you have already given.
---

# Quote pack

A quote pack answers "what should I do / what is true about X?" without asking
the reader to trust you. You choose which passages to show and in what order.
A script fetches the sources, cuts the passages out and builds the page. You
never write a word that appears on it.

That split is the whole point. A reader who distrusts AI summaries can check
every sentence against its source with one click, and knows there is no
sentence on the page that a model composed, trimmed or paraphrased.

## The invariant

`scripts/quotepack.py` generates all of the HTML. It accepts only URLs and
locators from you, and enforces the rest:

- The spec has no field for headings, labels, captions or commentary, and the
  script rejects unknown fields. Don't look for a way around this (a title in
  the filename, a "source" file you wrote yourself, a URL whose query string
  carries a message). If the page needs words that no source says, the honest
  result is a page without those words.
- Quotes are whole sentences in one contiguous run. There is no way to start
  or stop mid-sentence, drop a clause, or splice two separated sentences.
- A quoted list item is shown with the line that introduces its list, because
  "drink alcohol" under "Don't:" means the opposite on its own.
- The reverse holds too: a list stem ("Employers can:") or a heading is never
  a quote by itself. The script refuses a quote that ends on one, so extend
  the range into the items you mean, or quote the item and let the stem come
  with it.
- Surrounding sentences are printed in grey so the reader can see what the
  quote was sitting in.

Never edit the generated HTML, and never post-process it. If it's wrong,
change the spec and rebuild.

## Workflow

### 1. Find sources

Search for sources that speak directly to the question. Prefer primary and
accountable ones: official guidance, standards bodies, the paper rather than
the press release, vendor documentation rather than a blog about it. Search
results and page summaries are for finding URLs only; nothing you read there
is quotable.

Aim for a handful of good sources rather than many thin ones, and include the
best source you can find for the other side or for the main caveat.

### 2. Snapshot them

```bash
python3 <skill>/scripts/quotepack.py fetch URL [URL ...]
```

This saves a snapshot of each page under `quote-pack-work/` and writes a
numbered sentence listing to `quote-pack-work/sources/<host>-<id>.txt`:

```
[54] Habits that can improve your sleep include:

[58] - Avoiding large meals and alcohol before bedtime.
[59] - Avoiding caffeine in the afternoon or evening.
```

Run `fetch` and `build` from the same directory, since both look for
`quote-pack-work/` there. In the spec, use the same URL string you passed to
`fetch`. If the script prints `REDIRECTED`, check the listing's title line:
you may have been sent to a different page than the one you wanted.

Read these listings, and choose from them only. Papers can run to a thousand
sentences; `grep '## '` on the listing gives you the section map, then read
the sections that matter. The sentence number is the one in `[brackets]`; if
you use `grep -n`, the number it adds in front is a file line number and will
quote the wrong sentence without any error. They are exactly the text the
script can quote, split exactly where it will split. A web-fetch tool that
returns a summary is no substitute, since what it returns may not be verbatim.

A fetch can also "succeed" and be useless: a bot-challenge page, or the empty
shell of a page that renders with JavaScript. The script marks these `THIN`
when very little text came back, but the listing is the real check. If it
doesn't contain the article, treat it as a failure. For papers, PubMed
abstract pages and most publisher sites (Wiley, Elsevier, Nature) block
scripts; the PubMed Central copy (`pmc.ncbi.nlm.nih.gov/articles/PMC...`)
usually works.

If a fetch fails (paywall, bot block, a page rendered by JavaScript), pick a
different source. Don't paste the text into a local file to get around it;
that would make you the author of the "source". `--allow-local` exists for
documents the user gives you, and the page flags those as not independently
retrievable.

Markers in the listing: `## ` heading, `- ` list item, `| ` table row, and
`> ` text inside a blockquote, meaning the source is itself quoting someone
else. Check who is speaking before you use a `> ` line. Headings have their
own sentence numbers, so a range that spans one includes it (it renders as a
heading inside the quote); split the range in two if you don't want that.

### 3. Choose passages and their order

Order is the only rhetoric you have, so use it to build the argument the way a
careful brief would:

1. the passage that most directly answers the question
2. the reasons or evidence behind it
3. the strongest qualification, exception or opposing view
4. what to do in practice

Choosing fairly matters more here than anywhere, because selection is the one
place your judgment enters:

- A quote must mean on the page what it means in the source. Read around it.
  Don't quote a position the author sets up in order to knock down, a
  hypothetical, or a claim the next sentence walks back.
- Each passage should stand on its own. If it opens with "This" or "However",
  start one sentence earlier. When the antecedent is a whole paragraph back,
  the grey context is the fallback; raise `context` for that quote if needed.
- If good sources disagree, show the disagreement. If they don't support any
  clear answer, the pack should look like that too.
- Keep passages short, usually one to three sentences, and the pack to roughly
  six to twelve passages. The script refuses quotes longer than 8 sentences.

### 4. Write the spec

A JSON file with one key, `quotes`, in display order. Each entry is a `url`
plus a locator:

```json
{"quotes": [
  {"url": "https://www.cdc.gov/sleep/about/index.html", "from": 58, "to": 59},
  {"url": "https://www.nhs.uk/conditions/insomnia/", "from": 56},
  {"url": "https://example.org/paper", "match": "we found no association", "through": "in either cohort"}
]}
```

- `from` / `to`: sentence numbers from the listing, inclusive. `to` defaults
  to `from`. These refer to the snapshot, so they stay valid until you run
  `fetch --refresh`, which renumbers everything. Settle on your sources before
  you start picking numbers.
- `context` (optional, 0 to 6): grey sentences each side for this quote, when
  the default of 2 drags in too much (long list items) or too little.
- `match`: a snippet that occurs exactly once in the source. The quote becomes
  the whole sentence (or sentences) containing it. Add `through` to extend the
  quote to the sentence containing a later snippet. The snippets are search
  keys, never output; matching ignores case, spacing and curly-vs-straight
  quotes.

Sentence numbers are simplest. Use `match` when you want the spec to survive
a refetch.

### 5. Build and check

```bash
python3 <skill>/scripts/quotepack.py build spec.json -o quote-pack.html
```

The script prints every resolved quote, including any list lead-in it added.
Read that output against what you intended: right sentences, nothing that
changes meaning once isolated. Fix the spec and rebuild if needed. Options:
`--context N` (grey sentences each side, default 2), `--max-sentences N`,
`--workdir DIR`, `--allow-local`.

Then open the page for the user if you can (`open quote-pack.html` on macOS);
otherwise give them the path.

### 6. Reply

Keep the chat reply to logistics: where the file is, how many passages from
which sources, and anything that failed to fetch. Don't summarise the
argument or add your own conclusion alongside it. The user asked for evidence
they can weigh without taking your word, and a summary from you next to the
page quietly undoes that. If they then ask what you think, answer normally.

If you could not find sources that support an answer, say so plainly rather
than padding the pack with loosely related quotes.

When the pack is backing up something you said earlier and the sources turn
out not to support it, build the pack from what the sources do say, and tell
the user in one plain line that you are withdrawing the earlier claim. That
line is not a summary; leaving it out would let them think you still stand
behind it. Don't go on to restate the sources' position. The page does that.

## What the page contains

Every word is either source text (quotes, grey context, page titles taken
from the source's own `<title>`) or one of the fixed strings at the top of
the script (the standing notice, "Sources", "Retrieved", "Open at this
passage"). Each passage links back with a text-fragment URL, so the reader's
browser scrolls to and highlights the same words on the live page. A JSON
manifest embedded in the page records each source's URL, retrieval time,
content hash and sentence range.

Known limits, worth telling the user if they come up: sentence splitting is
deliberately cautious, so an unusual abbreviation or an odd citation marker
can make a "sentence" run long (never short); footnote and citation markers
such as a superscript `[1]` are dropped from quoted text when the script can
recognise them, and otherwise stay exactly as the source prints them (inline
`[6]` references and legislation.gov.uk's `[F1]` amendment marks, for
example); PDFs need `pypdf` or `pdftotext` and link to the page number
rather than the passage.
