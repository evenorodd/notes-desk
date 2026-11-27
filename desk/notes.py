"""Setting up the database and adding notes.

Both read CSV files in Python, put the rows in the staging tables, and let
the SQL functions in sql/04_load_real_notes.sql check and load them, so a
note is checked by exactly the same rules either way.
"""

import re

from . import (DeskError, HISTORY_CSV, NOTES_CSV, OBS_CSV, SQL_DIR, csvfiles, db, prices,
               schedule)

SCHEMA_FILES = ["01_schema.sql", "02_reference_data.sql", "03_nightly_job.sql", "04_load_real_notes.sql"]


def _stage(conn, notes, observations):
    db.run_many(conn, f"insert into stage_notes ({', '.join(csvfiles.NOTE_COLUMNS)}) "
                      f"values ({', '.join(['%s'] * len(csvfiles.NOTE_COLUMNS))})",
                [[n[c] for c in csvfiles.NOTE_COLUMNS] for n in notes])
    db.run_many(conn, "insert into stage_obs (cusip, obs_date, kind) values (%s, %s, %s)",
                [(o["cusip"], o["obs_date"], o["kind"]) for o in observations])


def _load(conn):
    """Calls load_real_notes(); turns its problem list into a DeskError."""
    try:
        return db.scalar(conn, "select load_real_notes()")
    except Exception as e:
        if not db.is_db_error(e):
            raise
        conn.rollback()
        lines = db.error_message(e).split("\n")
        raise DeskError(lines[0], [l.strip() for l in lines[1:] if l.strip()])


def setup(history_path=None):
    # Prices come from the price files in feeds/ (backfill --no-download replays
    # them). An extra history file is optional: my_history.csv if it's there,
    # or one named on the command line, which then must exist.
    if history_path is not None and not history_path.exists():
        raise DeskError(f"Can't find the price history file {history_path}.")
    history_path = history_path or (HISTORY_CSV if HISTORY_CSV.exists() else None)
    # Read every file first, so a typo is caught before the database is dropped.
    notes = csvfiles.read_notes(NOTES_CSV)
    observations = csvfiles.read_observations(OBS_CSV)
    history = csvfiles.read_prices(history_path) if history_path else []

    name = db.database_name()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise DeskError(f"Database name '{name}' should be letters, digits and underscores only.")

    print(f"Creating database {name} ...")
    admin = db.connect("postgres", autocommit=True)
    try:
        db.run(admin, f"drop database if exists {name}")
        db.run(admin, f"create database {name}")
    except Exception as e:
        if db.is_db_error(e) and "being accessed by other users" in db.error_message(e):
            raise DeskError(f"Database {name} is open somewhere else, probably pgAdmin. "
                            "Disconnect there (right-click the server, Disconnect) and run setup again.")
        raise
    finally:
        admin.close()

    conn = db.connect()
    try:
        for f in SCHEMA_FILES:
            print(f"Loading {f} ...")
            db.run_file(conn, SQL_DIR / f)

        print(f"Reading {len(notes)} notes and {len(observations)} observation dates ...")
        _stage(conn, notes, observations)
        db.scalar(conn, "select real_notes_underliers()")   # so prices for every underlier can load

        if history_path:
            print(f"Loading price history from {history_path.name} ...")
            problems = prices.history_problems(conn, history)
            if problems:
                raise DeskError(f"{history_path.name} has problems. Fix these and run setup again:", problems)
            prices.store(conn, history)

        print(_load(conn))
        conn.commit()
        counts = db.rows(conn, "select note_type, count(*) from notes group by 1 order by 1")
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("Notes by type: " + ", ".join(f"{t} {n}" for t, n in counts))
    print("Setup complete. The database has the notes but no prices or results yet.")
    print("Next: python3 notes_desk.py backfill --no-download   (runs every night in feeds/, oldest first)")


def add_notes(path, extra_dates_path=None, assume_yes=False):
    new = csvfiles.read_notes(path, with_schedule=True)
    extra = csvfiles.read_observations(extra_dates_path) if extra_dates_path else []

    problems = [p for n in new for p in schedule.schedule_problems(n)]
    if problems:
        raise DeskError(f"No notes added. Fix these in {path.name} and run it again:", problems)

    observations = list(extra)
    for n in new:
        observations += [{"cusip": n["cusip"], "obs_date": d, "kind": k} for d, k in schedule.build_dates(n)]

    conn = db.connect()
    try:
        if not db.scalar(conn, "select to_regclass('notes') is not null"):
            raise DeskError("The database isn't set up yet. Run: python3 notes_desk.py setup")
        db.run_file(conn, SQL_DIR / "04_load_real_notes.sql")   # fresh staging tables
        _stage(conn, new, observations)

        problems = [r[0] for r in db.rows(conn, "select unnest(check_real_notes())")]
        if problems:
            raise DeskError(f"No notes added. Fix these in {path.name} and run it again:", problems)

        from .textable import table
        headers, preview = db.query(conn, """
            select s.cusip, s.underlier, s.note_type as type, s.trade_date,
                   s.initial_level,
                   count(*) filter (where o.kind = 'coupon') as coupon_checks,
                   count(*) filter (where o.kind = 'call') as call_checks,
                   min(o.obs_date) as first_check,
                   min(o.obs_date) filter (where o.kind = 'maturity') as final_valuation,
                   case when exists (select 1 from indices i where i.index_code = s.underlier)
                        then '' else 'yes' end as new_underlier
            from stage_notes s left join stage_obs o using (cusip)
            group by s.cusip, s.underlier, s.note_type, s.trade_date, s.initial_level
            order by s.cusip""")
        print("\nNotes to add:")
        print(table(headers, preview))
        print("Compare the first and final dates and the check counts with each filing's schedule.")

        if not assume_yes:
            answer = input("Add these notes? [y/N] ").strip().lower()
            if not answer.startswith("y"):
                conn.rollback()
                print("Nothing added.")
                return

        cusips = [r[0] for r in preview]
        new_underliers = sorted({r[1] for r in preview if r[-1] == "yes"})
        print(_load(conn))

        note_rows = db.rows(conn, """
            select n.cusip, n.index_code, x.name, i.name, n.note_type, n.trade_date, n.maturity_date,
                   n.initial_level, n.coupon_rate, n.coupon_barrier_pct, n.barrier_pct, n.buffer_pct,
                   n.downside_rate, n.participation, n.cap_pct, n.autocall_pct, n.call_premium,
                   n.memory_coupon, n.source_url
            from notes n join issuers i using (issuer_id) join indices x using (index_code)
            where n.cusip = any(%s) order by n.cusip""", (cusips,))
        obs_rows = db.rows(conn, """
            select n.cusip, o.obs_date, o.kind
            from observation_dates o join notes n using (note_id)
            where n.cusip = any(%s) order by n.cusip, o.obs_date, o.kind""", (cusips,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    try:
        csvfiles.append_rows(NOTES_CSV, note_rows)
        csvfiles.append_rows(OBS_CSV, obs_rows)
        print("Added to real_notes/notes.csv and observation_dates.csv, so setup will keep them.")
    except OSError as e:
        print(f"WARNING: the notes are in the database, but writing real_notes/*.csv failed ({e}).\n"
              "Add them to those files by hand, or they'll be missing after the next setup.")
    if new_underliers:
        print(f"New underliers: {', '.join(new_underliers)}. The next nightly price file needs a close "
              "for each (price-template and fetch-prices include them).")
