"""One night's run: load the day's prices, run the job, write the reports.

For a schedule, use catch-up (below) rather than nightly: it runs every
trading night since the last successful run (README.md, "Scheduling").

Every run is recorded in the job_runs table. If anything is wrong (no price
file, a blank or implausible price, a database error), the run stops, is
marked failed, and an alert is raised. Set NOTES_DESK_SLACK_WEBHOOK to a Slack
incoming-webhook URL to have alerts posted there as well as printed.
"""

import datetime
import fcntl
import json
import os
import tempfile
import urllib.request
from pathlib import Path

from . import SQL_DIR, REPORTS_DIR, FEEDS_DIR, DeskError, csvfiles, db, prices, report
from .textable import table

LOCK_FILE = Path(tempfile.gettempdir()) / "notes_desk_nightly.lock"


def log(run_date, message):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [{run_date}] {message}", flush=True)


def alert(run_date, message):
    log(run_date, f"ALERT: {message}")
    url = os.environ.get("NOTES_DESK_SLACK_WEBHOOK")
    if url:
        try:
            body = json.dumps({"text": f"Notes desk nightly run {run_date}: {message}"}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log(run_date, f"(couldn't post the alert to Slack: {e})")


def price_file(run_date):
    return FEEDS_DIR / f"closes_{run_date}.csv"


def run(run_date, fetch=False, replay=True, reports=True, alerts=True, price=True, greeks=True):
    """One night. The options are for backfill, which replays many nights in
    date order: no replaying of later nights (it runs them itself), reports for
    the last night only, and no alert per night (it reports at the end)."""
    def notify(d, message):
        if alerts:
            alert(d, message)
        else:
            log(d, "not alerted: " + message.splitlines()[0])

    # Only one run at a time, even if cron fires while a manual rerun is going.
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        notify(run_date, "another run is still in progress")
        raise DeskError("Another nightly run is still in progress.")

    conn = db.connect()
    try:
        # The nightly logic lives in the database (sql/03_nightly_job.sql). Load
        # the current version on every run, so updating the code is enough: no
        # setup or manual reload. The file only defines functions, so this
        # changes no data.
        db.run_file(conn, SQL_DIR / "03_nightly_job.sql")
        run_id = db.scalar(conn, "insert into job_runs (job, run_date) values ('nightly', %s) returning run_id",
                           (run_date,))
        conn.commit()
        log(run_date, f"starting run {run_id}")

        def fail(message, problems=()):
            conn.rollback()
            detail = message + ("\n  " + "\n  ".join(problems) if problems else "")
            notify(run_date, detail)
            db.run(conn, "update job_runs set status = 'failed', finished_at = now(), message = %s "
                         "where run_id = %s", (detail, run_id))
            conn.commit()
            raise DeskError("Nightly run failed.")

        path = price_file(run_date)
        if fetch and not path.exists():
            # A file already there (say, one you corrected by hand) is used as it is.
            from .fetch import write_price_file
            log(run_date, "fetching closing prices")
            try:
                _, _, missing = write_price_file(conn, price_file, run_date, quiet=True)
            except DeskError as e:
                fail(f"couldn't fetch closing prices: {e}", e.problems)
            if missing:
                fail(f"the price feed had no close for some underliers. Type them into {path.name} "
                     f"and run: python3 notes_desk.py nightly {run_date}",
                     [f"{c}: {p}" for c, p in missing.items()])
        if not path.exists():
            fail(f"price file not found: {path}. Fetch one with: python3 notes_desk.py fetch-prices {run_date} "
                 f"(or make a blank one with: python3 notes_desk.py price-template {run_date})")

        log(run_date, f"loading {path.name}")
        try:
            rows = csvfiles.read_prices(path)
        except DeskError as e:
            fail(str(e), e.problems)
        problems = prices.nightly_problems(conn, rows, run_date)
        if problems:
            fail(f"{path.name} wasn't loaded:", problems)
        moves = prices.big_moves(conn, rows, run_date)
        prices.store(conn, rows)
        db.run(conn, "insert into feed_loads (run_date, filename, row_count) values (%s, %s, %s)",
               (run_date, str(path), len(rows)))

        log(run_date, "running nightly job")
        try:
            headers, steps = db.query(conn, "select * from run_nightly(%s)", (run_date,))
            later = []
            if replay:
                later = [r[0] for r in db.rows(conn, """select distinct run_date from note_daily_status
                                                         where run_date > %s order by 1""", (run_date,))]
                db.scalar(conn, "select replay_later_nights(%s)", (run_date,))
        except Exception as e:
            if not db.is_db_error(e):
                raise
            fail(f"nightly job failed: {db.error_message(e)}")
        conn.commit()
        print(table(headers, steps))

        if price:
            price_nights(run_date, [run_date] + later, refresh_rates=fetch, greeks=greeks)

        morning = notes = None
        if reports:
            morning, notes = report.write_reports(conn, run_date)
        if later and reports:
            # An earlier night was rerun: the later nights were replayed on top
            # of it, so their existing reports are rewritten to match (nights
            # without report files, such as backfilled ones, don't get new ones).
            for d in later:
                if (REPORTS_DIR / f"morning_{d}.txt").exists():
                    report.write_reports(conn, d)
            log(run_date, f"replayed {len(later)} later night(s) so calls and maturities stay right: "
                          + ", ".join(str(d) for d in later))
        warning = None
        if moves:
            warning = ("loaded, but these moved more than 20% in a day. Check them against another "
                       f"source; if one is wrong, fix {path.name} and rerun this date:\n  " + "\n  ".join(moves))
            notify(run_date, warning)
        db.run(conn, "update job_runs set status = 'ok', finished_at = now(), message = %s where run_id = %s",
               (warning, run_id))
        conn.commit()
        log(run_date, f"done. Report: {morning}  Every note: {notes.name}" if reports else "done.")
    finally:
        conn.close()
        lock.close()


def price_nights(run_date, nights, refresh_rates=False, greeks=True):
    """Model values for the night (and any replayed later nights). Pricing
    never fails a night: a problem is logged and the rest goes on."""
    from . import valuation
    if refresh_rates:
        refresh_treasury_rates(run_date)
    try:
        summary, problems = valuation.price(nights, greeks=greeks)
    except Exception as e:            # noqa: BLE001 - a pricing bug mustn't stop the reports
        log(run_date, f"pricing failed, so no model values tonight: {e}")
        return
    if problems:
        summary += f"; {len(problems)} not priced: " + "; ".join(problems[:3]) + (" ..." if len(problems) > 3 else "")
    log(run_date, summary)


def refresh_treasury_rates(run_date):
    from .rates import fetch_rates
    try:
        fetch_rates([run_date.year], quiet=True)
    except DeskError as e:
        log(run_date, f"couldn't update Treasury rates ({e}); pricing uses the latest ones on file")


def price_template(run_date, force=False):
    path = price_file(run_date)
    if path.exists() and not force:
        raise DeskError(f"{path} already exists. Add --force to replace it with a blank template.")
    conn = db.connect()
    try:
        codes = [r[0] for r in db.rows(conn, "select index_code from indices order by index_code")]
    finally:
        conn.close()
    csvfiles.write_rows(path, csvfiles.PRICE_COLUMNS, [[c, run_date, None] for c in codes])
    print(f"Wrote {path} with {len(codes)} underliers. Fill in each close, "
          f"then run: python3 notes_desk.py nightly {run_date}")


def catch_up():
    """Every trading night since the last successful run, in order: what a
    schedule should run. The download only has closes for days the market was
    open (and for today only once it has closed), so weekends, holidays and an
    early start need no special handling; a night missed while the computer was
    off is simply run next time."""
    from . import market_today
    from .backfill import _download, _nights
    conn = db.connect()
    try:
        last = db.scalar(conn, "select max(run_date) from job_runs where status = 'ok'")
        codes = [r[0] for r in db.rows(conn, "select index_code from indices order by 1")]
    finally:
        conn.close()
    today = market_today()
    if last is None:
        raise DeskError("No night has run successfully yet. Start the history with: "
                        "python3 notes_desk.py backfill 2026-01-01")
    start = last + datetime.timedelta(days=1)
    if start > today:
        log(today, f"nothing to do: {last} has already run")
        return []

    log(today, f"catching up: closes from {start} to {today}")
    refresh_treasury_rates(today)
    incomplete = {}
    try:
        _, incomplete = _download(codes, start, today, force=False)
    except DeskError as e:
        # "No data" for every underlier just means no trading days in the period.
        if not (e.problems and all("no data" in p.lower() for p in e.problems)):
            alert(today, f"catch-up couldn't download closes: {e}" + "".join(f"\n  {p}" for p in e.problems))
            raise
    nights = _nights(start, today)
    if incomplete:
        # Stop before the first day missing a close for an underlier in the
        # book; the next catch-up downloads it again.
        first = min(incomplete)
        nights = [d for d in nights if d < first]
        alert(first, f"no close for {', '.join(incomplete[first])} on {first}; that night and later ones "
                     "will run once the feed has it (catch-up tries again next time)")
    if not nights and incomplete:
        return []
    if not nights:
        log(today, f"no new closes since {last} (a weekend or holiday, or today's close isn't final "
                   "until 4:20 pm New York time)")
        return []
    for d in nights:
        run(d)   # stops at the first night that fails; the next catch-up retries it
    return nights
