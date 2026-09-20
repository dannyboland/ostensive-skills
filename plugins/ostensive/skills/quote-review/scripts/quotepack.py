#!/usr/bin/env python3
"""quotepack - build a quote-only briefing pack with "clean hands".

The model picks passages and their order; this script produces every character
of the page. Text on the page comes from exactly two places:

  1. the fetched sources (quoted sentences, greyed context, page titles), and
  2. the constant strings in this file (labels and the standing notice).

Nothing in the spec is ever rendered as prose. The spec may only contain URLs
and locators, and a locator is either a sentence-number range or a search
string that must already exist verbatim in the source. Quotes are always whole
sentences in one contiguous run, so nothing can be trimmed or spliced.

Usage:
  quotepack.py fetch URL [URL ...]      snapshot sources, write numbered listings
  quotepack.py build SPEC -o OUT.html   resolve locators and write the page

Standard library only. PDF sources need `pypdf` or the `pdftotext` binary.
"""
import argparse
import datetime
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFAULT_WORKDIR = "quote-pack-work"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) quotepack/1.0 (quote-with-attribution tool)"
MAX_BYTES = 25 * 1024 * 1024

# ---------------------------------------------------------------------------
# Constant strings. These are the only words on the page not taken from a source.
# ---------------------------------------------------------------------------
PAGE_TITLE = "Quote pack"
NOTICE = (
    "Every passage on this page is reproduced verbatim from the linked source, "
    "in whole sentences. An AI model chose the passages and their order. It did "
    "not write, shorten or reword any of them, and no other text on this page "
    "comes from it."
)
LEGEND_QUOTE = "Quoted passage"
LEGEND_CONTEXT = "Surrounding text from the same source, shown for context"
LABEL_OPEN = "Open at this passage"
LABEL_OPEN_PLAIN = "Open source"
LABEL_RETRIEVED = "Retrieved"
LABEL_PAGE = "Page"
LABEL_SOURCES = "Sources"
LABEL_LOCAL = "Local file. Readers cannot retrieve it independently."
LABEL_BLOCKQUOTE = "The source presents this as a quotation from elsewhere."


class QuotePackError(Exception):
    pass


# ---------------------------------------------------------------------------
# HTML -> blocks of text
# ---------------------------------------------------------------------------
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
BLOCK = {"address", "article", "aside", "blockquote", "body", "caption", "dd",
         "details", "div", "dl", "dt", "fieldset", "figcaption", "figure",
         "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
         "html", "li", "main", "nav", "ol", "p", "pre", "section", "summary",
         "table", "tbody", "tfoot", "thead", "tr", "ul"}
CELLS = {"td", "th"}  # a table row is one unit: a lone cell means nothing without its row
# Private-use sentinels keep superscript/subscript structure through the text
# pipeline, so "10<sup>6</sup>" is never flattened into "106".
SUP_OPEN, SUP_CLOSE, SUB_OPEN, SUB_CLOSE = "\ue000", "\ue001", "\ue002", "\ue003"
SENTINELS = re.compile("[\ue000-\ue003]")
FOOTNOTE_MARK = re.compile(  # "1", "[a]", "[footnote 28]", "15, 17", "3-5"
    r"^\s*[\[(]?\s*(?:(?:foot)?note|fn|ref)?\.?\s*[\w*\u2020\u2021]{1,3}"
    r"(?:\s*[,\u2013-]\s*\w{1,3})*\s*[\])]?\s*$", re.I)
CITATION_LIST = re.compile(r"^\s*\d{1,3}(?:\s*[,\u2013-]\s*\d{1,3})*\s*$")
CHALLENGE_TITLES = re.compile(r"client challenge|just a moment|attention required|access denied|"
                              r"are you a robot|captcha|enable javascript", re.I)
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_ALWAYS = {"script", "style", "noscript", "template", "svg", "iframe",
               "canvas", "object", "select", "textarea", "button"}
SKIP_CHROME = {"nav", "footer", "aside", "form"}
# Inline furniture that is not part of the author's sentence (footnote markers,
# wiki edit links, screen-reader-only labels).
SKIP_CLASSES = {"reference", "mw-editsection", "noprint", "sr-only",
                "visually-hidden", "screen-reader-text"}


