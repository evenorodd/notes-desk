"""One night's run: load the day's prices, run the job, write the reports.

Scheduled with cron at 6:15 pm Central on weekdays, after the US close:
    CRON_TZ=America/Chicago
    15 18 * * 1-5  cd /opt/notes_desk && python3 notes_desk.py nightly --fetch >> /var/log/notes_desk/nightly.log 2>&1

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

from . import FEEDS_DIR, DeskError, csvfiles, db, prices, report
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


def run(run_date, fetch=False):
    # Only one run at a time, even if cron fires while a manual rerun is going.
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        alert(run_date, "another run is still in progress")
        raise DeskError("Another nightly run is still in progress.")

    conn = db.connect()
    try:
        run_id = db.scalar(conn, "insert into job_runs (job, run_date) values ('nightly', %s) returning run_id",
                           (run_date,))
        conn.commit()
        log(run_date, f"starting run {run_id}")

        def fail(message, problems=()):
            conn.rollback()
            detail = message + ("\n  " + "\n  ".join(problems) if problems else "")
            alert(run_date, detail)
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
            later = [r[0] for r in db.rows(conn, """select distinct run_date from note_daily_status
                                                     where run_date > %s order by 1""", (run_date,))]
            db.scalar(conn, "select replay_later_nights(%s)", (run_date,))
        except Exception as e:
            if not db.is_db_error(e):
                raise
            fail(f"nightly job failed: {db.error_message(e)}")
        conn.commit()
        print(table(headers, steps))

        morning, notes = report.write_reports(conn, run_date)
        if later:
            # An earlier night was rerun: the later nights were replayed on top
            # of it, so their reports are rewritten too.
            for d in later:
                report.write_reports(conn, d)
            log(run_date, f"replayed {len(later)} later night(s) so calls and maturities stay right: "
                          + ", ".join(str(d) for d in later))
        warning = None
        if moves:
            warning = ("loaded, but these moved more than 20% in a day. Check them against another "
                       f"source; if one is wrong, fix {path.name} and rerun this date:\n  " + "\n  ".join(moves))
            alert(run_date, warning)
        db.run(conn, "update job_runs set status = 'ok', finished_at = now(), message = %s where run_id = %s",
               (warning, run_id))
        conn.commit()
        log(run_date, f"done. Report: {morning}  Every note: {notes.name}")
    finally:
        conn.close()
        lock.close()


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
