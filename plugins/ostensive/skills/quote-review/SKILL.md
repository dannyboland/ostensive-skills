---
name: quote-review
description: Review a document the user is writing by highlighting phrases and setting beside each one a verbatim passage from a style guide, or from a web source when the point is factual - one HTML page built by a script, with no AI-written comments, suggestions or rewrites on it. Use whenever the user asks for a quote review, an "ostensive review", a "sources only" review, a review "against the style guide", style-guide feedback, or feedback, editing notes or a fact-check on a draft where they want citations rather than the AI's opinion, don't want their wording rewritten, or don't trust AI suggestions. Also use when the user names a style guide (house style, GOV.UK, Google developer style, AP, Chicago, Microsoft) and a draft to check against it.
---

# Quote review

A quote review gives feedback on a draft without the reviewer saying anything.
You choose which phrases in the user's document to highlight, and which
passage from the style guide to put beside each one. For a factual problem,
you put a passage from a source beside it instead. A script builds the page:
the user's document as written, the highlights, and the quoted passages. You
never write a word that appears on it, so there are no comments, no suggested
rewrites and no "consider rephrasing".

The author gets two things from this. Every note is the style guide's own
rule, which they can check with one click, so there is nothing to argue with
you about. And their wording stays theirs: the page points at a phrase and
shows the rule, and the rewrite is left to the author.

This is the sibling of the `quote-pack` skill and uses its machinery
(`scripts/quotepack.py`) for fetching, sentence listings, locators and the
whole-sentence rules.

## The invariant

`scripts/quotereview.py` generates all of the HTML. The spec you write can
hold only: the path of the document, the URLs of the style guides, phrases
that already exist in the document, and locators into fetched sources.

- There is no field for a comment, label, severity or suggestion, and unknown
  fields are rejected. Don't look for a way round it (a "style guide" file you
  wrote yourself, a source picked because its wording happens to be the
  rewrite you want to suggest). If no guide or source says it, it doesn't go
  on the page.
- Every highlight needs at least one citation. A bare highlight would be your
  say-so.
- Quoted passages are whole sentences in one contiguous run, with grey
  context around them. A list item comes with the line that introduces its
  list, and a list stem or heading can't be quoted by itself.
- The document is read straight from the user's file and shown as written.
  Don't edit the user's file, and never edit the generated HTML.

## Workflow

### 1. Settle the document and the style guide

