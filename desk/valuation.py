"""Model values in the database: loads the inputs, calibrates each note's
spread once, and values every note in the book for a night. The maths is in
pricing.py; this file only gathers inputs and stores results.

    python3 notes_desk.py price 2026-11-25     value one night (normally done by nightly)
    python3 notes_desk.py price --all          value every night that has run

Inputs, all plain files you can check:
  feeds/rates_YEAR.csv              Treasury par yields (fetch-rates)
  feeds/history_YEAR.csv            closes before the book starts (fetch-history), for volatility
  real_notes/dividend_yields.csv    expected dividend yield per underlier (approximate)
  real_notes/estimated_values.csv   each issuer's estimated value on the trade date, from its filing
"""

import collections
import csv
import datetime
import statistics

from . import FEEDS_DIR, NOTES_DIR, SQL_DIR, DeskError, csvfiles, db, pricing
from .textable import table

DIVIDENDS_CSV = NOTES_DIR / "dividend_yields.csv"
ESTIMATES_CSV = NOTES_DIR / "estimated_values.csv"
RATES_STALE_DAYS = 10        # use the latest Treasury curve within this many days


def ensure_tables(conn):
    db.run_file(conn, SQL_DIR / "05_pricing.sql")


def _changed(conn, path):
    """True (and remembered) if this file is new or has changed since it was loaded."""
    st = path.stat()
    sig = f"{st.st_size}:{int(st.st_mtime)}"
    if db.scalar(conn, "select signature from pricing_input_files where filename = %s", (path.name,)) == sig:
        return False
    db.run(conn, """insert into pricing_input_files (filename, signature) values (%s, %s)
                    on conflict (filename) do update set signature = excluded.signature""", (path.name, sig))
    return True


def load_inputs(conn):
    """Rate files into treasury_rates; history files into market_data (never
    over a close that a nightly price file already loaded). Each file is
    loaded only when it's new or has changed."""
    from .rates import read_rate_file
    for path in sorted(FEEDS_DIR.glob("rates_*.csv")):
        if _changed(conn, path):
            db.run_many(conn, """insert into treasury_rates (rate_date, tenor_years, yield) values (%s, %s, %s)
                                 on conflict (rate_date, tenor_years) do update set yield = excluded.yield""",
                        read_rate_file(path))
    known = {r[0] for r in db.rows(conn, "select index_code from indices")}
    for path in sorted(FEEDS_DIR.glob("history_*.csv")):
        if _changed(conn, path):
            rows = [r for r in csvfiles.read_prices(path) if r["index_code"] in known]
            db.run_many(conn, """insert into market_data (index_code, price_date, close) values (%s, %s, %s)
                                 on conflict (index_code, price_date) do nothing""",
                        [(r["index_code"], r["price_date"], r["close"]) for r in rows])
    conn.commit()


def _read_csv(path, key, value):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r[key].strip(): float(r[value]) for r in csv.DictReader(fh) if r.get(value, "").strip()}


class Inputs:
    """Everything pricing needs from the database, read once and cached."""

    def __init__(self, conn):
        self.conn = conn
        self.dividends = _read_csv(DIVIDENDS_CSV, "index_code", "dividend_yield")
        self.estimates = _read_csv(ESTIMATES_CSV, "cusip", "estimated_value")
        cols = [r[0] for r in db.rows(conn, """select column_name from information_schema.columns
                                                where table_name = 'notes' order by ordinal_position""")]
        self.notes = {r[0]: dict(zip(cols, r)) for r in db.rows(conn, f"select {', '.join(cols)} from notes")}
        self.issuer = dict(db.rows(conn, "select n.note_id, i.name from notes n join issuers i using (issuer_id)"))
        sched = collections.defaultdict(lambda: collections.defaultdict(set))
        for nid, d, kind in db.rows(conn, "select note_id, obs_date, kind from observation_dates"):
            sched[nid][d].add(kind)
        self.schedule = {nid: sorted((d, k) for d, k in by.items()) for nid, by in sched.items()}
        closes = collections.defaultdict(list)
        for code, d, c in db.rows(conn, "select index_code, price_date, close from market_data order by 1, 2"):
            closes[code].append((d, float(c)))
        self.closes = closes
        curves = collections.defaultdict(list)
        for d, t, y in db.rows(conn, "select rate_date, tenor_years, yield from treasury_rates order by 1, 2"):
            curves[d].append((float(t), float(y)))
        self.curve_dates = sorted(curves)
        self.curves = curves
        self._built = {}

    def curve(self, d):
        """The latest Treasury curve on or before d (within RATES_STALE_DAYS)."""
        import bisect
        i = bisect.bisect_right(self.curve_dates, d) - 1
        if i < 0 or (d - self.curve_dates[i]).days > RATES_STALE_DAYS:
            return None
        day = self.curve_dates[i]
        if day not in self._built:
            self._built[day] = pricing.Curve(self.curves[day])
        return self._built[day]

    def closes_upto(self, code, d):
        import bisect
        series = self.closes.get(code, [])
        i = bisect.bisect_right(series, (d, float("inf")))
        return series[:i]

    def close_on(self, code, d):
        s = self.closes_upto(code, d)
        return s[-1][1] if s and s[-1][0] == d else None

    def vol(self, code, d):
        return pricing.realized_vol(self.closes_upto(code, d))

    def market(self, note, d, spot):
        """A Market for this note on date d, or the reason there isn't one."""
        curve = self.curve(d)
        if curve is None:
            return None, f"no Treasury rates within {RATES_STALE_DAYS} days of {d} (run fetch-rates)"
        vol = self.vol(note["index_code"], d)
        if vol is None:
            return None, f"not enough {note['index_code']} price history before {d} for volatility (run fetch-history)"
        return pricing.Market(d, spot, vol, self.dividends.get(note["index_code"], 0.0), curve), None

    def missed_coupons(self, note, d):
        """Memory notes: coupons missed since the last one paid, as of date d."""
        if not note.get("memory_coupon"):
            return 0
        start = float(note["initial_level"])
        cb = float(note["coupon_barrier_pct"] if note.get("coupon_barrier_pct") is not None
                   else note["barrier_pct"]) * start
        owed = 0
        series = self.closes.get(note["index_code"], [])
        for od, kinds in self.schedule.get(note["note_id"], []):
            if od > d or "coupon" not in kinds:
                continue
            # The level on the check date, or the next close if it fell on a holiday.
            level = next((c for cd, c in series if od <= cd <= d), None)
            if level is None:
                continue
            owed = 0 if level >= cb else owed + 1
        return owed


