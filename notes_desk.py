#!/usr/bin/env python3
"""Notes desk: nightly monitoring of structured notes in PostgreSQL.

    python3 notes_desk.py setup                     build the database (reads my_history.csv)
    python3 notes_desk.py fetch-prices 2026-11-23   download that day's closes into the price file
    python3 notes_desk.py price-template 2026-11-23 or: a blank price file to type in
    python3 notes_desk.py nightly 2026-11-23        load the prices, run the job, write the reports
    python3 notes_desk.py nightly --fetch           fetch today's closes and run, in one step
    python3 notes_desk.py add-notes new_notes.csv   add notes, building their check dates
    python3 notes_desk.py serve                     open the notes desk in your browser (http://localhost:8000)
    python3 notes_desk.py export                    save the website as files to share (export/)
    python3 notes_desk.py what-if                   example payouts and flags (changes nothing)
    python3 notes_desk.py test                      run the tests (changes nothing)

Run any command with -h for its options. See README.md for the full guide.
"""

import os
import sys

from desk.cli import main

if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Output piped into something like `head` that stopped reading early.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
