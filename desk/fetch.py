"""Fetching closing prices from Yahoo Finance's daily price feed.

Free, no account or key, and covers stocks and the indices (S&P 500, Nasdaq-100,
Russell 2000). It isn't an official data service: Yahoo can change or block it
without notice, and its terms are for personal use. Every fetched price still
goes through the same checks as a typed one (no blanks or missing underliers;
a move of more than 20% from the previous close is flagged for checking).

To use another data source, replace daily_closes() below. It only has to return
{date: close} for one symbol.
"""

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import FEEDS_DIR, DeskError, csvfiles, db

# Yahoo's symbol for an underlier, where it differs from our code. Stocks use
# their own ticker. Add a line here for any new index you add notes on.
YAHOO_SYMBOLS = {
    "SPX": "^GSPC",   # S&P 500
    "NDX": "^NDX",    # Nasdaq-100
    "RTY": "^RUT",    # Russell 2000 (the index, not the futures)
}

URL = os.environ.get("NOTES_DESK_PRICE_URL",
                     "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d")

# A bar for today is only a close once the market has shut. Wait a little past
# 4:00 pm New York time so the closing auction prints have come through.
CLOSE_IS_FINAL_AFTER = datetime.time(16, 20)
# After this, a weekday with no close at all is called a market holiday in the
# error message (rather than the feed running a little late).
HOLIDAY_IF_NO_CLOSE_BY = datetime.time(17, 0)
CENT = Decimal("0.01")
NEW_YORK = ZoneInfo("America/New_York")


def why_no_closes(run_date, found, now=None):
    """The feed has no close for run_date for any underlier: the error saying why."""
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(NEW_YORK)
    if run_date > now.date():
        return DeskError(f"{run_date} is in the future (it's {now:%Y-%m-%d %H:%M} in New York).")
    if run_date == now.date() and now.time() < CLOSE_IS_FINAL_AFTER:
        return DeskError(f"Today's closes aren't final until {CLOSE_IS_FINAL_AFTER:%H:%M} New York time "
                         f"(it's {now:%H:%M} there now). Run it again after that.")
    later = any(any(d > run_date for d in c) for c in found.values() if c)
    if run_date.weekday() >= 5 or later or (run_date == now.date() and now.time() >= HOLIDAY_IF_NO_CLOSE_BY):
        what = "a weekend" if run_date.weekday() >= 5 else "a market holiday"
        return DeskError(f"No closes for {run_date}: the market was closed ({what}).")
    return DeskError(f"No closes for {run_date} yet. If today was a trading day, the feed may be running "
                     "late: try again in a few minutes.")


def yahoo_symbol(code):
    return YAHOO_SYMBOLS.get(code, code.replace(".", "-"))   # e.g. BRK.B is BRK-B on Yahoo


def parse_chart(data, now=None):
    """Turns Yahoo's chart JSON into {date: close}, rounded to cents.

    Leaves out today's bar until the market has closed (during the day it holds
    the latest trade, not a close)."""
    chart = data.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise ValueError(err.get("description") or err.get("code") or str(err))
    results = chart.get("result") or []
    if not results:
        raise ValueError("no data returned")
    result = results[0]
    tz = ZoneInfo(result.get("meta", {}).get("exchangeTimezoneName") or "America/New_York")
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)

    stamps = result.get("timestamp") or []
    quotes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    closes = {}
    for ts, close in zip(stamps, quotes):
        if close is None:
            continue
        day = datetime.datetime.fromtimestamp(ts, tz).date()
        if day == now.date() and now.time() < CLOSE_IS_FINAL_AFTER:
            continue
        closes[day] = Decimal(str(close)).quantize(CENT)

    # Yahoo sometimes leaves the latest daily bar's close empty for stocks in the
    # hours after the close, while its summary ("meta") already holds the closing
    # price. Use that for its day when the bars have nothing for it.
    meta = result.get("meta", {})
    price, when = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if price is not None and when:
        day = datetime.datetime.fromtimestamp(when, tz).date()
        final = day < now.date() or now.time() >= CLOSE_IS_FINAL_AFTER
        if day not in closes and final:
            closes[day] = Decimal(str(price)).quantize(CENT)
    return closes


def parse_splits(data):
    """{date: ratio} for any stock splits in Yahoo's chart JSON (ratio = new
    shares per old share: a 4-for-1 split is 4, a 1-for-10 reverse split 0.1)."""
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    tz = ZoneInfo(result.get("meta", {}).get("exchangeTimezoneName") or "America/New_York")
    out = {}
    for s in ((result.get("events") or {}).get("splits") or {}).values():
        try:
            day = datetime.datetime.fromtimestamp(s["date"], tz).date()
            out[day] = Decimal(str(s["numerator"])) / Decimal(str(s["denominator"]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return out


SPLIT_COLUMNS = ["index_code", "split_date", "ratio", "handled"]


def read_splits():
    """[(code, date, ratio, handled)] from feeds/splits.csv."""
    import csv
    path = FEEDS_DIR / "splits.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["index_code"].strip(), datetime.date.fromisoformat(r["split_date"].strip()),
                 Decimal(r["ratio"].strip()), r.get("handled", "").strip().lower() == "yes")
                for r in csv.DictReader(fh) if r.get("index_code", "").strip()]


