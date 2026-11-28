"""Fill in history: python3 notes_desk.py backfill 2026-01-01

1. Downloads every underlier's daily closes for the period (one request each)
   and writes one price file per trading day in feeds/. Existing price files
   are kept as they are (they're the record of what was loaded) unless --force.
2. Runs the nightly job for every price file in the period, oldest first, so
   each coupon, call and maturity check is applied on its own date. Only the
   last night gets report files; each night still gets its run-log entry.
3. Checks every note's starting level against the downloaded close on its
   trade date, which catches typos in the note terms and stock splits.

With --no-download it skips step 1 and replays the price files already in
feeds/ (Docker's first start uses this).
"""

import contextlib
import datetime
import io

from . import FEEDS_DIR, DeskError, csvfiles, db, market_today
from .textable import table


def _download(codes, start, end, force):
    from .fetch import daily_closes, yahoo_symbol
    closes, failed = {}, {}
    for code in codes:
        try:
            got = daily_closes(yahoo_symbol(code), start, end)
            closes[code] = {d: c for d, c in got.items() if start <= d <= end}
            print(f"  {code:<5} {len(closes[code]):>4} closes", flush=True)
        except ValueError as e:
            failed[code] = str(e)
            print(f"  {code:<5} failed: {e}", flush=True)
    if not closes:
        raise DeskError("Couldn't download any prices:", [f"{c}: {p}" for c, p in failed.items()])

    days = sorted({d for by_day in closes.values() for d in by_day})
    written = kept = 0
    for d in days:
        path = FEEDS_DIR / f"closes_{d}.csv"
        if path.exists() and not force:
            kept += 1
            continue
        rows = [[c, d, closes[c][d]] for c in sorted(closes) if d in closes[c]]
        csvfiles.write_rows(path, csvfiles.PRICE_COLUMNS, rows)
        written += 1
    print(f"{len(days)} trading days: wrote {written} price files, kept {kept} that were already there.")
    return failed


def _nights(start, end):
    nights = []
    for f in FEEDS_DIR.glob("closes_*.csv"):
        try:
            d = datetime.date.fromisoformat(f.stem[len("closes_"):])
        except ValueError:
            continue
        if (start is None or d >= start) and d <= end:
            nights.append(d)
    return sorted(nights)


def _replay(nights):
    from .nightly import run
    failed = []
    month = None
    for i, d in enumerate(nights):
        last = i == len(nights) - 1
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                # Running in date order already decides every later night, so no
                # replaying; the last night also replays anything stored after it.
                # Sensitivities (delta, vega) only for the last night: the rest
                # get model values, which is what the history charts need.
                run(d, fetch=False, replay=last, reports=last, alerts=False, greeks=last)
        except DeskError:
            failed.append(d)
        if d.strftime("%Y-%m") != month:
            month = d.strftime("%Y-%m")
            print(f"  {month} ...", flush=True)
    print(f"Ran {len(nights) - len(failed)} of {len(nights)} nights.")
    if not failed:
        return []
    conn = db.connect()
    try:   # the reason each failed night was refused, from its run-log entry
        return [(d, (db.scalar(conn, """select message from job_runs where run_date = %s
                                         order by run_id desc limit 1""", (d,)) or "no reason recorded")
                 .replace("\n", " ")) for d in failed]
    finally:
        conn.close()


