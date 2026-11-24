"""What-if scenarios and tests. Both run inside a transaction that is rolled
back at the end, so neither changes your data."""

import datetime
import unittest
from decimal import Decimal

from . import PROJECT_DIR, DeskError, db
from .textable import money, pct, table

PAYOUT_NOTES = [("06749LM37", "Barclays HPE"), ("46661PYC5", "JPM SPX"), ("40058LCA6", "GS SPX"),
                ("46660TWB2", "JPM NDX"), ("46661QDG7", "JPM RTY")]
MOVES = ["0.80", "0.59", "0.20", "0.10", "0.03", "0.00", "-0.05", "-0.10", "-0.15", "-0.20",
         "-0.44", "-0.46", "-0.60"]


def what_if():
    conn = db.connect()
    try:
        present = [(c, label) for c, label in PAYOUT_NOTES
                   if db.scalar(conn, "select 1 from notes where cusip = %s", (c,))]
        if not present:
            raise DeskError("None of the what-if example notes are loaded.")

        print("\n== A. What $1,000 returns at maturity, by how far the underlier has moved from its start ==")
        print("   (Barclays HPE: principal only; its coupons are paid on top while HPE is above 55%)")
        rows = []
        for m in MOVES:
            row = [pct(Decimal(m), signed=True, places=0)]
            for cusip, _ in present:
                v = db.scalar(conn, "select note_payout_per_1000(n, n.initial_level * (1 + %s)) "
                                    "from notes n where cusip = %s", (Decimal(m), cusip))
                row.append(money(v))
            rows.append(row)
        print(table(["move"] + [label for _, label in present], rows))

        if db.scalar(conn, "select 1 from notes where cusip = '06749LM37'"):
            run = datetime.date(2027, 1, 4)
            print("\n== B. Barclays HPE note: what the nightly job flags on Jan 4, 2027 "
                  "(start $55.09, barrier $30.30) ==")
            rows = []
            for price in ["58.00", "50.00", "32.00", "29.00"]:
                db.run(conn, "delete from market_data where price_date = %s", (run,))
                db.run(conn, """
                    insert into market_data (index_code, price_date, close)
                    select i.index_code, %s,
                           case when i.index_code = 'HPE' then %s
                                else coalesce((select m.close from market_data m
                                               where m.index_code = i.index_code and m.price_date < %s
                                               order by m.price_date desc limit 1),
                                              (select max(initial_level) from notes n
                                               where n.index_code = i.index_code)) end
                    from indices i""", (run, Decimal(price), run))
                db.rows(conn, "select * from run_nightly(%s)", (run,))
                cushion, vs_call, flags = db.rows(conn, """
                    select s.cushion, s.dist_to_call,
                           coalesce((select string_agg(e.flag || ' (' || e.kind || ' ' || e.obs_date || ')', ', ')
                                     from events e where e.run_date = s.run_date and e.note_id = n.note_id), 'none')
                    from notes n join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
                    where n.cusip = '06749LM37'""", (run,))[0]
                rows.append([money(Decimal(price), cents=True), pct(cushion), pct(vs_call, signed=True), flags])
            print(table(["hpe_price", "cushion", "vs_call_trigger", "flags"], rows))
    finally:
        conn.rollback()
        conn.close()


def run_tests():
    print("Unit tests (no database):")
    suite = unittest.defaultTestLoader.discover(str(PROJECT_DIR / "tests"), pattern="test_*.py",
                                                top_level_dir=str(PROJECT_DIR))
    result = unittest.TextTestRunner(verbosity=1).run(suite)

    print("\nDatabase tests (tests/test_nightly.sql, rolled back afterwards):")
    conn = db.connect()
    try:
        db.run_file(conn, PROJECT_DIR / "tests" / "test_nightly.sql")
        print("All database tests passed.")
        db_ok = True
    except Exception as e:
        if not db.is_db_error(e):
            raise
        print(f"FAILED: {db.error_message(e)}")
        db_ok = False
    finally:
        conn.rollback()
        conn.close()

    if not (result.wasSuccessful() and db_ok):
        raise DeskError("Some tests failed.")