class BlockExtractor(HTMLParser):
    def __init__(self, skip_chrome=True):
        super().__init__(convert_charrefs=True)
        self.skip_chrome = skip_chrome
        self.stack = []  # (tag, is_skip)
        self.skip_depth = 0
        self.buf = []
        self.blocks = []  # {"kind", "text", "main"}
        self.title_buf = []
        self.og_title = None
        self.title_done = False
        self.sup = None  # [buf length at <sup>, saw a link inside]
        self.lists = []  # for each open list, the block that came just before it

    def _is_skip(self, tag, attrs):
        if tag in SKIP_ALWAYS or (self.skip_chrome and tag in SKIP_CHROME):
            return True
        a = dict(attrs)
        if "hidden" in a:
            return True
        if self.skip_chrome and a.get("role") in ("navigation", "banner", "contentinfo", "complementary"):
            return True
        style = (a.get("style") or "").replace(" ", "").lower()
        if "display:none" in style:
            return True
        classes = set((a.get("class") or "").split())
        return bool(classes & SKIP_CLASSES)

    def _kind(self):
        tags = [t for t, _ in self.stack]
        for t in reversed(tags):
            if t in HEADINGS:
                return "h"
        if "blockquote" in tags:
            return "bq"
        if "li" in tags:
            return "li"
        if "tr" in tags:
            return "row"
        return "p"

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if text:
            tags = {t for t, _ in self.stack}
            kind = self._kind()
            self.blocks.append({"kind": kind, "text": text,
                                "main": bool(tags & {"article", "main"}),
                                "lead": self.lists[-1] if kind == "li" and self.lists else None})

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            if a.get("property") == "og:title" and a.get("content"):
                self.og_title = a["content"]
        if tag in BLOCK:
            self._flush()
            if tag in ("ul", "ol"):
                self.lists.append(self.blocks[-1] if self.blocks else None)
        elif tag == "br" or tag in CELLS:
            self.buf.append(" ")
        elif tag == "sup":
            self.sup = [len(self.buf), False]
            self.buf.append(SUP_OPEN)
        elif tag == "sub":
            self.buf.append(SUB_OPEN)
        elif tag == "a" and self.sup:
            self.sup[1] = True
        if tag in VOID:
            return
        skip = self._is_skip(tag, attrs)
        self.stack.append((tag, skip))
        if skip:
            self.skip_depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK:
            self._flush()
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                break
        else:
            return  # stray end tag
        if tag == "title" and self.title_buf:
            self.title_done = True
        if tag in BLOCK:
            self._flush()
            self.sup = None
            if tag in ("ul", "ol") and self.lists:
                self.lists.pop()
        elif tag == "sup" and self.sup:
            # A linked superscript that is just a mark ("1", "[a]") is a footnote
            # pointer, not the author's words; left in it reads as "hours1".
            # Unlinked superscripts (exponents, ordinals) are kept.
            # An unlinked number list straight after punctuation ("plaque.15, 17")
            # is a citation too; an exponent never follows punctuation.
            at, linked = self.sup
            inner = "".join(self.buf[at + 1:])
            before = "".join(self.buf[:at]).rstrip()[-1:]
            if not inner.strip() or (linked and FOOTNOTE_MARK.match(inner)) or (
                    before in ".,;:!?)\"\u201d\u2019" and CITATION_LIST.match(inner)):
                del self.buf[at:]
            else:
                self.buf.append(SUP_CLOSE)
            self.sup = None
        elif tag == "sub":
            self.buf.append(SUB_CLOSE)
        for _, skip in self.stack[i:]:
            if skip:
                self.skip_depth -= 1
        del self.stack[i:]

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "title":
            if self.skip_depth == 0 and not self.title_done:  # not an <svg><title>
                self.title_buf.append(data)
        elif self.skip_depth == 0:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_blocks(markup):
    best = None
    for skip_chrome in (True, False):
        ex = BlockExtractor(skip_chrome=skip_chrome)
        ex.feed(markup)
        ex.close()
        blocks = ex.blocks
        main = [b for b in blocks if b["main"]]
        if sum(len(b["text"]) for b in main) >= 500:
            blocks = main
        best = (ex, blocks)
        # Malformed markup can leave a skipped tag open and swallow the page;
        # if almost nothing came out, retry keeping nav/footer/aside/form.
        if sum(len(b["text"]) for b in blocks) >= 200:
            break
    ex, blocks = best
    title = " ".join("".join(ex.title_buf).split()) or (ex.og_title or "").strip()
    index = {id(b): i for i, b in enumerate(blocks)}
    return title, [{"kind": b["kind"], "text": b["text"],
                    "lead": index.get(id(b["lead"]))} for b in blocks]


