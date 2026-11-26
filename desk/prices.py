"""Checking and storing closing prices."""

from decimal import Decimal

from . import db

MAX_DAILY_MOVE = Decimal("0.20")   # a bigger one-day move is more likely a bad price


def _known_codes(conn):
    return {r[0] for r in db.rows(conn, "select index_code from indices")}


def history_problems(conn, prices):
    known = _known_codes(conn)
    out, seen = [], set()
    for p in prices:
        where = f"line {p['_line']} {p['index_code'] or ''}".rstrip()
        if not p["index_code"] or not p["price_date"]:
            out.append(f"{where}: index_code and price_date are required")
            continue
        if p["index_code"] not in known:
            out.append(f"{where}: {p['index_code']} isn't an underlier of any note")
        if p["close"] is None:
            out.append(f"{where}: price is blank for {p['price_date']}")
        elif p["close"] <= 0:
            out.append(f"{where}: close must be positive")
        key = (p["index_code"], p["price_date"])
        if key in seen:
            out.append(f"{where}: {p['price_date']} is listed more than once")
        seen.add(key)
    return out


def nightly_problems(conn, prices, run_date):
    out = history_problems(conn, prices)
    for p in prices:
        if p["price_date"] and p["price_date"] != run_date:
            out.append(f"line {p['_line']} {p['index_code']}: date {p['price_date']} isn't the run date {run_date}")

    codes = [p["index_code"] for p in prices]
    missing = sorted(needed_codes(conn, run_date) - set(codes))
    if missing:
        out.append(f"missing a close for: {', '.join(missing)} "
                   "(python3 notes_desk.py price-template lists every underlier)")
    return out


def needed_codes(conn, run_date):
    """Underliers that need a close on this date: those of notes in the book that
    night (priced by then, not closed before it). Before a note is priced, its
    underlier isn't needed, which matters when filling in history."""
    return {r[0] for r in db.rows(conn, """
        select distinct index_code from notes
        where trade_date <= %s and (closed_date is null or closed_date >= %s)""", (run_date, run_date))}


def big_moves(conn, prices, run_date):
    """Closes more than 20% from the previous close. Real moves that size do
    happen (earnings, a crash), so these are loaded and flagged, not refused."""
    previous = dict(db.rows(conn, """
        select distinct on (index_code) index_code, close
        from market_data where price_date < %s
        order by index_code, price_date desc""", (run_date,)))
    out = []
    for p in prices:
        prev = previous.get(p["index_code"])
        if prev and p["close"] and p["close"] > 0 and abs(p["close"] / prev - 1) > MAX_DAILY_MOVE:
            out.append(f"{p['index_code']}: {p['close']} is {p['close'] / prev - 1:+.1%} "
                       f"from the previous close {prev}")
    return out


def store(conn, prices):
    db.run_many(conn, """
        insert into market_data (index_code, price_date, close) values (%s, %s, %s)
        on conflict (index_code, price_date) do update set close = excluded.close""",
        [(p["index_code"], p["price_date"], p["close"]) for p in prices])
