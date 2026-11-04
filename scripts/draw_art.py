#!/usr/bin/env python3
"""Draw CLIppet's hand-drawn artwork into ``resources/``.

Every shape is traced with a seeded wobble and inked twice, the way
papertown's ``hand.ts`` draws its boxes, so a line never quite meets itself
and a box never quite closes.  Lettering is the outline of LXGW WenKai GB
Lite (SIL Open Font License 1.1), so the SVGs need no fonts and look the
same on GitHub, in an editor and in a browser.  The same seeds always draw
the same pictures.

    python scripts/draw_art.py                  # writes resources/*.svg
    python scripts/draw_art.py --braille        # prints the mark as braille (clippet/tui/look.py)
    python scripts/draw_art.py --frame shot.svg --name tui-envs.svg   # frames a Textual screenshot

Needs ``fonttools``.  The font defaults to TeX Live's copy; pass ``--font``
for another copy of LXGW WenKai GB Lite.
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

DEFAULT_FONT = Path(
    "/usr/local/texlive/2026/texmf-dist/fonts/truetype/public/lxgw-fonts/LXGWWenKaiGBLite-Regular.ttf"
)

# papertown's paper palette (web/src/hand.css, server/papertown/tui/look.py)
INK = "#2f261d"
PAPER = "#fcf8f0"
SHEET = "#f4eee2"
WASH = "#eadcc3"
SHADOW = "#d8cab1"
BRAND = "#c2663f"
BRAND_DEEP = "#9e4f2f"
MUTED = "#857560"
FAINT = "#cfc2ac"
OK = "#5f8a52"
WARN = "#b37f22"
BAD = "#b9543f"
INFO = "#3a76b0"
PLUM = "#7a5c9e"
GOLD = "#8a6d1f"
NIGHT = "#2b241e"
NIGHT_INK = "#ece2d3"
NIGHT_MUTED = "#a3927b"

AGENT_COLORS = {"Claude": PLUM, "Codex": OK, "QoderCLI": INFO, "Antigravity": GOLD}
AGENT_COMMANDS = {"Claude": "claude", "Codex": "codex", "QoderCLI": "qodercli", "Antigravity": "agy"}

Point = tuple[float, float]


# --- the hand -----------------------------------------------------------------


class Pen:
    """A seeded hand: the same seed draws the same wobble."""

    def __init__(self, seed: int) -> None:
        self.state = seed & 0xFFFFFFFF

    def random(self) -> float:
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state / 4294967296

    def jitter(self, amount: float) -> float:
        return (self.random() - 0.5) * amount


def _fmt(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _pt(p: Point) -> str:
    return f"{_fmt(p[0])},{_fmt(p[1])}"


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def sample_line(a: Point, b: Point, step: float = 22.0) -> list[Point]:
    n = max(2, int(_dist(a, b) / step) + 2)
    return [(a[0] + (b[0] - a[0]) * i / (n - 1), a[1] + (b[1] - a[1]) * i / (n - 1)) for i in range(n)]


def sample_arc(c: Point, r: float, a0: float, a1: float, every: float = 18.0) -> list[Point]:
    n = max(2, int(abs(a1 - a0) / every) + 2)
    out = []
    for i in range(n):
        a = math.radians(a0 + (a1 - a0) * i / (n - 1))
        out.append((c[0] + math.cos(a) * r, c[1] + math.sin(a) * r))
    return out


def sample_bezier(points: list[Point], n: int = 18) -> list[Point]:
    """A quadratic or cubic Bézier through its control points."""

    out = []
    for i in range(n):
        t = i / (n - 1)
        pts = list(points)
        while len(pts) > 1:
            pts = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(pts, pts[1:])]
        out.append(pts[0])
    return out


def _dedupe(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or _dist(out[-1], p) > 0.05:
            out.append(p)
    return out


def rounded_rect(x: float, y: float, w: float, h: float, r: float = 10.0) -> list[Point]:
    """The outline of a rounded rectangle, clockwise from the top-left."""

    r = max(0.0, min(r, w / 2, h / 2))
    pts: list[Point] = []
    pts += sample_line((x + r, y), (x + w - r, y))
    if r:
        pts += sample_arc((x + w - r, y + r), r, -90, 0)
    pts += sample_line((x + w, y + r), (x + w, y + h - r))
    if r:
        pts += sample_arc((x + w - r, y + h - r), r, 0, 90)
    pts += sample_line((x + w - r, y + h), (x + r, y + h))
    if r:
        pts += sample_arc((x + r, y + h - r), r, 90, 180)
    pts += sample_line((x, y + h - r), (x, y + r))
    if r:
        pts += sample_arc((x + r, y + r), r, 180, 270)
    return _dedupe(pts)


def ellipse(cx: float, cy: float, rx: float, ry: float, start: float = 0.0, sweep: float = 360.0) -> list[Point]:
    n = max(8, int(abs(sweep) / 14) + 2)
    out = []
    for i in range(n):
        a = math.radians(start + sweep * i / (n - 1))
        out.append((cx + math.cos(a) * rx, cy + math.sin(a) * ry))
    return out


def rotate(points: list[Point], degrees: float, about: Point) -> list[Point]:
    a = math.radians(degrees)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy = about
    return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in points]


def center(points: list[Point]) -> Point:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def wobble(points: list[Point], pen: Pen, amp: float, wavelength: float = 70.0) -> list[Point]:
    """Move the points by a smooth, slowly changing offset, as a hand drifts."""

    if len(points) < 2 or amp <= 0:
        return list(points)
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + _dist(a, b))
    total = lengths[-1] or 1.0
    n = max(2, int(total / wavelength) + 2)
    dx = [pen.jitter(amp * 2) for _ in range(n)]
    dy = [pen.jitter(amp * 2) for _ in range(n)]
    out = []
    for (x, y), length in zip(points, lengths):
        t = length / total * (n - 1)
        i = min(int(t), n - 2)
        f = _smooth(t - i)
        out.append((x + dx[i] + (dx[i + 1] - dx[i]) * f, y + dy[i] + (dy[i + 1] - dy[i]) * f))
    return out


def path_d(points: list[Point], closed: bool = False) -> str:
    """A smooth path through the points (Catmull-Rom as cubic Béziers)."""

    pts = points
    n = len(pts)
    if n < 2:
        return ""

    def at(i: int) -> Point:
        if closed:
            return pts[i % n]
        return pts[max(0, min(n - 1, i))]

    d = [f"M{_pt(pts[0])}"]
    last = n if closed else n - 1
    for i in range(last):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{_pt(c1)} {_pt(c2)} {_pt(p2)}")
    if closed:
        d.append("Z")
    return "".join(d)


def loop(points: list[Point], pen: Pen, over: float = 0.06) -> list[Point]:
    """A closed outline drawn in one go from a random start, running a little past it."""

    n = len(points)
    k = int(pen.random() * n)
    turned = points[k:] + points[:k]
    extra = max(1, int(n * over))
    return turned + turned[1 : extra + 1]


def ink(
    points: list[Point],
    pen: Pen,
    *,
    width: float = 2.0,
    color: str = INK,
    amp: float = 1.2,
    passes: int = 2,
    opacity: float = 1.0,
    dash: str | None = None,
    wavelength: float = 70.0,
) -> str:
    """A line drawn once firmly and once lightly over itself."""

    parts = []
    for k in range(passes):
        pts = wobble(points, pen, amp * (1.0 if k == 0 else 1.4), wavelength)
        op = opacity if k == 0 else opacity * 0.32
        w = width if k == 0 else width * 0.75
        attrs = f' stroke-opacity="{op:.2f}"' if op < 0.999 else ""
        if dash:
            attrs += f' stroke-dasharray="{dash}"'
        parts.append(
            f'<path d="{path_d(pts)}" fill="none" stroke="{color}" stroke-width="{w:.2f}" '
            f'stroke-linecap="round" stroke-linejoin="round"{attrs}/>'
        )
        if dash:  # a dotted pencil line is drawn once
            break
    return "".join(parts)


def wash(
    points: list[Point],
    pen: Pen,
    color: str,
    *,
    opacity: float = 1.0,
    shift: Point = (1.4, 1.1),
    amp: float = 1.4,
) -> str:
    """A fill laid down a little off the ink line, like watercolour."""

    pts = wobble([(x + shift[0], y + shift[1]) for x, y in points], pen, amp)
    attrs = f' fill-opacity="{opacity:.2f}"' if opacity < 0.999 else ""
    return f'<path d="{path_d(pts, closed=True)}" fill="{color}"{attrs}/>'


# --- lettering ----------------------------------------------------------------


class Lettering:
    """Glyph outlines from one font file, placed by hand."""

    def __init__(self, font_path: Path) -> None:
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.ttLib import TTFont

        self._pen_cls = SVGPathPen
        self.font = TTFont(str(font_path))
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.metrics = self.font["hmtx"].metrics
        self._paths: dict[str, str] = {}

    def name(self, ch: str) -> str:
        return self.cmap.get(ord(ch), ".notdef")

    def advance(self, ch: str) -> float:
        return self.metrics[self.name(ch)][0]

    def width(self, text: str, size: float, tracking: float = 0.0) -> float:
        scale = size / self.upm
        return sum(self.advance(c) * scale + tracking for c in text) - (tracking if text else 0.0)

    def outline(self, glyph: str) -> str:
        if glyph not in self._paths:
            pen = self._pen_cls(self.glyphs)
            self.glyphs[glyph].draw(pen)
            self._paths[glyph] = pen.getCommands()
        return self._paths[glyph]


class Drawing:
    """One SVG: a body of fragments plus the glyphs its lettering uses."""

    def __init__(self, width: float, height: float, letters: Lettering, seed: int) -> None:
        self.width = width
        self.height = height
        self.letters = letters
        self.pen = Pen(seed)
        self.body: list[str] = []
        self.glyph_ids: dict[str, str] = {}
        self.defs: list[str] = []

    def add(self, fragment: str) -> None:
        self.body.append(fragment)

    # shapes ------------------------------------------------------------------

    def box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        r: float = 12.0,
        fill: str | None = PAPER,
        stroke: str = INK,
        width: float = 2.0,
        tilt: float = 0.0,
        shadow: bool = True,
        amp: float = 1.3,
        dash: str | None = None,
        fill_opacity: float = 1.0,
    ) -> None:
        outline = rounded_rect(x, y, w, h, r)
        if tilt:
            outline = rotate(outline, tilt, (x + w / 2, y + h / 2))
        if shadow:
            self.add(wash(outline, self.pen, SHADOW, opacity=0.75, shift=(5.0, 6.0), amp=1.2))
        if fill:
            self.add(wash(outline, self.pen, fill, opacity=fill_opacity))
        self.add(ink(loop(outline, self.pen), self.pen, width=width, color=stroke, amp=amp, dash=dash))

    def band(self, x: float, y: float, w: float, h: float, color: str, *, tilt: float = 0.0, about: Point | None = None) -> None:
        """A strip of colour (a title bar) laid down inside a box."""

        pts = rounded_rect(x, y, w, h, 3)
        if tilt:
            pts = rotate(pts, tilt, about or (x + w / 2, y + h / 2))
        self.add(wash(pts, self.pen, color, shift=(0.6, 0.6), amp=0.8))

    def line(self, a: Point, b: Point, *, color: str = INK, width: float = 2.0, amp: float = 1.0, dash: str | None = None, passes: int = 2) -> None:
        self.add(ink(sample_line(a, b), self.pen, width=width, color=color, amp=amp, dash=dash, passes=passes))

    def rule(self, x1: float, x2: float, y: float, *, color: str = BRAND, width: float = 2.4) -> None:
        """A hand-drawn underline: one stroke with a gentle wave."""

        pts = []
        n = max(6, int((x2 - x1) / 18))
        phase = self.pen.random() * math.pi
        for i in range(n + 1):
            t = i / n
            pts.append((x1 + (x2 - x1) * t, y + math.sin(t * math.pi * 2.2 + phase) * 1.8))
        self.add(ink(pts, self.pen, width=width, color=color, amp=0.8, passes=1))

    def ring(self, cx: float, cy: float, r: float, *, color: str = INK, width: float = 1.6, fill: str | None = None) -> None:
        start = self.pen.random() * 360
        pts = ellipse(cx, cy, r, r * (0.94 + self.pen.random() * 0.1), start, 380)
        if fill:
            self.add(wash(ellipse(cx, cy, r, r), self.pen, fill, shift=(0.5, 0.5), amp=0.5))
        self.add(ink(pts, self.pen, width=width, color=color, amp=0.6, passes=1))

    def arrow(
        self,
        controls: list[Point],
        *,
        color: str = INK,
        width: float = 2.0,
        head: float = 12.0,
        dash: str | None = None,
        amp: float = 1.2,
    ) -> None:
        pts = sample_bezier(controls, 20)
        pts = wobble(pts, self.pen, amp)
        self.add(ink(pts, self.pen, width=width, color=color, amp=0.3, dash=dash, passes=1 if dash else 2))
        tip = pts[-1]
        back = pts[-3]
        ang = math.atan2(tip[1] - back[1], tip[0] - back[0])
        for side in (-1, 1):
            spread = math.radians(27 + self.pen.jitter(8))
            length = head * (1 + self.pen.jitter(0.25))
            end = (tip[0] - math.cos(ang + side * spread) * length, tip[1] - math.sin(ang + side * spread) * length)
            self.add(ink(sample_line(tip, end, 6), self.pen, width=width, color=color, amp=0.4, passes=1))

    # lettering ---------------------------------------------------------------

    def _glyph_ref(self, glyph: str) -> str:
        gid = self.glyph_ids.get(glyph)
        if gid is None:
            gid = f"g{len(self.glyph_ids) + 1}"
            self.glyph_ids[glyph] = gid
            self.defs.append(f'<path id="{gid}" d="{self.letters.outline(glyph)}"/>')
        return gid

    def text(
        self,
        text: str,
        x: float,
        y: float,
        size: float,
        *,
        color: str = INK,
        anchor: str = "start",
        tilt: float = 0.0,
        tracking: float = 0.0,
        hand: float = 1.0,
        opacity: float = 1.0,
    ) -> float:
        """Letter *text* with its baseline at *y*; returns the width it took."""

        letters = self.letters
        width = letters.width(text, size, tracking)
        if anchor == "middle":
            x -= width / 2
        elif anchor == "end":
            x -= width
        scale = size / letters.upm
        cursor = 0.0
        uses = []
        base_angle = math.radians(tilt)
        for ch in text:
            advance = letters.advance(ch) * scale
            if not ch.isspace():
                glyph = letters.name(ch)
                gid = self._glyph_ref(glyph)
                angle = base_angle + math.radians(self.pen.jitter(3.0 * hand))
                s = scale * (1 + self.pen.jitter(0.035 * hand))
                gx = x + cursor * math.cos(base_angle)
                gy = y + cursor * math.sin(base_angle) + self.pen.jitter(size * 0.035 * hand)
                a, b = s * math.cos(angle), s * math.sin(angle)
                uses.append(
                    f'<use href="#{gid}" transform="matrix({a:.4f} {b:.4f} {b:.4f} {-a:.4f} {gx:.1f} {gy:.1f})"/>'
                )
            cursor += advance + tracking
        attrs = f' fill-opacity="{opacity:.2f}"' if opacity < 0.999 else ""
        self.add(f'<g fill="{color}"{attrs}>{"".join(uses)}</g>')
        return width

    def fit(self, text: str, size: float, max_width: float) -> float:
        """The largest size up to *size* at which *text* fits in *max_width*."""

        width = self.letters.width(text, size)
        return size if width <= max_width else size * max_width / width

    # the sheet ---------------------------------------------------------------

    def sheet(self, margin: float = 10.0) -> None:
        """The paper everything is drawn on, so the drawing reads on dark pages too."""

        outline = rounded_rect(margin, margin, self.width - 2 * margin, self.height - 2 * margin, 16)
        self.add(wash(outline, self.pen, SHADOW, opacity=0.6, shift=(3.0, 4.0), amp=1.5))
        self.add(wash(outline, self.pen, PAPER, shift=(0.0, 0.0), amp=1.5))
        self.add(f'<rect x="{margin}" y="{margin}" width="{self.width - 2 * margin}" '
                 f'height="{self.height - 2 * margin}" rx="16" filter="url(#grain)" opacity="0.5"/>')
        self.add(ink(loop(outline, self.pen, 0.03), self.pen, width=1.6, color=MUTED, amp=1.6, opacity=0.7))

    def svg(self, title: str, desc: str, *, grain: bool = True) -> str:
        defs = list(self.defs)
        if grain:
            defs.append(
                '<filter id="grain" x="0" y="0" width="100%" height="100%">'
                '<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="7" stitchTiles="stitch"/>'
                '<feColorMatrix type="matrix" values="0 0 0 0 0.36 0 0 0 0 0.27 0 0 0 0 0.18 3.2 0 0 0 -1.9"/>'
                "</filter>"
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_fmt(self.width)} {_fmt(self.height)}" '
            f'width="{_fmt(self.width)}" height="{_fmt(self.height)}" role="img">'
            f"<title>{title}</title><desc>{desc}</desc>"
            f'<defs>{"".join(defs)}</defs>{"".join(self.body)}</svg>\n'
        )


# --- the mark -----------------------------------------------------------------


def _clip_wire() -> list[Point]:
    """A paper clip, upright, 10 wide and 29 tall: three bends of one wire."""

    pts: list[Point] = []
    pts += sample_line((2.5, 9.0), (2.5, 20.0), 3)
    pts += sample_arc((5.0, 20.0), 2.5, 180, 0, 20)  # under the inner bend
    pts = pts[:-1] + [(7.5, 20.0)]
    pts += sample_line((7.5, 20.0), (7.5, 4.0), 3)
    pts += sample_arc((3.75, 4.0), 3.75, 0, -180, 20)  # over the top
    pts += sample_line((0.0, 4.0), (0.0, 24.0), 3)
    pts += sample_arc((5.0, 24.0), 5.0, 180, 0, 18)  # under the outer bend
    pts += sample_line((10.0, 24.0), (10.0, 7.0), 3)
    return _dedupe(pts)


def _place(points: list[Point], dx: float, dy: float, degrees: float, scale: float = 1.0) -> list[Point]:
    moved = [(x * scale + dx, y * scale + dy) for x, y in points]
    return rotate(moved, degrees, (dx, dy))


def mark_parts(pen: Pen) -> list[tuple[str, list[Point], dict]]:
    """The mark's shapes in a 64×64 field, in drawing order: (kind, points, style)."""

    back = rotate(rounded_rect(19, 8, 34, 38, 4), 9, (36, 27))
    front_box = (8.0, 17.0, 40.0, 40.0)
    fx, fy, fw, fh = front_box
    front = rotate(rounded_rect(fx, fy, fw, fh, 4.5), -4, (28, 37))
    bar = rotate(rounded_rect(fx, fy, fw, 10, 4), -4, (28, 37))
    bar_rule = rotate(sample_line((fx + 0.5, fy + 10), (fx + fw - 0.5, fy + 10), 6), -4, (28, 37))
    chevron = rotate([(15.5, 34.5), (21.5, 39.5), (15.5, 44.5)], -4, (28, 37))
    cursor = rotate(sample_line((24.5, 45.5), (33.5, 45.5), 3), -4, (28, 37))
    clip = _place(_clip_wire(), 43.0, 1.5, 14, 0.95)
    return [
        ("fill", back, {"color": WASH}),
        ("line", back, {"width": 2.2}),
        ("fill", front, {"color": PAPER}),
        ("fill", bar, {"color": BRAND}),
        ("line", bar_rule, {"width": 1.8}),
        ("line", front, {"width": 2.5}),
        ("open", chevron, {"width": 2.6}),
        ("open", cursor, {"width": 2.8, "color": BRAND}),
        ("halo", clip, {"width": 4.6, "color": PAPER}),
        ("open", clip, {"width": 2.0}),
    ]


