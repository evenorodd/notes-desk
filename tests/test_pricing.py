"""Checks on the pricing maths (desk/pricing.py). No database needed.

The methods check each other: the exact integration against Black-Scholes,
Monte Carlo against the exact integration, and the spread solver against
the value it was solved from.
"""

import datetime
import math
import unittest

from desk import pricing as P

D0 = datetime.date(2026, 1, 2)


def flat(rate):
    return P.Curve([(0.25, rate), (30, rate)])


def market(spot=100.0, vol=0.25, div=0.0, rate=0.04, date=D0):
    # Curve takes semi-annual par yields; this gives a continuous rate of `rate`.
    return P.Market(date, spot, vol, div, flat(2 * (math.exp(rate / 2) - 1)))


def note(**terms):
    n = {"note_type": "buffered", "initial_level": 100, "trade_date": D0,
         "maturity_date": D0 + datetime.timedelta(days=730), "participation": 1.0}
    n.update(terms)
    return n


def schedule_at_maturity(n):
    return [(n["maturity_date"], {"maturity"})]


def schedule_every(n, months, kinds_before=("coupon", "call")):
    """Check dates every `months` months to maturity, the last one the maturity."""
    out, d = [], n["trade_date"]
    while True:
        m = d.month - 1 + months
        d = d.replace(year=d.year + m // 12, month=m % 12 + 1)
        if d >= n["maturity_date"]:
            out.append((n["maturity_date"], {"coupon", "maturity"} if "coupon" in kinds_before else {"maturity"}))
            return out
        out.append((d, set(kinds_before)))


@unittest.skipUnless(P.available(), "numpy isn't installed")
class Exact(unittest.TestCase):
    def test_protected_note_is_a_bond_plus_a_call(self):
        # Full participation, no cap: $1,000 at maturity plus $10 per point above 100.
        n = note(note_type="protected")
        mkt = market(vol=0.30, div=0.01)
        t = 730 / P.YEAR
        expect = 1000 * math.exp(-0.04 * t) + 10 * P.bs_call(100, 100, t, 0.04, 0.01, 0.30)
        got, _ = P.value(n, schedule_at_maturity(n), mkt, spread=0.0)
        self.assertAlmostEqual(got, expect, places=6)

    def test_spread_discounts_the_whole_note(self):
        n = note(note_type="protected")
        a, _ = P.value(n, schedule_at_maturity(n), market(), 0.0)
        b, _ = P.value(n, schedule_at_maturity(n), market(), 0.01)
        self.assertAlmostEqual(b / a, math.exp(-0.01 * 730 / P.YEAR), places=9)

    def test_capped_buffer_note_against_options(self):
        # 1 + min(R, 20%) above the start; 0 down to -15%; then the loss past 15%.
        # = bond + call(100) - call(120) - put(85).
        n = note(buffer_pct=0.15, cap_pct=0.20)
        t, r, sig = 730 / P.YEAR, 0.04, 0.22
        call = lambda k: P.bs_call(100, k, t, r, 0, sig)
        put85 = call(85) - 100 + 85 * math.exp(-r * t)
        expect = 1000 * math.exp(-r * t) + 10 * (call(100) - call(120) - put85)
        got, _ = P.value(n, schedule_at_maturity(n), market(vol=sig), 0.0)
        self.assertAlmostEqual(got, expect, places=5)


@unittest.skipUnless(P.available(), "numpy isn't installed")
class MonteCarlo(unittest.TestCase):
    def test_agrees_with_exact_on_a_maturity_only_note(self):
        n = note(buffer_pct=0.10, cap_pct=0.35, participation=1.5)
        sched = schedule_at_maturity(n)
        exact, _ = P.value_exact(n, sched, market(), 0.005)
        mc, err = P.value_mc(n, sched, market(), 0.005, paths=200000)
        self.assertLess(abs(mc - exact), 4 * err + 0.05)

    def test_income_note_that_is_certain_to_be_called(self):
        # Almost no volatility and far above the start: called on the first call
        # date (after 3 coupons, the first two not callable), with that coupon.
        n = note(note_type="income", coupon_rate=0.12, barrier_pct=0.6, autocall_pct=1.0)
        sched = [(datetime.date(2026, 4, 2), {"coupon"}), (datetime.date(2026, 7, 2), {"coupon"}),
                 (datetime.date(2026, 10, 2), {"coupon", "call"}), (n["maturity_date"], {"coupon", "maturity"})]
        mkt = market(spot=150, vol=0.0001)
        got, _ = P.value_mc(n, sched, mkt, 0.0, paths=2000)
        disc = lambda d: math.exp(-0.04 * (d - D0).days / P.YEAR)
        per = P.coupon_per_period(n, sched)
        expect = sum(1000 * per * disc(d) for d, _ in sched[:3]) + 1000 * disc(sched[2][0])
        self.assertAlmostEqual(got, expect, places=4)

    def test_memory_coupon_pays_what_was_missed(self):
        n = note(note_type="income", coupon_rate=0.12, barrier_pct=0.6, memory_coupon=True)
        sched = [(D0 + datetime.timedelta(days=30), {"coupon"}), (n["maturity_date"], {"coupon", "maturity"})]
        mkt = market(spot=100, vol=0.0001)
        plain, _ = P.value_mc(n, sched, mkt, 0.0, missed=0, paths=2000)
        owed, _ = P.value_mc(n, sched, mkt, 0.0, missed=2, paths=2000)
        per = P.coupon_per_period(n, sched)
        t1 = 30 / P.YEAR
        self.assertAlmostEqual(owed - plain, 2 * 1000 * per * math.exp(-0.04 * t1), places=4)

    def test_same_inputs_same_value(self):
        n = note(note_type="income", coupon_rate=0.15, barrier_pct=0.6, autocall_pct=1.0, memory_coupon=True)
        n["maturity_date"] = D0 + datetime.timedelta(days=1095)
        sched = schedule_every(n, 3)
        self.assertEqual(P.value(n, sched, market(), 0.01), P.value(n, sched, market(), 0.01))

    def test_more_volatility_lowers_an_income_note(self):
        # The investor has sold a put (the barrier): more volatility hurts.
        n = note(note_type="income", coupon_rate=0.10, barrier_pct=0.7, autocall_pct=1.0)
        n["maturity_date"] = D0 + datetime.timedelta(days=1095)
        sched = schedule_every(n, 3)
        low, _ = P.value(n, sched, market(vol=0.15), 0.01)
        high, _ = P.value(n, sched, market(vol=0.45), 0.01)
        self.assertGreater(low, high)


@unittest.skipUnless(P.available(), "numpy isn't installed")
class Calibration(unittest.TestCase):
    def test_spread_reproduces_the_estimated_value(self):
        for n, sched in [
            (note(buffer_pct=0.15, cap_pct=0.25), None),
            (note(note_type="income", coupon_rate=0.14, barrier_pct=0.6, autocall_pct=1.0, memory_coupon=True), "q"),
        ]:
            sched = schedule_every(n, 3) if sched else schedule_at_maturity(n)
            mkt = market(vol=0.3, div=0.01)
            s = P.solve_spread(n, sched, mkt, 965.0)
            got, _ = P.value(n, sched, mkt, s)
            self.assertAlmostEqual(got, 965.0, places=2)
            self.assertTrue(-0.1 < s < 0.5)

    def test_realized_vol(self):
        import numpy as np
        rng = np.random.default_rng(1)
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.3 / math.sqrt(252), 253)))
        days = [D0 + datetime.timedelta(days=i) for i in range(253)]
        series = list(zip(days, closes))
        self.assertAlmostEqual(P.realized_vol(series), 0.30, delta=0.03)
        self.assertIsNone(P.realized_vol(series[:30]))
        # A month missing from the prices, with a big change across it, is left out.
        gap = series[:120] + [(d + datetime.timedelta(days=40), c * 3) for d, c in series[120:]]
        self.assertAlmostEqual(P.realized_vol(gap), P.realized_vol(series), delta=0.02)



