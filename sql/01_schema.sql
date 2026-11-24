-- Notes desk: core schema
-- One database is the source of truth. Reference data, positions,
-- market data and the nightly job's outputs all live here.

-- Reference data -----------------------------------------------------------

-- What a note is linked to: usually an index, sometimes a single stock.
create table indices (
    index_code text primary key,            -- 'SPX', 'NDX', 'HPE', ...
    name       text not null
);

create table issuers (
    issuer_id serial primary key,
    name      text not null unique
);

create table advisors (
    advisor_id    serial primary key,
    name          text not null,
    firm          text not null,
    max_index_pct numeric not null default 0.65   -- advisor's own concentration limit
);

create table accounts (
    account_id serial primary key,
    advisor_id int  not null references advisors,
    household  text not null
);

-- Notes and their terms ----------------------------------------------------

create table notes (
    note_id       serial primary key,
    cusip         text not null unique,
    issuer_id     int  not null references issuers,
    index_code    text not null references indices,
    note_type     text not null check (note_type in ('income', 'buffered', 'trigger_growth', 'protected')),
    trade_date    date not null,
    maturity_date date not null,    -- when the money is paid back
    initial_level numeric not null check (initial_level > 0),

    -- Income notes
    coupon_rate        numeric,     -- annual, e.g. 0.085
    coupon_barrier_pct numeric,     -- coupon paid if underlier >= this; defaults to barrier_pct
    memory_coupon      boolean not null default false,  -- missed coupons paid later if back above barrier

    -- Income and trigger-growth notes: principal lost from the start if final < this
    barrier_pct   numeric,          -- e.g. 0.70 = barrier 30% below start

    -- Automatic call (any type): called if underlier >= this on a call date
    autocall_pct  numeric,          -- e.g. 1.00 = at or above start; null = not callable
    call_premium  numeric,          -- growth notes: fixed return paid if called, e.g. 0.098

    -- Growth notes (buffered, trigger growth, principal-protected)
    buffer_pct    numeric,          -- buffered only, e.g. 0.15 = bank absorbs the first 15% of loss
    downside_rate numeric,          -- buffered only: losses past the buffer are multiplied by this (default 1)
    participation numeric,          -- upside: 1.00 = matches the gain, 2.00 = double it
    cap_pct       numeric,          -- the most the note can gain in total, e.g. 0.59 = 59%

    source_url    text,             -- the SEC filing (or issuer term sheet) the terms came from

    -- The level (as % of start) where the investor starts losing money.
    -- Derived from the terms, so it can never disagree with them.
    protection_pct numeric generated always as (
        case note_type
            when 'income'         then barrier_pct
            when 'trigger_growth' then barrier_pct
            when 'buffered'       then 1 - buffer_pct
        end
    ) stored,

    status      text not null default 'live' check (status in ('live', 'called', 'matured')),
    closed_date date,

    check (maturity_date > trade_date),
    check (note_type <> 'income'         or (coupon_rate is not null and barrier_pct is not null)),
    check (note_type <> 'buffered'       or (buffer_pct is not null and participation is not null)),
    check (note_type <> 'trigger_growth' or (barrier_pct is not null and participation is not null)),
    check (note_type <> 'protected'      or participation is not null)
);

-- Every date on which a note's rules get checked.
create table observation_dates (
    note_id  int  not null references notes,
    obs_date date not null,
    kind     text not null check (kind in ('coupon', 'call', 'maturity')),
    primary key (note_id, obs_date, kind)
);
create index on observation_dates (obs_date);

-- Who owns what.
create table holdings (
    account_id int     not null references accounts,
    note_id    int     not null references notes,
    notional   numeric not null check (notional > 0),
    primary key (account_id, note_id)
);
create index on holdings (note_id);

-- Market data ----------------------------------------------------------------

create table market_data (
    index_code text    not null references indices,
    price_date    date    not null,
    close      numeric not null check (close > 0),
    primary key (index_code, price_date)
);

-- A record of every feed file loaded, so any number can be traced back.
create table feed_loads (
    load_id   serial primary key,
    run_date  date        not null,
    filename  text        not null,
    row_count int         not null,
    loaded_at timestamptz not null default now()
);

-- Outputs of the nightly job (one set of rows per run date) -------------------

create table note_daily_status (
    run_date     date    not null,
    note_id      int     not null references notes,
    index_level  numeric not null,
    perf         numeric not null,   -- index change since trade date
    cushion      numeric,            -- how far the index can fall before losses start
    coupon_cushion numeric,          -- income notes: how far it can fall before coupons stop
    dist_to_call numeric,            -- >= 0 means at or above the call trigger
    payout_per_1000 numeric,         -- what $1,000 would return if the note matured at today's level
                                     -- (income notes: principal only, coupons not included)
    primary key (run_date, note_id)
);

create table events (
    run_date date    not null,
    note_id  int     not null references notes,
    obs_date date    not null,
    kind     text    not null,
    flag     text    not null,
    notional numeric not null,
    primary key (run_date, note_id, obs_date, kind)
);

create table concentration_breaches (
    run_date   date    not null,
    advisor_id int     not null references advisors,
    index_code text    not null references indices,
    exposure   numeric not null,
    pct        numeric not null,
    limit_pct  numeric not null,
    primary key (run_date, advisor_id, index_code)
);

-- Job bookkeeping, so a missed or failed run is visible.
create table job_runs (
    run_id      serial primary key,
    job         text        not null,
    run_date    date        not null,
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    status      text        not null default 'running' check (status in ('running', 'ok', 'failed')),
    message     text
);