def draw_mark(d: Drawing, x: float, y: float, size: float) -> None:
    """The mark: a terminal card and the card behind it, held by a paper clip."""

    k = size / 64.0
    pen = d.pen
    parts = []
    for kind, pts, style in mark_parts(pen):
        pts = [(x + px * k, y + py * k) for px, py in pts]
        width = style.get("width", 2.0) * k
        color = style.get("color", INK)
        if kind == "fill":
            parts.append(wash(pts, pen, color, shift=(0.5 * k, 0.5 * k), amp=0.35 * k))
        elif kind == "line":
            parts.append(ink(loop(pts, pen, 0.04), pen, width=width, color=color, amp=0.35 * k, wavelength=30 * k))
        elif kind == "halo":
            parts.append(ink(pts, pen, width=width, color=color, amp=0.0, passes=1))
        else:
            parts.append(ink(pts, pen, width=width, color=color, amp=0.3 * k, passes=2, wavelength=30 * k))
    d.add("".join(parts))


def draw_logo(letters: Lettering) -> str:
    d = Drawing(64, 64, letters, seed=11)
    draw_mark(d, 0, 0, 64)
    return d.svg("CLIppet", "A terminal card and the card behind it, held together by a paper clip.", grain=False)


# --- the banner ---------------------------------------------------------------