class RateFiles(unittest.TestCase):
    """The two rate downloads' formats (samples written like the real files)."""

    def test_treasury_csv(self):
        from desk.rates import parse_treasury
        text = ('Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
                '11/25/2026,4.31,4.30,4.29,4.25,4.21,4.12,3.98,3.80,3.75,3.79,3.90,4.08,4.42,4.45\n'
                '11/24/2026,4.32,4.31,4.30,4.26,4.22,4.13,3.99,3.81,3.76,3.80,,4.09,4.43,4.46\n')
        rows = parse_treasury(text)
        self.assertIn((datetime.date(2026, 11, 25), 0.25, 0.0425), rows)
        self.assertIn((datetime.date(2026, 11, 25), 0.125, 0.043), rows)
        self.assertIn((datetime.date(2026, 11, 24), 30.0, 0.0446), rows)
        self.assertEqual(len(rows), 14 + 13)          # the blank 7-year rate is skipped

    def test_fred_csv(self):
        from desk.rates import parse_fred
        text = ("observation_date,DGS1MO,DGS3MO,DGS6MO,DGS1,DGS2,DGS3,DGS5,DGS7,DGS10,DGS20,DGS30\n"
                "2026-11-25,4.31,4.25,4.12,3.98,3.80,3.75,3.79,3.90,4.08,4.42,4.45\n"
                "2026-11-26,.,.,.,.,.,.,.,.,.,.,.\n")
        rows = parse_fred(text)
        self.assertEqual(len(rows), 11)
        self.assertIn((datetime.date(2026, 11, 25), 10, 0.0408), rows)

    def test_not_a_rates_file(self):
        from desk.rates import parse_treasury
        with self.assertRaises(ValueError):
            parse_treasury("<html>Access denied</html>")

    @unittest.skipUnless(P.available(), "numpy isn't installed")
    def test_curve_interpolates_and_converts(self):
        c = P.Curve([(1, 0.04), (2, 0.05)])
        self.assertAlmostEqual(c.rate(1), 2 * math.log(1.02))
        self.assertAlmostEqual(c.rate(1.5), (2 * math.log(1.02) + 2 * math.log(1.025)) / 2)
        self.assertAlmostEqual(c.rate(10), 2 * math.log(1.025))


if __name__ == "__main__":
    unittest.main()