def check_start_levels(conn):
    """Every note's starting level against the close on its trade date. Some
    filings set the starting level on an earlier "strike date" (a few days
    before pricing), so a match with a close in the 10 days before counts too."""
    rows = db.rows(conn, """
        select n.cusip, n.index_code, n.trade_date, n.initial_level, m.close,
               (select max(e.price_date) from market_data e
                where e.index_code = n.index_code
                  and e.price_date between n.trade_date - 10 and n.trade_date - 1
                  and abs(e.close / n.initial_level - 1) <= 0.0005)
        from notes n
        left join market_data m on m.index_code = n.index_code and m.price_date = n.trade_date
        order by n.trade_date, n.cusip""")
    matched, strike, off, no_close = 0, [], [], []
    for cusip, code, td, initial, close, earlier in rows:
        if close is None:
            no_close.append([cusip, code, td])
        elif abs(close / initial - 1) <= 0.0005:   # within 0.05%: rounding
            matched += 1
        elif earlier:
            strike.append([cusip, code, td, f"{initial:,}", earlier])
        else:
            off.append([cusip, code, td, f"{initial:,}", f"{close:,}", f"{float(close / initial - 1):+.2%}"])
    print(f"\nStarting levels: {matched + len(strike)} of {len(rows)} match a real close"
          f" ({matched} on the trade date, {len(strike)} on an earlier strike date).")
    if strike:
        print("Set on a strike date before pricing (normal; worth confirming in the filing):")
        print(table(["cusip", "underlier", "trade_date", "start_level", "matches_close_on"], strike))
    if off:
        print("These match no close on or shortly before the trade date. Check each against its filing: "
              "a typo in notes.csv, or a stock split (the downloaded history is split-adjusted):")
        print(table(["cusip", "underlier", "trade_date", "start_level", "close_that_day", "difference"], off))
    if no_close:
        print("No downloaded close on the trade date for: "
              + ", ".join(f"{c} ({u}, {d})" for c, u, d in no_close))
    return off, no_close, strike


def backfill(start=None, end=None, download=True, force=False):
    end = end or market_today()
    if download and start is None:
        raise DeskError("Give the first date to fill in, e.g.: python3 notes_desk.py backfill 2026-01-01")
    if start and start > end:
        raise DeskError(f"The start date {start} is after the end date {end}.")

    conn = db.connect()
    try:
        if not db.scalar(conn, "select to_regclass('notes') is not null"):
            raise DeskError("The database isn't set up yet. Run: python3 notes_desk.py setup")
        codes = [r[0] for r in db.rows(conn, "select index_code from indices order by 1")]
    finally:
        conn.close()

    failed_downloads = {}
    if download:
        print(f"Downloading daily closes for {len(codes)} underliers, {start} to {end} ...", flush=True)
        failed_downloads = _download(codes, start, end, force)

    nights = _nights(start, end)
    if not nights:
        raise DeskError(f"No price files in feeds/ between {start or 'the start'} and {end}.")
    print(f"Running {len(nights)} nights in order, {nights[0]} to {nights[-1]} ...", flush=True)
    failed_nights = _replay(nights)

    conn = db.connect()
    try:
        warnings = db.rows(conn, """
            select distinct on (run_date) run_date, message from job_runs
            where run_date between %s and %s and status = 'ok' and message is not null
            order by run_date, run_id desc""", (nights[0], nights[-1]))
        closed = db.rows(conn, """
            select status, closed_date, cusip, index_code from notes
            where status <> 'live' order by closed_date, cusip""")
        live = db.scalar(conn, "select count(*) from notes where status = 'live'")
        check_start_levels(conn)
    finally:
        conn.close()

    if closed:
        print(f"\n{len(closed)} notes closed during the period ({live} still live):")
        print(table(["status", "on", "cusip", "underlier"], [list(r) for r in closed]))
    else:
        print(f"\nNo notes were called or matured during the period ({live} live).")
    if warnings:
        print(f"\n{len(warnings)} nights had prices that moved more than 20% in a day (worth a look):")
        for d, msg in warnings:
            print(f"  {d}: " + "; ".join(l.strip() for l in msg.splitlines()[1:]))
    if failed_downloads:
        print("\nCouldn't download: " + "; ".join(f"{c} ({p})" for c, p in failed_downloads.items()))
    if failed_nights:
        print(f"\n{len(failed_nights)} nights failed; fix their price files and run backfill again:")
        for d, why in failed_nights:
            print(f"  {d}: {why}")
    print(f"\nReports for {nights[-1]} are in reports/. The website and the AI tools show the full history.")
