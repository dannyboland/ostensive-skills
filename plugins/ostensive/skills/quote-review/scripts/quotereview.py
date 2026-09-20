#!/usr/bin/env python3
"""quotereview - review a draft using only other people's words.

The model picks phrases in the user's document to highlight, and picks
passages from a style guide (or, for factual points, from a web source) to set
beside them. This script produces every character of the page. Text on it
comes from exactly three places:

  1. the user's document, shown as written,
  2. the fetched style guides and sources (quoted whole sentences, grey
     context, page titles), and
  3. the constant strings in this file and in quotepack.py.

The spec has no field for a comment, a suggestion or a rewrite, so the model
cannot put one on the page. Fetching, sentence splitting, locators and the
whole-sentence rules are quotepack's; see quotepack.py.

Usage:
  quotereview.py fetch URL [URL ...]      snapshot guides/sources, write listings
  quotereview.py text DOCUMENT            print the document as the script reads it
  quotereview.py build SPEC -o OUT.html   resolve everything and write the page

The document may also be a web page (a URL or an .html file): the script
captures its visible text itself and says so on the page.

Standard library only. Documents: .md, .txt (and other plain text), .docx; .rtf,
.doc and .odt where macOS textutil or pandoc is available.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quotepack as qp  # noqa: E402
from quotepack import QuotePackError, esc  # noqa: E402

DEFAULT_WORKDIR = "quote-review-work"

# ---------------------------------------------------------------------------
# Constant strings: with quotepack's, the only words on the page that come
# from neither the document nor a source.
# ---------------------------------------------------------------------------
PAGE_TITLE = "Quote review"
NOTICE = (
    "The document below is shown as written. An AI model chose which phrases to "
    "highlight and which passages to set beside them. Every note is reproduced "
    "verbatim from the linked style guide or source, in whole sentences. The model "
    "did not write, shorten or reword any of them, and no other text on this page "
    "comes from it."
)
LEGEND_MARK = "Highlighted phrase in the document"
LABEL_STYLE = "Style guide"
LABEL_FACT = "Source"
LABEL_DOCUMENT = "Document"
LABEL_CAPTURED = "Web page, shown as retrieved"

SPEC_KEYS = {"document", "style_guides", "notes"}
NOTE_KEYS = {"phrase", "occurrence", "phrases", "cite"}


# ---------------------------------------------------------------------------
# Reading the document
# ---------------------------------------------------------------------------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_blocks(path):
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    blocks = []
    for p in root.iter(W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W + "t":
                parts.append(node.text or "")
            elif node.tag in (W + "tab", W + "br"):
                parts.append(" ")
        text = " ".join("".join(parts).split())
        if not text:
            continue
        style = p.find(f"{W}pPr/{W}pStyle")
        name = (style.get(W + "val") if style is not None else "") or ""
        listed = p.find(f"{W}pPr/{W}numPr") is not None
        kind = "h" if name.lower().startswith(("heading", "title")) else "li" if listed else "p"
        blocks.append({"kind": kind, "text": text})
    return blocks


def text_blocks(raw):
    """Plain text or markdown, shown as typed: markup characters are left alone."""
    blocks, para, fence = [], [], None

    def flush():
        if para:
            text = " ".join(" ".join(para).split())
            kind = "h" if re.match(r"#{1,6}\s", text) else "p"
            blocks.append({"kind": kind, "text": text})
            para.clear()

    for line in raw.splitlines():
        if fence is not None:
            fence.append(line)
            if line.strip().startswith(("```", "~~~")):
                blocks.append({"kind": "pre", "text": "\n".join(fence)})
                fence = None
        elif line.strip().startswith(("```", "~~~")):
            flush()
            fence = [line]
        elif not line.strip():
            flush()
        elif re.match(r"\s*([-*+]|\d+[.)])\s", line):
            flush()
            blocks.append({"kind": "li", "text": " ".join(line.split())})
        elif re.match(r"#{1,6}\s", line):
            flush()
            para.append(line)
            flush()
        else:
            para.append(line)
    if fence is not None:
        blocks.append({"kind": "pre", "text": "\n".join(fence)})
    flush()
    return blocks


def converted_text(path):
    """Plain text of a word-processor file, via the converter the system provides."""
    for cmd in (["textutil", "-convert", "txt", "-stdout", path],          # macOS
                ["pandoc", "--to", "plain", "--wrap=none", path]):
        if shutil.which(cmd[0]):
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.decode("utf-8", "replace")
    raise QuotePackError(f"{path}: can't read this format here (needs macOS textutil or pandoc); "
                         "ask the user for a .docx, .md or .txt copy")


def is_page(path):
    return path.lower().startswith(("http://", "https://")) or path.lower().endswith((".html", ".htm"))


def page_blocks(path, workdir, refresh=False):
    """(snapshot, blocks) for a document that is a web page: every visible block, nav and
    footer included, captured by this script so that nobody has to transcribe the page."""
    snap = qp.snapshot(path, workdir, refresh=refresh, allow_local=True, whole_page=True)
    blocks = []
    for s in snap["sentences"]:
        if s["block"] == len(blocks):
            blocks.append({"kind": s["kind"], "text": s["text"]})
        else:
            blocks[-1]["text"] += " " + s["text"]
    return snap, blocks


def read_document(path):
    if not os.path.isfile(path):
        raise QuotePackError(f"document not found: {path}")
    if path.lower().endswith(".docx"):
        blocks = docx_blocks(path)
    elif path.lower().endswith((".rtf", ".doc", ".odt", ".pages")):
        # a word processor's paragraph is one line of the converted text
        blocks = text_blocks(converted_text(path).replace("\n", "\n\n"))
    else:
        with open(path, "rb") as f:
            blocks = text_blocks(f.read().decode("utf-8", "replace"))
    if not blocks:
        raise QuotePackError(f"{path}: no text found")
    return blocks


# ---------------------------------------------------------------------------
# Finding phrases
# ---------------------------------------------------------------------------
def phrase_pattern(phrase):
    tokens = phrase.translate(qp.FOLD).split()
    if not tokens:
        raise QuotePackError("empty phrase")
    return re.compile(r"\s+".join(re.escape(t) for t in tokens), re.I)


def find_phrase(blocks, phrase):
    """Every (block, start, end) where phrase occurs, in document order."""
    pat, hits = phrase_pattern(phrase), []
    for bi, b in enumerate(blocks):
        folded = b["text"].translate(qp.FOLD)  # one-to-one, so offsets carry over
        hits += [(bi, m.start(), m.end()) for m in pat.finditer(folded)]
    return hits


def note_phrases(note):
    """The note's phrases as [{"phrase", "occurrence"?}]: one, or several sharing the note."""
    items = note["phrases"] if "phrases" in note else [note]
    return [{"phrase": i} if isinstance(i, str) else i for i in items]


