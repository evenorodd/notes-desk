"""Command-line interface: python3 notes_desk.py <command> ..."""

import argparse
import datetime
import sys
from pathlib import Path

from . import DeskError, db, market_today


def _date(text):
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a date like 2026-11-20")


def main(argv=None):
    from .folder_owner import in_docker
    in_docker()   # inside the container: write files as the folder's owner, not root
    p = argparse.ArgumentParser(
        prog="notes_desk.py",
        description="Nightly monitoring of structured notes. Run a command with -h for its details.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    s = sub.add_parser("setup", help="build the database from real_notes/*.csv (prices then come from feeds/ via backfill --no-download)",
                       description="Drops and recreates the database, loads every note in real_notes/ "
                                   "and, if one is given (or my_history.csv exists), the prices in a history file. "
                                   "Then run backfill --no-download to load feeds/ and run every night.")
    s.add_argument("history", nargs="?", type=Path, help="optional extra price history CSV (index_code,price_date,close); normally not needed")

    s = sub.add_parser("price-template", help="write a blank price file for a date",
                       description="Writes feeds/closes_DATE.csv with a blank row for every underlier.")
    s.add_argument("date", type=_date)
    s.add_argument("--force", action="store_true", help="replace the file if it already exists")

    s = sub.add_parser("fetch-prices", help="download closing prices and write the price file",
                       description="Gets every underlier's close from Yahoo Finance and writes "
                                   "feeds/closes_DATE.csv. Any it can't get are left blank to fill in.")
    s.add_argument("date", nargs="?", type=_date,
                   help="the trading day (default: the latest day with closes)")
    s.add_argument("--force", action="store_true", help="replace the file if it already exists")

    s = sub.add_parser("backfill", help="download history and replay every night since a date",
                       description="Downloads every underlier's daily closes from START to END (one price file "
                                   "per trading day in feeds/), runs the nightly job for each day in order so "
                                   "every coupon, call and maturity check is applied, then checks each note's "
                                   "starting level against the close on its trade date.")
    s.add_argument("start", nargs="?", type=_date, help="first date, e.g. 2026-01-01 (needed unless --no-download)")
    s.add_argument("--end", type=_date, help="last date (default: today in New York)")
    s.add_argument("--no-download", action="store_true",
                   help="don't download: replay the price files already in feeds/")
    s.add_argument("--force", action="store_true", help="replace price files that already exist")

    s = sub.add_parser("nightly", help="load a day's prices, run the job, write the reports",
                       description="Loads feeds/closes_DATE.csv, runs the nightly job and writes "
                                   "reports/morning_DATE.txt and reports/notes_DATE.csv.")
    s.add_argument("date", nargs="?", type=_date, default=market_today(),
                   help="the run date (default: today in New York)")
    s.add_argument("--fetch", action="store_true",
                   help="download the closes first if feeds/closes_DATE.csv doesn't exist yet")

    sub.add_parser("catch-up", help="fetch and run every trading night since the last successful run (for schedules)",
                   description="Downloads the closes since the last night that ran successfully and runs each "
                               "trading night in order. Weekends and holidays have no closes, so they're skipped, "
                               "and a night missed because the computer was off is run the next time.")

    s = sub.add_parser("add-notes", help="add new notes from a CSV, building their check dates",
                       description="Adds notes to the running database (keeping every price loaded) "
                                   "and to real_notes/*.csv. Start from real_notes/new_notes_template.csv.")
    s.add_argument("file", type=Path, help="new notes CSV (notes.csv columns plus the 4 schedule columns)")
    s.add_argument("extra_dates", nargs="?", type=Path,
                   help="optional CSV of extra check dates (cusip,obs_date,kind) for irregular schedules")
    s.add_argument("--yes", action="store_true", help="don't ask before adding")

    sub.add_parser("what-if", help="payouts and flags for example notes at made-up prices (changes nothing)")
    sub.add_parser("test", help="run the unit and database tests (changes nothing)")

    s = sub.add_parser("serve", help="open the notes desk in your web browser",
                       description="Starts a small website at http://localhost:8000 showing the dashboard, "
                                   "every note, and the run history. Only this computer can open it.")
    s.add_argument("--port", type=int, default=8000, help="port to use (default: 8000)")
    s.add_argument("--host", default="127.0.0.1",
                   help="address to listen on (default: 127.0.0.1, this computer only; Docker uses 0.0.0.0)")

    s = sub.add_parser("mcp", help="serve tools to AI assistants over MCP (stdin/stdout)",
                       description="Runs an MCP server on standard input and output, for Claude Desktop, "
                                   "Claude Code and other MCP clients. Your MCP client starts this; see README.md.")
    s.add_argument("--allow-runs", action="store_true",
                   help="also offer run_nightly, which fetches prices and runs the nightly job (writes)")

    s = sub.add_parser("export", help="save the website as plain HTML files to share",
                       description="Writes the dashboard, the notes table and every note's page for one night "
                                   "to export/, as files that open in any browser with no server or database.")
    s.add_argument("date", nargs="?", type=_date, help="the night (default: the latest)")

    args = p.parse_args(argv)
    try:
        if args.command == "setup":
            from .notes import setup
            setup(args.history)
        elif args.command == "price-template":
            from .nightly import price_template
            price_template(args.date, args.force)
        elif args.command == "fetch-prices":
            from .fetch import write_price_file
            from .nightly import price_file
            conn = db.connect()
            try:
                write_price_file(conn, price_file, args.date, args.force)
            finally:
                conn.close()
        elif args.command == "backfill":
            from .backfill import backfill
            backfill(args.start, args.end, download=not args.no_download, force=args.force)
        elif args.command == "nightly":
            from .nightly import run
            run(args.date, args.fetch)
        elif args.command == "catch-up":
            from .nightly import catch_up
            catch_up()
        elif args.command == "add-notes":
            from .notes import add_notes
            add_notes(args.file, args.extra_dates, args.yes)
        elif args.command == "what-if":
            from .whatif import what_if
            what_if()
        elif args.command == "test":
            from .whatif import run_tests
            run_tests()
        elif args.command == "serve":
            from .web import serve
            serve(args.port, args.host)
        elif args.command == "mcp":
            from .mcp_server import main as mcp_main
            mcp_main(args.allow_runs)
        elif args.command == "export":
            from .web import export
            export(args.date)
    except DeskError as e:
        print(e, file=sys.stderr)
        for problem in e.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except Exception as e:
        if db._driver is not None and db.is_db_error(e):
            print(f"Database error: {db.error_message(e)}", file=sys.stderr)
            return 1
        raise
    return 0