You need the draft: a file (`.md`, `.txt` or other plain text, `.docx`;
`.rtf`, `.doc` and `.odt` too where macOS `textutil` or `pandoc` exists), or
a web page, given as its URL or an `.html` file
and the style guide to review against. If the user hasn't named a guide and
the project doesn't make it obvious, ask which one they write to; the choice
is theirs, and a review against the wrong guide is noise. Public guides are
usually one page per topic (Google's developer style guide, GOV.UK's A to Z,
Microsoft's), so expect to fetch several pages of the same guide.

A guide the user gives you as a local file works with `--allow-local`: a
PDF, plain text or markdown, or a DRM-free EPUB. This is how book-length
guides come in (Williams' *Style*, Le Guin's *Steering the Craft*, Strunk), and
also the user's own workshop notes or house rules. Each note then shows the
book's title, author and section, and says it is a local file. Several guides
can be used in one review; list them all in `style_guides`. See "Book-length
guides" below before you start on one.

For anything that isn't plain text, run `quotereview.py text DOC` to read the
text the way the script sees it.

When the thing to review is a live page, put the URL in `document` and let
the script capture it. Don't save the page's text to a file yourself: deciding
what counts as the page is an editorial act, and it would leave no record.
The script takes every visible block in order, navigation and footer
included, keeps code blocks line for line, and prints the address and
retrieval time on the review. If the user has the page's source in a repo and
wants that reviewed instead, use the source file.

### 2. Read the draft and decide what deserves a note

Read the whole draft first. Note the places where it departs from the guide,
and any factual claim you have real reason to doubt. Be selective: a page
with forty highlights gets ignored. Prefer the departures that affect a
reader. Where the same problem recurs, make it one note with several
`phrases` (see step 4) so the page shows a habit, instead of several notes
that quote the same passage or one note that looks like a one-off.

### 3. Snapshot the guide pages and sources

```bash
python3 <skill>/scripts/quotereview.py fetch URL [URL ...]
```

Run `fetch` and `build` from the same directory. Each URL gets a numbered
sentence listing under `quote-review-work/sources/`. Choose passages from
those listings only; they are exactly what the script can quote. Long pages
(a word list can run to thousands of entries) are easier with `grep`. The
sentence number is the one in `[brackets]`; if you use `grep -n`, the number
it adds in front is a file line number and will quote the wrong sentence
without any error.
Watch for `THIN` and `REDIRECTED` in the fetch output: both mean the listing
may not be the page you wanted.

For a factual point, fetch a primary or accountable source that states the
fact directly (the project's own documentation, the official record, the
paper). The quote has to make the correction by itself, since you can't
explain it, so pick the sentence that plainly states what is true. If you
can't find a source that says it, leave the claim unhighlighted and mention
it in your chat reply instead.

### Book-length guides

A book is fetched once into one long listing, often thousands of sentences,
so you navigate it instead of reading it top to bottom:

```bash
python3 <skill>/scripts/quotereview.py fetch --allow-local steering-the-craft.epub
python3 <skill>/scripts/quotereview.py outline            # headings + sentence numbers
python3 <skill>/scripts/quotereview.py show 671 716       # read that run of sentences
python3 <skill>/scripts/quotereview.py search "adverbs qualifiers"
```

`outline` is the table of contents with sentence numbers, and in a craft book
the chapter titles are usually the topic index you need. `show` prints a run
of sentences, so you can read a chapter without reaching for `grep -n`.
`search` ranks sentences by keyword across everything fetched and shows the
section each hit is in; it only matches words, so use the guide's vocabulary
as well as your own ("needless words" as well as "wordy", "what tells" as well
as "show, don't tell"), and treat a hit as a place to start reading, never as
the quote.

Read by chapter, not by hit. An author's defence of a construction usually
sits in the same paragraph as the complaint about it ("I could almost state
this as a rule, but I won't"), and you only see the pair by reading the
passage through. That is what lets you leave alone what the author would
defend, and quote the qualification when you do flag something. These tools only help you find passages: nothing
they print reaches the page.

For a short guide, or once you know which chapters matter, the better order
is guide first, draft second: read the relevant chapters, then read the draft
with them in mind. You will catch what the guide cares about, which is not
always what you would have flagged.

Craft books argue more than they legislate. Le Guin or Williams will often
give a principle, the reasoning, and when to break it. Quote the sentence
that states the principle, and keep the author's own qualification when it
bears on the phrase you highlighted. Such arguments often run longer than
the quote limit; give the note two citations (the complaint, then the
defence) instead of one long one. In a rulebook the rule is often a
heading ("13. Omit needless words."); start the range at the heading and
carry it into the text below, since a heading can't be quoted alone. Skip
the author's exercises and their examples of bad writing, unless the example
is the point and its framing sentence comes with it.

Craft books are full of other people's prose: pages of Twain, Austen or
Woolf quoted as examples, sample sentences, exercise instructions. In the
listing, lines marked `» ` are set apart from the main text (in a PDF this
is judged from indentation) and lines marked `> ` are blockquotes. Check
whose words those are before citing one: the card will carry the book
author's name, and the page adds a line saying the passage is set apart, but
it is the author's own argument the user asked for.

Notes, such as workshop handouts, tend to be fragments. They quote fine, but
a fragment has to make sense to someone who wasn't in the room; prefer the
lines that do. Use only notes the user gives you. Writing up notes yourself
and citing them would put your words on the page by the back door.

### 4. Write the spec

```json
{"document": "draft.md",
 "style_guides": ["https://developers.google.com/style/word-list",
                  "https://developers.google.com/style/voice"],
 "notes": [
   {"phrase": "In order to",
    "cite": [{"url": "https://developers.google.com/style/word-list", "from": 1100, "to": 1101}]},
   {"phrase": "first released in 2010",
    "cite": [{"url": "https://en.wikipedia.org/wiki/Kubernetes",
              "match": "Kubernetes was announced by Google on June 6, 2014"}]}
 ]}
```

- `phrase`: the words to highlight, copied from the document. Matching
  ignores case, spacing and curly-vs-straight quotes, but the phrase must sit
  inside one paragraph and must not overlap another note's phrase. If it
  occurs more than once, lengthen it or add `"occurrence": 2`. Highlight the
  few words that are the problem, not the whole sentence.
- `phrases`: use this in place of `phrase` when the same fault is a habit.
  List each place it occurs, as strings or `{"phrase": ..., "occurrence": 2}`,
  and give the passage once: `{"phrases": ["heaves a sigh", "makes leisurely
  progress", "picks up her pace"], "cite": [...]}`. The page marks them 3a,
  3b, 3c beside a single card, which shows the author that it is a pattern
  without anyone saying so. Prefer this to repeating a citation, and to
  marking one instance and hoping they generalise. Keep it to real instances
  of the same thing; five or six is plenty.
- `cite`: one or more passages. Each has a `url` and a locator, the same as
  in quote-pack: `from`/`to` sentence numbers from the listing, or a `match`
  snippet (optionally with `through`) that occurs exactly once in the source.
  Optional `context` (0 to 6) sets the grey sentences each side.
- `style_guides`: the URLs that are style guide pages. A citation to one of
  them is labelled "Style guide" on the page; any other URL is labelled
  "Source". List every guide page you cite.

Choose the passage that states the rule, and include the guide's exception
when it has one and the exception could apply ("Use in order to when needed
to clarify meaning"). Quoting only the half of a rule that supports your
highlight is the review equivalent of quoting out of context.

### 5. Build and check

```bash
python3 <skill>/scripts/quotereview.py build review.json -o quote-review.html
```

The script prints each highlighted phrase with the passages resolved for it.
Read that list the way the author will: does each passage, with no
explanation, make clear what is wrong with the phrase next to it? If the
connection needs you to explain it, find a better passage or drop the note.
Notes are numbered in reading order whatever order the spec lists them in.
Options: `--context N` (default 1), `--max-sentences N` (default 6),
`--workdir DIR`, `--allow-local`.

Then open the page for the user if you can (`open quote-review.html` on
macOS); otherwise give them the path.

### 6. Reply

Keep the chat reply to logistics: where the page is, how many notes, which
guide pages and sources were used, anything that failed to fetch. Don't
restate the notes, and don't offer rewrites there either; putting your
suggestions in chat next to a page built to contain none undoes the point.
Four things do belong in the reply, briefly: a factual doubt you couldn't
find a source for, a problem the guide has no rule about, a rule the draft
breaks by leaving something out (see Known limits), and anything you chose
not to highlight because the guide defends it (deliberate repetition under a
guide that argues for repetition). Say each in a line, naming the guide's
section where there is one, and leave it there. List every note you dropped
for want of a passage, not just one: the author needs to know what the page
could not say, and a second guide may cover it. One craft book rarely covers
a whole story; if several dropped notes share a gap (emotion told instead of
shown, register, dialogue tags), say which kind of guide would fill it. If the user then asks for your own edits, give them.

## What the page contains

Every word is the user's document, source text (quoted passages, grey
context, page titles from the source's own `<title>`), or one of the fixed
strings in the two scripts (the standing notice, "Style guide", "Source",
"Document", "Open at this passage"). Each highlight is numbered and linked to
its note; each note links to the same words on the live page. A JSON manifest
in the page records every citation's URL, retrieval time, content hash and
sentence range.

Known limits: a note has to point at words that are in the document, so a
fault of omission (a list with no introductory sentence, a code block with no
language) can't be highlighted; the document is shown as typed, so markdown markup appears
literally; `.docx` formatting, tables and superscripts are flattened to plain
paragraphs; quoted passages inherit quote-pack's limits (cautious sentence
splitting that can run long but never short, citation markers dropped only
when recognised).
