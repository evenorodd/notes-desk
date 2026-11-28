-- Model values (desk/valuation.py). Safe to run any number of times: setup
-- runs it, and so does every pricing run, so an existing database gets these
-- tables without a rebuild.

-- Treasury par yields, from feeds/rates_YEAR.csv (fetch-rates).
create table if not exists treasury_rates (
    rate_date   date    not null,
    tenor_years numeric not null,     -- 0.0833 = 1 month ... 30
    yield       numeric not null,     -- 0.0412 = 4.12%
    primary key (rate_date, tenor_years)
);

-- Which rate and history files have been loaded (name, size, time changed),
-- so each is loaded again only when it changes.
create table if not exists pricing_input_files (
    filename text primary key,
    signature text not null
);

-- Each note's spread over Treasuries: solved on its trade date so the model
-- matches the issuer's estimated value (real_notes/estimated_values.csv).
create table if not exists note_calibrations (
    note_id         int primary key references notes on delete cascade,
    estimated_value numeric,          -- per $1,000, from the filing
    spread          numeric not null, -- 0.012 = 1.2% a year over Treasuries
    source          text not null,    -- 'filing', or 'issuer average' when the filing gave none
    vol             numeric,          -- the inputs it was solved with
    rate            numeric,
    calibrated_at   timestamptz not null default now()
);

-- One row per note per night: the model value and how it would move.
create table if not exists note_valuations (
    run_date     date    not null,
    note_id      int     not null references notes on delete cascade,
    model_value  numeric not null,    -- per $1,000
    std_error    numeric,             -- Monte Carlo standard error (0 when exact)
    delta_1pct   numeric,             -- value change per 1% move in the underlier, per $1,000
    vega_1pt     numeric,             -- value change per volatility point, per $1,000
    vol          numeric,             -- the inputs used
    div_yield    numeric,
    rate         numeric,
    spread       numeric,
    method       text    not null,    -- 'exact' or 'monte carlo'
    primary key (run_date, note_id)
);
