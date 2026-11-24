-- Database tests for the nightly job, run against a hand-built note whose
-- answers are known in advance. `python3 notes_desk.py test` runs this inside
-- a transaction and rolls it back, so it is safe against a database with real
-- data. A failed check raises an error naming what went wrong.

-- A test index and one income note: starts at 100, barrier 70%, call trigger 100%.
insert into indices values ('TEST', 'Test index');
insert into notes (cusip, issuer_id, index_code, note_type, trade_date, maturity_date,
                   initial_level, coupon_rate, barrier_pct, autocall_pct)
values ('TEST00001', 1, 'TEST', 'income', '2026-01-02', '2027-12-31', 100, 0.10, 0.70, 1.00);
insert into observation_dates (note_id, obs_date, kind)
select note_id, d::date, 'call' from notes,
       (values ('2027-01-04'), ('2027-01-15')) v(d) where cusip = 'TEST00001';
insert into holdings (account_id, note_id, notional)
select 1, note_id, 250000 from notes where cusip = 'TEST00001';

-- A second note whose only call date, Jan 18 2027, is Martin Luther King Day:
-- no close that day, so the check must be taken at the next close (Jan 19).
insert into notes (cusip, issuer_id, index_code, note_type, trade_date, maturity_date,
                   initial_level, coupon_rate, barrier_pct, autocall_pct)
values ('TEST00002', 1, 'TEST', 'income', '2026-01-02', '2027-12-31', 100, 0.10, 0.70, 1.00);
insert into observation_dates (note_id, obs_date, kind)
select note_id, '2027-01-18', 'call' from notes where cusip = 'TEST00002';

-- Give every index a close on the test dates (real indices just repeat their last close).
insert into market_data (index_code, price_date, close)
select i.index_code, v.d::date,
       case when i.index_code = 'TEST' then v.lvl
            else coalesce((select close from market_data m where m.index_code = i.index_code
                           order by price_date desc limit 1),
                          (select max(initial_level) from notes n where n.index_code = i.index_code)) end
from indices i,
     (values ('2027-01-01', 77.0), ('2027-01-04', 103.0), ('2027-01-19', 105.0)) v(d, lvl)
on conflict do nothing;

do $$
declare
    s   note_daily_status;
    e   events;
    n1  bigint;
    n2  bigint;
    st  text;