def _terminal(d: Drawing, x: float, y: float, w: float, h: float, lines: list[tuple[str, str, float]], tilt: float = 0.0) -> None:
    d.box(x, y, w, h, r=14, fill=NIGHT, width=2.4, tilt=tilt)
    for i, color in enumerate(("#e0705a", "#e0ad4c", "#8cb878")):
        d.ring(x + 26 + i * 22, y + 22, 6.2, color=color, width=1.4, fill=color)
    d.line((x + 12, y + 42), (x + w - 12, y + 42), color="#54493d", width=1.6, passes=1)
    cy = y + 82
    for text, color, size in lines:
        d.text(text, x + 28, cy, size, color=color, hand=0.6)
        cy += size * 1.55


def draw_banner(letters: Lettering) -> str:
    W, H = 1200, 660
    d = Drawing(W, H, letters, seed=5)
    d.sheet()

    # title: the mark, the name and a line under it
    title_size = 92
    tw = letters.width("CLIppet", title_size)
    left = (W - (tw + 110)) / 2
    draw_mark(d, left, 38, 96)
    d.text("CLIppet", left + 112, 122, title_size, color=BRAND, hand=0.8)
    d.rule(left + 116, left + 112 + tw, 140, color=BRAND, width=2.6)
    d.text("One CLI to orchestrate them all.", W / 2, 192, 30, color=MUTED, anchor="middle", hand=0.7)

    # the terminal in the middle
    tx, ty, tww, th = 330, 232, 540, 168
    _terminal(
        d, tx, ty, tww, th,
        [("$ clippet -e work codex resume --last", NIGHT_INK, 25),
         ("→ codex · HOME = ~/.clippet/envs/work", NIGHT_MUTED, 19)],
        tilt=-0.6,
    )

    # left: the HOME envs it can run in
    d.box(58, 226, 156, 92, r=8, fill=WASH, tilt=-6.0, width=1.8)
    d.text("personal", 132, 262, 23, color=MUTED, anchor="middle", tilt=-5.0)
    d.box(92, 286, 156, 86, r=8, fill=PAPER, tilt=3.5, width=1.8)
    d.text("work", 170, 338, 27, color=INK, anchor="middle", tilt=3.0)
    d.text("HOME envs", 160, 408, 23, color=MUTED, anchor="middle")
    d.arrow([(262, 326), (292, 310), (320, 306)], color=MUTED, width=1.8, head=10, dash="1.5 7")

    # right: where accounts come from, on a slip of paper
    d.box(914, 236, 196, 168, r=10, fill="#f6ead8", tilt=2.5, width=1.7)
    d.text("accounts from", 1012, 268, 18, color=MUTED, anchor="middle", tilt=2.0)
    for i, note in enumerate(["Cockpit Tools", "sub2api", "CLIProxyAPI", "auth.json"]):
        d.text(note, 1012, 302 + i * 28, 21, color=BRAND_DEEP if i == 0 else INK, anchor="middle", tilt=2.0, hand=0.6)
    d.arrow([(908, 318), (894, 312), (878, 308)], color=MUTED, width=1.8, head=10, dash="1.5 7")

    # bottom: the agents, each card drawn a little crooked
    names = ["Claude", "Codex", "QoderCLI", "Antigravity"]
    tilts = [-2.2, 1.4, -1.0, 2.0]
    for i, name in enumerate(names):
        cx = 228 + i * 248
        bx, by, bw, bh = cx - 92, 480, 184, 104
        d.box(bx, by, bw, bh, r=14, fill=PAPER, tilt=tilts[i], width=2.0)
        d.text(name, cx, by + 64, d.fit(name, 34, bw - 24), color=AGENT_COLORS[name], anchor="middle",
               tilt=tilts[i] * 0.7)
        start = (tx + tww / 2 + (i - 1.5) * 70, ty + th + 4)
        end = (cx + (i - 1.5) * 6, by - 10)
        mid = ((start[0] + end[0]) / 2, start[1] + 52)
        d.arrow([start, mid, end], color=INK, width=1.9, head=11)

    d.text("same flags in, structured results out — interactive, headless or in parallel", W / 2, 626, 19,
           color=MUTED, anchor="middle", hand=0.5)
    return d.svg("CLIppet", "One CLI to orchestrate them all: clippet launches Claude, Codex, QoderCLI, Antigravity "
                 "and other agent CLIs in a HOME env with the account you choose.")


