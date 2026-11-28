"""Writing price files from a download (backfill and catch-up). No database:
the download and the "which underliers are in the book" lookup are faked."""

import datetime
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import desk.backfill as B
import desk.fetch as F
from desk import csvfiles

MON, TUE = datetime.date(2026, 11, 23), datetime.date(2026, 11, 24)


class Download(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.saved = (B.FEEDS_DIR, F.daily_closes, B._needed_by_day)
        B.FEEDS_DIR = Path(self.dir.name)
        B._needed_by_day = lambda days: {d: {"SPX", "NVDA"} for d in days}
        self.feed = {"SPX": {MON: Decimal("6600.10"), TUE: Decimal("6650.20")},
                     "NVDA": {MON: Decimal("180.00"), TUE: Decimal("182.00")}}
        self.down = set()

        def fake(symbol, start=None, end=None, code=None):
            if code in self.down:
                raise ValueError("HTTP 500 from the price feed")
            return dict(self.feed[code])
        F.daily_closes = fake

    def tearDown(self):
        B.FEEDS_DIR, F.daily_closes, B._needed_by_day = self.saved
        self.dir.cleanup()

    def codes_in(self, d):
        return sorted(r["index_code"] for r in csvfiles.read_prices(B.FEEDS_DIR / f"closes_{d}.csv"))

    def test_no_file_for_a_day_missing_an_underlier(self):
        self.down = {"NVDA"}
        failed, incomplete = B._download(["SPX", "NVDA"], MON, TUE, force=False)
        self.assertIn("NVDA", failed)
        self.assertEqual(incomplete, {MON: ["NVDA"], TUE: ["NVDA"]})
        self.assertFalse((B.FEEDS_DIR / f"closes_{MON}.csv").exists())

    def test_the_next_download_completes_it(self):
        self.down = {"NVDA"}
        B._download(["SPX", "NVDA"], MON, TUE, force=False)
        self.down = set()
        failed, incomplete = B._download(["SPX", "NVDA"], MON, TUE, force=False)
        self.assertEqual((failed, incomplete), ({}, {}))
        self.assertEqual(self.codes_in(MON), ["NVDA", "SPX"])

    def test_an_old_partial_file_is_filled_in_not_overwritten(self):
        # A file written before this fix, without NVDA, and with a hand-corrected SPX close.
        csvfiles.write_rows(B.FEEDS_DIR / f"closes_{MON}.csv", csvfiles.PRICE_COLUMNS, [["SPX", MON, "6600.00"]])
        B._download(["SPX", "NVDA"], MON, TUE, force=False)
        rows = {r["index_code"]: r["close"] for r in csvfiles.read_prices(B.FEEDS_DIR / f"closes_{MON}.csv")}
        self.assertEqual(rows, {"SPX": Decimal("6600.00"), "NVDA": Decimal("180.00")})

    def test_underliers_not_in_the_book_dont_matter(self):
        B._needed_by_day = lambda days: {d: {"SPX"} for d in days}
        self.down = {"NVDA"}
        _, incomplete = B._download(["SPX", "NVDA"], MON, TUE, force=False)
        self.assertEqual(incomplete, {})
        self.assertEqual(self.codes_in(MON), ["SPX"])


if __name__ == "__main__":
    unittest.main()
