#!/usr/bin/env python3
"""Tests for the guarantees quotepack makes. Run: python3 test_quotepack.py"""
import json
import os
import re
import tempfile
import unittest

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

    def test_local_sources_need_opt_in(self):
        with self.assertRaises(qp.QuotePackError):
            qp.snapshot(self.src, self.work)


if __name__ == "__main__":
    unittest.main()
