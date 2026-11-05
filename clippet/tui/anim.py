"""How the TUI moves, after papertown's terminal view: while an agent works a
star turns (· ✢ ✳ ✶ ✻ ✽ and back) and a gleam runs along the words saying
what it does and counters ease to their values.  Everything is drawn from one
clock (the app's ``t``)."""

from __future__ import annotations

import math

from rich.cells import cell_len
from rich.text import Text

FRAMES = "·✢✳✶✻✽✻✶✳✢"
BLOCKS = " ▏▎▍▌▋▊▉█"


def star(t: float, speed: float = 4) -> str:
    return FRAMES[int(t * speed) % len(FRAMES)]


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(a: str, b: str, k: float) -> str:
    """The colour *k* of the way from *a* to *b*."""

    k = min(1.0, max(0.0, k))
    (r1, g1, b1), (r2, g2, b2) = _rgb(a), _rgb(b)
    return f"#{round(r1 + (r2 - r1) * k):02x}{round(g1 + (g2 - g1) * k):02x}{round(b1 + (b2 - b1) * k):02x}"


def breathe(t: float, period: float = 2.4) -> float:
    """0 → 1 → 0, smoothly, once a period."""

    return (1 - math.cos(t / period * 2 * math.pi)) / 2


def gleam(text: str, t: float, base: str, hi: str, period: float = 2.2, width: float = 4.0) -> Text:
    """The words with a light passing along them, left to right, once a period."""

    centres, x = [], 0.0
    for ch in text:
        w = cell_len(ch)
        centres.append(x + w / 2)
        x += w
    head = (t % period) / period * (x + 2 * width) - width
    out = Text()
    for ch, c in zip(text, centres):
        k = max(0.0, 1 - abs(c - head) / width)
        out.append(ch, style=mix(base, hi, k * k * (3 - 2 * k)))
    return out


def bar(frac: float, width: int, fill: str, back: str) -> Text:
    """A bar *width* cells wide, filled to *frac* in eighths of a cell."""

    frac = min(1.0, max(0.0, frac))
    eighths = round(frac * width * 8)
    full, part = divmod(eighths, 8)
    out = Text("█" * full, style=fill)
    if full < width:
        out.append(BLOCKS[part], style=f"{fill} on {back}")
        out.append(" " * (width - full - 1), style=f"on {back}")
    return out


def dur(seconds: float | None) -> str:
    """12s · 3m 04s · 2h 05m · 3d 4h"""

    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


class Tween:
    """A number easing towards its target, so counters count instead of jumping."""

    def __init__(self, value: float = 0.0, rate: float = 7.0) -> None:
        self.value = self.target = float(value)
        self.rate = rate

    def set(self, target: float) -> None:
        self.target = float(target)

    def step(self, dt: float) -> bool:
        """Move on by *dt* seconds; True while still moving."""

        if abs(self.target - self.value) < 0.005:
            self.value = self.target
            return False
        self.value += (self.target - self.value) * (1 - math.exp(-self.rate * dt))
        return True
