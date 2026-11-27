"""A small local website for the notes desk: python3 notes_desk.py serve

Uses only Python's built-in web server and reads straight from the database,
so it always shows the latest nightly results. It listens on 127.0.0.1 only:
it can be opened from this computer and nowhere else.

Pages
    /                 dashboard for a night (pick the night with ?date=YYYY-MM-DD)
    /notes            every note, with filters and sortable columns
    /note/CUSIP       one note: terms in plain English, price chart, payout
                      calculator, schedule, nightly history, holdings
    /runs             nightly run history, and a form to run the nightly job
    /reports/NAME     the report files in reports/
"""

import contextlib
import datetime
import html
import io
import json
import re
import secrets
import traceback
import urllib.parse
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import REPORTS_DIR, DeskError, db, market_today

TOKEN = secrets.token_urlsafe(16)
STATIC = {"on": False, "date": None}   # set by export(): pages rendered as files   # guards the run form against other websites posting to it

FLAG_LABELS = {
    "likely_call": "Likely call",
    "call_unlikely": "Call date, not called at today's price",
    "coupon_on_track": "Coupon on track",
    "coupon_missed_if_today": "Coupon would be missed",
    "coupon_at_risk": "Coupon at risk",
    "maturing": "Maturing",
    "maturing_with_loss": "Maturing with a loss",
    "below_protection": "Below protection",
}
FLAG_TONE = {"likely_call": "good", "call_unlikely": "neutral", "coupon_on_track": "good", "coupon_missed_if_today": "bad", "coupon_at_risk": "warn",
             "maturing": "neutral", "maturing_with_loss": "bad", "below_protection": "bad"}
TYPE_LABELS = {"income": "Income", "buffered": "Buffered", "trigger_growth": "Trigger growth",
               "protected": "Principal-protected"}


# --- formatting --------------------------------------------------------------

def esc(v):
    return html.escape("" if v is None else str(v))


def pct(v, signed=False, places=1):
    if v is None:
        return "—"
    return f"{float(v) * 100:{'+' if signed else ''}.{places}f}%"


def money(v, cents=False):
    if v is None:
        return "—"
    return f"${float(v):,.2f}" if cents else f"${float(v):,.0f}"


def num(v, places=2):
    if v is None:
        return "—"
    return f"{float(v):,.{places}f}"


def tone(cushion, warn=0.10):
    if cushion is None:
        return "neutral"
    return "bad" if cushion < 0 else "warn" if cushion < warn else "good"


def chip(text, kind="neutral"):
    return f'<span class="chip {kind}">{esc(text)}</span>'


def sort_value(v):
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, datetime.date):
        return v.isoformat()
    return esc(v)


def cell(text, value=None, cls=""):
    """A table cell; value is what the column sorts by."""
    v = sort_value(value) if value is not None else ""
    return f'<td class="{cls}" data-v="{v}">{text}</td>'


def date_param(query, conn):
    """The night to show: ?date=, else the latest night with results."""
    raw = (query.get("date") or [""])[0]
    nights = [r[0] for r in db.rows(conn, "select distinct run_date from note_daily_status order by 1 desc")]
    if raw:
        try:
            d = datetime.date.fromisoformat(raw)
        except ValueError:
            raise DeskError(f"'{raw}' isn't a date. Use YYYY-MM-DD.")
        return d, nights
    return (nights[0] if nights else None), nights


# --- layout ------------------------------------------------------------------

CSS = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1b1f24; --muted: #5d6673; --line: #e2e5ea;
  --accent: #2459c9; --accent-soft: #e8eefb;
  --good: #1d7a46; --good-soft: #e5f4ec; --warn: #9a6200; --warn-soft: #fdf1dc;
  --bad: #b42318; --bad-soft: #fde8e6; --neutral-soft: #eef0f3;
  --chart-line: #2459c9; --chart-grid: #e8eaee;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111418; --panel: #191d23; --text: #e6e8eb; --muted: #9aa3ae; --line: #2b313a;
    --accent: #7aa2ff; --accent-soft: #1f2a44;
    --good: #5cc98a; --good-soft: #15291f; --warn: #f0b44c; --warn-soft: #2e2413;
    --bad: #ff8a7a; --bad-soft: #341a18; --neutral-soft: #242931;
    --chart-line: #7aa2ff; --chart-grid: #262c34; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
    --bg: #111418; --panel: #191d23; --text: #e6e8eb; --muted: #9aa3ae; --line: #2b313a;
    --accent: #7aa2ff; --accent-soft: #1f2a44;
    --good: #5cc98a; --good-soft: #15291f; --warn: #f0b44c; --warn-soft: #2e2413;
    --bad: #ff8a7a; --bad-soft: #341a18; --neutral-soft: #242931;
    --chart-line: #7aa2ff; --chart-grid: #262c34; color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header.top { background: var(--panel); border-bottom: 1px solid var(--line); }