def locate_phrase(blocks, note, n):
    phrase, where = note["phrase"], f"note {n}"
    hits = find_phrase(blocks, phrase)
    if not hits:
        raise QuotePackError(f"{where}: phrase not found in the document: {phrase!r} "
                             "(a phrase cannot cross a paragraph break)")
    occurrence = note.get("occurrence")
    if occurrence is None:
        if len(hits) > 1:
            raise QuotePackError(f"{where}: {phrase!r} occurs {len(hits)} times; lengthen it "
                                 'or add "occurrence": 1..' + str(len(hits)))
        return hits[0]
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or not 1 <= occurrence <= len(hits):
        raise QuotePackError(f"{where}: occurrence must be 1..{len(hits)}")
    return hits[occurrence - 1]


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------
def load_spec(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    free_text = ("The spec carries only a document path, URLs, phrases that already exist in "
                 "the document, and locators; comments and suggestions are not possible by design.")
    if not isinstance(spec, dict) or not {"document", "notes"} <= set(spec):
        raise QuotePackError('spec must be {"document": ..., "style_guides": [...], "notes": [...]}')
    if set(spec) - SPEC_KEYS:
        raise QuotePackError(f"unknown keys {sorted(set(spec) - SPEC_KEYS)}. {free_text}")
    guides = spec.get("style_guides", [])
    if not isinstance(spec["document"], str) or not isinstance(guides, list) \
            or not all(isinstance(g, str) for g in guides):
        raise QuotePackError("document must be a path or URL, and style_guides a list of URLs")
    if not isinstance(spec["notes"], list) or not spec["notes"]:
        raise QuotePackError("spec has no notes")
    for n, note in enumerate(spec["notes"], 1):
        if not isinstance(note, dict) or ("phrase" in note) == ("phrases" in note):
            raise QuotePackError(f"note {n}: needs a phrase, or phrases for a habit (not both)")
        if "phrases" in note and (not isinstance(note["phrases"], list) or not note["phrases"]
                                  or "occurrence" in note):
            raise QuotePackError(f"note {n}: phrases must be a non-empty list; put any "
                                 'occurrence inside the item, {"phrase": ..., "occurrence": 2}')
        for item in note_phrases(note):
            if not isinstance(item, dict) or not isinstance(item.get("phrase"), str) \
                    or ("phrases" in note and set(item) - {"phrase", "occurrence"}):
                raise QuotePackError(f"note {n}: each phrase is a string or "
                                     '{"phrase": ..., "occurrence": n}')
        if set(note) - NOTE_KEYS:
            raise QuotePackError(f"note {n}: unknown keys {sorted(set(note) - NOTE_KEYS)}. {free_text}")
        cites = note.get("cite")
        if not isinstance(cites, list) or not cites:
            raise QuotePackError(f"note {n}: needs at least one citation in cite. A highlight "
                                 "with nothing quoted beside it would be the model's say-so.")
        for c in cites:
            if not isinstance(c, dict) or not isinstance(c.get("url"), str):
                raise QuotePackError(f"note {n}: each citation needs a url")
            if set(c) - qp.ALLOWED_KEYS:
                raise QuotePackError(f"note {n}: unknown citation keys "
                                     f"{sorted(set(c) - qp.ALLOWED_KEYS)}. {free_text}")
    return spec


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
CSS = """
main.review{max-width:74rem}
.docname{margin:2.5rem 0 1rem;padding-bottom:.6rem;border-bottom:1px solid var(--rule)}
.pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,26rem);gap:0 2.5rem;align-items:start}
.doc p,.doc pre{margin:0 0 1rem}
.doc .h{font-weight:600;font-size:1.15em;margin-top:.6rem}
.doc .li{padding-left:1.1rem}
.doc pre{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;
background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:.7rem .9rem}
mark{background:var(--hl);color:inherit;border-bottom:2px solid var(--accent);padding:.08em 0;
scroll-margin-top:4rem;box-decoration-break:clone;-webkit-box-decoration-break:clone}
mark:target{background:var(--hl-strong)}
a.ref{font:600 11px ui-sans-serif,system-ui,sans-serif;color:var(--accent);text-decoration:none;
vertical-align:super;padding:0 .15em}
.notes{display:flex;flex-direction:column;gap:.9rem;margin-bottom:1.25rem}
.card{background:var(--card);border:1px solid var(--rule);border-radius:6px;
padding:1rem 1.1rem .8rem;font-size:15px;line-height:1.55;scroll-margin-top:4rem}
.card:target{border-color:var(--accent)}
.card .tag{display:flex;flex-wrap:wrap;gap:.2rem .6rem;align-items:baseline;margin-bottom:.6rem}
.card .tag a{font-weight:600;color:var(--accent);text-decoration:none}
.card .tag span{letter-spacing:.1em;text-transform:uppercase;font-size:11px}
.card .passage+.src{margin-bottom:.4rem}
.card .src+.passage{margin-top:1rem}
:root{--hl:#fde9b8;--hl-strong:#f9d272}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--hl:#4a3a12;--hl-strong:#6b5316}}
:root[data-theme="dark"]{--hl:#4a3a12;--hl-strong:#6b5316}
@media (max-width:60rem){.pair{grid-template-columns:minmax(0,1fr)}.notes{margin-left:1rem}}
@media print{.card{break-inside:avoid}}
"""


def render_block(block, marks):
    """The block's text as written, with <mark> around each highlighted span."""
    text, out, pos = block["text"], [], 0
    for label, start, end in sorted(marks, key=lambda m: m[1]):
        n = label.rstrip("abcdefghijklmnopqrstuvwxyz")
        out.append(qp.markup(text[pos:start]))
        out.append(f'<mark id="h{label}">{qp.markup(text[start:end])}</mark>'
                   f'<a class="ref" href="#n{n}">{label}</a>')
        pos = end
    out.append(qp.markup(text[pos:]))
    tag = "pre" if block["kind"] == "pre" else "p"
    return f'<{tag} class="{block["kind"]}">{"".join(out)}</{tag}>'


def span_labels(n, spans):
    """"3" for a note with one phrase; "3a", "3b", ... when a habit is marked in several places."""
    if len(spans) == 1:
        return [str(n)]
    letters = "abcdefghijklmnopqrstuvwxyz"
    return [f"{n}{letters[i % 26] * (i // 26 + 1)}" for i in range(len(spans))]


def render(doc_line, blocks, notes, doc_entry=None):
    """notes: [(n, [(block index, start, end), ...], [(label, snap, first, last, context)])]"""
    cards_at, marks_at, seen, manifest = {}, {}, [], [doc_entry] if doc_entry else []
    for note in notes:
        cards_at.setdefault(note[1][0][0], []).append(note)  # the card sits by the first instance
        for label, (bi, start, end) in zip(span_labels(note[0], note[1]), note[1]):
            marks_at.setdefault(bi, []).append((label, start, end))
    rows = []
    for bi, block in enumerate(blocks):
        cards = []
        for n, spans, cites in cards_at.get(bi, []):
            parts = []
            for label, snap, first, last, context in cites:
                if not any(s is snap for s in seen):
                    seen.append(snap)
                parts.append(qp.render_card(snap, first, last, context))
                manifest.append({"note": n, "kind": label, **qp.manifest_entry(snap, first, last)})
            labels = " / ".join(dict.fromkeys(c[0] for c in cites))
            links = " ".join(f'<a href="#h{l}">{l}</a>' for l in span_labels(n, spans))
            cards.append(f'<div class="card" id="n{n}"><div class="tag ui">'
                         f'{links}<span>{labels}</span></div>{"".join(parts)}</div>')
        rows.append(f'<section class="pair"><div class="doc">{render_block(block, marks_at.get(bi, []))}'
                    f'</div><aside class="notes">{"".join(cards)}</aside></section>')

    data = json.dumps(manifest).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="quotereview">
<title>{PAGE_TITLE}</title>
<style>{qp.CSS}{CSS}</style>
</head>
<body>
<main class="review">
<header>
<h1>{PAGE_TITLE}</h1>
<p class="ui">{NOTICE}</p>
<ul class="legend ui">
<li><mark>Aa</mark> {LEGEND_MARK}</li>
<li><span class="q">Aa</span> {qp.LEGEND_QUOTE}</li>
<li><span class="ctx">Aa</span> {qp.LEGEND_CONTEXT}</li>
</ul>
</header>
<p class="docname ui">{doc_line}</p>
{chr(10).join(rows)}
{qp.render_sources(seen)}
</main>
<script type="application/json" id="quotereview-manifest">{data}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_text(args):
    if is_page(args.document):
        blocks = page_blocks(args.document, args.workdir, refresh=args.refresh)[1]
    else:
        blocks = read_document(args.document)
    for b in blocks:
        print(qp.plain(b["text"]).replace(" \u21b5 ", "\n") + "\n")
    return 0


def cmd_build(args):
    spec = load_spec(args.spec)
    doc, doc_entry = spec["document"], None
    if is_page(doc):
        page, blocks = page_blocks(doc, args.workdir)
        where = qp.source_name(page)
        if not page["local"]:
            where = f'<a href="{esc(page["final_url"])}" rel="noopener noreferrer">{esc(where)}</a>'
        doc_line = (f'{LABEL_DOCUMENT}: {esc(page["title"] or qp.source_name(page))} &middot; {where}<br>'
                    f'{LABEL_CAPTURED} &middot; {qp.LABEL_RETRIEVED} {esc(page["retrieved"])}')
        doc_entry = {"document": page["url"], "sha256": page["sha256"], "retrieved": page["retrieved"]}
    else:
        blocks = read_document(doc)
        doc_line = f"{LABEL_DOCUMENT}: {esc(os.path.basename(doc))}"
    guides = {qp.cache_key(u) for u in spec.get("style_guides", [])}

    notes, taken, snaps = [], [], {}
    for n, note in enumerate(spec["notes"], 1):
        spans = []
        for item in note_phrases(note):
            bi, start, end = locate_phrase(blocks, item, n)
            for other, obi, ostart, oend in taken:
                if obi == bi and start < oend and ostart < end:
                    raise QuotePackError(
                        f"note {n}: {item['phrase']!r} overlaps a phrase of note {other}. "
                        "Use one note with several citations instead.")
            taken.append((n, bi, start, end))
            spans.append((bi, start, end))
        spans.sort()
        cites = []
        for c in note["cite"]:
            key = qp.cache_key(c["url"])
            if key not in snaps:
                cached = os.path.exists(os.path.join(args.workdir, "cache", key + ".json"))
                if "from" in c and not cached:
                    raise QuotePackError(f"note {n}: sentence numbers refer to a snapshot; "
                                         f"run `fetch` on {c['url']} first")
                snaps[key] = qp.snapshot(c["url"], args.workdir, allow_local=args.allow_local)
            try:
                first, last = qp.resolve(c, n, snaps[key])
            except QuotePackError as e:
                raise QuotePackError(str(e).replace(f"quote {n}:", f"note {n}:", 1))
            if last - first + 1 > args.max_sentences:
                raise QuotePackError(f"note {n}: {last - first + 1} sentences exceeds "
                                     f"--max-sentences {args.max_sentences}")
            context = c.get("context", args.context)
            if not isinstance(context, int) or isinstance(context, bool) or not 0 <= context <= 6:
                raise QuotePackError(f"note {n}: context must be an integer from 0 to 6")
            cites.append((LABEL_STYLE if key in guides else LABEL_FACT, snaps[key], first, last, context))
        notes.append((n, spans, cites))

    # A guide URL that is listed but never cited, or cited but not listed, is
    # usually a typo, and would quietly put the wrong label on a card.
    cited = {qp.cache_key(c["url"]): c["url"] for note in spec["notes"] for c in note["cite"]}
    guide_hosts = {qp.urllib.parse.urlparse(u).netloc for u in spec.get("style_guides", [])}
    for u in spec.get("style_guides", []):
        if qp.cache_key(u) not in cited:
            print(f"warning: style guide never cited: {u}")
    for key, u in cited.items():
        if key not in guides and qp.urllib.parse.urlparse(u).netloc in guide_hosts - {""}:
            print(f'warning: labelled "{LABEL_FACT}" but on the same site as a style guide: {u}\n'
                  "         Add it to style_guides if it is a guide page.")

    # Number the notes in reading order, whatever order the spec listed them in.
    notes.sort(key=lambda x: x[1][0])
    notes = [(i,) + note[1:] for i, note in enumerate(notes, 1)]

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(render(doc_line, blocks, notes, doc_entry))
    print(f"wrote {args.output}\n")
    for n, spans, cites in notes:
        for label, (bi, start, end) in zip(span_labels(n, spans), spans):
            print(f"{label}. “{qp.plain(blocks[bi]['text'][start:end])}”")
        for label, snap, first, last, _ in cites:
            sents = snap["sentences"]
            print(f"   {label} [{first}-{last}] {qp.source_name(snap)}")
            for i in sorted(qp.lead_ins(snap, first, last)):
                print(f"      (with list lead-in [{i}]) {qp.plain(sents[i]['text'])}")
            print("      " + qp.plain(" ".join(s["text"] for s in sents[first:last + 1])))
        print()
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=DEFAULT_WORKDIR, help="snapshot and listing directory")
    common.add_argument("--allow-local", action="store_true",
                        help="permit a local file as a style guide or source (flagged on the page)")

    f = sub.add_parser("fetch", parents=[common], help="snapshot style guides and sources")
    f.add_argument("urls", nargs="+")
    f.add_argument("--refresh", action="store_true", help="refetch even if cached")
    f.set_defaults(func=qp.cmd_fetch)

    qp.add_navigation(sub, common)

    t = sub.add_parser("text", parents=[common], help="print the document as the script reads it")
    t.add_argument("document")
    t.add_argument("--refresh", action="store_true", help="recapture a web page")
    t.set_defaults(func=cmd_text)

    b = sub.add_parser("build", parents=[common], help="build the review page from a spec")
    b.add_argument("spec")
    b.add_argument("-o", "--output", required=True)
    b.add_argument("--context", type=int, default=1, help="context sentences each side (default 1)")
    b.add_argument("--max-sentences", type=int, default=6, help="longest allowed quote (default 6)")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except QuotePackError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
