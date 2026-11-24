"""Unit tests for building check dates from a schedule (no database needed).

The expected dates come from real filings, so these also check that the
rules match how issuers actually set their schedules.

    python3 -m unittest tests.test_schedule
"""

import unittest
from datetime import date
from decimal import Decimal

from desk.schedule import add_months, build_dates, next_weekday, schedule_problems


def note(**kw):
    base = {"cusip": "TEST00001", "note_type": "income", "autocall_pct": Decimal("1.00"),
            "obs_frequency": "monthly", "first_obs_date": None, "first_call_date": None,
            "final_valuation_date": None}
    base.update(kw)
    return base


class DateHelpers(unittest.TestCase):
    def test_month_end_clamps_without_drifting(self):
        start = date(2027, 1, 31)
        self.assertEqual([add_months(start, i) for i in range(4)],
                         [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)])

    def test_leap_year(self):
        self.assertEqual(add_months(date(2027, 2, 28), 12), date(2028, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))

    def test_weekend_moves_to_monday(self):
        self.assertEqual(next_weekday(date(2026, 12, 12)), date(2026, 12, 14))   # Saturday
        self.assertEqual(next_weekday(date(2026, 12, 13)), date(2026, 12, 14))   # Sunday
        self.assertEqual(next_weekday(date(2026, 12, 14)), date(2026, 12, 14))   # Monday


class RealSchedules(unittest.TestCase):
    def test_jpmorgan_coinbase_monthly(self):
        # 46661Q2G9: monthly on the 13th, callable from Feb 2027, final 2029-11-13
        d = build_dates(note(first_obs_date=date(2026, 12, 13), first_call_date=date(2027, 2, 13),
                             final_valuation_date=date(2029, 11, 13)))
        coupons = [x for x, k in d if k == "coupon"]
        calls = [x for x, k in d if k == "call"]
        self.assertEqual(len(coupons), 36)
        self.assertEqual(len(calls), 33)
        self.assertEqual(coupons[0], date(2026, 12, 14))      # the 13th is a Sunday
        self.assertEqual(calls[0], date(2027, 2, 15))         # the 13th is a Saturday
        self.assertEqual(calls[-1], date(2029, 10, 15))       # the final date isn't a call date
        self.assertIn((date(2029, 11, 13), "maturity"), d)

    def test_barclays_nvidia_sunday_before_final(self):
        # 06749LYL4: quarterly from 2027-02-12, callable from 2027-05-12, final Mon 2028-11-13.
        # The Nov 12, 2028 check is a Sunday and moves onto the final date, so it must not
        # appear twice.
        d = build_dates(note(obs_frequency="quarterly", first_obs_date=date(2027, 2, 12),
                             first_call_date=date(2027, 5, 12), final_valuation_date=date(2028, 11, 13)))
        self.assertEqual(len(d), len(set(d)), "a date/kind pair is listed twice")
        self.assertEqual([x for x, k in d if k == "coupon"],
                         [date(2027, 2, 12), date(2027, 5, 12), date(2027, 8, 12), date(2027, 11, 12),
                          date(2028, 2, 14), date(2028, 5, 12), date(2028, 8, 14), date(2028, 11, 13)])
        self.assertEqual(len([x for x, k in d if k == "call"]), 6)

    def test_buffered_note_with_one_call_date(self):
        # 06749LQ25: one call date 2027-11-26, final 2028-11-13
        d = build_dates(note(note_type="buffered", obs_frequency="annual",
                             first_obs_date=date(2027, 11, 26), final_valuation_date=date(2028, 11, 13)))
        self.assertEqual(d, [(date(2027, 11, 26), "call"), (date(2028, 11, 13), "maturity")])

    def test_growth_note_without_calls(self):
        d = build_dates(note(note_type="protected", autocall_pct=None, obs_frequency="none",
                             final_valuation_date=date(2031, 10, 27)))
        self.assertEqual(d, [(date(2031, 10, 27), "maturity")])

    def test_income_note_not_callable(self):
        d = build_dates(note(autocall_pct=None, obs_frequency="quarterly", first_obs_date=date(2027, 3, 1),
                             final_valuation_date=date(2027, 12, 1)))
        self.assertFalse([k for _, k in d if k == "call"])
        self.assertEqual(len([k for _, k in d if k == "coupon"]), 4)


class ScheduleProblems(unittest.TestCase):
    def test_reports_each_problem(self):
        problems = schedule_problems(note(obs_frequency="yearly"))
        self.assertTrue(any("obs_frequency" in p for p in problems))
        self.assertTrue(any("final_valuation_date is required" in p for p in problems))

    def test_first_date_after_final(self):
        problems = schedule_problems(note(first_obs_date=date(2028, 1, 1), final_valuation_date=date(2027, 1, 1)))
        self.assertTrue(any("before final_valuation_date" in p for p in problems))

    def test_valid_schedule(self):
        self.assertEqual(schedule_problems(note(first_obs_date=date(2027, 1, 1),
                                                final_valuation_date=date(2028, 1, 1))), [])


if __name__ == "__main__":
    unittest.main()
