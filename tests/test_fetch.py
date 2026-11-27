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


if __name__ == "__main__":
    unittest.main()


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