begin
    -- Day 1: index at 77, so cushion = 77 / 70 - 1 = 10%, and 23% below the call trigger.
    perform run_nightly('2027-01-01');
    select ns.* into s from note_daily_status ns join notes n using (note_id)
    where n.cusip = 'TEST00001' and ns.run_date = '2027-01-01';
    assert round(s.cushion, 6) = 0.10,      format('cushion should be 10%%, got %s', s.cushion);
    assert round(s.dist_to_call, 6) = -0.23, format('distance to call should be -23%%, got %s', s.dist_to_call);

    -- A call check on Jan 4 is within 30 days but the index is below the trigger: no event.
    assert not exists (select 1 from events ev join notes n using (note_id)
                       where n.cusip = 'TEST00001' and ev.run_date = '2027-01-01'),
           'no call should be flagged while the index is below the trigger';

    -- Running the same day twice must give identical results.
    select count(*) into n1 from events where run_date = '2027-01-01';
    perform run_nightly('2027-01-01');
    select count(*) into n2 from events where run_date = '2027-01-01';
    assert n1 = n2, format('rerun changed event count from %s to %s', n1, n2);

    -- Day 2 (Jan 4): index at 103 on a call date, so the note is called today.
    perform run_nightly('2027-01-04');
    select status into st from notes where cusip = 'TEST00001';
    assert st = 'called', format('note should be called, status is %s', st);

    -- Once called, the Jan 15 call date must not show up as an upcoming event.
    assert not exists (select 1 from events ev join notes n using (note_id)
                       where n.cusip = 'TEST00001' and ev.run_date = '2027-01-04'),
           'a called note should have no upcoming events';

    -- Rerunning an earlier night after the call keeps the called note in it.
    perform run_nightly('2027-01-01');
    assert exists (select 1 from note_daily_status ns join notes n using (note_id)
                   where n.cusip = 'TEST00001' and ns.run_date = '2027-01-01'),
           'rerunning Jan 1 after the Jan 4 call dropped the note';
    select status into st from notes where cusip = 'TEST00001';
    assert st = 'called', format('rerunning an earlier night undid a later call: %s', st);

    -- Correcting the call date's price and rerunning it re-decides the call.
    update market_data set close = 95 where index_code = 'TEST' and price_date = '2027-01-04';
    perform run_nightly('2027-01-04');
    select status into st from notes where cusip = 'TEST00001';
    assert st = 'live', format('a corrected price below the trigger should undo the call, status is %s', st);
    update market_data set close = 103 where index_code = 'TEST' and price_date = '2027-01-04';
    perform run_nightly('2027-01-04');
    select status into st from notes where cusip = 'TEST00001';
    assert st = 'called', format('restoring the price should call it again, status is %s', st);

    -- The holiday check: no run on Jan 18, so the Jan 19 run takes it at 105.
    perform run_nightly('2027-01-19');
    select status into st from notes where cusip = 'TEST00002';
    assert st = 'called', format('a call date on a holiday should be taken at the next close, status is %s', st);
    assert (select closed_date from notes where cusip = 'TEST00002') = '2027-01-19',
           'the holiday call should be recorded on the day it was observed';

    -- Missing market data must stop the job rather than produce numbers.
    begin
        perform run_nightly('2027-02-01');
        raise exception 'job ran without market data';
    exception when raise_exception then
        assert sqlerrm like 'Missing closing levels%', 'unexpected error: ' || sqlerrm;
    end;

    -- Payout rules, checked against the real JPMorgan and Goldman terms.
    -- JPMorgan: 2x upside capped at $1,140.60, 10% buffer, 1.1111 downside rate.
    -- Goldman: $1,000 floor, 1x upside capped at $1,590.
    declare
        j notes;
        g notes;
    begin
        select * into j from notes where cusip = '46661PYC5';
        select * into g from notes where cusip = '40058LCA6';
        if j.note_id is not null then
            assert note_payout_per_1000(j, j.initial_level * 1.03) = 1060.00, 'JPM +3% should pay $1,060';
            assert note_payout_per_1000(j, j.initial_level * 1.20) = 1140.60, 'JPM +20% should hit the $1,140.60 cap';
            assert note_payout_per_1000(j, j.initial_level * 0.95) = 1000.00, 'JPM -5% is inside the buffer';
            assert note_payout_per_1000(j, j.initial_level * 0.80) = 888.89,  'JPM -20% should pay $888.89';
            assert note_payout_per_1000(g, g.initial_level * 1.20) = 1200.00, 'Goldman +20% should pay $1,200';
            assert note_payout_per_1000(g, g.initial_level * 1.80) = 1590.00, 'Goldman +80% should hit the $1,590 cap';
            assert note_payout_per_1000(g, g.initial_level * 0.50) = 1000.00, 'Goldman -50% is principal protected';
        end if;
    end;

    -- JPMorgan Russell 2000: 1.5x upside capped at $1,263, 10% buffer, 1-to-1 past it.
    declare
        r notes;
    begin
        select * into r from notes where cusip = '46661QDG7';
        if r.note_id is not null then
            assert note_payout_per_1000(r, r.initial_level * 1.10) = 1150.00, 'RTY +10% should pay $1,150';
            assert note_payout_per_1000(r, r.initial_level * 1.20) = 1263.00, 'RTY +20% should hit the $1,263 cap';
            assert note_payout_per_1000(r, r.initial_level * 0.85) = 950.00,  'RTY -15% should pay $950';
        end if;
    end;

end $$;