# --- how it works -------------------------------------------------------------


def _numbered(d: Drawing, n: int, x: float, y: float) -> None:
    d.ring(x, y, 17, color=BRAND, width=2.0, fill="#f3dccb")
    d.text(str(n), x, y + 9, 26, color=BRAND_DEEP, anchor="middle", hand=0.4)


def draw_how_it_works(letters: Lettering) -> str:
    W, H = 1200, 1010
    d = Drawing(W, H, letters, seed=23)
    d.sheet()
    d.text("How a run goes", W / 2, 72, 40, color=INK, anchor="middle")
    d.rule(W / 2 - 128, W / 2 + 128, 88, color=BRAND)

    # where a run comes from
    entries = [("$ clippet -e work codex", 160), ("clippet tui", 230), ("runner.execute(…)", 300)]
    for i, (label, y) in enumerate(entries):
        d.box(46, y - 34, 250, 52, r=10, fill=PAPER if i != 1 else "#f3dccb", width=1.6, tilt=(-1.5, 1.0, -0.8)[i], shadow=False)
        d.text(label, 171, y, d.fit(label, 22, 220), color=INK, anchor="middle", hand=0.6)
    d.text("CLI · TUI · Python", 171, 362, 20, color=MUTED, anchor="middle")
    for y in (150, 220, 290):
        d.arrow([(302, y - 8), (326, y - 10 + (220 - y) * 0.2), (346, 196)], color=MUTED, width=1.6, head=9)

    stages = [
        ("Pick an identity", ["HOME env · credential file · real HOME", "Codex accounts: Cockpit, sub2api, CLIProxyAPI…"]),
        ("Build the command", ["AgentRequest → the agent's own flags", "+ every argument you passed through"]),
        ("Set up the sandbox", ["HOME → the env, or a temp dir", "inject auth (0600) · drop CODEX_HOME, OPENAI_*…"]),
        ("Run the agent", ["claude · codex · qodercli · pi · copilot · agy"]),
        ("Read the result", ["output_text · tool_calls · usage · session_id"]),
    ]
    bx, bw, y = 356, 524, 128
    tops = []
    for n, (title, lines) in enumerate(stages, 1):
        bh = 108 + 30 * (len(lines) - 1)
        tops.append((y, bh))
        d.box(bx, y, bw, bh, r=14, fill=PAPER, width=2.0, tilt=(-0.6, 0.5, -0.4, 0.6, -0.5)[n - 1])
        _numbered(d, n, bx + 34, y + 38)
        d.text(title, bx + 66, y + 47, 28, color=INK)
        for i, line in enumerate(lines):
            d.text(line, bx + 66, y + 84 + i * 30, d.fit(line, 19, bw - 90), color=MUTED, hand=0.5)
        y += bh + 50
    for (top, bh), (nxt, _) in zip(tops, tops[1:]):
        d.arrow([(bx + bw / 2, top + bh + 6), (bx + bw / 2 + 6, (top + bh + nxt) / 2), (bx + bw / 2, nxt - 8)],
                color=INK, width=2.0, head=11)
    sandbox, run, result = tops[2][0], tops[3][0], tops[4][0]

    # notes on the right
    d.text("your real ~/.codex", 1040, sandbox + 58, 21, color=OK, anchor="middle")
    d.text("is never touched", 1040, sandbox + 84, 21, color=OK, anchor="middle")
    d.arrow([(950, sandbox + 72), (922, sandbox + 74), (892, sandbox + 78)], color=OK, width=1.6, head=9, dash="1.5 6")

    for i in range(3):
        d.box(960 + i * 14, run - 34 + i * 18, 150, 56, r=10, fill=(WASH, "#f6ead8", PAPER)[i], width=1.6,
              tilt=(-4, 2, -1)[i], shadow=i == 2)
    d.text("acct-c", 1063, run + 36, 22, color=INK, anchor="middle")
    d.text("ClippetRunner runs", 1040, run + 90, 19, color=MUTED, anchor="middle")
    d.text("envs side by side", 1040, run + 114, 19, color=MUTED, anchor="middle")
    d.arrow([(952, run + 30), (920, run + 40), (892, run + 50)], color=MUTED, width=1.6, head=9, dash="1.5 6")

    d.text("refreshed logins", 1040, result + 44, 21, color=BRAND_DEEP, anchor="middle")
    d.text("are saved back", 1040, result + 70, 21, color=BRAND_DEEP, anchor="middle")
    d.arrow([(950, result + 58), (922, result + 56), (892, result + 54)], color=BRAND_DEEP, width=1.6, head=9, dash="1.5 6")
    return d.svg("How a CLIppet run goes", "Pick an identity, build the command, set up the sandbox, run the agent, read the result.")


