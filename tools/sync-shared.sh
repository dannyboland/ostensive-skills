#!/bin/sh
# quotepack.py is shared by both skills. Each skill has to be self-contained to
# be installed or uploaded by itself, so quote-review carries a copy. Edit the
# quote-pack one, then run this. test_quotereview.py fails if the copies drift.
set -e
cd "$(dirname "$0")/../plugins/ostensive/skills"
cp quote-pack/scripts/quotepack.py quote-review/scripts/quotepack.py
echo "synced quote-review/scripts/quotepack.py"
