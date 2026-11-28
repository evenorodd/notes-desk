"""Tests for reading Yahoo's price feed (no network: uses saved-style JSON)."""

import datetime
import unittest
from decimal import Decimal
from zoneinfo import ZoneInfo

from desk.fetch import parse_chart, yahoo_symbol

NY = ZoneInfo("America/New_York")


def stamp(day, hour=9, minute=30):
    """Yahoo stamps each daily bar with the session's 9:30 am New York open."""
    return int(datetime.datetime.combine(day, datetime.time(hour, minute), NY).timestamp())


def chart(bars):
    return {"chart": {"result": [{
        "meta": {"symbol": "^GSPC", "exchangeTimezoneName": "America/New_York"},
        "timestamp": [stamp(d) for d, _ in bars],
        "indicators": {"quote": [{"close": [c for _, c in bars]}]},
    }], "error": None}}


THU, FRI, MON = datetime.date(2026, 11, 19), datetime.date(2026, 11, 20), datetime.date(2026, 11, 23)


class ParseChart(unittest.TestCase):
    def test_closes_by_date_rounded_to_cents(self):
        data = chart([(THU, 7175.830078125), (FRI, 6910.39013671875)])
        got = parse_chart(data, now=datetime.datetime(2026, 11, 21, 12, tzinfo=NY))
        self.assertEqual(got, {THU: Decimal("7175.83"), FRI: Decimal("6910.39")})

    def test_todays_bar_is_left_out_until_the_close(self):
        data = chart([(FRI, 6910.39), (MON, 6950.12)])
        during = parse_chart(data, now=datetime.datetime(2026, 11, 23, 15, 59, tzinfo=NY))
        self.assertEqual(list(during), [FRI])
        after = parse_chart(data, now=datetime.datetime(2026, 11, 23, 16, 30, tzinfo=NY))
        self.assertEqual(after[MON], Decimal("6950.12"))

    def test_now_in_another_time_zone(self):
        # 3:30 pm Chicago is 4:30 pm New York, so Monday's close is final.
        data = chart([(MON, 6950.12)])
        got = parse_chart(data, now=datetime.datetime(2026, 11, 23, 15, 30, tzinfo=ZoneInfo("America/Chicago")))
        self.assertIn(MON, got)

    def test_missing_closes_are_skipped(self):
        data = chart([(THU, None), (FRI, 6910.39)])
        got = parse_chart(data, now=datetime.datetime(2026, 11, 21, tzinfo=NY))
        self.assertEqual(list(got), [FRI])

    def test_close_from_summary_when_todays_bar_is_empty(self):
        data = chart([(FRI, 6910.39), (MON, None)])
        data["chart"]["result"][0]["meta"].update(
            regularMarketPrice=350.017, regularMarketTime=stamp(MON, 16, 0))
        after = parse_chart(data, now=datetime.datetime(2026, 11, 23, 20, 15, tzinfo=NY))
        self.assertEqual(after[MON], Decimal("350.02"))
        during = parse_chart(data, now=datetime.datetime(2026, 11, 23, 15, 0, tzinfo=NY))
        self.assertNotIn(MON, during)

    def test_bar_close_wins_over_summary(self):
        data = chart([(MON, 349.50)])
        data["chart"]["result"][0]["meta"].update(
            regularMarketPrice=349.80, regularMarketTime=stamp(MON, 19, 59))   # an after-hours trade
        got = parse_chart(data, now=datetime.datetime(2026, 11, 23, 20, 15, tzinfo=NY))
        self.assertEqual(got[MON], Decimal("349.50"))

    def test_feed_error_is_reported(self):
        data = {"chart": {"result": None, "error": {"code": "Not Found",
                                                     "description": "No data found, symbol may be delisted"}}}
        with self.assertRaisesRegex(ValueError, "symbol may be delisted"):
            parse_chart(data)

    def test_symbols(self):
        self.assertEqual(yahoo_symbol("SPX"), "^GSPC")
        self.assertEqual(yahoo_symbol("RTY"), "^RUT")
        self.assertEqual(yahoo_symbol("AAPL"), "AAPL")
        self.assertEqual(yahoo_symbol("BRK.B"), "BRK-B")