# --- accounts -----------------------------------------------------------------


def draw_accounts(letters: Lettering) -> str:
    W, H = 1200, 600
    d = Drawing(W, H, letters, seed=41)
    d.sheet()
    d.text("One account per env", 56, 70, 34, color=INK)
    d.rule(58, 340, 86, color=BRAND)

    sources = [
        ("Cockpit Tools", "live account store", BRAND),
        ("Cockpit export", "backup · data-transfer", MUTED),
        ("sub2api", "account export", MUTED),
        ("CLIProxyAPI", "codex-*.json", MUTED),
        ("Codex auth.json", "or a token bundle", MUTED),
    ]
    for i, (name, sub, color) in enumerate(sources):
        y = 128 + i * 86
        tilt = (-2.0, 1.5, -1.0, 2.0, -1.5)[i]
        d.box(56, y, 250, 68, r=8, fill=PAPER if i else "#f3dccb", width=1.7, tilt=tilt, shadow=i == 0)
        d.text(name, 78, y + 32, 24, color=INK if i else BRAND_DEEP, tilt=tilt * 0.6)
        d.text(sub, 78, y + 56, 17, color=color if i else BRAND, tilt=tilt * 0.6, hand=0.5)
        d.arrow([(312, y + 34), (350, y + 34 + (300 - y) * 0.2), (398, 330)], color=FAINT if i else MUTED, width=1.6, head=8)

    # the import
    d.text("clippet env import", 500, 268, 19, color=INK, anchor="middle", hand=0.5)
    d.text("acct-a cockpit --account 2", 500, 294, d.fit("acct-a cockpit --account 2", 19, 205), color=INK, anchor="middle", hand=0.5)
    d.arrow([(410, 330), (500, 316), (596, 322)], color=INK, width=2.2, head=13)

    # the env, with another one behind it
    ex, ey, ew, eh = 606, 252, 300, 228
    d.box(ex + 16, ey + 14, ew, eh, r=16, fill=WASH, width=1.6, tilt=3.0, shadow=False)
    d.box(ex, ey, ew, eh, r=16, fill=PAPER, width=2.2, tilt=-1.2)
    d.text("~/.clippet/envs/acct-a", ex + ew / 2, ey + 42, d.fit("~/.clippet/envs/acct-a", 22, ew - 40), color=BRAND_DEEP, anchor="middle")
    rows = [("✓", ".codex/auth.json", "alice@example.com · plus", OK),
            ("·", ".codex/config.toml", "copied from yours", MUTED),
            ("↗", ".gitconfig", "linked to your HOME", INFO)]
    for i, (sign, path, note, color) in enumerate(rows):
        y = ey + 92 + i * 46
        d.text(sign, ex + 26, y, 22, color=color)
        d.text(path, ex + 52, y, 20, color=INK, hand=0.5)
        d.text(note, ex + 52, y + 20, 15, color=MUTED, hand=0.4)

    # the launch
    d.arrow([(ex + ew + 8, 370), (952, 352), (984, 362)], color=INK, width=2.2, head=13)
    _terminal(d, 990, 300, 186, 124, [("$ clippet", NIGHT_INK, 17), ("  -e acct-a codex", NIGHT_INK, 17)], tilt=1.2)
    d.text("Codex, logged in", 1083, 460, 20, color=OK, anchor="middle")
    d.text("as alice", 1083, 484, 20, color=OK, anchor="middle")

    # linked: the newest login comes from the source before every launch
    d.arrow([(300, 126), (470, 64), (700, 92), (752, 236)], color=BRAND, width=1.8, head=11, dash="1.5 7")
    d.text("linked: the newest login is pulled before each launch", 575, 150, 17, color=BRAND, anchor="middle", hand=0.5)

    d.text("Each env keeps its own login: OAuth refresh tokens rotate, so copies would break each other.",
           W / 2, 566, 18, color=MUTED, anchor="middle", hand=0.4)
    return d.svg("One account per env", "Codex accounts from Cockpit Tools, sub2api, CLIProxyAPI or auth.json are "
                 "imported into a HOME env; the env then launches Codex as that account.")