def calibrate(conn, inputs, upto):
    """Solves the spread, on its trade date, of each note traded by `upto`
    that isn't calibrated yet. (Waiting until then matters: the volatility
    on the trade date needs the closes up to it.) Notes whose filing gives no
    estimated value get their issuer's average spread (or the book's), marked
    as such. Returns problems, one line per note."""
    done = {r[0] for r in db.rows(conn, "select note_id from note_calibrations")}
    todo = [n for nid, n in inputs.notes.items() if nid not in done and n["trade_date"] <= upto]
    problems, fallback = [], []
    for n in sorted(todo, key=lambda n: n["trade_date"]):
        ev = inputs.estimates.get(n["cusip"])
        if ev is None:
            fallback.append(n)
            continue
        mkt, why = inputs.market(n, n["trade_date"], n["initial_level"])
        if mkt is None:
            problems.append(f"{n['cusip']}: can't calibrate: {why}")
            continue
        sched = inputs.schedule.get(n["note_id"], [])
        try:
            s = pricing.solve_spread(n, sched, mkt, ev)
        except pricing.PricingError as e:
            # Usually the volatility is far from what the issuer used. Borrow a
            # spread rather than leave the note unpriced, and say so.
            problems.append(f"{n['cusip']}: can't match its estimated value ({e}); using its issuer's average spread")
            fallback.append(n)
            continue
        db.run(conn, """insert into note_calibrations (note_id, estimated_value, spread, source, vol, rate)
                        values (%s, %s, %s, 'filing', %s, %s)""",
               (n["note_id"], ev, s, mkt.vol, mkt.curve.rate(pricing.years_between(n["trade_date"], n["maturity_date"]))))
    if fallback:
        by_issuer = collections.defaultdict(list)
        for nid, s in db.rows(conn, "select note_id, spread from note_calibrations where source = 'filing'"):
            by_issuer[inputs.issuer[nid]].append(float(s))
        everyone = [s for v in by_issuer.values() for s in v]
        for n in fallback:
            pool = by_issuer.get(inputs.issuer[n["note_id"]]) or everyone
            if not pool:
                problems.append(f"{n['cusip']}: no estimated value, and no other notes to borrow a spread from")
                continue
            source = "issuer average" if by_issuer.get(inputs.issuer[n["note_id"]]) else "book average"
            db.run(conn, """insert into note_calibrations (note_id, estimated_value, spread, source)
                            values (%s, null, %s, %s)""", (n["note_id"], statistics.median(pool), source))
    conn.commit()
    return problems


