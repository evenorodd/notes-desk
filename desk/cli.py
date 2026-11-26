"""Command-line interface: python3 notes_desk.py <command> ..."""

import argparse
import datetime
import sys
from pathlib import Path

from . import DeskError, db


def _date(text):
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a date like 2026-11-20")


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="notes_desk.py",
        description="Nightly monitoring of structured notes. Run a command with -h for its details.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    s = sub.add_parser("setup", help="build the database from real_notes/*.csv and your price history",
                       description="Drops and recreates the database, loads every note in real_notes/ "
                                   "and the prices in the history file.")
    s.add_argument("history", nargs="?", type=Path, help="price history CSV (default: my_history.csv)")

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

    s = sub.add_parser("nightly", help="load a day's prices, run the job, write the reports",
                       description="Loads feeds/closes_DATE.csv, runs the nightly job and writes "
                                   "reports/morning_DATE.txt and reports/notes_DATE.csv.")
    s.add_argument("date", nargs="?", type=_date, default=datetime.date.today(),
                   help="the run date (default: today)")
    s.add_argument("--fetch", action="store_true",
                   help="download the closes first if feeds/closes_DATE.csv doesn't exist yet")

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
        elif args.command == "nightly":
            from .nightly import run
            run(args.date, args.fetch)
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
