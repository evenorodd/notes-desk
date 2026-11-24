"""The morning report and the every-note CSV, written after each nightly run."""

from . import REPORTS_DIR, csvfiles, db
from .textable import money, pct, table


def morning_report(conn, run_date):
    out = []

    def section(title, headers, rows, note=None):
        out.append(f"\n== {title} ==")
        if note:
            out.append(f"   {note}")
        out.append(table(headers, rows))

    live = db.rows(conn, """
        select count(distinct n.note_id), count(distinct h.account_id), sum(h.notional)
        from notes n join holdings h using (note_id) where n.status = 'live'""")[0]
    section("Live book", ["live_notes", "client_accounts", "live_notional"],
            [[live[0], live[1], money(live[2])]])

    rows = db.rows(conn, """
        select m.index_code, p.close, m.close, m.close / p.close - 1
        from market_data m
        cross join lateral (select close from market_data x
                            where x.index_code = m.index_code and x.price_date < m.price_date
                            order by x.price_date desc limit 1) p
        where m.price_date = %s and abs(m.close / p.close - 1) > 0.20
        order by abs(m.close / p.close - 1) desc""", (run_date,))
    if rows:
        section("Prices to double-check (moved more than 20% in a day)",
                ["underlier", "previous_close", "close", "move"],
                [[u, f"{a:,}", f"{b:,}", pct(m, True)] for u, a, b, m in rows],
                note="Real moves this size happen, but so do bad prices. If one is wrong, "
                     f"fix feeds/closes_{run_date}.csv and rerun this date.")

    rows = db.rows(conn, """
        select n.note_type, count(*), count(*) filter (where s.cushion < 0), avg(s.payout_per_1000)
        from notes n join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
        group by 1 order by 1""", (run_date,))
    section("Notes by type", ["type", "notes", "below_protection", "avg_per_1000_if_matured_today"],
            [[t, n, b, money(a, cents=True)] for t, n, b, a in rows],
            note=f"Full detail for every note: reports/notes_{run_date}.csv")

    rows = db.rows(conn, """
        select n.cusip, n.note_type, n.index_code, s.perf, s.cushion, s.payout_per_1000, n.maturity_date
        from notes n join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
        where s.cushion is not null order by s.cushion limit 15""", (run_date,))
    section("Notes closest to losing principal (lowest cushion)",
            ["cusip", "type", "linked_to", "since_start", "cushion", "per_1000_if_matured_today", "matures"],
            [[c, t, u, pct(p, True), pct(cu, True), money(v, cents=True), m] for c, t, u, p, cu, v, m in rows])

    rows = db.rows(conn, """
        select flag, count(*), sum(notional) from events where run_date = %s
        group by flag order by sum(notional) desc""", (run_date,))
    section("Events in the next 30 days, plus notes already below protection",
            ["flag", "notes", "notional"], [[f, n, money(v)] for f, n, v in rows])

    rows = db.rows(conn, """
        select e.obs_date, n.cusip, i.name, n.index_code, s.dist_to_call, e.notional
        from events e join notes n using (note_id) join issuers i using (issuer_id)
        join note_daily_status s on s.note_id = e.note_id and s.run_date = e.run_date
        where e.run_date = %s and e.flag = 'likely_call'
        order by e.obs_date, e.notional desc limit 10""", (run_date,))
    section("Likely calls (cash coming back)",
            ["obs_date", "cusip", "issuer", "linked_to", "above_trigger", "notional"],
            [[d, c, i, u, pct(a), money(v)] for d, c, i, u, a, v in rows])

    rows = db.rows(conn, """
        select e.obs_date, n.cusip, n.index_code, e.flag, s.coupon_cushion, e.notional
        from events e join notes n using (note_id)
        join note_daily_status s on s.note_id = e.note_id and s.run_date = e.run_date
        where e.run_date = %s and e.flag in ('coupon_at_risk', 'coupon_missed_if_today')
        order by s.coupon_cushion limit 10""", (run_date,))
    section("Coupons at risk or missed at today's price",
            ["obs_date", "cusip", "linked_to", "flag", "above_coupon_barrier", "notional"],
            [[d, c, u, f, pct(cc, True), money(v)] for d, c, u, f, cc, v in rows])

    rows = db.rows(conn, """
        select a.name, c.index_code, c.pct, c.limit_pct
        from concentration_breaches c join advisors a using (advisor_id)
        where c.run_date = %s order by c.pct desc limit 5""", (run_date,))
    section("Concentration breaches", ["advisor", "linked_to", "exposure", "limit"],
            [[a, u, pct(p, places=0), pct(l, places=0)] for a, u, p, l in rows])

    return "\n".join(out) + "\n"


NOTES_REPORT_COLUMNS = ["cusip", "issuer", "note_type", "underlier", "trade_date", "maturity_date",
                        "initial_level", "close", "since_start", "cushion", "coupon_cushion",
                        "vs_call_trigger", "payout_per_1000", "next_check", "source_url"]


def notes_csv_rows(conn, run_date):
    return db.rows(conn, """
        select n.cusip, i.name, n.note_type, n.index_code, n.trade_date, n.maturity_date,
               n.initial_level, s.index_level, round(s.perf, 4), round(s.cushion, 4),
               round(s.coupon_cushion, 4), round(s.dist_to_call, 4), s.payout_per_1000,
               (select min(o.obs_date) from observation_dates o
                where o.note_id = n.note_id and o.obs_date > %s),
               n.source_url
        from notes n join issuers i using (issuer_id)
        join note_daily_status s on s.note_id = n.note_id and s.run_date = %s
        order by s.cushion nulls last""", (run_date, run_date))


def write_reports(conn, run_date):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    morning = REPORTS_DIR / f"morning_{run_date}.txt"
    morning.write_text(morning_report(conn, run_date))
    notes = REPORTS_DIR / f"notes_{run_date}.csv"
    csvfiles.write_rows(notes, NOTES_REPORT_COLUMNS, notes_csv_rows(conn, run_date))
    return morning, notes
