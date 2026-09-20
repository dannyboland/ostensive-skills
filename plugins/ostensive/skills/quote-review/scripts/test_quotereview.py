#!/usr/bin/env python3
"""Tests for the guarantees quotereview makes. Run: python3 test_quotereview.py"""
import json
import os
import re
import tempfile
import unittest
from types import SimpleNamespace

import quotereview as qr
from quotereview import qp

GUIDE = """<html><head><title>House Style</title></head><body><article><h1>House style</h1>
<p>Avoid in order to; instead, use to. Use active voice where you can.</p>
<h2>Don’t</h2><ul><li>say please in instructions</li><li>write click here</li></ul>
<p>""" + "Filler sentence so the article passes the main-content threshold. " * 10 + """</p>
</article></body></html>"""

DRAFT = """# Plan

In order to ship, the “work” will be done by us. Please click here.

In order to   rest,
we stop.
"""


class QuoteReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.guide = os.path.join(self.dir, "guide.html")
        self.draft = os.path.join(self.dir, "draft.md")
        for path, text in ((self.guide, GUIDE), (self.draft, DRAFT)):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

    def build(self, notes, guides=True):
        spec = os.path.join(self.dir, "spec.json")
        with open(spec, "w") as f:
            json.dump({"document": self.draft, "style_guides": [self.guide] if guides else [],
                       "notes": notes}, f)
        out = os.path.join(self.dir, "out.html")
        qr.cmd_build(SimpleNamespace(spec=spec, output=out, workdir=os.path.join(self.dir, "w"),
                                     allow_local=True, context=1, max_sentences=6))
        return open(out, encoding="utf-8").read()

    def cite(self, match):
        return [{"url": self.guide, "match": match}]

    def test_shared_quotepack_copy_has_not_drifted(self):
        here = os.path.dirname(os.path.abspath(__file__))
        original = os.path.join(here, "..", "..", "quote-pack", "scripts", "quotepack.py")
        if not os.path.exists(original):
            self.skipTest("installed standalone; no sibling skill to compare with")
        with open(original, "rb") as a, open(os.path.join(here, "quotepack.py"), "rb") as b:
            self.assertEqual(a.read(), b.read(), "run tools/sync-shared.sh")

    def test_highlight_and_whole_sentence_note(self):
        page = self.build([{"phrase": "will be done by us", "cite": self.cite("active voice")}])
        self.assertIn('<mark id="h1">will be done by us</mark>', page)
        self.assertIn('<span class="q">Use active voice where you can.</span>', page)
        self.assertIn(">Style guide<", page)

    def test_non_guide_url_is_labelled_source(self):
        page = self.build([{"phrase": "ship", "cite": self.cite("active voice")}], guides=False)
        self.assertIn(">Source<", page)
        self.assertNotIn(">Style guide<", page)

    def test_phrase_matching_is_forgiving_about_quotes_and_spacing(self):
        page = self.build([{"phrase": 'the "work" will', "cite": self.cite("active voice")},
                           {"phrase": "in order to rest, we", "cite": self.cite("instead, use to")}])
        self.assertIn("<mark id=\"h1\">the “work” will</mark>", page)
        self.assertIn('<mark id="h2">In order to rest, we</mark>', page)

    def test_ambiguous_missing_and_overlapping_phrases_are_refused(self):
        c = self.cite("active voice")
        for notes in ([{"phrase": "In order to", "cite": c}],
                      [{"phrase": "synergy", "cite": c}],
                      [{"phrase": "In order to", "occurrence": 3, "cite": c}],
                      [{"phrase": "done by us", "cite": c}, {"phrase": "by us. Please", "cite": c}]):
            with self.assertRaises(qr.QuotePackError):
                self.build(notes)
        self.assertIn('id="h2"', self.build([{"phrase": "In order to", "occurrence": 1, "cite": c},
                                             {"phrase": "In order to", "occurrence": 2, "cite": c}]))

    def test_spec_has_no_room_for_model_text(self):
        c = self.cite("active voice")
        for notes in ([{"phrase": "ship", "cite": c, "comment": "tighten this"}],
                      [{"phrase": "ship", "cite": [{**c[0], "suggestion": "deliver"}]}],
                      [{"phrase": "ship"}],
                      [{"phrase": "ship", "cite": []}]):
            with self.assertRaises(qr.QuotePackError):
                self.build(notes)

    def test_list_item_note_brings_its_lead_in(self):
        page = self.build([{"phrase": "click here", "cite": self.cite("write click here")}])
        self.assertIn('<span class="q">Don’t</span>', page)

    def test_every_word_is_document_source_or_constant(self):
        page = self.build([{"phrase": "In order to", "occurrence": 1, "cite": self.cite("instead, use to")},
                           {"phrase": "Please", "cite": self.cite("say please")}])
        body = re.sub(r"<(style|script)\b.*?</\1>", " ", page, flags=re.S)
        words = set(re.findall(r"[^\W\d_]+", qp.html.unescape(re.sub(r"<[^>]+>", " ", body))))
        snap = qp.snapshot(self.guide, os.path.join(self.dir, "w"), allow_local=True)
        consts = [v for m in (qr, qp) for k, v in vars(m).items()
                  if k.isupper() and isinstance(v, str) and k != "CSS"]
        allowed = " ".join([DRAFT, "draft.md guide.html Aa", snap["title"], snap["retrieved"]]
                           + [s["text"] for s in snap["sentences"]] + consts)
        self.assertEqual(words - set(re.findall(r"[^\W\d_]+", allowed)), set())


if __name__ == "__main__":
    unittest.main()
