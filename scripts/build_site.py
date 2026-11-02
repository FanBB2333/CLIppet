#!/usr/bin/env python3
"""Fill in the generated parts of website/index.html.

The page shows CLIppet's mark from resources/logo.svg, with its colours
turned into CSS variables so it follows the paper and ink themes.  It
letters headings in the same hand as the drawings: a Latin subset of LXGW
WenKai GB Lite (SIL Open Font License 1.1).  The font is embedded as a
data: URL because browsers will not load a font file next to a page that
was opened from disk.  Both parts sit between markers in the page, and
this script rewrites them.

    python scripts/build_site.py            # rewrite website/index.html
    python scripts/build_site.py --check    # exit 1 if the page is stale

Needs fonttools and brotli (for WOFF2).  The font defaults to TeX Live's
copy; pass ``--font`` for another copy of LXGW WenKai GB Lite.
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from draw_art import BRAND, DEFAULT_FONT, INK, PAPER, WASH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "website" / "index.html"
LOGO = ROOT / "resources" / "logo.svg"

# Every character the page may letter in the hand: printable ASCII and a few marks.
HAND_TEXT = "".join(chr(c) for c in range(0x20, 0x7F)) + "→←↑↓·…—–‘’“”✓✻★×"

MARK_COLOURS = {
    f'fill="{WASH}"': 'style="fill:var(--mk-wash)"',
    f'fill="{PAPER}"': 'style="fill:var(--mk-paper)"',
    f'fill="{BRAND}"': 'style="fill:var(--mk-brand)"',
    f'stroke="{INK}"': 'style="stroke:var(--mk-ink)"',
    f'stroke="{BRAND}"': 'style="stroke:var(--mk-brand)"',
    f'stroke="{PAPER}"': 'style="stroke:var(--mk-paper)"',
}


def mark_symbol() -> str:
    """resources/logo.svg as a <symbol>, its colours left to the page's theme."""

    match = re.search(r"</defs>(.*)</svg>", LOGO.read_text(encoding="utf-8"), re.S)
    if not match:
        raise SystemExit(f"{LOGO}: no drawing after </defs>")
    body = match.group(1)
    for old, new in MARK_COLOURS.items():
        body = body.replace(old, new)
    left = re.findall(r"#[0-9a-fA-F]{6}", body)
    if left:
        raise SystemExit(f"{LOGO} uses colours the page does not theme: {sorted(set(left))}")
    return f'<symbol id="mark" viewBox="0 0 64 64">{body}</symbol>'


def hand_font(font_path: Path) -> str:
    """An @font-face rule carrying the Latin subset of the font as WOFF2."""

    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    options.flavor = "woff2"
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]  # keep the copyright and the licence
    options.hinting = False
    options.desubroutinize = True
    font = TTFont(str(font_path), recalcTimestamp=False)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=HAND_TEXT)
    subsetter.subset(font)
    buffer = io.BytesIO()
    font.flavor = "woff2"
    font.save(buffer)
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        "/* LXGW WenKai GB Lite, Latin subset (scripts/build_site.py).\n"
        "   Copyright 2022-2025 LXGW; Copyright 2020 The Klee Project Authors.\n"
        "   SIL Open Font License 1.1: https://openfontlicense.org */\n"
        '@font-face{font-family:"CLIppet Hand";font-display:swap;'
        f'src:url(data:font/woff2;base64,{data}) format("woff2")}}'
    )


def render(page: str, font_path: Path) -> str:
    blocks = [
        ("<!-- MARK_START -->", "<!-- MARK_END -->", mark_symbol()),
        ("/* HAND_FONT_START */", "/* HAND_FONT_END */", hand_font(font_path)),
    ]
    for start, end, content in blocks:
        pattern = re.escape(start) + r".*?" + re.escape(end)
        page, count = re.subn(pattern, lambda _: f"{start}\n{content}\n{end}", page, flags=re.S)
        if count != 1:
            raise SystemExit(f"{PAGE.name} must contain exactly one {start} … {end} block")
    return page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--check", action="store_true", help="only report whether the page is stale")
    args = parser.parse_args()

    source = PAGE.read_text(encoding="utf-8")
    rendered = render(source, args.font)
    if args.check:
        if rendered != source:
            print("website/index.html is stale; run python scripts/build_site.py")
            return 1
        print("website/index.html is up to date")
        return 0
    PAGE.write_text(rendered, encoding="utf-8")
    print("wrote", PAGE, f"({len(rendered.encode('utf-8')) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