def text_to_blocks(text):
    blocks = []
    for page_no, page in enumerate(text.split("\f"), 1):
        for para in re.split(r"\n\s*\n", page):
            t = " ".join(para.split())
            if t:
                blocks.append({"kind": "p", "text": t, "page": page_no})
    return blocks


def pdf_to_text(raw):
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(io.BytesIO(raw))
        return "\f".join((p.extract_text() or "") for p in reader.pages)
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-", "-"], input=raw,
                             capture_output=True, check=True)
        return out.stdout.decode("utf-8", "replace")
    raise QuotePackError("PDF source needs `pip install pypdf` or the pdftotext binary")


# ---------------------------------------------------------------------------
# Sentence splitting. Deliberately conservative: when unsure, do not split. A
# missed boundary only makes a quote longer; a false boundary would produce a
# partial sentence, which is the thing this tool exists to prevent.
# ---------------------------------------------------------------------------
ABBREV = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st", "mt", "ft", "rev",
    "hon", "gen", "col", "lt", "sgt", "capt", "cmdr", "gov", "sen", "rep",
    "pres", "inc", "ltd", "co", "corp", "plc", "llc", "bros", "vs", "v", "etc",
    "al", "cf", "eg", "ie", "viz", "approx", "est", "dept", "univ", "assn",
    "fig", "figs", "eq", "eqs", "sec", "ch", "vol", "vols", "pp", "p", "para",
    "ed", "eds", "edn", "trans", "op", "cit", "ibid", "nos", "art", "ref",
    "refs", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "mon", "tue", "tues", "wed", "thu", "thur", "thurs",
    "fri", "sat", "sun", "ave", "blvd", "rd", "min", "max", "no",
}
BOUNDARY = re.compile(
    "([.!?]+)((?:[\"'\u201d\u2019)\\]]|\ue000[^\ue001]*\ue001)*)\\s+(?=[\"'\u201c\u2018(\\[]?[A-Z0-9])")


def split_sentences(text):
    sentences, start = [], 0
    for m in BOUNDARY.finditer(text):
        if m.group(1).startswith("."):
            if len(m.group(1)) > 1:  # ellipsis
                continue
            before = text[start:m.start()]
            token = before.split()[-1] if before.split() else ""
            word = token.lstrip("([\"'“‘").lower()
            if word in ABBREV:
                continue
            if len(word) == 1 and word.isalpha():  # initials: "J. Smith"
                continue
            if "." in word:  # "e.g", "u.s", "ph.d", "3.2"
                continue
            if word.isdigit() and len(before.split()) == 1:  # "1. Introduction"
                continue
        end = m.end(2)
        sentences.append(text[start:end])
        start = m.end()
    tail = text[start:]
    if tail:
        sentences.append(tail)
    return sentences


# ---------------------------------------------------------------------------
# Fetch and snapshot
# ---------------------------------------------------------------------------
def is_local(url):
    return urllib.parse.urlparse(url).scheme in ("", "file")


def local_path(url):
    p = urllib.parse.urlparse(url)
    return urllib.request.url2pathname(p.path) if p.scheme == "file" else url


def canonical(url):
    if is_local(url):
        return "file://" + os.path.abspath(local_path(url))
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise QuotePackError(f"unsupported URL scheme: {url}")
    return urllib.parse.urlunparse(p._replace(fragment=""))


def cache_key(url):
    return hashlib.sha256(canonical(url).encode()).hexdigest()[:16]


