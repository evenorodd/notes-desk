"""Model values for the notes: what each note is worth today, before maturity.

A structured note is a bond from the issuing bank with options built into its
payout. Its model value per $1,000 is the expected payout under the standard
option-pricing assumptions (the underlier moves randomly with a constant
volatility around the risk-free rate, less dividends), discounted at the
bank's own borrowing rate: the Treasury rate plus a spread.

Two methods, which check each other in the tests:

  * Notes paid only at maturity (buffered, protected, trigger growth without a
    call): the expected payout is an integral over the final level, computed
    exactly (Gauss-Legendre between the payout's kinks).
  * Notes with coupons or calls: Monte Carlo. Only the check dates matter, so
    each path is simulated straight from one check date to the next.

The spread is calibrated once per note: on its trade date, it's the spread at
which the model matches the issuer's own estimated value from the filing
(estimated_value in notes.csv). That spread then stays fixed, so the model
answers "what would the issuer's own method say today?", given the inputs.

This is a model value, not a price anyone will pay: a dealer's bid is usually
lower, and the inputs (historical volatility, one flat volatility per
underlier, approximate dividend yields) are simplifications.
"""

import datetime
import math

try:
    import numpy as np
except ImportError:          # pricing is optional; everything else works without it
    np = None

PATHS = 20000                # Monte Carlo paths (half antithetic)
SEED = 20260101              # fixed, so the same inputs always give the same value
YEAR = 365.0


class PricingError(Exception):
    pass


def available():
    return np is not None


# ---------------------------------------------------------------------------
# The payout rules (the same as note_payout_per_1000 in sql/03_nightly_job.sql)

def payout_per_1000(n, level):
    """What $1,000 returns at maturity (coupons aside) if the underlier finishes
    at `level`: a number or a numpy array of levels."""
    x = np.asarray(level, dtype=float) / float(n["initial_level"])
    x = np.where(np.abs(x - np.round(x, 9)) < 1e-12, np.round(x, 9), x)   # 0.5499999999 -> 0.55, as in SQL
    cap = float(n["cap_pct"]) if n.get("cap_pct") is not None else 1e9
    part = float(n["participation"]) if n.get("participation") is not None else 0.0
    t = n["note_type"]
    if t == "income":
        r = np.where(x >= float(n["barrier_pct"]), 1.0, x)
    elif t == "buffered":
        buf = float(n["buffer_pct"])
        down = float(n["downside_rate"]) if n.get("downside_rate") is not None else 1.0
        r = np.where(x >= 1, 1 + np.minimum(part * (x - 1), cap),
                     np.where(x >= 1 - buf, 1.0, 1 + (x - 1 + buf) * down))
    elif t == "trigger_growth":
        r = np.where(x >= 1, 1 + np.minimum(part * (x - 1), cap),
                     np.where(x >= float(n["barrier_pct"]), 1.0, x))
    elif t == "protected":
        r = 1 + np.minimum(part * np.maximum(x - 1, 0), cap)
    else:
        raise PricingError(f"no payout rule for note type {t!r}")
    return 1000 * np.maximum(r, 0)


def kinks(n):
    """Levels (as a fraction of the start) where the payout bends or jumps."""
    t, k = n["note_type"], [1.0]
    if n.get("barrier_pct") is not None:
        k.append(float(n["barrier_pct"]))
    if t == "buffered":
        k.append(1 - float(n["buffer_pct"]))
    if n.get("cap_pct") is not None and n.get("participation"):
        k.append(1 + float(n["cap_pct"]) / float(n["participation"]))
    return sorted(set(x for x in k if x > 0))


def coupon_per_period(n, schedule):
    """Each coupon, as a fraction of principal: the annual rate divided by the
    number of coupon dates a year (monthly, quarterly...)."""
    coupons = [d for d, kinds in schedule if "coupon" in kinds]
    if not coupons or n.get("coupon_rate") is None:
        return 0.0
    years = (n["maturity_date"] - n["trade_date"]).days / YEAR
    per_year = max(1, round(len(coupons) / years))
    return float(n["coupon_rate"]) / per_year


# ---------------------------------------------------------------------------
# Market inputs

class Curve:
    """Treasury par yields by maturity (years), used as continuously
    compounded zero rates, interpolated linearly and flat beyond the ends."""

    def __init__(self, points):
        pts = sorted((float(t), float(y)) for t, y in points if y is not None)
        if not pts:
            raise PricingError("no interest rates")
        self.t = [p[0] for p in pts]
        # Treasury par yields are semi-annual bond yields; convert to continuous.
        self.r = [2 * math.log(1 + p[1] / 2) for p in pts]

    def rate(self, t):
        if t <= self.t[0]:
            return self.r[0]
        if t >= self.t[-1]:
            return self.r[-1]
        for i in range(1, len(self.t)):
            if t <= self.t[i]:
                w = (t - self.t[i - 1]) / (self.t[i] - self.t[i - 1])
                return self.r[i - 1] + w * (self.r[i] - self.r[i - 1])

    def discount(self, t, spread=0.0):
        return math.exp(-(self.rate(t) + spread) * t)