header.top .wrap { display: flex; align-items: center; gap: 24px; height: 52px; }
.brand { font-weight: 650; letter-spacing: -0.01em; color: var(--text); }
nav a { color: var(--muted); padding: 15px 2px; margin-right: 14px; border-bottom: 2px solid transparent; }
nav a.on { color: var(--text); border-bottom-color: var(--accent); }
nav a:hover { text-decoration: none; color: var(--text); }
.wrap { max-width: 1320px; margin: 0 auto; padding: 0 20px; }
main.wrap { padding-top: 22px; padding-bottom: 60px; }
#notes th, #notes td { padding: 6px 7px; font-size: 13px; }
#notes th { white-space: normal; vertical-align: bottom; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 15px; margin: 0 0 10px; }
.sub { color: var(--muted); margin: 0 0 18px; }
.row { display: flex; gap: 16px; flex-wrap: wrap; align-items: flex-start; }
.grow { flex: 1 1 420px; min-width: 0; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 18px; margin-bottom: 16px; }
.panel > h2:first-child { margin-top: 0; }
.note { color: var(--muted); font-size: 13px; margin: -4px 0 10px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.tile .k { color: var(--muted); font-size: 12px; }
.tile .v { font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tile .s { color: var(--muted); font-size: 12px; }
.tile.bad .v { color: var(--bad); } .tile.warn .v { color: var(--warn); } .tile.good .v { color: var(--good); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
th { color: var(--muted); font-weight: 550; font-size: 12px; background: var(--panel); position: sticky; top: 0; }
th.sortable { cursor: pointer; user-select: none; }
th.sortable:hover { color: var(--text); }
th.asc::after { content: " ▲"; font-size: 9px; } th.desc::after { content: " ▼"; font-size: 9px; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: var(--neutral-soft); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.chip { display: inline-block; padding: 1px 8px; border-radius: 99px; font-size: 12px; font-weight: 550;
  background: var(--neutral-soft); color: var(--muted); }
.chip.good { background: var(--good-soft); color: var(--good); }
.chip.warn { background: var(--warn-soft); color: var(--warn); }
.chip.bad { background: var(--bad-soft); color: var(--bad); }
.t-good { color: var(--good); } .t-warn { color: var(--warn); } .t-bad { color: var(--bad); }
.bar { position: relative; height: 8px; width: 120px; background: var(--neutral-soft); border-radius: 4px;
  display: inline-block; vertical-align: middle; margin-left: 8px; overflow: hidden; }
.bar i { position: absolute; top: 0; bottom: 0; }
.banner { border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; border: 1px solid; }
.banner.warn { background: var(--warn-soft); border-color: var(--warn); }
.banner.bad { background: var(--bad-soft); border-color: var(--bad); }
.banner.good { background: var(--good-soft); border-color: var(--good); }
.banner b { display: block; margin-bottom: 4px; }
.snap { background: var(--accent-soft); color: var(--text); font-size: 13px; padding: 7px 0; }
.controls { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
select, input[type=text], input[type=date], input[type=search] {
  font: inherit; color: var(--text); background: var(--panel); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 9px; }
button { font: inherit; background: var(--accent); color: #fff; border: 0; border-radius: 7px;
  padding: 7px 14px; cursor: pointer; font-weight: 550; }
button:hover { filter: brightness(1.08); }
label.check { display: inline-flex; gap: 6px; align-items: center; color: var(--muted); }
dl.terms { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0; }
dl.terms dt { color: var(--muted); } dl.terms dd { margin: 0; font-variant-numeric: tabular-nums; }
.plain { background: var(--accent-soft); border-radius: 8px; padding: 10px 12px; margin: 0 0 14px; }
.plain p { margin: 0 0 6px; } .plain p:last-child { margin: 0; }
pre.log { background: var(--neutral-soft); border-radius: 8px; padding: 12px; overflow-x: auto;
  font-size: 12.5px; white-space: pre; margin: 0; }
svg text { fill: var(--muted); font-size: 11px; font-family: inherit; }
svg .grid { stroke: var(--chart-grid); }
svg .price { stroke: var(--chart-line); fill: none; stroke-width: 2; }
svg .dot { fill: var(--chart-line); }
svg .lvl { stroke-width: 1.2; stroke-dasharray: 5 4; }
svg .lvl.start { stroke: var(--muted); } svg .lvl.prot { stroke: var(--bad); }
svg .lvl.call { stroke: var(--good); } svg .lvl.coupon { stroke: var(--warn); }
svg text.lvl-start { fill: var(--muted); } svg text.lvl-prot { fill: var(--bad); }
svg text.lvl-call { fill: var(--good); } svg text.lvl-coupon { fill: var(--warn); }
svg .payoff { stroke: var(--chart-line); fill: none; stroke-width: 2.2; }
svg .par { stroke: var(--muted); stroke-dasharray: 3 3; }
svg .marker { stroke: var(--text); stroke-width: 1; stroke-dasharray: 2 3; }
svg .marker-dot { fill: var(--text); }
svg .today { stroke: var(--accent); stroke-width: 1; }
.calc { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.calc input[type=range] { width: 260px; }
.calc .out { font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); padding: 8px 0; }
@media (max-width: 640px) {
  header.top .wrap { gap: 12px; } nav a { margin-right: 8px; } .wrap { padding: 0 16px; }
  .calc input[type=range] { width: 100%; }
}
"""

SORT_JS = """
document.querySelectorAll('table.sortable').forEach(function (t) {
  t.querySelectorAll('th').forEach(function (th, i) {
    th.classList.add('sortable');
    th.addEventListener('click', function () {
      var asc = !th.classList.contains('asc');
      t.querySelectorAll('th').forEach(function (h) { h.classList.remove('asc', 'desc'); });
      th.classList.add(asc ? 'asc' : 'desc');
      var body = t.tBodies[0], rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var x = a.cells[i].dataset.v, y = b.cells[i].dataset.v;
        if (x === '' && y === '') return 0; if (x === '') return 1; if (y === '') return -1;
        var nx = parseFloat(x), ny = parseFloat(y), c;
        var isNum = /^[-+]?(\\d+\\.?\\d*|\\.\\d+)([eE][-+]?\\d+)?$/;   // a real number, not a date like 2026-09-30
        if (isNum.test(x) && isNum.test(y)) c = nx - ny;
        else c = x < y ? -1 : x > y ? 1 : 0;
        return asc ? c : -c;
      });
      rows.forEach(function (r) { body.appendChild(r); });
    });
  });
});
"""


def page(title, body, active="", script="", wide=False):
    links = [("/", "Dashboard", "dash"), ("/notes", "Notes", "notes"), ("/runs", "Runs", "runs")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{t}</a>' for h, t, k in links)
    snap = (f'<div class="snap"><div class="wrap">Read-only snapshot of the notes desk app, from the nightly run '
            f'for {STATIC["date"]:%B %-d, %Y}. Click any CUSIP for its page.</div></div>') if STATIC["on"] else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Notes desk</title>
<style>{CSS}</style></head>
<body>
<header class="top"><div class="wrap"><a class="brand" href="/">Notes desk</a><nav>{nav}</nav></div></header>{snap}
<main class="wrap{" wide" if wide else ""}">{body}</main>
<script>{SORT_JS}{script}</script>
</body></html>"""


def night_picker(path, d, nights, extra=""):
    if not nights or STATIC["on"]:
        return ""
    opts = "".join(f'<option value="{n}"{" selected" if n == d else ""}>{n:%a %b %-d, %Y}</option>'
                   for n in nights)
    return (f'<form method="get" action="{path}" class="controls">{extra}'
            f'<label class="check">Night <select name="date" onchange="this.form.submit()">{opts}</select></label>'
            f'</form>')


def table_html(headers, rows_html, cls="sortable", numeric=()):
    th = "".join(f'<th class="{"n" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    if not rows_html:
        return '<p class="empty">None.</p>'
    return f'<div class="scroll"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(rows_html)}</tbody></table></div>'


def cushion_bar(c):
    """A small bar: the fill shows the cushion from -30% to +60%, zero marked."""
    if c is None:
        return ""
    lo, hi = -0.30, 0.60
    zero = (0 - lo) / (hi - lo) * 100
    x = (min(max(float(c), lo), hi) - lo) / (hi - lo) * 100
    left, width = (x, zero - x) if x < zero else (zero, x - zero)
    color = {"bad": "var(--bad)", "warn": "var(--warn)", "good": "var(--good)"}[tone(c)]
    return (f'<span class="bar"><i style="left:{left:.1f}%;width:{width:.1f}%;background:{color}"></i>'
            f'<i style="left:{zero:.1f}%;width:1px;background:var(--muted)"></i></span>')


# --- dashboard ---------------------------------------------------------------

def dashboard(conn, query):
    d, nights = date_param(query, conn)
    if d is None:
        return page("Dashboard", """<h1>Dashboard</h1>
<div class="panel"><p>No nightly runs yet.</p>
<p>Run one from the <a href="/runs">Runs</a> page, or with <span class="mono">python3 notes_desk.py nightly DATE --fetch</span>.</p></div>""", "dash")

    run = db.rows(conn, """select status, message, finished_at from job_runs
                           where run_date = %s order by run_id desc limit 1""", (d,))
    out = [f'<h1>Night of {d:%A, %B %-d, %Y}</h1>',
           f'<p class="sub">From the nightly run for {d}. '
           f'<a href="/reports/morning_{d}.txt">Morning report</a> · '
           f'<a href="/reports/notes_{d}.csv">Every-note CSV</a></p>',
           night_picker("/", d, nights)]

    if run and run[0][0] == "failed":
        out.append(f'<div class="banner bad"><b>The latest run for this night failed.</b>'
                   f'<pre class="log">{esc(run[0][1])}</pre>The figures below are from an earlier successful run.</div>')

    big = db.rows(conn, """
        select m.index_code, p.close, m.close, m.close / p.close - 1
        from market_data m
        cross join lateral (select close from market_data x where x.index_code = m.index_code
                            and x.price_date < m.price_date order by x.price_date desc limit 1) p
        where m.price_date = %s and abs(m.close / p.close - 1) > 0.20
        order by abs(m.close / p.close - 1) desc""", (d,))
    if big:
        items = ", ".join(f"<b style='display:inline'>{esc(u)}</b> {pct(m, True)} ({num(a)} → {num(b)})"
                          for u, a, b, m in big)
        out.append(f'<div class="banner warn"><b>Prices to double-check</b>These moved more than 20% in a day: '
                   f'{items}. Real moves this size happen, but so do bad prices. If one is wrong, fix '
                   f'<span class="mono">feeds/closes_{d}.csv</span> and rerun this night.</div>')

    k = db.rows(conn, """
        select count(*), sum(h.notional),
               count(*) filter (where s.cushion < 0), coalesce(sum(h.notional) filter (where s.cushion < 0), 0),
               count(*) filter (where s.cushion >= 0 and s.cushion < 0.10),
               sum(s.payout_per_1000 * h.notional / 1000)
        from note_daily_status s join holdings h using (note_id)
        where s.run_date = %s""", (d,))[0]
    ev = db.rows(conn, """select count(*) filter (where flag = 'likely_call'),
                                 count(*) filter (where flag in ('coupon_at_risk', 'coupon_missed_if_today')),
                                 count(*) filter (where flag not in ('below_protection'))
                          from events where run_date = %s""", (d,))[0]
    out.append(f"""<div class="tiles">
<div class="tile"><div class="k">Notes valued</div><div class="v">{k[0]}</div><div class="s">{money(k[1])} notional</div></div>
<div class="tile {'bad' if k[2] else 'good'}"><div class="k">Below protection</div><div class="v">{k[2]}</div><div class="s">{money(k[3])} notional</div></div>
<div class="tile {'warn' if k[4] else ''}"><div class="k">Within 10% of protection</div><div class="v">{k[4]}</div><div class="s">could slip below on a bad day</div></div>
<div class="tile"><div class="k">Value if all matured today</div><div class="v">{money(k[5])}</div><div class="s">{pct((k[5] or 0) / k[1] - 1 if k[1] else None, True)} vs notional, excl. coupons</div></div>
<div class="tile {'good' if ev[0] else ''}"><div class="k">Likely calls, next 30 days</div><div class="v">{ev[0]}</div><div class="s">cash coming back early</div></div>
<div class="tile {'warn' if ev[1] else ''}"><div class="k">Coupons at risk</div><div class="v">{ev[1]}</div><div class="s">next 30 days</div></div>
</div>""")

    # Underliers
    rows = db.rows(conn, """
        select i.index_code, i.name, m.close, p.close,
               (select count(*) from notes n where n.index_code = i.index_code and n.status = 'live'),
               (select coalesce(sum(h.notional), 0) from notes n join holdings h using (note_id)
                 where n.index_code = i.index_code and n.status = 'live'),
               (select count(*) from notes n join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
                 where n.index_code = i.index_code and s.cushion < 0)
        from indices i
        left join market_data m on m.index_code = i.index_code and m.price_date = %s
        left join lateral (select close from market_data x where x.index_code = i.index_code
                           and x.price_date < %s order by x.price_date desc limit 1) p on true
        order by 6 desc, 1""", (d, d, d))
    urows = []
    for code, name, close, prev, n, notional, below in rows:
        chg = (close / prev - 1) if close and prev else None
        urows.append("<tr>" + cell(f'<a href="/notes?u={esc(code)}&date={d}" title="{esc(name)}"><b>{esc(code)}</b></a>', code)
                     + cell(num(close), close, "n")
                     + cell(f'<span class="{"t-bad" if chg is not None and chg < 0 else "t-good"}">{pct(chg, True, 2)}</span>', chg, "n")
                     + cell(str(n), n, "n") + cell(money(notional), notional, "n")
                     + cell(chip(str(below), "bad") if below else "0", below, "n") + "</tr>")
    underliers = table_html(["Underlier", "Close", "Day", "Notes", "Notional", "Below protection"],
                            urows, numeric={1, 2, 3, 4, 5})

    # Closest to losing principal
    rows = db.rows(conn, """
        select n.cusip, n.note_type, n.index_code, s.perf, s.cushion, s.payout_per_1000, n.maturity_date
        from notes n join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
        where s.cushion is not null order by s.cushion limit 15""", (d,))
    crows = ["<tr>" + cell(f'<a class="mono" href="/note/{esc(c)}?date={d}">{esc(c)}</a>', c)
             + cell(esc(TYPE_LABELS.get(t, t)), t) + cell(esc(u), u)
             + cell(pct(p, True), p, "n")
             + cell(f'<span class="t-{tone(cu)}">{pct(cu, True)}</span>{cushion_bar(cu)}', cu, "n")
             + cell(money(v, True), v, "n") + cell(str(m), m) + "</tr>"
             for c, t, u, p, cu, v, m in rows]
    closest = table_html(["CUSIP", "Type", "Linked to", "Since start", "Cushion", "Per $1,000 today", "Matures"],
                         crows, numeric={3, 4, 5})

    # Events
    rows = db.rows(conn, """
        select e.obs_date, n.cusip, n.index_code, e.kind, e.flag, e.notional,
               s.cushion, s.coupon_cushion, s.dist_to_call
        from events e join notes n using (note_id)
        join note_daily_status s on s.note_id = e.note_id and s.run_date = e.run_date
        where e.run_date = %s and e.flag <> 'below_protection'
        order by e.obs_date, e.notional desc""", (d,))
    erows = []
    for od, c, u, kind, flag, notional, cu, ccu, dc in rows:
        measure = {"likely_call": ("above call trigger", dc), "call_unlikely": ("vs call trigger", dc),
                   "coupon_on_track": ("above coupon barrier", ccu), "coupon_at_risk": ("above coupon barrier", ccu),
                   "coupon_missed_if_today": ("above coupon barrier", ccu)}.get(flag, ("cushion", cu))
        erows.append("<tr>" + cell(str(od), od) + cell(f'<a class="mono" href="/note/{esc(c)}?date={d}">{esc(c)}</a>', c)
                     + cell(esc(u), u) + cell(chip(FLAG_LABELS.get(flag, flag), FLAG_TONE.get(flag, "neutral")), flag)
                     + cell(f'{pct(measure[1], True)} <span class="note">{measure[0]}</span>', measure[1], "n")
                     + cell(money(notional), notional, "n") + "</tr>")
    events = table_html(["Date", "CUSIP", "Linked to", "What", "Distance", "Notional"], erows, numeric={4, 5})

    rows = db.rows(conn, """select a.name, c.index_code, c.exposure, c.pct, c.limit_pct
                            from concentration_breaches c join advisors a using (advisor_id)
                            where c.run_date = %s order by c.pct desc""", (d,))
    conc = table_html(["Advisor", "Linked to", "Exposure", "Share of book", "Limit"],
                      ["<tr>" + cell(esc(a), a) + cell(esc(u), u) + cell(money(e), e, "n")
                       + cell(f'<span class="t-bad">{pct(p, places=0)}</span>', p, "n")
                       + cell(pct(l, places=0), l, "n") + "</tr>" for a, u, e, p, l in rows], numeric={2, 3, 4})

    out.append(f"""<div class="panel"><h2>Closest to losing principal</h2>
<p class="note">Cushion: how much further the underlier can fall before principal is lost at maturity. Negative means it's already past that point. <a href="/notes?date={d}">All notes →</a></p>{closest}</div>
<div class="row">
<div class="grow"><div class="panel"><h2>Underliers</h2>
<p class="note">Click one to see the notes linked to it.</p>{underliers}</div></div>
<div class="grow"><div class="panel"><h2>Coming up in the next 30 days</h2>
<p class="note">Every coupon, call and final-valuation date, and how each looks at this night's close.</p>{events}</div>
<div class="panel"><h2>Concentration breaches</h2>{conc}</div></div>
</div>""")
    return page(f"Night of {d}", "".join(out), "dash")


# --- all notes ---------------------------------------------------------------

NOTES_FILTER_JS = """
(function () {
  var f = document.getElementById('filters'); if (!f) return;
  var rows = Array.prototype.slice.call(document.querySelectorAll('#notes tbody tr'));
  function apply() {
    var t = f.type.value, u = f.u.value, q = f.q.value.trim().toLowerCase(), below = f.below.checked, shown = 0;
    rows.forEach(function (r) {
      var ok = (!t || r.dataset.type === t) && (!u || r.dataset.u === u) &&
               (!q || r.dataset.text.indexOf(q) >= 0) && (!below || r.dataset.below === '1');
      r.style.display = ok ? '' : 'none'; if (ok) shown++;
    });
    document.getElementById('count').textContent = shown + ' of ' + rows.length + ' notes';
  }
  var h = location.hash.slice(1);   // notes.html#NDX opens filtered to one underlier
  if (h && f.u.querySelector('option[value="' + h + '"]')) f.u.value = h;
  ['change', 'input'].forEach(function (ev) { f.addEventListener(ev, apply); });
  f.addEventListener('submit', function (e) { e.preventDefault(); });
  apply();
})();
"""


def notes_page(conn, query):
    d, nights = date_param(query, conn)
    rows = db.rows(conn, """
        select n.cusip, i.name, n.note_type, n.index_code, n.trade_date, n.maturity_date, n.initial_level,
               s.index_level, s.perf, s.cushion, s.coupon_cushion, s.dist_to_call, s.payout_per_1000,
               (select min(o.obs_date) from observation_dates o where o.note_id = n.note_id and o.obs_date > %s),
               n.status,
               (select string_agg(o.kind, ' + ' order by o.kind) from observation_dates o
                where o.note_id = n.note_id and o.obs_date = (select min(x.obs_date) from observation_dates x
                                                              where x.note_id = n.note_id and x.obs_date > %s))
        from notes n join issuers i using (issuer_id)
        left join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
        where %s::date is null or n.trade_date <= %s::date   -- only notes in the book that night
        order by s.cushion nulls last, n.cusip""", (d, d, d, d, d))
    pick_u = (query.get("u") or [""])[0]
    pick_t = (query.get("type") or [""])[0]
    underliers = sorted({r[3] for r in rows})
    types = sorted({r[2] for r in rows})

    trs = []
    for (c, issuer, t, u, td, md, init, close, perf, cu, ccu, dc, pay, nxt, status, nxt_kind) in rows:
        text = " ".join(str(x) for x in (c, issuer, t, u)).lower()
        trs.append(f'<tr data-type="{esc(t)}" data-u="{esc(u)}" data-text="{esc(text)}" '
                   f'data-below="{1 if cu is not None and cu < 0 else 0}">'
                   + cell(f'<a class="mono" href="/note/{esc(c)}?date={d or ""}">{esc(c)}</a>'
                          + ("" if status == "live" else " " + chip(status, "good")), c)
                   + cell(esc(issuer.replace(" Financial Company LLC", "").replace(", Inc.", "")), issuer)
                   + cell(esc(TYPE_LABELS.get(t, t)), t) + cell(esc(u), u)
                   + cell(str(td), td)
                   + cell(num(init), init, "n") + cell(num(close), close, "n")
                   + cell(pct(perf, True), perf, "n")
                   + cell(f'<span class="t-{tone(cu)}">{pct(cu, True)}</span>' if cu is not None else "—", cu, "n")
                   + cell(pct(ccu, True) if ccu is not None else "—", ccu, "n")
                   + cell(pct(dc, True) if dc is not None else "—", dc, "n")
                   + cell(money(pay, True), pay, "n")
                   + cell(f'{nxt} <span class="note">{esc(nxt_kind)}</span>' if nxt else "—", nxt)
                   + cell(str(md), md) + "</tr>")

    topts = '<option value="">All types</option>' + "".join(
        f'<option value="{esc(t)}"{" selected" if t == pick_t else ""}>{esc(TYPE_LABELS.get(t, t))}</option>' for t in types)
    uopts = '<option value="">All underliers</option>' + "".join(
        f'<option value="{esc(u)}"{" selected" if u == pick_u else ""}>{esc(u)}</option>' for u in underliers)
    head = ["CUSIP", "Issuer", "Type", "Linked to", "Start date", "Start level", "Close", "Since start", "Cushion",
            "Coupon cushion", "Vs call", "Per $1,000", "Next check", "Matures"]
    body = f"""<h1>Notes</h1>
<p class="sub">{f"Values from the night of {d}." if d else "No nightly runs yet, so values are blank."}
Click a column to sort. Click a CUSIP for the full picture.</p>
{night_picker("/notes", d, nights)}
<div class="panel">
<form id="filters" class="controls">
  <select name="type">{topts}</select>
  <select name="u">{uopts}</select>
  <input type="search" name="q" placeholder="Search CUSIP or issuer" aria-label="Search">
  <label class="check"><input type="checkbox" name="below"> Below protection only</label>
  <span id="count" class="note" style="margin:0 0 0 auto"></span>
</form>
<div class="scroll"><table id="notes" class="sortable"><thead><tr>
{"".join(f'<th class="{"n" if 5 <= i <= 11 else ""}">{h}</th>' for i, h in enumerate(head))}
</tr></thead><tbody>{"".join(trs)}</tbody></table></div></div>"""
    return page("Notes", body, "notes", NOTES_FILTER_JS)


# --- one note ----------------------------------------------------------------

def plain_terms(n):
    """The note's payout in plain English."""
    u = esc(n["index_code"])
    p = []
    if n["note_type"] == "income":
        cb = n["coupon_barrier_pct"] or n["barrier_pct"]
        p.append(f"<b>Coupons:</b> {pct(n['coupon_rate'], places=2)} a year, paid on each coupon date that {u} "
                 f"closes at or above {pct(cb, places=0)} of its start "
                 f"({num(n['initial_level'] * cb)})."
                 + (" Missed coupons are paid later if it gets back above (memory coupon)." if n["memory_coupon"] else ""))
    else:
        cap = f", capped at a {pct(n['cap_pct'], places=2)} total gain" if n["cap_pct"] is not None else ", with no cap"
        part = float(n["participation"])
        p.append(f"<b>Upside:</b> {'the same as' if part == 1 else f'{part:g}× '} the gain in {u}{cap}.")
    if n["autocall_pct"] is not None:
        prem = f", paying a {pct(n['call_premium'], places=2)} return" if n["call_premium"] else ""
        p.append(f"<b>Early call:</b> repaid early{prem} if {u} closes at or above {pct(n['autocall_pct'], places=0)} "
                 f"of its start ({num(n['initial_level'] * n['autocall_pct'])}) on a call date.")
    if n["note_type"] in ("income", "trigger_growth"):
        p.append(f"<b>At maturity:</b> all your money back if {u} is at or above {pct(n['barrier_pct'], places=0)} "
                 f"of its start ({num(n['initial_level'] * n['barrier_pct'])}). Below that, the protection is gone "
                 f"and you lose the full fall from the start (a barrier).")
    elif n["note_type"] == "buffered":
        rate = float(n["downside_rate"] or 1)
        more = f", each 1% costing {rate:.4g}%" if rate != 1 else ", one for one"
        p.append(f"<b>Downside:</b> the first {pct(n['buffer_pct'], places=0)} of a fall costs nothing (a buffer). "
                 f"Beyond {num(n['initial_level'] * (1 - n['buffer_pct']))} you lose the further fall{more}.")
    elif n["note_type"] == "protected":
        p.append("<b>Downside:</b> none at maturity. You get at least $1,000 per $1,000 back, whatever happens "
                 "(as long as the issuer can pay).")
    p.append(f"All of this depends on <b>{esc(n['issuer'])}</b> being able to pay.")
    return "".join(f"<p>{x}</p>" for x in p)


def svg_price_chart(points, levels, width=640, height=260):
    """Line chart of closes, with dashed lines at the note's key levels."""
    if not points:
        return '<p class="empty">No prices yet.</p>'
    pad_l, pad_r, pad_t, pad_b = 58, 120, 12, 26
    xs = [p[0].toordinal() for p in points]
    vals = [float(p[1]) for p in points] + [float(v) for _, v, _ in levels]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or hi * 0.1
    lo, hi = lo - span * 0.08, hi + span * 0.08
    x0, x1 = min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1
    W, H = width - pad_l - pad_r, height - pad_t - pad_b

    def X(o): return pad_l + (o - x0) / (x1 - x0) * W
    def Y(v): return pad_t + (hi - float(v)) / (hi - lo) * H

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Price chart">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        out.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                   f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:,.0f}</text>')
    for o, anchor in ((x0, "start"), (x1, "end")):
        out.append(f'<text x="{X(o):.1f}" y="{height - 6}" text-anchor="{anchor}">'
                   f'{datetime.date.fromordinal(o):%b %-d, %Y}</text>')
    for label, v, cls in levels:
        out.append(f'<line class="lvl {cls}" x1="{pad_l}" x2="{pad_l + W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                   f'<text class="lvl-{cls}" x="{pad_l + W + 6}" y="{Y(v) + 4:.1f}">{esc(label)} {float(v):,.0f}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(v):.1f}" for i, (x, (_, v)) in enumerate(zip(xs, points)))
    out.append(f'<path class="price" d="{path}"/>')
    if len(points) <= 40:   # dots only while there are few enough to see
        for x, (_, v) in zip(xs, points):
            out.append(f'<circle class="dot" cx="{X(x):.1f}" cy="{Y(v):.1f}" r="3"/>')
    out.append("</svg>")
    return "".join(out)


def svg_payoff(curve, today_move, width=640, height=260):
    """Payout per $1,000 at maturity against the underlier's move from its start."""
    pad_l, pad_r, pad_t, pad_b = 58, 16, 12, 28
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    mlo, mhi = curve[0][0], curve[-1][0]
    vals = [float(v) for _, v in curve]
    lo, hi = min(min(vals), 900) * 0.95, max(max(vals), 1100) * 1.03

    def X(m): return pad_l + (m - mlo) / (mhi - mlo) * W
    def Y(v): return pad_t + (hi - v) / (hi - lo) * H

    out = [f'<svg id="payoff" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Payout chart" '
           f'data-mlo="{mlo}" data-mhi="{mhi}" data-lo="{lo}" data-hi="{hi}" data-padl="{pad_l}" '
           f'data-padt="{pad_t}" data-w="{W}" data-h="{H}">']
    for v in range(0, 3001, 200):
        if lo <= v <= hi:
            out.append(f'<line class="grid" x1="{pad_l}" x2="{pad_l + W}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                       f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">${v:,}</text>')
    for m in range(-80, 101, 20):
        if mlo <= m <= mhi:
            out.append(f'<text x="{X(m):.1f}" y="{height - 8}" text-anchor="middle">{m:+d}%</text>')
    out.append(f'<line class="par" x1="{pad_l}" x2="{pad_l + W}" y1="{Y(1000):.1f}" y2="{Y(1000):.1f}"/>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(m):.1f},{Y(float(v)):.1f}" for i, (m, v) in enumerate(curve))
    out.append(f'<path class="payoff" d="{path}"/>')
    if today_move is not None and mlo <= today_move <= mhi:
        out.append(f'<line class="today" x1="{X(today_move):.1f}" x2="{X(today_move):.1f}" '
                   f'y1="{pad_t}" y2="{pad_t + H}"/>'
                   f'<text x="{X(today_move) + 4:.1f}" y="{pad_t + 10}" style="fill:var(--accent)">today</text>')
    out.append(f'<line id="pm-line" class="marker" x1="0" x2="0" y1="{pad_t}" y2="{pad_t + H}"/>'
               f'<circle id="pm-dot" class="marker-dot" r="4.5" cx="0" cy="0"/></svg>')
    return "".join(out)


PAYOFF_JS = """
(function () {
  var s = document.getElementById('payoff'); if (!s) return;
  var curve = JSON.parse(document.getElementById('curve').textContent);
  var r = document.getElementById('move'), lbl = document.getElementById('move-lbl'),
      out = document.getElementById('pay-out'), lvl = document.getElementById('lvl-out');
  var d = s.dataset, mlo = +d.mlo, mhi = +d.mhi, lo = +d.lo, hi = +d.hi,
      pl = +d.padl, pt = +d.padt, w = +d.w, h = +d.h, start = +r.dataset.start;
  function X(m) { return pl + (m - mlo) / (mhi - mlo) * w; }
  function Y(v) { return pt + (hi - v) / (hi - lo) * h; }
  function show() {
    var m = +r.value, v = curve[m - mlo][1];
    lbl.textContent = (m > 0 ? '+' : '') + m + '%';
    out.textContent = '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    lvl.textContent = (start * (1 + m / 100)).toLocaleString(undefined, {maximumFractionDigits: 2});
    var pl_ = document.getElementById('pm-line'), dot = document.getElementById('pm-dot');
    pl_.setAttribute('x1', X(m)); pl_.setAttribute('x2', X(m));
    dot.setAttribute('cx', X(m)); dot.setAttribute('cy', Y(v));
  }
  r.addEventListener('input', show); show();
})();
"""


def note_page(conn, cusip, query):
    d, nights = date_param(query, conn)
    headers, found = db.query(conn, """
        select n.*, i.name as issuer, x.name as underlier_name
        from notes n join issuers i using (issuer_id) join indices x using (index_code)
        where n.cusip = %s""", (cusip,))
    if not found:
        return None
    n = dict(zip(headers, found[0]))

    st = db.rows(conn, """select index_level, perf, cushion, coupon_cushion, dist_to_call, payout_per_1000
                          from note_daily_status where note_id = %s and run_date = %s""", (n["note_id"], d))
    st = st[0] if st else None

    prices = db.rows(conn, """select price_date, close from market_data
                              where index_code = %s and price_date > %s order by price_date""",
                     (n["index_code"], n["trade_date"]))
    points = [(n["trade_date"], n["initial_level"])] + list(prices)
    init = n["initial_level"]
    levels = [("Start", init, "start")]
    if n["protection_pct"] is not None:
        levels.append(("Barrier" if n["note_type"] != "buffered" else "Buffer", init * n["protection_pct"], "prot"))
    if n["autocall_pct"] is not None:
        levels.append(("Call", init * n["autocall_pct"], "call"))
    cb = n["coupon_barrier_pct"]
    if n["note_type"] == "income" and cb is not None and cb != n["barrier_pct"]:
        levels.append(("Coupon", init * cb, "coupon"))
    levels = [(lbl, v, cls) for lbl, v, cls in levels if not (cls == "call" and n["autocall_pct"] == 1)]

    curve = [(int(m), float(v)) for m, v in db.rows(conn, """
        select m, note_payout_per_1000(n, n.initial_level * (1 + m / 100.0))
        from notes n, generate_series(-80, 100) m where n.cusip = %s order by m""", (cusip,))]
    today_move = round(float(st[1]) * 100) if st else None
    start_move = max(-80, min(100, today_move if today_move is not None else 0))

    obs = db.rows(conn, """select obs_date, string_agg(kind, ' + ' order by kind) from observation_dates
                           where note_id = %s group by obs_date order by obs_date""", (n["note_id"],))
    today = d or datetime.date.today()
    next_d = next((od for od, _ in obs if od > today), None)
    next_call = db.scalar(conn, """select min(obs_date) from observation_dates
                                   where note_id = %s and kind = 'call' and obs_date > %s""", (n["note_id"], today))
    if next_call is None:
        call_text, call_soon = "no call dates left", False
    else:
        call_soon = (next_call - today).days <= 30
        call_text = (f"would be called if it closes here on {next_call:%b %-d, %Y}" if st and st[4] is not None and st[4] >= 0
                     else f"below the call level; next call date {next_call:%b %-d, %Y}")
        if not call_soon:
            call_text += f" ({(next_call - today).days} days away)"
    obs_rows = []
    for od, kinds in obs:
        cls = "t-good" if od == next_d else ("" if od > today else "note")
        tag = chip("next", "good") if od == next_d else ""
        obs_rows.append(f'<tr><td class="{cls}">{od}</td><td class="{cls}">{esc(kinds)}</td><td>{tag}</td></tr>')

    hist = db.rows(conn, """select run_date, index_level, perf, cushion, payout_per_1000
                            from note_daily_status where note_id = %s order by run_date desc limit 30""", (n["note_id"],))
    hist_rows = ["<tr>" + cell(f'<a href="/?date={r}">{r}</a>', r) + cell(num(lv), lv, "n")
                 + cell(pct(p, True), p, "n")
                 + cell(f'<span class="t-{tone(cu)}">{pct(cu, True)}</span>' if cu is not None else "—", cu, "n")
                 + cell(money(v, True), v, "n") + "</tr>" for r, lv, p, cu, v in hist]

    hold = db.rows(conn, """select a.household, v.name, h.notional from holdings h
                            join accounts a using (account_id) join advisors v using (advisor_id)
                            where h.note_id = %s order by h.notional desc""", (n["note_id"],))
    hold_rows = ["<tr>" + cell(esc(hh), hh) + cell(esc(adv), adv) + cell(money(x), x, "n") + "</tr>"
                 for hh, adv, x in hold]

    terms = [("Issuer", esc(n["issuer"])), ("Linked to", f'{esc(n["index_code"])} · {esc(n["underlier_name"])}'),
             ("Type", esc(TYPE_LABELS.get(n["note_type"], n["note_type"]))),
             ("Trade date", n["trade_date"]), ("Maturity date", n["maturity_date"]),
             ("Start level", num(init))]
    for key, label, fmt in (("coupon_rate", "Coupon (a year)", lambda v: pct(v, places=2)),
                            ("coupon_barrier_pct", "Coupon barrier", lambda v: pct(v, places=0)),
                            ("barrier_pct", "Barrier", lambda v: pct(v, places=0)),
                            ("buffer_pct", "Buffer", lambda v: pct(v, places=0)),
                            ("downside_rate", "Downside rate", lambda v: f"{float(v):.5g}×"),
                            ("participation", "Participation", lambda v: f"{float(v):g}×"),
                            ("cap_pct", "Cap", lambda v: pct(v, places=2)),
                            ("autocall_pct", "Call trigger", lambda v: pct(v, places=0)),
                            ("call_premium", "Call premium", lambda v: pct(v, places=2))):
        if n[key] is not None:
            terms.append((label, fmt(n[key])))
    if n["note_type"] == "income":
        terms.append(("Memory coupon", "yes" if n["memory_coupon"] else "no"))
    terms.append(("Status", esc(n["status"]) + (f" ({n['closed_date']})" if n["closed_date"] else "")))
    if n["source_url"]:
        terms.append(("Filing", f'<a href="{esc(n["source_url"])}" target="_blank" rel="noopener">SEC filing ↗</a>'))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in terms)

    if st:
        lv, perf, cu, ccu, dc, pay = st
        tiles = f"""<div class="tiles">
<div class="tile"><div class="k">{esc(n["index_code"])} close</div><div class="v">{num(lv)}</div><div class="s">{pct(perf, True)} since start</div></div>
<div class="tile {tone(cu)}"><div class="k">Cushion</div><div class="v">{pct(cu, True) if cu is not None else "None needed"}</div><div class="s">{"principal can't be lost at maturity" if cu is None else "room before principal is lost" if cu >= 0 else "past the protection level"}</div></div>
<div class="tile"><div class="k">Per $1,000 if it matured today</div><div class="v">{money(pay, True)}</div><div class="s">{"principal only, coupons extra" if n["note_type"] == "income" else "principal plus any gain"}</div></div>
{f'<div class="tile {tone(ccu)}"><div class="k">Above coupon barrier</div><div class="v">{pct(ccu, True)}</div><div class="s">negative on a coupon date: no coupon</div></div>' if ccu is not None else ""}
{f'<div class="tile {"good" if dc >= 0 and call_soon else ""}"><div class="k">Vs call trigger</div><div class="v">{pct(dc, True)}</div><div class="s">{call_text}</div></div>' if dc is not None else ""}
</div>"""
    elif d and d < n["trade_date"]:
        tiles = (f'<div class="panel"><p class="empty">This note was priced on {n["trade_date"]:%B %-d, %Y}, '
                 f'so it has no results for {d:%B %-d, %Y}. Pick a later night.</p></div>')
    else:
        tiles = f'<div class="panel"><p class="empty">No nightly result for this note on {d}.</p></div>'

    body = f"""<p class="sub" style="margin-bottom:6px"><a href="/notes?date={d or ""}">← All notes</a></p>
<h1><span class="mono" style="font-size:20px">{esc(cusip)}</span> · {esc(n["index_code"])} {esc(TYPE_LABELS.get(n["note_type"], n["note_type"]).lower())} note</h1>
<p class="sub">{esc(n["issuer"])} · matures {n["maturity_date"]:%B %-d, %Y}{f" · values from the night of {d}" if d else ""}</p>
{night_picker(f"/note/{esc(cusip)}", d, nights)}
{tiles}
<div class="row">
<div class="grow">
<div class="panel"><h2>How it pays</h2><div class="plain">{plain_terms(n)}</div><dl class="terms">{dl}</dl></div>
<div class="panel"><h2>Schedule</h2><p class="note">Coupon, call and final valuation dates. The note is paid on the maturity date, a few days after the final check.</p>
<div class="scroll" style="max-height:340px;overflow-y:auto"><table><thead><tr><th>Date</th><th>Check</th><th></th></tr></thead><tbody>{"".join(obs_rows)}</tbody></table></div></div>
</div>
<div class="grow">
<div class="panel"><h2>{esc(n["index_code"])} since the trade date</h2>{svg_price_chart(points, levels)}</div>
<div class="panel"><h2>Payout calculator</h2>
<p class="note">What $1,000 pays at maturity for a given move in {esc(n["index_code"])} from its start{" (principal only; coupons are paid on top)" if n["note_type"] == "income" else ""}. Drag to try a move.</p>
{svg_payoff(curve, today_move)}
<div class="calc"><input type="range" id="move" min="{curve[0][0]}" max="{curve[-1][0]}" step="1" value="{start_move}" data-start="{float(init)}" aria-label="Move from start">
<span>Move <b id="move-lbl"></b> → level <b id="lvl-out"></b></span><span class="out" id="pay-out"></span></div>
<script type="application/json" id="curve">{json.dumps(curve)}</script></div>
<div class="panel"><h2>Nightly history</h2><p class="note">The latest 30 nights.</p>{table_html(["Night", "Close", "Since start", "Cushion", "Per $1,000"], hist_rows, numeric={1, 2, 3, 4})}</div>
<div class="panel"><h2>Holdings</h2>{table_html(["Household", "Advisor", "Notional"], hold_rows, numeric={2})}</div>
</div></div>"""
    return page(cusip, body, "notes", PAYOFF_JS)


# --- runs --------------------------------------------------------------------

def runs_page(conn, result=None):
    # One row per night: its latest run, and how many times it has run.
    rows = db.rows(conn, """
        select distinct on (run_date) run_date, run_id, to_char(started_at, 'YYYY-MM-DD HH24:MI'),
               extract(epoch from finished_at - started_at)::float, status, message,
               count(*) over (partition by run_date)
        from job_runs order by run_date desc, run_id desc""")
    trs = []
    for rd, rid, started, took, status, msg, attempts in rows:
        kind = {"ok": "warn" if msg else "good", "failed": "bad"}.get(status, "neutral")
        label = "ok, with warnings" if status == "ok" and msg else status
        trs.append("<tr>" + cell(f'<a href="/?date={rd}">{rd}</a>', rd)
                   + cell(chip(label, kind), status)
                   + cell(esc(started), started)
                   + cell(f"{float(took):.1f}s" if took is not None else "", took, "n")
                   + cell(str(attempts), attempts, "n") + cell(str(rid), rid, "n")
                   + f'<td style="white-space:pre-wrap;max-width:520px">{esc(msg)}</td></tr>')
    counts = db.rows(conn, """
        select count(distinct run_date),
               count(distinct run_date) filter (where status = 'failed' and not exists (
                   select 1 from job_runs j2 where j2.run_date = job_runs.run_date and j2.run_id > job_runs.run_id)),
               min(run_date), max(run_date)
        from job_runs""")[0]
    # One row per night: the latest price file loaded, and how many times.
    loads = db.rows(conn, """
        select distinct on (run_date) run_date, filename, row_count,
               to_char(loaded_at, 'YYYY-MM-DD HH24:MI'), count(*) over (partition by run_date)
        from feed_loads order by run_date desc, load_id desc""")
    n_underliers = db.scalar(conn, "select count(*) from indices")
    lrows, short = [], 0
    for r, f, c, t, times in loads:
        if c < n_underliers:
            short += 1
        rows_cell = (cell(chip(str(c), "warn"), c, "n") if c < n_underliers else cell(str(c), c, "n"))
        lrows.append("<tr>" + cell(str(r), r) + cell(f'<span class="mono">{esc(str(f).split("/")[-1])}</span>', f)
                     + rows_cell + cell(esc(t), t) + cell(str(times), times, "n") + "</tr>")
    loads_note = (f"{len(loads)} nights. Each night's latest price file; \"Rows\" is how many closes it had "
                  f"(one per underlier, {n_underliers} in all) and \"Loads\" how many times it has been loaded."
                  + (f" {short} night(s) had fewer than {n_underliers} closes." if short else ""))

    today = market_today()
    res = ""
    if result:
        ok, d, log_text = result
        res = (f'<div class="banner {"good" if ok else "bad"}"><b>{"Run finished" if ok else "Run failed"} for {d}</b>'
               f'<pre class="log">{esc(log_text)}</pre>'
               + (f'<p style="margin:8px 0 0"><a href="/?date={d}">Open the dashboard for {d} →</a></p>' if ok else "")
               + "</div>")
    run_form = "" if STATIC["on"] else f"""<div class="panel"><h2>Run the nightly job</h2>
<p class="note">Same as <span class="mono">python3 notes_desk.py nightly DATE [--fetch]</span>. Rerunning a night is safe: it replaces that night's results.</p>
<form method="post" action="/run" class="controls">
<input type="hidden" name="token" value="{TOKEN}">
<input type="date" name="date" value="{today}" required aria-label="Night">
<label class="check"><input type="checkbox" name="fetch" checked> Download closes first if there's no price file yet</label>
<button type="submit">Run nightly</button></form></div>"""
    body = f"""<h1>Runs</h1>
<p class="sub">{"Every nightly run in the database when this snapshot was made." if STATIC["on"] else "Every nightly run, whether it came from cron, the command line or this page."}</p>
{res}
{run_form}
<div class="panel"><h2>Run history</h2>
<p class="note">{counts[0]} nights{f", {counts[2]} to {counts[3]}" if counts[0] else ""}. Each night's latest run; "Runs" counts how many times it has been run (backfills and reruns add to it).{f" {counts[1]} night(s) whose latest run failed." if counts[1] else ""}</p>
<div style="max-height:560px;overflow:auto">{table_html(["Night", "Status", "Last run", "Took", "Runs", "Run #", "Message"], trs, numeric={3, 4, 5})}</div></div>
<div class="panel"><h2>Price files loaded</h2>
<p class="note">{loads_note}</p>
<div style="max-height:560px;overflow:auto">{table_html(["Night", "File", "Rows", "Last loaded", "Loads"], lrows, numeric={2, 4})}</div></div>"""
    return page("Runs", body, "runs")


def run_nightly(d, fetch):
    from .nightly import run
    buf = io.StringIO()
    ok = True
    with contextlib.redirect_stdout(buf):
        try:
            run(d, fetch)
        except DeskError as e:
            ok = False
            print(str(e))
            for p in e.problems:
                print(f"  {p}")
    return ok, buf.getvalue()


# --- export: the website as static files -----------------------------------------

def static_links(text, d):
    """Rewrites the website's links so the pages work as plain files."""
    text = re.sub(r'href="/note/([A-Z0-9]+)(\?[^"]*)?"', r'href="note_\1.html"', text)
    text = re.sub(r'href="/notes\?u=([^"&]+)[^"]*"', r'href="notes.html#\1"', text)
    text = re.sub(r'href="/notes(\?[^"]*)?"', 'href="notes.html"', text)
    text = re.sub(r'href="/reports/([^"]+)"', r'href="\1"', text)
    text = text.replace('href="/runs"', 'href="runs.html"')
    text = text.replace(f'href="/?date={d}"', 'href="index.html"').replace('href="/"', 'href="index.html"')
    # Other nights aren't in the snapshot: show their dates without a link.
    return re.sub(r'<a href="/\?date=([0-9-]+)">([^<]*)</a>', r'\2', text)


def export(run_date=None, out_dir=None):
    """Writes the dashboard, the notes table and every note's page as plain HTML
    files, for sharing: they open in any browser with no server or database."""
    from . import PROJECT_DIR
    out = out_dir or PROJECT_DIR / "export"
    conn = db.connect(autocommit=True)
    try:
        d, nights = date_param({"date": [str(run_date)]} if run_date else {}, conn)
        if d is None or d not in nights:
            raise DeskError(f"There are no nightly results for {run_date or 'any night'} to export. "
                            "Run the nightly job first.")
        STATIC.update(on=True, date=d)
        q = {"date": [str(d)]}
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            if old.is_file():
                old.unlink()
        pages = {"index.html": re.sub(r"<title>[^<]*</title>", "<title>Notes Desk Snapshot</title>", dashboard(conn, q), count=1),
                 "notes.html": notes_page(conn, q), "runs.html": runs_page(conn)}
        for (cusip,) in db.rows(conn, "select cusip from notes order by cusip"):
            pages[f"note_{cusip}.html"] = note_page(conn, cusip, q)
        for name, text in pages.items():
            (out / name).write_text(static_links(text, d), encoding="utf-8")
        copied = []
        for name in (f"morning_{d}.txt", f"notes_{d}.csv"):
            if (REPORTS_DIR / name).exists():
                (out / name).write_bytes((REPORTS_DIR / name).read_bytes())
                copied.append(name)
    finally:
        STATIC.update(on=False, date=None)
        conn.close()
    print(f"Wrote {len(pages)} pages{' and the two report files' if len(copied) == 2 else ''} "
          f"for the night of {d} to {out}/")
    print(f"Open {out / 'index.html'} in a browser, or zip the folder to share it.")
    return out


# --- server ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "NotesDesk"

    def log_message(self, fmt, *args):
        pass   # keep the terminal quiet; errors are still printed

    def send(self, status, body, ctype="text/html; charset=utf-8", extra=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def error_page(self, status, title, message):
        self.send(status, page(title, f'<h1>{esc(title)}</h1><div class="panel"><pre class="log">{esc(message)}</pre></div>'))

    def handle_safely(self, fn):
        try:
            fn()
        except DeskError as e:
            self.error_page(500, "Something needs fixing", str(e) + "".join(f"\n  {p}" for p in e.problems))
        except Exception as e:
            traceback.print_exc()
            self.error_page(500, "Unexpected error", f"{type(e).__name__}: {e}\n\n(Details are in the terminal running serve.)")

    def do_GET(self):
        self.handle_safely(self._get)

    def do_POST(self):
        self.handle_safely(self._post)

    def _get(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        path = urllib.parse.unquote(url.path)

        if path.startswith("/reports/"):
            name = path.split("/", 2)[2]
            f = REPORTS_DIR / name
            if not re.fullmatch(r"(morning|notes)_\d{4}-\d{2}-\d{2}\.(txt|csv)", name) or not f.exists():
                return self.error_page(404, "Report not found",
                                       f"There's no reports/{name}. Reports are written by each nightly run.")
            ctype = "text/plain; charset=utf-8"
            extra = [("Content-Disposition", f'attachment; filename="{name}"')] if name.endswith(".csv") else []
            return self.send(200, f.read_bytes(), ctype, extra)
        if path == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")

        conn = db.connect(autocommit=True)
        try:
            if path == "/":
                body = dashboard(conn, query)
            elif path == "/notes":
                body = notes_page(conn, query)
            elif path.startswith("/note/"):
                cusip = path[len("/note/"):].strip("/").upper()
                body = note_page(conn, cusip, query)
                if body is None:
                    return self.error_page(404, "Note not found", f"There's no note with CUSIP {cusip}.")
            elif path == "/runs":
                body = runs_page(conn)
            else:
                return self.error_page(404, "Page not found", f"There's no page at {path}.")
        finally:
            conn.close()
        self.send(200, body)

    def _post(self):
        if self.path != "/run":
            return self.error_page(404, "Page not found", self.path)
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if (form.get("token") or [""])[0] != TOKEN:
            return self.error_page(403, "Form expired",
                                   "This form is from before the server restarted. Reload the Runs page and try again.")
        try:
            d = datetime.date.fromisoformat((form.get("date") or [""])[0])
        except ValueError:
            return self.error_page(400, "Bad date", "Pick a date for the run.")
        ok, log_text = run_nightly(d, "fetch" in form)
        print(f"[serve] nightly {d}: {'finished' if ok else 'failed'}", flush=True)
        conn = db.connect(autocommit=True)
        try:
            self.send(200, runs_page(conn, (ok, d, log_text)))
        finally:
            conn.close()


def serve(port=8000, host="127.0.0.1"):
    conn = db.connect()   # fail early, with the usual friendly message, if the database isn't there
    conn.close()
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        raise DeskError(f"Couldn't start on port {port} ({e.strerror}). Try another: "
                        f"python3 notes_desk.py serve --port {port + 1}")
    where = "(only on this computer)" if host == "127.0.0.1" else "(see compose.yaml for who can reach it)"
    print(f"Notes desk is running at http://localhost:{port}  {where}")
    print("Leave this window open while you use it. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
