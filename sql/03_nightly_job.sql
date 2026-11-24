-- The nightly job. All the business logic lives in one function so it runs
-- as a single transaction: it either finishes completely or changes nothing.
-- Rerunning it for the same date gives the same result.
--
--   select * from run_nightly('2026-11-20');

-- What $1,000 in a note returns at maturity if the underlier finishes at p_level.
-- The payout rules for every note type live here and nowhere else.
create or replace function note_payout_per_1000(n notes, p_level numeric)
returns numeric language sql immutable as $$
    select round(1000 * greatest(0, case n.note_type
        -- Income: full principal unless the underlier finishes below the barrier,
        -- then the full loss from the start. (Coupons are paid separately.)
        when 'income' then
            case when p_level >= n.initial_level * n.barrier_pct then 1
                 else p_level / n.initial_level end
        -- Buffered: upside x participation up to the cap; the bank absorbs losses
        -- down to the buffer; past it, losses (times the downside rate) hit the investor.
        when 'buffered' then
            case when p_level >= n.initial_level then
                     1 + least(n.participation * (p_level / n.initial_level - 1), coalesce(n.cap_pct, 1e9))
                 when p_level >= n.initial_level * (1 - n.buffer_pct) then 1
                 else 1 + (p_level / n.initial_level - 1 + n.buffer_pct) * coalesce(n.downside_rate, 1)
            end
        -- Trigger growth: upside x participation up to the cap; full principal at or
        -- above the barrier; below it, the full loss from the start.
        when 'trigger_growth' then
            case when p_level >= n.initial_level then
                     1 + least(n.participation * (p_level / n.initial_level - 1), coalesce(n.cap_pct, 1e9))
                 when p_level >= n.initial_level * n.barrier_pct then 1
                 else p_level / n.initial_level
            end
        -- Principal protected: never below $1,000; upside x participation up to the cap.
        when 'protected' then
            1 + least(n.participation * greatest(p_level / n.initial_level - 1, 0), coalesce(n.cap_pct, 1e9))
    end), 2)
$$;

create or replace function run_nightly(p_run_date date)
returns table (step text, rows_affected bigint)
language plpgsql as $$
declare
    n        bigint;
    missing  text;
    prev_run date;