class Market:
    """One night's inputs for one note: underlier level, volatility, dividend
    yield and the Treasury curve."""

    def __init__(self, date, spot, vol, div, curve):
        self.date, self.spot, self.vol, self.div, self.curve = date, float(spot), float(vol), float(div), curve

    def bumped(self, spot=1.0, vol=0.0):
        return Market(self.date, self.spot * spot, self.vol + vol, self.div, self.curve)


# ---------------------------------------------------------------------------
# Cash flows

def remaining_schedule(n, schedule, date):
    """The check dates still ahead: [(date, {"coupon", "call", "maturity"})]."""
    return [(d, kinds) for d, kinds in schedule if d > date]


def _times(dates, date):
    return [(d - date).days / YEAR for d in dates]


def simulate_cashflows(n, schedule, mkt, missed=0, paths=PATHS, seed=SEED):
    """Monte Carlo: for each path, the cash flows (per $1,000) on each remaining
    check date. Returns (times, cash) with cash shaped (paths, dates); the
    discounting is left to the caller, so a spread can be solved for cheaply.
    `missed` = coupons already missed that a memory coupon would still pay."""
    ahead = remaining_schedule(n, schedule, mkt.date)
    if not ahead:
        raise PricingError("no check dates left")
    times = _times([d for d, _ in ahead], mkt.date)
    pay_times = list(times)
    final_pay = (n["maturity_date"] - mkt.date).days / YEAR
    pay_times[-1] = max(final_pay, times[-1])        # the final payout is paid on the maturity date

    # Log-levels on the check dates, simulated exactly from date to date.
    rng = np.random.default_rng(seed)
    half = paths // 2
    z = rng.standard_normal((half, len(times)))
    z = np.concatenate([z, -z])                      # antithetic pairs
    s0, sig, q = mkt.spot, mkt.vol, mkt.div
    logs, prev_t, prev_drift = np.full(len(z), math.log(s0)), 0.0, 0.0
    levels = np.empty_like(z)
    for i, t in enumerate(times):
        dt = t - prev_t
        drift = mkt.curve.rate(t) * t - q * t        # log of the forward, less log spot
        logs = logs + (drift - prev_drift) - 0.5 * sig * sig * dt + sig * math.sqrt(max(dt, 0)) * z[:, i]
        levels[:, i] = np.exp(logs)
        prev_t, prev_drift = t, drift

    start = float(n["initial_level"])
    coupon = coupon_per_period(n, schedule)
    cb = float(n["coupon_barrier_pct"] if n.get("coupon_barrier_pct") is not None
               else (n.get("barrier_pct") or 0)) * start
    call_at = float(n["autocall_pct"]) * start if n.get("autocall_pct") is not None else None
    premium = float(n["call_premium"]) if n.get("call_premium") is not None else 0.0
    memory = bool(n.get("memory_coupon"))

    cash = np.zeros_like(levels)
    alive = np.ones(len(z), dtype=bool)
    owed = np.full(len(z), float(missed if memory else 0))
    for i, (d, kinds) in enumerate(ahead):
        s = levels[:, i]
        if "coupon" in kinds and coupon:
            paid = alive & (s >= cb)
            cash[:, i] += np.where(paid, 1000 * coupon * (1 + owed), 0.0)
            if memory:
                owed = np.where(paid, 0.0, np.where(alive, owed + 1, owed))
        if "maturity" in kinds:
            cash[:, i] += np.where(alive, payout_per_1000(n, s), 0.0)
            alive[:] = False
        elif "call" in kinds and call_at is not None:
            called = alive & (s >= call_at)
            cash[:, i] += np.where(called, 1000 * (1 + premium), 0.0)
            alive &= ~called
    return np.array(pay_times), cash


def _discounts(curve, times, spread):
    return np.array([curve.discount(t, spread) for t in times])


def value_mc(n, schedule, mkt, spread, missed=0, paths=PATHS):
    times, cash = simulate_cashflows(n, schedule, mkt, missed, paths)
    pv = cash @ _discounts(mkt.curve, times, spread)
    return float(pv.mean()), float(pv.std(ddof=1) / math.sqrt(len(pv)))


def maturity_only(n, schedule):
    """True when nothing can happen before maturity: no coupons, no calls."""
    return n.get("autocall_pct") is None and not any(
        "coupon" in k or "call" in k for _, k in schedule)


_GL = None


def _gauss_legendre():
    global _GL
    if _GL is None:
        _GL = np.polynomial.legendre.leggauss(64)
    return _GL


