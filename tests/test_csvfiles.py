"""Unit tests for reading the CSV files (no database needed).

    python3 -m unittest tests.test_csvfiles
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from desk import DeskError
from desk.csvfiles import NOTE_COLUMNS, append_rows, read_notes, read_prices


def write(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


class Prices(unittest.TestCase):
    def test_reads_prices(self):
        rows = read_prices(write("index_code,price_date,close\nSPX,2026-11-20,6910.39\nAAPL,2026-11-20,\n"))
        self.assertEqual(rows[0]["close"], Decimal("6910.39"))
        self.assertEqual(rows[0]["price_date"], date(2026, 11, 20))
        self.assertIsNone(rows[1]["close"])           # blank is allowed here; the loader reports it

    def test_reports_every_bad_cell_with_line_numbers(self):
        with self.assertRaises(DeskError) as ctx:
            read_prices(write("index_code,price_date,close\nSPX,11/20/2026,6910\nAAPL,2026-11-20,$351.20\n"))
        problems = "\n".join(ctx.exception.problems)
        self.assertIn("line 2 SPX", problems)
        self.assertIn("isn't a date", problems)
        self.assertIn("line 3 AAPL", problems)
        self.assertIn("no $ or commas", problems)

    def test_wrong_header(self):
        with self.assertRaises(DeskError) as ctx:
            read_prices(write("ticker,date,price\nSPX,2026-11-20,1\n"))
        self.assertIn("missing these columns", str(ctx.exception))

    def test_excel_style_file_with_bom_and_blank_lines(self):
        rows = read_prices(write("﻿index_code,price_date,close\r\nSPX,2026-11-20,1\r\n,,\r\n"))
        self.assertEqual(len(rows), 1)


class Notes(unittest.TestCase):
    def test_memory_coupon_and_blanks(self):
        header = ",".join(NOTE_COLUMNS)
        row = "X1234567A,AAPL,\"Apple, Inc.\",Some Bank,income,2026-11-12,2029-11-15,354.88,0.088,,0.70,,,,,1.00,,yes,"
        notes = read_notes(write(f"{header}\n{row}\n"))
        self.assertEqual(notes[0]["underlier_name"], "Apple, Inc.")
        self.assertIs(notes[0]["memory_coupon"], True)
        self.assertIsNone(notes[0]["coupon_barrier_pct"])

    def test_append_keeps_one_row_per_line(self):
        path = write("a,b\n1,2")                         # no newline at the end
        append_rows(path, [["3", Decimal("4.50")], [None, True]])
        self.assertEqual(path.read_text(), "a,b\n1,2\n3,4.50\n,true\n")


if __name__ == "__main__":
    unittest.main()
