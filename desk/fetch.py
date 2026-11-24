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

from . import DeskError, csvfiles, db

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
CENT = Decimal("0.01")


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


def daily_closes(symbol):
    """{date: close} for the last month of trading days."""
    url = URL.format(symbol=urllib.parse.quote(symbol))
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
    return parse_chart(data)


def fetch(codes, run_date=None):
    """Fetches every code's close.

    Returns (run_date, {code: close or None}, {code: problem}). With no run_date,
    uses the latest date that most underliers have a close for."""
    found, problems = {}, {}
    for code in codes:
        try:
            found[code] = daily_closes(yahoo_symbol(code))
        except ValueError as e:
            problems[code] = str(e)

    if not any(found.values()):
        raise DeskError("Couldn't get any closing prices:", [f"{c}: {p}" for c, p in problems.items()])
    if run_date is None:
        run_date = Counter(max(c) for c in found.values() if c).most_common(1)[0][0]

    if not any(run_date in c for c in found.values()):
        raise DeskError(f"No closes for {run_date}. The market may have been closed that day "
                        "(weekend or holiday), or today's close isn't final until 4:20 pm New York time.")

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
