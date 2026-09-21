#!/usr/bin/env python3
"""Tests for the guarantees quotepack makes. Run: python3 test_quotepack.py"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import quotepack as qp

PAGE = """<html><head><title>Sleep &amp; You</title><script>var s = "Not. Text.";</script></head>
<body><nav><p>Menu. Links.</p></nav><article><h1>On Sleep</h1>
<p>Dr. J. Smith of the U.S. Sleep Lab studied 3.5 million people. Adults need 7–9
hours<sup><a href="#n1">[1]</a></sup>, e.g. eight. “Is that enough?” she asked. It ran
for 10<sup>6</sup> seconds. See Fig. 2 for details.</p>
<p hidden>Hidden words. Never quotable.</p>
<blockquote><p>Sleep is the best meditation.</p></blockquote>
<h2>Don’t</h2><ul><li>drink coffee late</li><li>eat a big meal &lt;b&gt;late&lt;/b&gt;</li></ul>
<p>Some habits hurt. Habits to avoid:</p><ul><li>napping after lunch</li><li>screens in bed</li></ul>
<table><tr><td>Newborn</td><td>14–17 hours</td></tr></table>
<p>""" + "Padding sentence to pass the main-content threshold. " * 12 + """</p>
</article><footer><p>Footer. Junk.</p></footer></body></html>"""


class QuotePackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.src = os.path.join(cls.tmp.name, "page.html")
        with open(cls.src, "w", encoding="utf-8") as f:
            f.write(PAGE)
        cls.work = os.path.join(cls.tmp.name, "work")
        cls.snap = qp.snapshot(cls.src, cls.work, allow_local=True)
        cls.texts = [qp.plain(s["text"]) for s in cls.snap["sentences"]]

    def build(self, quotes):
        resolved = [(self.snap, *qp.resolve(q, n, self.snap), 1) for n, q in enumerate(quotes, 1)]
        return qp.render(resolved)

    def test_sentences_are_whole(self):
        self.assertIn("Dr. J. Smith of the U.S. Sleep Lab studied 3.5 million people.", self.texts)
        self.assertIn("See Fig. 2 for details.", self.texts)
        self.assertIn("“Is that enough?” she asked.", self.texts)

    def test_no_split_inside_a_quotation(self):
        text = ("It\u2019s a noise\u2014\u201cHe was walking. Suddenly he saw her.\u201d Then more. "
                "\"One. Two,\" she said.")
        self.assertEqual(qp.split_sentences(text),
                         ["It\u2019s a noise\u2014\u201cHe was walking. Suddenly he saw her.\u201d",
                          "Then more.", "\"One. Two,\" she said."])

    def test_footnote_dropped_but_exponent_kept(self):
        self.assertIn("Adults need 7–9 hours, e.g. eight.", self.texts)
        self.assertIn("It ran for 10^(6) seconds.", self.texts)
        self.assertIn("10<sup>6</sup>", self.build([{"url": self.src, "match": "seconds"}]))

    def test_chrome_hidden_and_script_text_excluded(self):
        joined = " ".join(self.texts)
        for junk in ("Menu", "Footer", "Hidden words", "Not. Text"):
            self.assertNotIn(junk, joined)

    def test_table_row_is_one_unit(self):
        self.assertIn("Newborn 14–17 hours", self.texts)

    def test_match_expands_to_whole_sentence(self):
        first, last = qp.resolve({"url": self.src, "match": "adults need 7-9"}, 1, self.snap)
        self.assertEqual(first, last)
        self.assertEqual(self.texts[first], "Adults need 7–9 hours, e.g. eight.")

    def test_match_folds_typographic_spaces_and_dashes(self):
        self.assertEqual(qp.fold("7\u20139\u00a0hours\u2009a\u202fnight \u2212 \u201cor\u201d so"),
                         "7-9 hours a night - \"or\" so")

    def test_match_must_exist_and_be_unique(self):
        with self.assertRaises(qp.QuotePackError):
            qp.resolve({"url": self.src, "match": "Adults need ten hours"}, 1, self.snap)
        with self.assertRaises(qp.QuotePackError):
            qp.resolve({"url": self.src, "match": "Padding sentence"}, 1, self.snap)

    def test_list_item_brings_its_lead_in(self):
        i = self.texts.index("drink coffee late")
        lead = self.texts.index("Don’t")
        self.assertEqual(qp.lead_ins(self.snap, i + 1, i + 1), {lead})
        page = self.build([{"url": self.src, "from": i + 1}])
        self.assertIn('<span class="q">Don’t</span>', page)
        self.assertIn('<span class="ctx">drink coffee late</span>', page)

    def test_stem_or_heading_alone_is_refused(self):
        stem = self.texts.index("Habits to avoid:")
        for bad in ({"from": stem}, {"from": stem - 1, "to": stem},
                    {"from": self.texts.index("Don\u2019t")}):
            with self.assertRaises(qp.QuotePackError):
                qp.resolve({"url": self.src, **bad}, 1, self.snap)
        self.assertEqual(qp.resolve({"url": self.src, "from": stem, "to": stem + 1}, 1, self.snap),
                         (stem, stem + 1))
        page = self.build([{"url": self.src, "from": stem + 2}])
        self.assertIn('<span class="q">Habits to avoid:</span>', page)
        self.assertIn('<span class="ctx">napping after lunch</span>', page)

    def test_spec_rejects_free_text(self):
        for bad in ({"quotes": [{"url": "u", "from": 1, "heading": "Buy now"}]},
                    {"quotes": [{"url": "u", "from": 1}], "title": "My view"},
                    {"quotes": []}):
            path = os.path.join(self.tmp.name, "bad.json")
            with open(path, "w") as f:
                json.dump(bad, f)
            with self.assertRaises(qp.QuotePackError):
                qp.load_spec(path)

    def test_source_markup_is_escaped(self):
        page = self.build([{"url": self.src, "match": "eat a big meal"}])
        self.assertIn("&lt;b&gt;late&lt;/b&gt;", page)

    def test_every_word_on_page_is_source_or_constant(self):
        """The invariant: strip source text and script constants; nothing may remain."""
        page = self.build([{"url": self.src, "match": "adults need"},
                           {"url": self.src, "match": "drink coffee"}])
        body = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S)
        words = set(re.findall(r"[^\W\d_]+", qp.html.unescape(re.sub(r"<[^>]+>", " ", body))))
        allowed = " ".join(self.texts + [self.snap["title"], os.path.basename(self.src),
                                         self.snap["retrieved"], "Aa"] +
                           [v for k, v in vars(qp).items()
                            if k.isupper() and isinstance(v, str) and k != "CSS"])
        self.assertEqual(words - set(re.findall(r"[^\W\d_]+", allowed)), set())

    def test_epub_chapters_metadata_and_section(self):
        import zipfile
        path = os.path.join(self.tmp.name, "guide.epub")
        chapter = "<html><body><h2>%s</h2><p>%s</p></body></html>"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:'
                       'xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            z.writestr("OPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf" '
                       'xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Craft</dc:title>'
                       '<dc:creator>A. Writer</dc:creator><dc:creator>B. Reviser</dc:creator></metadata><manifest>'
                       '<item id="b" href="two.xhtml"/><item id="a" href="one.xhtml"/></manifest>'
                       '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            z.writestr("OPS/one.xhtml", chapter % ("Rhythm", "Read it aloud. Listen for the beat."))
            z.writestr("OPS/two.xhtml", chapter % ("Adverbs", "Cut most of them. Keep the ones that work."))
        snap = qp.snapshot(path, self.work, allow_local=True)
        texts = [s["text"] for s in snap["sentences"]]
        self.assertEqual((snap["title"], snap["author"]), ("Craft", "A. Writer, B. Reviser"))
        self.assertEqual(texts[:2] + texts[3:5], ["Rhythm", "Read it aloud.", "Adverbs", "Cut most of them."])
        i = texts.index("Cut most of them.")
        self.assertEqual(qp.section_of(snap, i), "Adverbs")
        card = qp.render_card(snap, i, i, 1)
        self.assertIn("A. Writer", card)
        self.assertIn("Section: Adverbs", card)

    def test_pdf_text_repairs_page_breaks_and_finds_headings(self):
        wide = "This line is as wide as the body text of the book usually runs on a page."
        text = "\n".join([wide, wide, "It takes craft.", "OPINION PIECE: ON COMMAS",
                          "Do we expect somebody to play the violin without learning the",
                          "", "\fviolin? Of course not.", "", "the captain full speed ahead"])
        blocks = qp.text_to_blocks(text, extracted=True)
        self.assertEqual([b["kind"] for b in blocks], ["p", "h", "p", "p"])
        self.assertEqual(blocks[1]["text"], "OPINION PIECE: ON COMMAS")
        self.assertTrue(blocks[2]["text"].endswith("without learning the violin? Of course not."))
        inset = qp.text_to_blocks("\n".join([wide, wide + " It is so.1", "Next comes this.", "",
                                             "    An example set in", "    from the margin."]), extracted=True)
        self.assertEqual([b["kind"] for b in inset], ["p", "in"])
        self.assertIn("It is so. Next comes this.", inset[0]["text"])
        # plain text files are taken as typed: no repairs
        self.assertEqual(len(qp.text_to_blocks("cut the\n\nline here")), 2)

    def test_local_sources_need_opt_in(self):
        with self.assertRaises(qp.QuotePackError):
            qp.snapshot(self.src, self.work)


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.work = os.path.join(self.dir, "work")
        self.srcs = []
        for name in ("a.html", "b.html"):
            self.srcs.append(os.path.join(self.dir, name))
            with open(self.srcs[-1], "w", encoding="utf-8") as f:
                f.write(PAGE)

    def cli(self, *argv):
        """(exit code or message, stdout) of one command-line run"""
        out = io.StringIO()
        with mock.patch("sys.argv", ["quotepack.py", *argv, "--workdir", self.work]), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            qp.main()
        return exit_.exception.code, out.getvalue()

    def spec(self, content):
        path = os.path.join(self.dir, "spec.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_build_failures_are_one_line_errors(self):
        out = os.path.join(self.dir, "out.html")
        unreachable = json.dumps({"quotes": [{"url": "http://127.0.0.1:9/x", "match": "a"}]})
        for spec in (self.spec("{"), os.path.join(self.dir, "missing.json"), self.spec(unreachable)):
            code, _ = self.cli("build", spec, "-o", out)
            self.assertRegex(code, r"^error: .+$")
        self.assertFalse(os.path.exists(out))

    def test_show_names_one_of_several_sources(self):
        self.assertEqual(self.cli("fetch", *self.srcs, "--allow-local")[0], 0)
        self.assertIn("--url", self.cli("show", "0", "2")[0])
        code, out = self.cli("show", "0", "2", "--url", self.srcs[1])
        self.assertEqual(code, 0)
        self.assertIn("[0] ## On Sleep", out)

    def test_navigation_reads_only_what_was_fetched(self):
        code, _ = self.cli("search", "sleep", self.srcs[0])
        self.assertIn("not fetched yet", code)
        self.assertFalse(os.path.exists(os.path.join(self.work, "cache")))


if __name__ == "__main__":
    unittest.main()