def decode(raw, content_type):
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
    enc = m.group(1) if m else "utf-8"
    if isinstance(enc, bytes):
        enc = enc.decode("ascii")
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def download(url, allow_local):
    if is_local(url):
        if not allow_local:
            raise QuotePackError(f"{url}: local files need --allow-local")
        path = local_path(url)
        with open(path, "rb") as f:
            raw = f.read()
        ext = os.path.splitext(path)[1].lower()
        ctype = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain"}.get(ext, "text/html")
        return raw, ctype, canonical(url)
    req = urllib.request.Request(canonical(url), headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "en",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise QuotePackError(f"{url}: larger than {MAX_BYTES} bytes")
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw, resp.headers.get("Content-Type", ""), resp.geturl()


def snapshot(url, workdir, refresh=False, allow_local=False):
    """Return the cached snapshot for url, fetching it if needed."""
    cache_dir = os.path.join(workdir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, cache_key(url) + ".json")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        if snap["local"] and not allow_local:
            raise QuotePackError(f"{url}: local files need --allow-local")
        return snap

    raw, ctype, final_url = download(url, allow_local)
    if raw[:5] == b"%PDF-" or "application/pdf" in ctype:
        kind, title, blocks = "pdf", "", text_to_blocks(pdf_to_text(raw))
    elif "text/plain" in ctype:
        kind, title, blocks = "text", "", text_to_blocks(decode(raw, ctype))
    else:
        kind = "html"
        title, blocks = html_to_blocks(decode(raw, ctype))

    sentences, block_last = [], {}
    for bi, b in enumerate(blocks):
        parts = [b["text"]] if b["kind"] == "h" else split_sentences(b["text"])
        for s in parts:
            sentences.append({"block": bi, "kind": b["kind"], "text": s,
                              "page": b.get("page"), "lead": block_last.get(b.get("lead"))})
        block_last[bi] = len(sentences) - 1
    if not sentences:
        raise QuotePackError(f"{url}: no readable text (script-rendered or blocked page?)")

    snap = {
        "url": canonical(url),
        "final_url": final_url if is_local(url) else canonical(final_url),
        "local": is_local(url),
        "type": kind,
        "title": title,
        "retrieved": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sentences": sentences,
    }
    with open(os.path.join(cache_dir, cache_key(url) + ".raw"), "wb") as f:
        f.write(raw)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return snap


def write_listing(snap, workdir):
    """Numbered sentence listing: the text the model reads and chooses from."""
    out_dir = os.path.join(workdir, "sources")
    os.makedirs(out_dir, exist_ok=True)
    host = urllib.parse.urlparse(snap["final_url"]).netloc or "local"
    path = os.path.join(out_dir, f"{host}-{cache_key(snap['url'])}.txt")
    marks = {"h": "## ", "bq": "> ", "li": "- ", "row": "| ", "p": ""}
    lines = [f"# title: {snap['title']}", f"# url: {snap['url']}",
             f"# retrieved: {snap['retrieved']}",
             "# [n] = sentence number. '## ' heading, '> ' inside a blockquote "
             "(the source quoting someone else), '- ' list item, '| ' table row.", ""]
    prev_block = None
    for i, s in enumerate(snap["sentences"]):
        if prev_block is not None and s["block"] != prev_block:
            lines.append("")
        prev_block = s["block"]
        lines.append(f"[{i}] {marks[s['kind']]}{plain(s['text'])}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# Locators
# ---------------------------------------------------------------------------
FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                      "–": "-", "—": "-", "−": "-", " ": " ",
                      " ": " ", " ": " "})


def fold(s):
    return " ".join(SENTINELS.sub("", s).translate(FOLD).split()).lower()


def find_span(snap, needle, after=0):
    """Sentence index range covered by each occurrence of needle, from sentence `after` on."""
    spans, pos, text = [], 0, []
    for s in snap["sentences"][after:]:
        t = fold(s["text"])
        spans.append((pos, pos + len(t)))
        text.append(t)
        pos += len(t) + 1
    hay, n = " ".join(text), fold(needle)
    if not n:
        raise QuotePackError("empty match string")
    hits, at = [], hay.find(n)
    while at != -1:
        lo, hi = at, at + len(n)
        idx = [i for i, (a, b) in enumerate(spans) if a < hi and b > lo]
        hit = (after + idx[0], after + idx[-1])
        if hit not in hits:  # repeats inside one sentence are the same quote
            hits.append(hit)
        at = hay.find(n, at + 1)
    return hits


