"""Header easter egg: the flag links to one of the three songs, chosen at random."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import style  # noqa: E402


def test_flag_link_picks_each_url():
    rng = random.Random(0)
    seen = {style.flag_link(rng) for _ in range(60)}
    assert len(seen) == 3
    for url in style.FLAG_LINKS:
        assert any(url.replace("&", "&amp;") in s for s in seen)
    one = next(iter(seen))
    assert 'target="_blank"' in one and 'rel="noopener noreferrer"' in one and "cm-flag" in one
