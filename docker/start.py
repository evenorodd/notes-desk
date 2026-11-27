"""Container start-up: wait for PostgreSQL, build the database on the first
start (setup, then a nightly run for every price file in feeds/), then serve
the website. Later starts keep the database and go straight to the website."""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desk import FEEDS_DIR, DeskError, db   # noqa: E402
from desk.folder_owner import run_as_folder_owner   # noqa: E402

APP = [sys.executable, str(Path(__file__).resolve().parent.parent / "notes_desk.py")]


run_as_folder_owner(Path(__file__).resolve().parent.parent)

for _ in range(60):
    try:
        db.connect("postgres").close()
        break
    except DeskError:
        time.sleep(1)
else:
    sys.exit("PostgreSQL didn't start within a minute. Check: docker compose logs db")

try:
    conn = db.connect()
    built = db.scalar(conn, "select to_regclass('notes') is not null")
    conn.close()
except DeskError:
    built = False

if not built:
    print("First start: building the database ...", flush=True)
    subprocess.run(APP + ["setup"], check=True)
    if any(FEEDS_DIR.glob("closes_*.csv")):
        # Every price file in feeds/, oldest first, so each check lands on its date.
        if subprocess.run(APP + ["backfill", "--no-download"]).returncode != 0:
            print("(Replaying the price history didn't finish; the website's Runs page shows why.)", flush=True)

os.execv(APP[0], APP + ["serve", "--host", "0.0.0.0", "--port", "8000"])
