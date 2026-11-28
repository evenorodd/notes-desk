"""Inputs for pricing that come from outside: Treasury yields and a year of
price history before the book starts (for volatility on early trade dates).

    python3 notes_desk.py fetch-rates 2025 2026      feeds/rates_2025.csv, feeds/rates_2026.csv
    python3 notes_desk.py fetch-history 2025          feeds/history_2025.csv

Both are plain CSV files in feeds/, like the nightly price files, so they're
the record of what was used and can be fixed by hand.

Rates come from the US Treasury's daily par yield curve (one download per
year); if that fails, from the same series on FRED (St. Louis Fed). Either
address can be replaced with NOTES_DESK_RATES_URL / NOTES_DESK_RATES_URL_FRED.
"""

import csv
import datetime
import io
import os
import re
import urllib.error
import urllib.request

from . import FEEDS_DIR, DeskError, csvfiles, db

TREASURY_URL = os.environ.get(
    "NOTES_DESK_RATES_URL",
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
    "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
FRED_SERIES = {"DGS1MO": 1 / 12, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1, "DGS2": 2, "DGS3": 3,
               "DGS5": 5, "DGS7": 7, "DGS10": 10, "DGS20": 20, "DGS30": 30}
FRED_URL = os.environ.get(
    "NOTES_DESK_RATES_URL_FRED",
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + ",".join(FRED_SERIES)
    + "&cosd={year}-01-01&coed={year}-12-31")
RATE_COLUMNS = ["rate_date", "tenor_years", "yield"]


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (notes_desk)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP {e.code}")
    except urllib.error.URLError as e:
        raise ValueError(f"couldn't connect ({e.reason})")


def _date(text):
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognized date {text!r}")


def _tenor(heading):
    """'3 Mo' / '1.5 Month' / '10 Yr' -> years, or None for other columns."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mo|mos|month|months|yr|yrs|year|years)\s*", heading.lower())
    if not m:
        return None
    n = float(m.group(1))
    return round(n / 12, 4) if m.group(2).startswith("mo") else n


def parse_treasury(text):
    """[(date, tenor_years, yield as a decimal)] from the Treasury's CSV."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not rows[0] or rows[0][0].strip().lower() != "date":
        raise ValueError("not the Treasury yield-curve format (no Date column)")
    tenors = [_tenor(h) for h in rows[0]]
    if not any(tenors):
        raise ValueError("no maturity columns found")
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        d = _date(r[0])
        for t, v in zip(tenors, r):
            if t is not None and v.strip() not in ("", "N/A"):
                out.append((d, t, round(float(v) / 100, 6)))
    return out


def parse_fred(text):
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][0].strip().lower() not in ("date", "observation_date"):
        raise ValueError("not the FRED CSV format")
    tenors = [FRED_SERIES.get(h.strip()) for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        d = _date(r[0])
        for t, v in zip(tenors, r):
            if t is not None and v.strip() not in ("", "."):
                out.append((d, round(t, 4), round(float(v) / 100, 6)))
    return out


def rates_for_year(year):
    problems = []
    for name, url, parse in (("Treasury", TREASURY_URL, parse_treasury), ("FRED", FRED_URL, parse_fred)):
        try:
            rows = parse(_get(url.format(year=year)))
            if rows:
                return rows, name
            problems.append(f"{name}: no rates in the download")
        except ValueError as e:
            problems.append(f"{name}: {e}")
    raise DeskError(f"Couldn't download Treasury rates for {year}:", problems)


def fetch_rates(years, quiet=False):
    """Downloads each year's rates into feeds/rates_YEAR.csv (replacing it:
    the Treasury revises nothing, but the current year grows every day)."""
    for year in years:
        rows, source = rates_for_year(year)
        rows.sort()
        path = FEEDS_DIR / f"rates_{year}.csv"
        csvfiles.write_rows(path, RATE_COLUMNS, [[d, t, y] for d, t, y in rows])
        if not quiet:
            days = len({d for d, _, _ in rows})
            print(f"{path.name}: {days} days of Treasury rates (from {source}, latest {rows[-1][0]})")


def read_rate_file(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [(_date(r["rate_date"]), float(r["tenor_years"]), float(r["yield"])) for r in csv.DictReader(fh)]


def fetch_history(year):
    """A year of daily closes for every underlier, before the nightly price
    files begin, written to feeds/history_YEAR.csv. Volatility on a trade date
    early in the book needs a year of closes before it."""
    from .fetch import daily_closes, yahoo_symbol
    conn = db.connect()
    try:
        codes = [r[0] for r in db.rows(conn, "select index_code from indices order by 1")]
    finally:
        conn.close()
    start, end = datetime.date(year, 1, 1), datetime.date(year, 12, 31)
    rows, failed = [], []
    for code in codes:
        try:
            got = daily_closes(yahoo_symbol(code), start, end)
            rows += [[code, d, c] for d, c in sorted(got.items()) if start <= d <= end]
            print(f"  {code:<5} {sum(1 for r in rows if r[0] == code):>4} closes", flush=True)
        except ValueError as e:
            failed.append(f"{code}: {e}")
            print(f"  {code:<5} failed: {e}", flush=True)
    if not rows:
        raise DeskError(f"Couldn't download any {year} closes:", failed)
    path = FEEDS_DIR / f"history_{year}.csv"
    csvfiles.write_rows(path, csvfiles.PRICE_COLUMNS, rows)
    print(f"{path.name}: {len(rows)} closes for {len(codes) - len(failed)} underliers.")
    return failed