class WhyNoCloses(unittest.TestCase):
    """The message when the feed has no close at all for the date asked for."""
    THANKSGIVING = datetime.date(2026, 11, 26)

    def why(self, run_date, now, found=None):
        from desk.fetch import why_no_closes
        found = found if found is not None else {"SPX": {datetime.date(2026, 11, 25): Decimal("1")}}
        return str(why_no_closes(run_date, found, now=now.replace(tzinfo=NY)))

    def test_too_early_today(self):
        self.assertIn("aren't final until 16:20", self.why(MON, datetime.datetime(2026, 11, 23, 15, 0)))

    def test_holiday_today_after_five(self):
        self.assertIn("market holiday", self.why(self.THANKSGIVING, datetime.datetime(2026, 11, 26, 17, 30)))

    def test_maybe_late_feed_just_after_the_close(self):
        self.assertIn("running late", self.why(self.THANKSGIVING, datetime.datetime(2026, 11, 26, 16, 30)))

    def test_past_holiday_with_later_closes(self):
        found = {"SPX": {datetime.date(2026, 11, 25): Decimal("1"), datetime.date(2026, 11, 27): Decimal("1")}}
        self.assertIn("market holiday", self.why(self.THANKSGIVING, datetime.datetime(2026, 11, 28, 9), found))

    def test_weekend(self):
        self.assertIn("weekend", self.why(datetime.date(2026, 11, 21), datetime.datetime(2026, 11, 23, 9)))



class Splits(unittest.TestCase):
    """Stock splits reported by the price feed (events=split in Yahoo's chart API)."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        import desk.fetch as F
        self.F = F
        self.dir = tempfile.TemporaryDirectory()
        self.saved = F.FEEDS_DIR
        F.FEEDS_DIR = Path(self.dir.name)

    def tearDown(self):
        self.F.FEEDS_DIR = self.saved
        self.dir.cleanup()

    def chart(self, splits):
        return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                                      "timestamp": [], "indicators": {"quote": [{"close": []}]},
                                      "events": {"splits": splits}}], "error": None}}

    def test_parse_forward_and_reverse(self):
        june = int(datetime.datetime(2026, 6, 10, 13, 30, tzinfo=datetime.timezone.utc).timestamp())
        july = int(datetime.datetime(2026, 7, 1, 13, 30, tzinfo=datetime.timezone.utc).timestamp())
        got = self.F.parse_splits(self.chart({str(june): {"date": june, "numerator": 4, "denominator": 1},
                                              str(july): {"date": july, "numerator": 1, "denominator": 10}}))
        self.assertEqual(got, {datetime.date(2026, 6, 10): Decimal(4), datetime.date(2026, 7, 1): Decimal("0.1")})
        self.assertEqual(self.F.parse_splits({"chart": {"result": [{}]}}), {})

    def test_recorded_once(self):
        d = datetime.date(2026, 6, 10)
        self.F.record_splits("NVDA", {d: Decimal(4)})
        self.F.record_splits("NVDA", {d: Decimal(4)})
        self.assertEqual(self.F.read_splits(), [("NVDA", d, Decimal(4), False)])

    def test_night_across_an_unhandled_split_is_refused(self):
        import desk.prices as P
        d = datetime.date(2026, 6, 10)
        self.F.record_splits("NVDA", {d: Decimal(4)})
        saved = P.db.scalar
        P.db.scalar = lambda conn, sql, params=None: datetime.date(2026, 6, 9)   # previous close
        try:
            got = P.split_problems(None, d, {"NVDA", "SPX"})
            self.assertEqual(len(got), 1)
            self.assertIn("NVDA had a 4-for-1 split on 2026-06-10", got[0])
            self.assertEqual(P.split_problems(None, d, {"SPX"}), [])          # not in the book
            self.assertEqual(P.split_problems(None, datetime.date(2026, 6, 9), {"NVDA"}), [])   # before it
        finally:
            P.db.scalar = saved


if __name__ == "__main__":
    unittest.main()
