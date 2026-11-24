"""Plain-text tables and number formatting for the reports."""

import re
from decimal import Decimal

_NUMBER_TEXT = re.compile(r"^[+-]?\$?[\d,]+(\.\d+)?%?$")   # 1,234  $1,000.00  -3.8%


def _numeric(v):
    return v is None or isinstance(v, (int, float, Decimal)) or bool(_NUMBER_TEXT.match(str(v)))


def table(headers, rows, right_align=None):
    """Formats rows as an aligned text table. Numeric columns are right-aligned."""
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    if right_align is None:
        right_align = [bool(rows) and all(_numeric(r[i]) for r in rows) for i in range(len(headers))]

    def line(values):
        return " | ".join(v.rjust(w) if ra else v.ljust(w)
                          for v, w, ra in zip(values, widths, right_align)).rstrip()

    out = [" " + line(headers), "-" + "-+-".join("-" * w for w in widths) + "-"]
    out += [" " + line(r) for r in cells]
    if not rows:
        out.append(" (none)")
    return "\n".join(out)


def money(v, cents=False):
    if v is None:
        return ""
    return f"${v:,.2f}" if cents else f"${v:,.0f}"


def pct(v, signed=False, places=1):
    if v is None:
        return "n/a"
    return f"{v * 100:{'+' if signed else ''}.{places}f}%"