def record_splits(code, splits):
    """Adds splits the feed reported to feeds/splits.csv (once each)."""
    if not splits:
        return
    known = {(c, d) for c, d, _, _ in read_splits()}
    new = [(code, d, r) for d, r in sorted(splits.items()) if (code, d) not in known]
    if not new:
        return
    rows = [[c, d, r, "yes" if h else ""] for c, d, r, h in read_splits()]
    rows += [[c, d, r.normalize(), ""] for c, d, r in new]
    csvfiles.write_rows(FEEDS_DIR / "splits.csv", SPLIT_COLUMNS, rows)


def daily_closes(symbol, start=None, end=None, code=None):
    """{date: close} for the last month of trading days, or for start..end.
    Any stock split the feed reports is recorded in feeds/splits.csv (give
    `code`, the underlier's code here, for that)."""
    url = URL.format(symbol=urllib.parse.quote(symbol))
    if start:
        # Ask for a date range instead of the last month.
        to_epoch = lambda d: int(datetime.datetime.combine(d, datetime.time(), datetime.timezone.utc).timestamp())
        last = (end or datetime.date.today()) + datetime.timedelta(days=1)
        url = url.split("?")[0] + f"?period1={to_epoch(start)}&period2={to_epoch(last)}&interval=1d"
    if "events=" not in url:
        url += ("&" if "?" in url else "?") + "events=split"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (notes_desk)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        # Yahoo answers an unknown symbol with a 404 and an error message in the body.
        try:
            data = json.load(e)
        except Exception:
            raise ValueError(f"HTTP {e.code} from the price feed")
    except urllib.error.URLError as e:
        raise ValueError(f"couldn't reach the price feed ({e.reason})")
    closes = parse_chart(data)
    if code:
        record_splits(code, parse_splits(data))
    return closes


def fetch(codes, run_date=None):
    """Fetches every code's close.

    Returns (run_date, {code: close or None}, {code: problem}). With no run_date,
    uses the latest date that most underliers have a close for."""
    found, problems = {}, {}
    for code in codes:
        try:
            found[code] = daily_closes(yahoo_symbol(code), code=code)
        except ValueError as e:
            problems[code] = str(e)

    if not any(found.values()):
        raise DeskError("Couldn't get any closing prices:", [f"{c}: {p}" for c, p in problems.items()])
    if run_date is None:
        run_date = Counter(max(c) for c in found.values() if c).most_common(1)[0][0]

    if not any(run_date in c for c in found.values()):
        raise why_no_closes(run_date, found)

    closes = {}
    for code in codes:
        closes[code] = found.get(code, {}).get(run_date)
        if code in found and closes[code] is None:
            problems[code] = f"no close for {run_date} from the feed"
    return run_date, closes, problems


def write_price_file(conn, path_for, run_date=None, force=False, quiet=False):
    """Fetches closes for every underlier and writes feeds/closes_DATE.csv.

    path_for(date) gives the file path. Underliers the feed couldn't price are
    written with a blank close, so the file shows exactly what to fill in."""
    codes = [r[0] for r in db.rows(conn, "select index_code from indices order by index_code")]
    if not codes:
        raise DeskError("There are no underliers in the database yet. Run: python3 notes_desk.py setup")

    if run_date is not None and not force and path_for(run_date).exists():
        raise DeskError(f"{path_for(run_date)} already exists. Add --force to fetch and replace it.")

    run_date, closes, problems = fetch(codes, run_date)
    path = path_for(run_date)
    if path.exists() and not force:
        raise DeskError(f"{path} already exists. Add --force to fetch and replace it.")

    csvfiles.write_rows(path, csvfiles.PRICE_COLUMNS, [[c, run_date, closes[c]] for c in codes])
    if not quiet:
        from .textable import table
        print(table(["underlier", "yahoo_symbol", "close"],
                    [[c, yahoo_symbol(c), "" if closes[c] is None else f"{closes[c]:,}"] for c in codes]))
        print(f"Wrote {path} with closes for {run_date}.")
        if problems:
            print("Couldn't get these, so they're blank. Type them in before running nightly:")
            for c, p in problems.items():
                print(f"  {c}: {p}")
        else:
            print(f"Next: python3 notes_desk.py nightly {run_date}")
    return run_date, path, problems