begin
    -- 1. Refuse to run on incomplete data. Using yesterday's prices silently
    --    is worse than not running at all.
    select string_agg(i.index_code, ', ' order by i.index_code) into missing
    from indices i
    where not exists (select 1 from market_data m
                      where m.index_code = i.index_code and m.price_date = p_run_date);
    if missing is not null then
        raise exception 'Missing closing levels for % on %', missing, p_run_date;
    end if;

    -- The last night that ran before this one. Check dates since then are due
    -- tonight: a check that fell on a market holiday is taken at the next
    -- close, as the filings say. On the very first run, only today's are due.
    select max(run_date) into prev_run from note_daily_status where run_date < p_run_date;
    prev_run := coalesce(prev_run, p_run_date - 1);

    -- A rerun re-decides tonight's calls and maturities from scratch, so a
    -- corrected price can undo (or make) a call. Notes closed on a later night
    -- stay closed: rerun later nights in order to replay those.
    update notes set status = 'live', closed_date = null where closed_date = p_run_date;

    -- 2. Snapshot every note that was open on this date: live ones, plus any
    --    closed on a later night (so rerunning an old date still shows them).
    delete from note_daily_status where run_date = p_run_date;
    insert into note_daily_status (run_date, note_id, index_level, perf, cushion, coupon_cushion,
                                   dist_to_call, payout_per_1000)
    select p_run_date, nt.note_id, m.close,
           m.close / nt.initial_level - 1,
           m.close / (nt.initial_level * nt.protection_pct) - 1,
           case when nt.note_type = 'income' then
               m.close / (nt.initial_level * coalesce(nt.coupon_barrier_pct, nt.barrier_pct)) - 1 end,
           m.close / (nt.initial_level * nt.autocall_pct) - 1,
           note_payout_per_1000(nt, m.close)
    from notes nt
    join market_data m on m.index_code = nt.index_code and m.price_date = p_run_date
    where nt.closed_date is null or nt.closed_date > p_run_date;
    get diagnostics n = row_count;
    step := 'notes valued'; rows_affected := n; return next;

    -- 3. Apply the calls and maturities due tonight (since the last run).
    update notes nt
    set status = 'called', closed_date = p_run_date
    from observation_dates o
    join note_daily_status s on s.note_id = o.note_id and s.run_date = p_run_date
    where o.note_id = nt.note_id and o.obs_date > prev_run and o.obs_date <= p_run_date
      and o.kind = 'call' and s.dist_to_call >= 0 and nt.status = 'live';
    get diagnostics n = row_count;
    step := 'notes called today'; rows_affected := n; return next;

    -- The payout is fixed on the final valuation date (the 'maturity' check),
    -- a few days before the money is paid on maturity_date.
    update notes nt
    set status = 'matured', closed_date = p_run_date
    from observation_dates o
    where o.note_id = nt.note_id and o.obs_date > prev_run and o.obs_date <= p_run_date
      and o.kind = 'maturity' and nt.status = 'live';
    get diagnostics n = row_count;
    step := 'notes matured today'; rows_affected := n; return next;

    -- 4. Flag what is coming up in the next 30 days.
    delete from events where run_date = p_run_date;
    insert into events (run_date, note_id, obs_date, kind, flag, notional)
    select * from (
        select p_run_date, o.note_id, o.obs_date, o.kind,
               case
                   when o.kind = 'call'     and s.dist_to_call >= 0     then 'likely_call'
                   when o.kind = 'coupon'   and s.coupon_cushion < 0    then 'coupon_missed_if_today'
                   when o.kind = 'coupon'   and s.coupon_cushion < 0.10 then 'coupon_at_risk'
                   when o.kind = 'maturity' and s.cushion < 0           then 'maturing_with_loss'
                   when o.kind = 'maturity'                             then 'maturing'
               end as flag,
               (select sum(h.notional) from holdings h where h.note_id = o.note_id) as notional
        from observation_dates o
        join notes nt on nt.note_id = o.note_id
                     and (nt.closed_date is null or nt.closed_date > p_run_date)
        join note_daily_status s on s.note_id = o.note_id and s.run_date = p_run_date
        where o.obs_date > p_run_date and o.obs_date <= p_run_date + 30
    ) e
    where e.flag is not null and e.notional is not null;
    get diagnostics n = row_count;
    step := 'events flagged'; rows_affected := n; return next;

    -- 4b. Watch list: notes already below their buffer or barrier, even if
    --     maturity is far off (those within 30 days are flagged above).
    insert into events (run_date, note_id, obs_date, kind, flag, notional)
    select p_run_date, nt.note_id, o.obs_date, 'maturity', 'below_protection',
           (select sum(h.notional) from holdings h where h.note_id = nt.note_id)
    from notes nt
    join note_daily_status s on s.note_id = nt.note_id and s.run_date = p_run_date
    join observation_dates o on o.note_id = nt.note_id and o.kind = 'maturity'
    where (nt.closed_date is null or nt.closed_date > p_run_date)
      and s.cushion < 0 and o.obs_date > p_run_date + 30
      and exists (select 1 from holdings h where h.note_id = nt.note_id);
    get diagnostics n = row_count;
    step := 'notes below protection'; rows_affected := n; return next;

    -- 5. Concentration: each advisor's exposure to each index vs. their limit.
    --    Exposure is notional (the client money committed to that underlier),
    --    not market value: this demo has no secondary-market prices.
    delete from concentration_breaches where run_date = p_run_date;
    insert into concentration_breaches (run_date, advisor_id, index_code, exposure, pct, limit_pct)
    select p_run_date, x.advisor_id, x.index_code, x.amt, x.amt / x.total, ad.max_index_pct
    from (
        select a.advisor_id, nt.index_code, sum(h.notional) as amt,
               sum(sum(h.notional)) over (partition by a.advisor_id) as total
        from holdings h
        join accounts a using (account_id)
        join notes nt using (note_id)
        where nt.closed_date is null or nt.closed_date > p_run_date
        group by a.advisor_id, nt.index_code
    ) x
    join advisors ad using (advisor_id)
    where x.amt / x.total > ad.max_index_pct;
    get diagnostics n = row_count;
    step := 'concentration breaches'; rows_affected := n; return next;
end;
$$;