# --- the isolation design (docs/isolated-agent-environment.md) ---------------------


def draw_isolation(letters: Lettering) -> str:
    W, H = 960, 660
    d = Drawing(W, H, letters, seed=59)
    d.sheet()
    d.text("隔离环境", 60, 76, 34, color=INK)
    d.rule(62, 200, 92, color=BRAND)

    d.box(40, 116, 880, 500, r=18, fill=PAPER, width=2.0, shadow=False)
    d.text("ClippetRunner", 70, 154, 24, color=MUTED)

    d.box(86, 178, 640, 236, r=16, fill="#f6ead8", width=2.2, tilt=-0.4)
    d.text("IsolatedEnvironment", 112, 216, 24, color=BRAND_DEEP)
    for i, (title, lines, tilt) in enumerate((
        ("沙箱 HOME", ["临时目录，或持久的 env", "~/.clippet/envs/<name>"], -1.2),
        ("凭证提供者", ["文件 · 生成的内容 · 环境变量", "写入 HOME，权限 0600"], 1.0),
    )):
        x = 116 + i * 304
        d.box(x, 240, 276, 120, r=12, fill=PAPER, width=1.8, tilt=tilt)
        d.text(title, x + 22, 280, 24, color=INK, tilt=tilt * 0.6)
        for j, line in enumerate(lines):
            d.text(line, x + 22, 312 + j * 26, d.fit(line, 17, 240), color=MUTED, tilt=tilt * 0.6, hand=0.5)
    d.text("环境变量：先清洗 CODEX_HOME、OPENAI_*…，再应用覆盖", 116, 396, 18, color=INFO, hand=0.5)

    d.arrow([(406, 420), (414, 452), (406, 482)], color=INK, width=2.0, head=11)
    d.text("subprocess(env=…, cwd=workspace)", 430, 462, 18, color=MUTED, hand=0.5)
    d.box(236, 494, 340, 84, r=14, fill=NIGHT, width=2.2, tilt=0.6)
    d.text("Agent 进程", 406, 532, 25, color=NIGHT_INK, anchor="middle")
    d.text("$HOME 指向沙箱", 406, 562, 18, color=NIGHT_MUTED, anchor="middle")

    d.text("结束后", 820, 470, 20, color=BRAND_DEEP, anchor="middle")
    d.text("刷新过的登录", 820, 498, 20, color=BRAND_DEEP, anchor="middle")
    d.text("写回原处", 820, 526, 20, color=BRAND_DEEP, anchor="middle")
    d.arrow([(584, 540), (700, 560), (760, 540)], color=BRAND, width=1.6, head=9, dash="1.5 6")
    d.arrow([(820, 446), (840, 360), (752, 300)], color=BRAND, width=1.6, head=10, dash="1.5 6")
    d.text("临时 HOME 用完即删", 824, 236, 18, color=OK, anchor="middle")
    d.text("持久 env 原样保留", 824, 262, 18, color=OK, anchor="middle")
    return d.svg("隔离环境", "ClippetRunner 为每次运行准备隔离环境：沙箱 HOME、注入凭证、清洗环境变量，再启动 agent 进程；"
                 "结束后把刷新过的登录写回。")


# --- architecture -------------------------------------------------------------


def _layer(d: Drawing, x: float, y: float, w: float, h: float, name: str, about: str, module: str,
           color: str, tint: str) -> None:
    """A layer: a tinted shelf with its name, what it does and its package along the top."""

    d.box(x, y, w, h, r=18, fill=tint, stroke=color, width=1.5, shadow=False, amp=1.6)
    nw = d.text(name, x + 22, y + 34, 24, color=color)
    room = w - 44 - nw - 14
    if module:
        room -= d.letters.width(module, 17) + 16
        d.text(module, x + w - 22, y + 34, 17, color=BRAND_DEEP, anchor="end", hand=0.4)
    if about:
        d.text(about, x + 22 + nw + 14, y + 34, d.fit(about, 17, room), color=MUTED, hand=0.5)


def _card(d: Drawing, x: float, y: float, w: float, h: float, title: str, module: str = "",
          lines: tuple[str, ...] = (), *, color: str = INK, tilt: float = 0.0, dash: str | None = None,
          size: float = 22) -> None:
    """A module on a layer: its name, its file, and a line or two about it."""

    d.box(x, y, w, h, r=12, fill=PAPER, width=1.8, tilt=tilt, dash=dash, shadow=dash is None)
    t = tilt * 0.6
    room = w - 36
    if module:
        room -= d.letters.width(module, 15.5) + 12
        d.text(module, x + w - 18, y + 31, 15.5, color=BRAND_DEEP, anchor="end", tilt=t, hand=0.4)
    d.text(title, x + 18, y + 31, d.fit(title, size, room), color=color, tilt=t, hand=0.7)
    for i, line in enumerate(lines):
        d.text(line, x + 18, y + 57 + i * 22, d.fit(line, 16, w - 36), color=MUTED, tilt=t, hand=0.45)


def _flow(d: Drawing, x: float, y1: float, y2: float, label: str, *, color: str = INK, dash: str | None = None,
          side: str = "right", at: float | None = None) -> None:
    """A vertical arrow from y1 to y2 with what it carries written beside it (at baseline *at*)."""

    d.arrow([(x, y1), (x + 4, (y1 + y2) / 2), (x, y2)], color=color, width=1.9 if dash else 2.0, head=11, dash=dash)
    mid = at if at is not None else (y1 + y2) / 2 + 6
    if side == "right":
        d.text(label, x + 16, mid, 16.5, color=MUTED, hand=0.5)
    else:
        d.text(label, x - 14, mid, 16.5, color=MUTED, anchor="end", hand=0.5)