def price_night(conn, inputs, d, greeks=True):
    """Values every note in the book on date d. Returns (priced, problems)."""
    spreads = dict(db.rows(conn, "select note_id, spread from note_calibrations"))
    live = db.rows(conn, """select s.note_id, s.index_level from note_daily_status s
                            join notes n using (note_id)
                            where s.run_date = %s and (n.closed_date is null or n.closed_date > %s)""", (d, d))
    db.run(conn, "delete from note_valuations where run_date = %s", (d,))
    rows, problems = [], []
    for nid, level in live:
        n = inputs.notes[nid]
        if nid not in spreads:
            problems.append(f"{n['cusip']}: not calibrated")
            continue
        mkt, why = inputs.market(n, d, level)
        if mkt is None:
            problems.append(f"{n['cusip']}: {why}")
            continue
        sched = inputs.schedule.get(nid, [])
        spread = float(spreads[nid])
        missed = inputs.missed_coupons(n, d)
        try:
            v, err = pricing.value(n, sched, mkt, spread, missed)
            delta, vega = pricing.greeks(n, sched, mkt, spread, missed) if greeks else (None, None)
        except pricing.PricingError as e:
            problems.append(f"{n['cusip']}: {e}")
            continue
        t = pricing.years_between(d, n["maturity_date"])
        method = "exact" if pricing.maturity_only(n, sched) else "monte carlo"
        rows.append((d, nid, round(v, 2), round(err, 2), _r(delta), _r(vega), round(mkt.vol, 4),
                     mkt.div, round(mkt.curve.rate(t), 5), round(spread, 5), method))
    db.run_many(conn, """insert into note_valuations (run_date, note_id, model_value, std_error, delta_1pct,
                             vega_1pt, vol, div_yield, rate, spread, method)
                         values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", rows)
    conn.commit()
    return len(rows), problems


def _r(x):
    return None if x is None else round(x, 2)


def price(nights, quiet=False, greeks=True):
    """Calibrates any new notes, then values each night. Returns a short
    summary line and the problems; never raises for a single note."""
    if not pricing.available():
        return "not priced: numpy isn't installed (pip install numpy)", []
    conn = db.connect()
    try:
        ensure_tables(conn)
        load_inputs(conn)
        if not db.scalar(conn, "select exists (select 1 from treasury_rates)"):
            return "not priced yet: no Treasury rates on file (run pricing-setup once)", []
        inputs = Inputs(conn)
        problems = calibrate(conn, inputs, max(nights))
        priced = 0
        for d in nights:
            n, p = price_night(conn, inputs, d, greeks)
            priced += n
            problems += [f"{d} {x}" for x in p] if len(nights) > 1 else p
        return f"priced {priced} note-night(s)" if len(nights) > 1 else f"priced {priced} notes", problems
    finally:
        conn.close()


def price_command(run_date=None, all_nights=False):
    """`price DATE` / `price --all`."""
    conn = db.connect()
    try:
        if all_nights:
            nights = [r[0] for r in db.rows(conn, "select distinct run_date from note_daily_status order by 1")]
        else:
            nights = [run_date or db.scalar(conn, "select max(run_date) from note_daily_status")]
    finally:
        conn.close()
    if not nights or nights[0] is None:
        raise DeskError("No nights have run yet, so there's nothing to price.")
    print(f"Pricing {len(nights)} night(s), {nights[0]} to {nights[-1]} ...", flush=True)
    summary, problems = price(nights, greeks=True)
    print(summary + ".")
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems[:40]:
            print("  " + p)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
    conn = db.connect()
    try:
        last = nights[-1]
        rows = db.rows(conn, """
            select n.cusip, n.index_code, n.note_type, c.estimated_value, v.model_value, v.delta_1pct,
                   v.vega_1pt, round(v.vol * 100, 1), round(v.spread * 10000), v.method
            from note_valuations v join notes n using (note_id) left join note_calibrations c using (note_id)
            where v.run_date = %s order by v.model_value""", (last,))
        if rows:
            print(f"\nModel values on {last}, per $1,000 (lowest first; spread in basis points):")
            print(table(["cusip", "underlier", "type", "est_value_at_issue", "model_value", "per_1pct_move",
                         "per_vol_point", "vol_%", "spread_bp", "method"], [list(r) for r in rows[:15]]))
            if len(rows) > 15:
                print(f"... and {len(rows) - 15} more (all of them are on the website and in the notes CSV).")
    finally:
        conn.close()


def setup_inputs(history_year=None):
    """`pricing-setup`: a year of history before the book, the Treasury rates
    for every year since, then values every night that has run."""
    from . import market_today
    from .rates import fetch_history, fetch_rates
    conn = db.connect()
    try:
        first = db.scalar(conn, "select min(trade_date) from notes")
    finally:
        conn.close()
    if first is None:
        raise DeskError("No notes in the database yet. Run setup first.")
    year = history_year or first.year - 1
    print(f"1. Price history for {year} (volatility needs a year of closes before {first}) ...", flush=True)
    fetch_history(year)
    years = list(range(year, market_today().year + 1))
    print(f"\n2. Treasury rates for {', '.join(map(str, years))} ...", flush=True)
    fetch_rates(years)
    print("\n3. Model values for every night that has run ...", flush=True)
    price_command(all_nights=True)
