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

APP = [sys.executable, str(Path(__file__).resolve().parent.parent / "notes_desk.py")]

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
    for f in sorted(FEEDS_DIR.glob("closes_*.csv")):
        night = f.stem[len("closes_"):]
        if subprocess.run(APP + ["nightly", night]).returncode != 0:
            print(f"(The run for {night} didn't finish; the website will show why on its Runs page.)", flush=True)

os.execv(APP[0], APP + ["serve", "--host", "0.0.0.0", "--port", "8000"])