def expected_final(n, mkt, t_obs):
    """E[payout at maturity] under the model, integrated exactly over the final
    level: Gauss-Legendre on each stretch between the payout's kinks."""
    s0, sig = mkt.spot, mkt.vol
    if t_obs <= 0:
        return float(payout_per_1000(n, s0))
    m = math.log(s0) + (mkt.curve.rate(t_obs) - mkt.div - 0.5 * sig * sig) * t_obs
    sd = sig * math.sqrt(t_obs)
    start = float(n["initial_level"])
    breaks = sorted({(math.log(k * start) - m) / sd for k in kinks(n)} | {-12.0, 12.0})
    breaks = [b for b in breaks if -12 <= b <= 12]
    x, w = _gauss_legendre()
    total = 0.0
    for a, b in zip(breaks, breaks[1:]):
        zz = (b - a) / 2 * x + (a + b) / 2
        dens = np.exp(-0.5 * zz * zz) / math.sqrt(2 * math.pi)
        total += (b - a) / 2 * float(np.sum(w * dens * payout_per_1000(n, np.exp(m + sd * zz))))
    return total


def value_exact(n, schedule, mkt, spread):
    ahead = remaining_schedule(n, schedule, mkt.date)
    final = next((d for d, k in ahead if "maturity" in k), None)
    if final is None:
        raise PricingError("no final valuation date left")
    t_obs = (final - mkt.date).days / YEAR
    t_pay = max((n["maturity_date"] - mkt.date).days / YEAR, t_obs)
    return expected_final(n, mkt, t_obs) * mkt.curve.discount(t_pay, spread), 0.0


def value(n, schedule, mkt, spread, missed=0):
    """(model value per $1,000, Monte Carlo standard error; 0 when exact)."""
    if maturity_only(n, schedule):
        return value_exact(n, schedule, mkt, spread)
    return value_mc(n, schedule, mkt, spread, missed)


def greeks(n, schedule, mkt, spread, missed=0):
    """How the value moves: per 1% move in the underlier (delta) and per
    volatility point (vega), both per $1,000. Bumps reuse the same random
    numbers, so Monte Carlo noise mostly cancels."""
    up = value(n, schedule, mkt.bumped(spot=1.01), spread, missed)[0]
    dn = value(n, schedule, mkt.bumped(spot=0.99), spread, missed)[0]
    vu = value(n, schedule, mkt.bumped(vol=0.01), spread, missed)[0]
    vd = value(n, schedule, mkt.bumped(vol=-0.01), spread, missed)[0]
    return (up - dn) / 2, (vu - vd) / 2


def solve_spread(n, schedule, mkt, target, missed=0):
    """The spread over Treasuries at which the model value equals `target`
    (the issuer's estimated value). The value falls as the spread rises, so
    bisection is safe; Monte Carlo cash flows are simulated once."""
    if maturity_only(n, schedule):
        ahead = remaining_schedule(n, schedule, mkt.date)
        final = next(d for d, k in ahead if "maturity" in k)
        t_obs = (final - mkt.date).days / YEAR
        t_pay = max((n["maturity_date"] - mkt.date).days / YEAR, t_obs)
        e = expected_final(n, mkt, t_obs)
        # e * exp(-(r + s) t) = target  =>  s = ln(e / target) / t - r
        return math.log(e / float(target)) / t_pay - mkt.curve.rate(t_pay)
    times, cash = simulate_cashflows(n, schedule, mkt, missed)

    def pv(s):
        return float((cash @ _discounts(mkt.curve, times, s)).mean())
    lo, hi = -0.10, 0.50
    if not pv(hi) <= float(target) <= pv(lo):
        raise PricingError(f"no spread between {lo:.0%} and {hi:.0%} matches the estimated value {target}")
    for _ in range(60):
        mid = (lo + hi) / 2
        if pv(mid) > float(target):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


MAX_GAP_DAYS = 7            # a return across a longer gap in the prices isn't a daily move


def realized_vol(closes, window=252, minimum=60):
    """Annualized volatility of daily log returns over the last `window`
    closes, given as (date, close) pairs, oldest first. Returns across a gap
    of more than a week (missing prices) are left out. None if fewer than
    `minimum` returns remain."""
    recent = closes[-(window + 1):]
    r = [math.log(float(b) / float(a)) for (da, a), (db_, b) in zip(recent, recent[1:])
         if (db_ - da).days <= MAX_GAP_DAYS]
    if len(r) < minimum:
        return None
    return float(np.std(r, ddof=1) * math.sqrt(252))


def bs_call(s, k, t, r, q, sig):
    """Black-Scholes call, for the tests."""
    d1 = (math.log(s / k) + (r - q + 0.5 * sig * sig) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    nd = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * math.exp(-q * t) * nd(d1) - k * math.exp(-r * t) * nd(d2)


def years_between(a, b):
    return (b - a).days / YEAR


def as_date(d):
    return d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d))