ALLOWED_KEYS = {"url", "from", "to", "match", "through", "context"}


def is_open_stem(text):
    """True for a line that only introduces what follows ("Employers can:")."""
    return SENTINELS.sub("", text).rstrip("\"'\u201d\u2019)]")[-1:] not in (".", "!", "?")


def resolve(item, n, snap):
    """Turn one spec item into an inclusive (first, last) sentence range."""
    first, last = locate(item, n, snap)
    sents = snap["sentences"]
    # A quote has to say something by itself. A heading, or the stem of a list
    # without any of its items, is verbatim but asserts nothing.
    if sents[last]["kind"] == "h":
        raise QuotePackError(
            f"quote {n}: ends on a heading ({plain(sents[last]['text'])!r}). A heading belongs "
            "to the text after it; extend the range into that text or stop before the heading.")
    introduces = any(s.get("lead") == last for s in sents[last + 1:])
    if introduces and is_open_stem(sents[last]["text"]):
        raise QuotePackError(
            f"quote {n}: ends on a list stem ({plain(sents[last]['text'])!r}) with none of its "
            f"items. Extend `to` to take in the items you mean, or quote just the item "
            "(the stem is added automatically and the items between are shown in grey).")
    return first, last


def locate(item, n, snap):
    where = f"quote {n}"
    total = len(snap["sentences"])
    if "from" in item:
        if "match" in item or "through" in item:
            raise QuotePackError(f"{where}: use either from/to or match/through, not both")
        first, last = item["from"], item.get("to", item["from"])
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (first, last)):
            raise QuotePackError(f"{where}: from/to must be integers")
        if not 0 <= first <= last < total:
            raise QuotePackError(f"{where}: range {first}-{last} outside 0-{total - 1}")
        return first, last
    if "match" not in item:
        raise QuotePackError(f"{where}: needs from/to or match")
    if not isinstance(item["match"], str):
        raise QuotePackError(f"{where}: match must be a string")
    hits = find_span(snap, item["match"])
    if not hits:
        raise QuotePackError(f"{where}: match text not found verbatim in {snap['url']}")
    if len(hits) > 1:
        raise QuotePackError(f"{where}: match text occurs {len(hits)} times "
                             f"(sentences {[h[0] for h in hits]}); lengthen it or use from/to")
    first, last = hits[0]
    if "through" in item:
        if not isinstance(item["through"], str):
            raise QuotePackError(f"{where}: through must be a string")
        ends = find_span(snap, item["through"], after=first)
        if not ends:
            raise QuotePackError(f"{where}: through text not found after the match")
        last = max(last, ends[0][1])
    return first, last


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or set(spec) != {"quotes"} or not isinstance(spec["quotes"], list):
        raise QuotePackError('spec must be {"quotes": [...]} and nothing else')
    if not spec["quotes"]:
        raise QuotePackError("spec has no quotes")
    for n, item in enumerate(spec["quotes"], 1):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise QuotePackError(f"quote {n}: needs a url")
        extra = set(item) - ALLOWED_KEYS
        if extra:
            raise QuotePackError(
                f"quote {n}: unknown keys {sorted(extra)}. The spec carries only URLs and "
                "locators; headings, labels and commentary are not possible by design.")
    return spec["quotes"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
CSS = """
:root{--bg:#f7f5f0;--card:#fffefb;--ink:#1d1c1a;--ctx:#8f8b83;--muted:#6b675f;
--rule:#e2ded4;--accent:#9a3b1e;--mark:#fbf1d8}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161513;
--card:#1e1d1a;--ink:#ece9e1;--ctx:#7d796f;--muted:#a19c91;--rule:#33312c;
--accent:#e08a66;--mark:#2f2a1c}}
:root[data-theme="dark"]{--bg:#161513;--card:#1e1d1a;--ink:#ece9e1;--ctx:#7d796f;
--muted:#a19c91;--rule:#33312c;--accent:#e08a66;--mark:#2f2a1c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:17px/1.65 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif}
main{max-width:46rem;margin:0 auto;padding:3rem 16px 4rem}
.ui{font:13px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--muted)}
header h1{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 .75rem;
font-family:ui-sans-serif,system-ui,sans-serif;color:var(--accent)}
header p{margin:0 0 1rem;max-width:38rem}
.legend{display:flex;flex-wrap:wrap;gap:.4rem 1.5rem;margin:0;padding:0;list-style:none}
.legend span{font-family:"Iowan Old Style",Palatino,Georgia,serif;font-size:15px}
ol.pack{list-style:none;margin:2.5rem 0 0;padding:0;counter-reset:q}
ol.pack>li{counter-increment:q;background:var(--card);border:1px solid var(--rule);
border-radius:6px;padding:1.5rem 1.5rem 1.1rem;margin:0 0 1.25rem;position:relative}
ol.pack>li::before{content:counter(q);position:absolute;top:-.7rem;left:1.25rem;
background:var(--bg);padding:0 .5rem;font:600 12px ui-sans-serif,system-ui,sans-serif;
color:var(--accent)}
.passage p{margin:0 0 .8rem}
.passage .h{font-weight:600}
.passage .bq{margin-left:1rem;padding-left:.9rem;border-left:2px solid var(--rule)}
.passage .row{font-size:.92em}
.passage .li{padding-left:1.1rem;text-indent:-1.1rem}
.passage .li::before{content:"\\2022\\00a0\\00a0";color:var(--ctx)}
.q{color:var(--ink);background:var(--mark);box-decoration-break:clone;
-webkit-box-decoration-break:clone;padding:.08em 0}
.ctx{color:var(--ctx)}
.src{border-top:1px solid var(--rule);margin-top:1rem;padding-top:.7rem;
display:flex;flex-wrap:wrap;gap:.2rem 1rem;align-items:baseline}
.src cite{font-style:normal;font-weight:600;color:var(--ink);flex:1 1 16rem;overflow-wrap:anywhere}
.src a,.sources a{color:var(--accent);text-underline-offset:2px}
.note{flex-basis:100%}
.sources{margin-top:3rem;border-top:1px solid var(--rule);padding-top:1.25rem}
.sources h2{font-size:12px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 .75rem;color:var(--muted)}
.sources ul{margin:0;padding:0;list-style:none}
.sources li{margin:0 0 .6rem;overflow-wrap:anywhere}
@media (max-width:30rem){body{font-size:16px}ol.pack>li{padding:1.25rem 1rem .9rem}}
@media print{body{background:#fff}ol.pack>li{break-inside:avoid}}
"""


def esc(s):
    return html.escape(s, quote=True)


def plain(text):
    """Sentence text for terminals and listings: 10^(6), H_(2)O."""
    return (text.replace(SUP_OPEN, "^(").replace(SUB_OPEN, "_(")
            .replace(SUP_CLOSE, ")").replace(SUB_CLOSE, ")"))


def markup(text):
    t = esc(text)
    t = re.sub(f"{SUP_OPEN}([^\ue000-\ue003]*){SUP_CLOSE}", r"<sup>\1</sup>", t)
    t = re.sub(f"{SUB_OPEN}([^\ue000-\ue003]*){SUB_CLOSE}", r"<sub>\1</sub>", t)
    return SENTINELS.sub("", t)


def lead_ins(snap, first, last):
    """Lead-in sentences that the quoted list items depend on for their meaning.

    "Drink alcohol." under "Avoid:" says the opposite on its own, so a list item
    is only ever shown together with the line that introduces its list.
    """
    sents, found = snap["sentences"], set()
    for i in range(first, last + 1):
        lead = sents[i].get("lead")
        while lead is not None and lead < first and lead not in found:
            if sents[lead]["kind"] != "h" and not is_open_stem(sents[lead]["text"]):
                break  # a complete sentence before the list; ordinary context
            found.add(lead)
            lead = sents[lead].get("lead")
    return found


def source_name(snap):
    if snap["local"]:
        return os.path.basename(local_path(snap["final_url"]))
    return urllib.parse.urlparse(snap["final_url"]).netloc


def fragment_link(snap, first, last):
    """Deep link that makes the reader's browser highlight the passage at the source."""
    url = snap["final_url"]
    sents = snap["sentences"]
    if snap["type"] == "pdf":
        page = sents[first].get("page")
        return (f"{url}#page={page}" if page else url), False
    if snap["type"] != "html" or snap["local"]:
        return url, False
    enc = lambda s: urllib.parse.quote(s, safe="").replace("-", "%2D")
    words = SENTINELS.sub("", " ".join(s["text"] for s in sents[first:last + 1])).split()
    if len(words) <= 8:
        frag = enc(" ".join(words))
    else:
        frag = enc(" ".join(words[:4])) + "," + enc(" ".join(words[-4:]))
    return f"{url}#:~:text={frag}", True


def render_passage(snap, first, last, context):
    sents = snap["sentences"]
    leads = lead_ins(snap, first, last)
    lo, hi = max(0, first - context), min(len(sents) - 1, last + context)
    lo = min([lo, *leads])  # everything between a lead-in and its item stays visible
    while hi > last and sents[hi]["kind"] == "h":  # a trailing heading belongs to what follows
        hi -= 1
    out, i = [], lo
    while i <= hi:
        block, kind, spans = sents[i]["block"], sents[i]["kind"], []
        while i <= hi and sents[i]["block"] == block:
            cls = "q" if first <= i <= last or i in leads else "ctx"
            spans.append(f'<span class="{cls}">{markup(sents[i]["text"])}</span>')
            i += 1
        out.append(f'<p class="{kind}">{" ".join(spans)}</p>')
    return "\n".join(out)


def render_card(snap, first, last, context):
    """One quoted passage with its grey context and attribution line."""
    sents = snap["sentences"]
    link, deep = fragment_link(snap, first, last)
    notes = []
    if snap["local"]:
        notes.append(LABEL_LOCAL)
    if any(s["kind"] == "bq" for s in sents[first:last + 1]):
        notes.append(LABEL_BLOCKQUOTE)
    page = sents[first].get("page")
    meta = [f'<cite>{esc(snap["title"] or source_name(snap))}</cite>',
            f'<span>{esc(source_name(snap))}</span>']
    if page:
        meta.append(f"<span>{LABEL_PAGE} {page}</span>")
    if not snap["local"]:
        meta.append(f'<a href="{esc(link)}" rel="noopener noreferrer">'
                    f'{LABEL_OPEN if deep else LABEL_OPEN_PLAIN}</a>')
    meta += [f'<span class="note">{esc(n)}</span>' for n in notes]
    return (f'<div class="passage">\n{render_passage(snap, first, last, context)}\n</div>'
            f'<div class="src ui">{"".join(meta)}</div>')


def manifest_entry(snap, first, last):
    return {"url": snap["url"], "sha256": snap["sha256"], "retrieved": snap["retrieved"],
            "sentences": [first, last], "lead_ins": sorted(lead_ins(snap, first, last))}


def render_sources(seen):
    sources = []
    for snap in seen:
        name = esc(snap["title"] or source_name(snap))
        if snap["local"]:
            head = f"{name} <span>{esc(source_name(snap))}</span>"
        else:
            head = (f'<a href="{esc(snap["final_url"])}" rel="noopener noreferrer">{name}</a> '
                    f'<span>{esc(source_name(snap))}</span>')
        sources.append(f"<li>{head}<br>{LABEL_RETRIEVED} {esc(snap['retrieved'])}</li>")
    return (f'<section class="sources ui">\n<h2>{LABEL_SOURCES}</h2>\n<ul>\n'
            + "\n".join(sources) + "\n</ul>\n</section>")


def render(resolved):
    cards, seen, manifest = [], [], []
    for snap, first, last, context in resolved:
        if not any(s is snap for s in seen):
            seen.append(snap)
        cards.append(f"<li>{render_card(snap, first, last, context)}</li>")
        manifest.append(manifest_entry(snap, first, last))

    data = json.dumps(manifest).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="quotepack">
<title>{PAGE_TITLE}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>{PAGE_TITLE}</h1>
<p class="ui">{NOTICE}</p>
<ul class="legend ui">
<li><span class="q">Aa</span> {LEGEND_QUOTE}</li>
<li><span class="ctx">Aa</span> {LEGEND_CONTEXT}</li>
</ul>
</header>
<ol class="pack">
{chr(10).join(cards)}
</ol>
{render_sources(seen)}
</main>
<script type="application/json" id="quotepack-manifest">{data}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_fetch(args):
    failed = 0
    for url in args.urls:
        try:
            snap = snapshot(url, args.workdir, refresh=args.refresh, allow_local=args.allow_local)
            path = write_listing(snap, args.workdir)
            n = len(snap["sentences"])
            thin = n < 15 or CHALLENGE_TITLES.search(snap["title"] or "")
            print(f"{'THIN ' if thin else 'ok   '} {url}\n      {n} sentences -> {path}")
            moved = urllib.parse.urlparse(snap["final_url"])
            asked = urllib.parse.urlparse(snap["url"])
            if not snap["local"] and moved.path.rstrip("/") != asked.path.rstrip("/"):
                print(f"      REDIRECTED to {snap['final_url']}\n"
                      f"      Its title is: {snap['title'] or '(none)'}\n"
                      "      Make sure this is the page you meant.")
            if thin:
                print("      Very little text came back: likely a bot-challenge page or a "
                      "script-rendered shell. Read the listing before relying on it.")
        except Exception as e:  # keep going: one dead source should not sink the rest
            failed += 1
            print(f"FAIL  {url}\n      {e}")
    return 1 if failed else 0


def cmd_build(args):
    items = load_spec(args.spec)
    resolved, snaps = [], {}
    for n, item in enumerate(items, 1):
        key = cache_key(item["url"])
        if key not in snaps:
            cached = os.path.exists(os.path.join(args.workdir, "cache", key + ".json"))
            if "from" in item and not cached:
                raise QuotePackError(f"quote {n}: sentence numbers refer to a snapshot; "
                                     f"run `fetch` on {item['url']} first")
            snaps[key] = snapshot(item["url"], args.workdir, allow_local=args.allow_local)
        first, last = resolve(item, n, snaps[key])
        if last - first + 1 > args.max_sentences:
            raise QuotePackError(f"quote {n}: {last - first + 1} sentences exceeds "
                                 f"--max-sentences {args.max_sentences}")
        context = item.get("context", args.context)
        if not isinstance(context, int) or isinstance(context, bool) or not 0 <= context <= 6:
            raise QuotePackError(f"quote {n}: context must be an integer from 0 to 6")
        resolved.append((snaps[key], first, last, context))

    page = render(resolved)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"wrote {args.output}\n")
    for n, (snap, first, last, _) in enumerate(resolved, 1):
        sents = snap["sentences"]
        text = plain(" ".join(s["text"] for s in sents[first:last + 1]))
        print(f"{n}. [{first}-{last}] {source_name(snap)}")
        for i in sorted(lead_ins(snap, first, last)):
            print(f"   (with list lead-in [{i}]) {plain(sents[i]['text'])}")
        print(f"   {text}\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workdir", default=DEFAULT_WORKDIR, help="snapshot and listing directory")
    common.add_argument("--allow-local", action="store_true",
                        help="permit local files as sources (flagged on the page)")

    f = sub.add_parser("fetch", parents=[common], help="snapshot URLs and write numbered sentence listings")
    f.add_argument("urls", nargs="+")
    f.add_argument("--refresh", action="store_true", help="refetch even if cached")
    f.set_defaults(func=cmd_fetch)

    b = sub.add_parser("build", parents=[common], help="build the page from a spec")
    b.add_argument("spec")
    b.add_argument("-o", "--output", required=True)
    b.add_argument("--context", type=int, default=2, help="context sentences each side (default 2)")
    b.add_argument("--max-sentences", type=int, default=8, help="longest allowed quote (default 8)")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args()
    try:
        sys.exit(args.func(args))
    except QuotePackError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