def draw_architecture(letters: Lettering) -> str:
    W, H = 1200, 1290
    d = Drawing(W, H, letters, seed=73)
    d.sheet()
    d.text("Architecture", W / 2, 74, 40, color=INK, anchor="middle")
    d.rule(W / 2 - 118, W / 2 + 118, 90, color=BRAND)
    d.text("the modules in clippet/ and what calls what", W / 2, 124, 19, color=MUTED, anchor="middle", hand=0.5)

    L, R = 40, 1160
    spine = 416  # a run goes down here …
    lane = 1118  # … and its result climbs back up here

    # entry points
    ay = 150
    _layer(d, L, ay, R - L, 166, "Entry points", "where a run starts", "", BRAND_DEEP, "#f5e3d6")
    entries = [
        ("CLI", "cli.py", ("clippet -e work codex [args…]", "interactive, or -p for a headless run")),
        ("TUI", "tui/", ("clippet tui", "envs · accounts · runs · project · checks")),
        ("Python SDK", "orchestrator.py", ("ClippetRunner · adapters", "execute · execute_parallel · run_async")),
    ]
    for i, (title, module, lines) in enumerate(entries):
        _card(d, 62 + i * 366, ay + 52, 342, 98, title, module, lines, tilt=(-0.5, 0.4, -0.3)[i])

    # identity, and the accounts it can draw on
    by, bh = 372, 296
    _layer(d, L, by, 632, bh, "Identity", "which HOME and login a run uses", "config/", WARN, "#f6ebd4")
    identity = [
        (".clippet.json", "project.py", ("pins an env per agent", "found from cwd up to the git root")),
        ("HOME envs", "environments.py", ("~/.clippet/envs/<name>", "create · clone · import · sync")),
        ("Composite config", "registry.py", ("YAML / JSON → named adapters", "→ a ClippetRunner")),
        ("Detector", "detector.py", ("a file or a folder → its agent", "→ adapter + IsolationConfig")),
    ]
    for i, (title, module, lines) in enumerate(identity):
        _card(d, 62 + (i % 2) * 307, by + 52 + (i // 2) * 118, 289, 102, title, module, lines,
              tilt=(0.4, -0.5, -0.3, 0.5)[i])

    ax, aw = 764, 316
    _layer(d, ax, by, aw, bh, "Accounts", "", "auth/", PLUM, "#efe7f1")
    d.text("Codex logins in any format", ax + 22, by + 62, 16, color=MUTED, hand=0.5)
    sources = [("Cockpit Tools store · cockpit.py", BRAND_DEEP), ("Cockpit export · sub2api", INK),
               ("CLIProxyAPI · auth.json · tokens", INK)]
    for i, (source, color) in enumerate(sources):
        y = by + 92 + i * 25
        d.text("·", ax + 24, y, 18, color=MUTED)
        d.text(source, ax + 40, y, d.fit(source, 17.5, aw - 62), color=color, hand=0.5)
    d.arrow([(ax + aw / 2, by + 150), (ax + aw / 2 + 4, by + 162), (ax + aw / 2, by + 174)], color=PLUM,
            width=1.8, head=9)
    cx, cy, cw = ax + 18, by + 180, aw - 36
    _card(d, cx, cy, cw, 100, "CodexCredential", "codex.py", ("→ native .codex/auth.json",
                                                              "email · plan · expiry (jwt.py)"), tilt=0.4)
    d.arrow([(cx - 4, cy + 22), (cx - 50, cy + 14), (cx - 80, by + 132), (662, by + 106)],
            color=PLUM, width=1.8, head=10)
    d.text("import · link", (672 + ax) / 2, by + 86, 15, color=PLUM, anchor="middle", hand=0.4)
    d.arrow([(cx - 4, cy + 66), (cx - 52, cy + 72), (662, by + 228)], color=PLUM, width=1.8, head=10)
    d.text("or -c file", (672 + ax) / 2, cy + 98, 15, color=PLUM, anchor="middle", hand=0.4)

    # adapters
    cy0 = 722
    _layer(d, L, cy0, R - L, 236, "Adapters", "AgentRequest in, AgentResult out", "adapters/ · parsers/", INFO,
           "#e6edf3")
    bx, bw = 62, 578
    _card(d, bx, cy0 + 52, bw, 168, "BaseSubprocessAdapter", "base.py",
          ("AgentRequest → the agent's own flags, plus your arguments",
           "run() · run_async(on_output=…) streams · cancel kills the agent"), tilt=-0.2)
    chips = [("Claude", "claude.py"), ("Codex", "codex.py"), ("Antigravity", "antigravity.py"), ("QoderCLI", "qodercli.py")]
    for i, (name, module) in enumerate(chips):
        x = bx + 16 + i * 139
        y = cy0 + 152
        d.box(x, y, 129, 54, r=8, fill="#f7f0e4", width=1.4, shadow=False, tilt=(-1.0, 0.8, -0.6, 1.0)[i])
        d.text(name, x + 12, y + 24, d.fit(name, 19, 105), color=AGENT_COLORS[name], hand=0.6)
        d.text(module, x + 12, y + 44, d.fit(module, 14.5, 105), color=MUTED, hand=0.4)
    _card(d, 660, cy0 + 52, 222, 168, "OpenAIAdapter", "api.py",
          ("optional: clippet[api]", "calls the API itself:", "no CLI, no sandbox", "personas.py: personas"),
          dash="2 7", size=21)
    _card(d, 902, cy0 + 52, 236, 168, "Parsers", "extractors.py",
          ("stdout · JSON events", "→ AgentResult:", "text · tool calls", "usage · session id"), tilt=0.3)

    # the sandbox, with the agent inside it
    dy0 = 1012
    _layer(d, L, dy0, R - L, 214, "Sandbox", "one per run, around the agent process", "isolation.py", OK, "#e7eee0")
    _card(d, 62, dy0 + 52, 578, 146, "IsolatedEnvironment", "", (
        "HOME → the env (kept), or a temp dir (removed afterwards)",
        "drops CODEX_HOME, OPENAI_API_KEY, ANTHROPIC_* … from the parent",
        "credential providers write files (0600) and set env vars",
        "CredentialSyncBack saves refreshed logins back to their source",
    ), tilt=0.2)
    d.arrow([(646, dy0 + 125), (676, dy0 + 121), (704, dy0 + 125)], color=INK, width=2.0, head=10)
    d.text("spawns", 675, dy0 + 108, 15, color=MUTED, anchor="middle", hand=0.4)
    d.text("agent CLIs · $HOME = the sandbox", 718, dy0 + 62, 16, color=MUTED, hand=0.5)
    for i, agent in enumerate(("Claude", "Codex", "Antigravity", "QoderCLI")):
        x = 718 + (i % 2) * 213
        y = dy0 + 74 + (i // 2) * 66
        d.box(x, y, 199, 54, r=10, fill=NIGHT, width=2.0, tilt=(-0.8, 0.6, 0.5, -0.7)[i])
        d.text("$", x + 18, y + 35, 21, color=NIGHT_MUTED, hand=0.4)
        d.text(AGENT_COMMANDS[agent], x + 40, y + 35, 21, color=NIGHT_INK, hand=0.5)
        d.ring(x + 176, y + 27, 6, color=AGENT_COLORS[agent], width=1.4, fill=AGENT_COLORS[agent])

    # what goes down, and what comes back
    _flow(d, spine, ay + 168, by - 4, "who to run as: -e env · -c file · .clippet.json")
    _flow(d, spine, by + bh + 2, cy0 - 4, "an adapter, with its IsolationConfig")
    _flow(d, spine, cy0 + 238, dy0 - 4, "its command + IsolationConfig")
    _flow(d, 995, dy0 + 70, cy0 + 224, "stdout · JSON events, line by line", color=BRAND_DEEP, dash="1.5 7",
          side="left", at=(cy0 + 238 + dy0 - 4) / 2 + 6)
    _flow(d, lane, cy0 + 48, ay + 170, "AgentResult, back to the caller", color=BRAND_DEEP, dash="1.5 7",
          side="left", at=(ay + 168 + by - 4) / 2 + 6)

    d.text("shared types — models.py: AgentRequest · AgentResult · IsolationConfig · ToolCallRecord · "
           "protocols.py: ClippetAdapter", W / 2, 1260, 16.5, color=MUTED, anchor="middle", hand=0.4)
    return d.svg("CLIppet architecture", "The CLI, the TUI and the Python SDK pick an identity through config/ "
                 "(Codex accounts come from auth/); an adapter turns the request into the agent's command; "
                 "isolation.py prepares the sandbox HOME the agent runs in; parsers turn its output into an AgentResult.")


# --- a frame for a terminal screenshot ----------------------------------------


def frame_screenshot(letters: Lettering, screenshot: Path, caption: str) -> str:
    """Put a Textual SVG screenshot on a sheet of paper with a hand-drawn edge."""

    raw = screenshot.read_text(encoding="utf-8")
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', raw)
    if not match:
        raise SystemExit(f"{screenshot}: no viewBox")
    sw, sh = float(match.group(1)), float(match.group(2))
    inner = re.sub(r"<\?xml[^>]*\?>", "", raw).strip()
    pad = 34
    W, H = sw + pad * 2, sh + pad * 2 + 46
    d = Drawing(W, H, letters, seed=3)
    d.sheet()
    d.add(f'<svg x="{pad}" y="{pad}" width="{sw}" height="{sh}" viewBox="0 0 {sw} {sh}">'
          f'{re.sub(r"^<svg[^>]*>", "", inner, count=1)}')
    d.add(ink(loop(rounded_rect(pad - 4, pad - 4, sw + 8, sh + 8, 12), d.pen), d.pen, width=2.0, color=INK, amp=1.6))
    d.text(caption, W / 2, H - 30, 22, color=MUTED, anchor="middle", hand=0.5)
    return d.svg("clippet tui", caption)


# --- the mark as braille, for the TUI -------------------------------------------


def braille_mark(cols: int = 24, rows: int = 12) -> list[tuple[str, str]]:
    """The mark in braille dots: one (text, colour key) pair per run of cells."""

    w, h = cols * 2, rows * 4
    ink_dots = [[False] * w for _ in range(h)]
    color_at = [["ink"] * cols for _ in range(rows)]
    pen = Pen(11)
    parts = mark_parts(pen)
    k = w / 64.0

    def stamp(px: float, py: float, radius: float) -> None:
        for yy in range(int(py - radius) - 1, int(py + radius) + 2):
            for xx in range(int(px - radius) - 1, int(px + radius) + 2):
                if 0 <= xx < w and 0 <= yy < h and (xx + 0.5 - px) ** 2 + (yy + 0.5 - py) ** 2 <= radius * radius:
                    ink_dots[yy][xx] = True

    def inside(poly: list[Point], px: float, py: float) -> bool:
        hit = False
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                hit = not hit
        return hit

    for kind, pts, style in parts:
        scaled = [(x * k, y * k) for x, y in pts]
        if kind == "fill" and style.get("color") == BRAND:  # the title bar is laid in solid
            for yy in range(h):
                for xx in range(w):
                    if inside(scaled, xx + 0.5, yy + 0.5):
                        ink_dots[yy][xx] = True
                        color_at[yy // 4][xx // 2] = "brand"
        if kind in ("line", "open"):
            radius = max(0.5, style.get("width", 2.0) * k * 0.3)
            seq = scaled + (scaled[:1] if kind == "line" else [])
            for a, b in zip(seq, seq[1:]):
                steps = max(1, int(_dist(a, b) * 4))
                for i in range(steps + 1):
                    stamp(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, radius)
            if style.get("color") == BRAND:
                for x, y in scaled:
                    c, r = int(x / 2), int(y / 4)
                    if 0 <= c < cols and 0 <= r < rows:
                        color_at[r][c] = "brand"

    bits = ((0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20), (0, 3, 0x40), (1, 3, 0x80))
    out: list[tuple[str, str]] = []
    for r in range(rows):
        for c in range(cols):
            code = 0
            for dx, dy, bit in bits:
                if ink_dots[r * 4 + dy][c * 2 + dx]:
                    code |= bit
            ch = chr(0x2800 + code) if code else " "
            key = color_at[r][c]
            if out and out[-1][1] == key and out[-1][0][-1:] != "\n":
                out[-1] = (out[-1][0] + ch, key)
            else:
                out.append((ch, key))
        out.append(("\n", "ink"))
    return out


# --- main ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "resources")
    parser.add_argument("--braille", action="store_true", help="print the mark as braille rows")
    parser.add_argument("--frame", type=Path, help="frame a Textual SVG screenshot")
    parser.add_argument("--name", default="tui.svg", help="file name for --frame (in --out)")
    parser.add_argument("--caption", default="clippet tui")
    args = parser.parse_args()

    if args.braille:
        rows = []
        line: list[tuple[str, str]] = []
        for text, key in braille_mark():
            if text == "\n":
                rows.append(line)
                line = []
            else:
                line.append((text, key))
        print("MARK = [")
        for row in rows:
            print(f"    {row!r},")
        print("]")
        return

    letters = Lettering(args.font)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.frame:
        target = args.out / args.name
        target.write_text(frame_screenshot(letters, args.frame, args.caption), encoding="utf-8")
        print("wrote", target)
        return
    for name, draw in (("logo.svg", draw_logo), ("banner.svg", draw_banner),
                       ("how-it-works.svg", draw_how_it_works), ("accounts.svg", draw_accounts),
                       ("isolation.svg", draw_isolation), ("architecture.svg", draw_architecture)):
        target = args.out / name
        target.write_text(draw(letters), encoding="utf-8")
        print("wrote", target, f"({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
