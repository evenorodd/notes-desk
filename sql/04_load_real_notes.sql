-- Staging tables and the functions that check and load notes.
--
-- The Python app reads the notes CSV files (and, for add-notes, builds each
-- note's check dates from its schedule), puts the rows in stage_notes and
-- stage_obs, then calls:
--
--   real_notes_underliers()  adds each note's underlier to the indices table
--   check_real_notes()       returns a list of problems, each starting with the CUSIP
--   load_real_notes()        loads everything, or nothing if there are any problems

set client_min_messages = warning;
drop table if exists stage_notes, stage_obs;

create table stage_notes (
    cusip text, underlier text, underlier_name text, issuer text, note_type text,
    trade_date date, maturity_date date, initial_level numeric,
    coupon_rate numeric, coupon_barrier_pct numeric, barrier_pct numeric,
    buffer_pct numeric, downside_rate numeric, participation numeric, cap_pct numeric,
    autocall_pct numeric, call_premium numeric, memory_coupon boolean, source_url text
);

create table stage_obs (cusip text, obs_date date, kind text);

-- Adds each staged note's underlier to the indices table (before prices load).
create or replace function real_notes_underliers() returns int language sql as $$
    with ins as (
        insert into indices (index_code, name)
        select distinct on (underlier) underlier, underlier_name
        from stage_notes where underlier is not null
        order by underlier
        on conflict (index_code) do nothing
        returning 1
    ) select count(*)::int from ins
$$;

create or replace function check_real_notes() returns text[] language plpgsql as $$
declare
    problems text[] := '{}';
    p text;
begin
    -- Observation dates that fall on a weekend move to the next Monday
    -- (filings say a check on a non-trading day moves to the next trading day).
    update stage_obs set obs_date = obs_date + (8 - extract(isodow from obs_date))::int
    where extract(isodow from obs_date) in (6, 7);

    for p in
        -- Checks on each note
        select cusip || ': appears more than once' from stage_notes group by cusip having count(*) > 1
        union all
        select s.cusip || ': is already in the database' from stage_notes s
         where exists (select 1 from notes n where n.cusip = s.cusip)
        union all
        select coalesce(cusip, '(blank)') || ': CUSIP must be 9 characters' from stage_notes
         where cusip is null or length(cusip) <> 9
        union all
        select cusip || ': unknown note_type "' || coalesce(note_type, '') || '"' from stage_notes
         where note_type is null or note_type not in ('income', 'buffered', 'trigger_growth', 'protected')
        union all
        select cusip || ': missing underlier, issuer, trade_date or maturity_date' from stage_notes
         where underlier is null or issuer is null or trade_date is null or maturity_date is null
        union all
        select cusip || ': maturity_date must be after trade_date' from stage_notes
         where maturity_date <= trade_date
        union all
        select cusip || ': income notes need coupon_rate and barrier_pct' from stage_notes
         where note_type = 'income' and (coupon_rate is null or barrier_pct is null)
        union all
        select cusip || ': buffered notes need buffer_pct and participation' from stage_notes
         where note_type = 'buffered' and (buffer_pct is null or participation is null)
        union all
        select cusip || ': trigger_growth notes need barrier_pct and participation' from stage_notes
         where note_type = 'trigger_growth' and (barrier_pct is null or participation is null)
        union all
        select cusip || ': protected notes need participation' from stage_notes
         where note_type = 'protected' and participation is null
        union all
        select cusip || ': a percentage is out of range (enter 10% as 0.10)' from stage_notes
         where coupon_rate not between 0 and 1 or coupon_barrier_pct not between 0.1 and 1.5
            or barrier_pct not between 0.1 and 1.5 or buffer_pct not between 0 and 1
            or autocall_pct not between 0.5 and 1.5 or cap_pct not between 0 and 10
            or participation not between 0 and 10 or call_premium not between 0 and 5
            or downside_rate not between 1 and 5
        union all
        select cusip || ': initial_level must be positive' from stage_notes where initial_level <= 0
        union all
        select cusip || ': initial_level is required (the starting level from the filing)'
          from stage_notes where initial_level is null
        -- Checks on the observation dates
        union all
        select distinct o.cusip || ': has observation dates but no note' from stage_obs o
         where not exists (select 1 from stage_notes s where s.cusip = o.cusip)
        union all
        select o.cusip || ': unknown observation kind "' || coalesce(o.kind, '') || '"' from stage_obs o
         where o.kind is null or o.kind not in ('coupon', 'call', 'maturity')
        union all
        select o.cusip || ': observation date ' || o.obs_date || ' is outside its trade and maturity dates'
          from stage_obs o join stage_notes s using (cusip)
         where o.obs_date <= s.trade_date or o.obs_date > s.maturity_date
        union all
        select s.cusip || ': needs exactly one maturity observation date' from stage_notes s
         where (select count(*) from stage_obs o where o.cusip = s.cusip and o.kind = 'maturity') <> 1
        union all
        select s.cusip || ': income note has no coupon dates' from stage_notes s
         where s.note_type = 'income'
           and not exists (select 1 from stage_obs o where o.cusip = s.cusip and o.kind = 'coupon')
        union all
        select s.cusip || ': has an autocall_pct but no call dates, or call dates but no autocall_pct'
          from stage_notes s
         where (s.autocall_pct is not null)
               <> exists (select 1 from stage_obs o where o.cusip = s.cusip and o.kind = 'call')
        union all
        select o.cusip || ': ' || o.obs_date || ' ' || o.kind || ' is listed more than once'
          from stage_obs o group by o.cusip, o.obs_date, o.kind having count(*) > 1
    loop
        problems := problems || p;
    end loop;
    return problems;
end;
$$;

create or replace function load_real_notes() returns text language plpgsql as $$
declare
    problems text[];
    n_notes int;
    n_obs int;
begin
    perform real_notes_underliers();
    problems := check_real_notes();
    if cardinality(problems) > 0 then
        raise exception E'Notes not loaded. Fix these and try again:\n  %',
            array_to_string(problems, E'\n  ');
    end if;

    insert into issuers (name)
    select distinct issuer from stage_notes
    on conflict (name) do nothing;

    insert into notes (cusip, issuer_id, index_code, note_type, trade_date, maturity_date,
                       initial_level, coupon_rate, coupon_barrier_pct, memory_coupon, barrier_pct,
                       autocall_pct, call_premium, buffer_pct, downside_rate, participation,
                       cap_pct, source_url)
    select s.cusip, i.issuer_id, s.underlier, s.note_type, s.trade_date, s.maturity_date,
           s.initial_level, s.coupon_rate, s.coupon_barrier_pct, coalesce(s.memory_coupon, false), s.barrier_pct,
           s.autocall_pct, s.call_premium, s.buffer_pct, s.downside_rate, s.participation,
           s.cap_pct, s.source_url
    from stage_notes s
    join issuers i on i.name = s.issuer;
    get diagnostics n_notes = row_count;

    insert into observation_dates (note_id, obs_date, kind)
    select n.note_id, o.obs_date, o.kind
    from stage_obs o join notes n using (cusip);
    get diagnostics n_obs = row_count;

    -- One example household holding $100,000 of each note.
    insert into accounts (advisor_id, household)
    select (select min(advisor_id) from advisors), 'Example household'
    where not exists (select 1 from accounts where household = 'Example household');

    insert into holdings (account_id, note_id, notional)
    select a.account_id, n.note_id, 100000
    from accounts a
    join notes n on n.cusip in (select cusip from stage_notes)
    where a.household = 'Example household';

    drop table stage_notes, stage_obs;
    return format('Loaded %s notes and %s observation dates', n_notes, n_obs);
end;
$$;
