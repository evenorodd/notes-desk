# Notes desk

A working example of a nightly monitoring system for a book of structured
notes: a Python app on one PostgreSQL database, with a report and a website
that show which notes need attention.

It tracks **75 real notes** whose terms come from SEC-filed pricing
supplements (424B2), valued every night against real closing prices.

**New here? Start with [OVERVIEW.md](OVERVIEW.md):** a one-page map of the
folders, commands, tables and reports, with a diagram.

## Quick start with Docker (Mac, Windows or Linux)

The easiest way to run it: nothing to install but Docker.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Get the code (see [Getting and updating the code](#getting-and-updating-the-code)
   for Git on Windows, Mac and Linux), then start it from the project folder:
   ```bash
   git clone https://github.com/evenorodd/notes-desk.git
   cd notes-desk
   docker compose up --build
   ```
3. Open **http://localhost:8000**.

The first start takes a minute or two: it downloads PostgreSQL and Python,
builds the database, loads the 75 notes and runs the nightly job for every
price file in `feeds/`. Later starts take seconds.

| To | Run |
|---|---|
| Stop it (the database is kept) | `Ctrl+C`, or `docker compose down` |
| Start it again | `docker compose up` |
| Fetch today's closes and run the nightly job | `docker compose exec app python3 notes_desk.py nightly --fetch` |
| Run any other command below | `docker compose exec app python3 notes_desk.py <command>` |
| Start over with a fresh database | `docker compose down -v`, then `docker compose up` |

The website is only reachable from your own computer. Reports and price files
appear in your `reports/` and `feeds/` folders as usual.

**Each trading night** (after 4:20 pm New York time), with Docker Desktop
running, open a terminal in the project folder (on Windows: type `powershell`
in File Explorer's address bar there) and run:

```bash
docker compose up -d                                           # start it, if it isn't running
docker compose exec app python3 notes_desk.py nightly --fetch  # today's closes, the job, the reports
```

Then refresh http://localhost:8000, or open the new file in `reports/`. "Today"
means today in New York, whatever your computer's time zone. To pick up new
code and price files from GitHub instead, see
[Updating](#3-updating-to-the-latest-version).

To schedule it on Windows, create a Task Scheduler task for weekdays at 6:15
pm Eastern that runs `docker` with the arguments
`compose -f C:\path\to\notes-desk\compose.yaml exec -T app python3 notes_desk.py nightly --fetch`
(Docker Desktop must be running).

## Getting and updating the code

The code lives at **https://github.com/evenorodd/notes-desk**. Git gets it
once and then updates it in place with one command. That matters with Docker:
it names the app after its folder, so a fresh copy in a new folder starts with
a new, empty database instead of carrying on.

### 1. Install Git (once)

| System | How |
|---|---|
| **Windows** | In PowerShell: `winget install --id Git.Git -e`, or the installer from [git-scm.com](https://git-scm.com/download/win) with its default options. Then close and reopen PowerShell. |
| **Mac** | In Terminal, type `git --version`. If Git isn't there, macOS offers to install the Command Line Developer Tools, which include it. (Or `brew install git` if you use Homebrew.) |
| **Linux** | Fedora: `sudo dnf install git`. Ubuntu or Debian: `sudo apt install git`. |

Check with `git --version`.

Prefer buttons to commands? [GitHub Desktop](https://desktop.github.com/) (Windows
and Mac) does the same: **File → Clone repository → URL** to get the code, and
**Fetch origin**, then **Pull origin**, to update. The Docker commands below
still run in a terminal.

### 2. Get the code (once)

Open a terminal where you want the project (PowerShell on Windows, Terminal on
a Mac) and run:

```bash
git clone https://github.com/evenorodd/notes-desk.git
cd notes-desk
```

This makes a `notes-desk` folder. Run every other command from inside it. On
Windows, a quick way to open PowerShell there later is to type `powershell` in
File Explorer's address bar while in the folder.

### 3. Updating to the latest version

From the project folder:

```bash
git pull                                                        # new code and price files
docker compose up --build -d                                    # rebuild the app with the new code
docker compose exec app python3 notes_desk.py backfill --no-download   # load any new nights
```

- `git pull` downloads only what changed. Your database, reports and anything
  you've run locally are untouched.
- `--build` is needed because the container runs a copy of the code made when
  it was built; the database is kept.
- `backfill --no-download` replays every night from the price files in
  `feeds/`, so nights added on GitHub show up. It takes seconds and is safe to
  repeat.

**Without Docker,** it's `git pull`, then restart `serve` if it's running, and
run `python3 notes_desk.py backfill --no-download` for new price files. The
nightly job reloads its database logic itself, so no setup is needed after an
update.

**Without Git (downloading the ZIP).** This works too; Git just makes updating
quicker. On the GitHub page, click **Code → Download ZIP**, then:

| | Windows | Mac |
|---|---|---|
| Unzip | Right-click the ZIP in Downloads → **Extract All** | Safari usually unzips it for you; otherwise double-click it |
| Open a terminal in the folder | Open the `notes-desk-main` folder, type `powershell` in the address bar, press Enter | Open **Terminal**, type `cd ` (with a space), drag the `notes-desk-main` folder onto the window, press Enter |

Then, with Docker Desktop running, the same commands on both:

```
docker compose up --build -d
docker compose exec app python3 notes_desk.py backfill --no-download
```

and open http://localhost:8000. The second command is only needed for an
update; the first start builds the database by itself.

To update later, download the ZIP again and repeat. It's fine if the new copy
unzips into a differently named folder (`notes-desk-main (1)`,
`notes-desk-main 2`): `compose.yaml` gives the app a fixed name, so Docker
keeps using the same database. Any price files you fetched yourself are in the
old folder's `feeds/`, so copy them across, or just run `nightly --fetch` again.

If `git pull` says your local changes would be overwritten, you've edited a
file that the update also changes. `git stash` sets your edits aside, then
`git pull` again (`git stash pop` brings your edits back).

## Running it without Docker

Needs Python 3.9 or later, PostgreSQL, and the psycopg driver (setup on
Fedora is below). Then:

```bash
python3 notes_desk.py setup                       # build the database (reads my_history.csv)
python3 notes_desk.py fetch-prices 2026-11-23     # download that day's closes into the price file
python3 notes_desk.py price-template 2026-11-23   # or: a blank price file to type in
python3 notes_desk.py nightly 2026-11-23          # load the prices, run the job, write the reports
python3 notes_desk.py nightly --fetch             # fetch today's closes and run, in one step
python3 notes_desk.py backfill 2026-01-01         # download history and replay every night since then
python3 notes_desk.py add-notes new_notes.csv     # add notes, building their check dates
python3 notes_desk.py serve                       # the website, at http://localhost:8000
python3 notes_desk.py export                      # save the website as files to share
python3 notes_desk.py mcp                         # tools for AI assistants (see "Ask Claude about the book")
python3 notes_desk.py mcp --allow-runs            # the same, plus a tool that runs the nightly job
python3 notes_desk.py what-if                     # example payouts and flags (changes nothing)
python3 notes_desk.py test                        # run the tests (changes nothing)
```

Run any command with `-h` for its options.

*A demonstration project, not investment advice. Note terms are from public
SEC filings; prices are from public sources and may contain errors.*

## The notes

| | Notes | Underliers |
|---|---|---|
| Buffered growth | 50 | S&P 500, Nasdaq-100, Russell 2000 |
| Income (contingent coupon, autocallable) | 15 | AAPL, AMZN, COIN, DELL, GNRC, HPE, LRCX, MSFT, MU, NVDA, PLTR, SPOT |
| Principal protected | 10 | S&P 500 |

Issuers: JPMorgan, BofA, Barclays, UBS, Bank of Nova Scotia, Royal Bank of
Canada, Goldman Sachs and Morgan Stanley. Every note is linked to a single
index or stock; "worst-of" notes on several underliers aren't supported yet.

- **Terms** are in `real_notes/notes.csv`, one row per note, with a link to its
  filing in the `source_url` column.
- **Check dates** (every coupon, call and maturity date) are in
  `real_notes/observation_dates.csv`.
- **Holdings** are hypothetical: one example household holds $100,000 of each.

All notes were live on 2026-11-20. Autocallable notes were included only if
their first call date is after that, so none can already have been called.

## How it's organised

The database does the financial work; Python handles files, scheduling and reports.

| File | What it does |
|---|---|
| `notes_desk.py` | The command you run |
| `desk/cli.py` | Commands and their options |
| `desk/notes.py` | `setup` and `add-notes`: read the CSV files, stage them, load them |
| `desk/schedule.py` | Builds a note's check dates from its schedule |
| `desk/prices.py` | Checks and stores closing prices |
| `desk/nightly.py` | `nightly` and `price-template`: locking, job log, alerts |
| `desk/fetch.py` | `fetch-prices`: gets closes from Yahoo Finance's daily price feed |
| `desk/web.py` | `serve`: the local website (dashboard, notes, one page per note, runs) |
| `desk/report.py` | The morning report and the every-note CSV |
| `desk/whatif.py` | `what-if` and `test` |
| `desk/csvfiles.py`, `desk/db.py`, `desk/textable.py` | CSV reading, database connection, text tables |
| `sql/01_schema.sql` | Tables: notes and terms, check dates, holdings, prices, and the job's outputs |
| `sql/02_reference_data.sql` | The example advisor the household belongs to |
| `sql/03_nightly_job.sql` | `note_payout_per_1000()`: payout rules for every note type. `run_nightly(date)`: the nightly job |
| `sql/04_load_real_notes.sql` | Checks and loads notes (used by `setup` and `add-notes`) |
| `real_notes/` | The notes, their check dates, and a template for new notes |
| `my_history.csv` | Your starting prices, filled in before setup |
| `tests/` | Unit tests (`test_*.py`) and database tests (`test_nightly.sql`) |

## Installing

### 1. Install PostgreSQL and the Python driver

Run these one line at a time.

- **Fedora:**
  ```bash
  sudo dnf install postgresql-server postgresql python3-psycopg3
  sudo postgresql-setup --initdb
  sudo systemctl enable --now postgresql
  ```
- **Ubuntu or Debian:** `sudo apt install postgresql python3-psycopg2`
- **Mac:** `brew install postgresql@16`, `brew services start postgresql@16`,
  then `python3 -m pip install "psycopg[binary]"`
- **Windows:** install WSL (`wsl --install` in PowerShell), open Ubuntu, then follow the Ubuntu steps

Python 3.9 or newer is needed (Fedora 43 has it). The app works with either
psycopg 3 or psycopg2; it uses whichever is installed.

### 2. Give your own account access to PostgreSQL

On Linux (Fedora, Ubuntu, WSL), create a database user with your login name
(a "could not change directory" warning here is harmless):

```bash
sudo -u postgres createuser --superuser $USER
```

Mac with Homebrew already has a user with your login name, so skip that.

The app connects through the local socket as your login user, with no
password. If you've set `PGHOST` or `PGUSER` in your terminal, clear them with
`unset PGHOST PGUSER`: `PGHOST=localhost` switches to a network connection
that Fedora rejects by default. The database is called `notes_desk` unless you
set `PGDATABASE`.

### 3. Get the code

See [Getting and updating the code](#getting-and-updating-the-code) for
installing Git on your system. Then:

```bash
git clone https://github.com/evenorodd/notes-desk.git && cd notes-desk
python3 notes_desk.py test       # optional: unit tests run now; database tests need setup first
```

## Running it

All prices come from CSV files in this format, one row per underlier per date:

```csv
index_code,price_date,close
SPX,2026-11-20,6910.39
AAPL,2026-11-20,351.20
```

Rules: the header must be exactly `index_code,price_date,close`; codes in
capitals; dates as `YYYY-MM-DD`; prices as plain numbers (`6910.39`, not
`$6,910.39`). Use the index itself, not its futures (SPX, not ES). Files saved
from Excel or LibreOffice are fine.

### First run, for Fri 2026-11-20

1. **Check `my_history.csv`.** It comes filled in with the real 2026-11-19
   close for all 15 underliers, the trading day before the first run: the
   nightly load checks each close against the previous one. (Each note's
   starting level is part of its terms in `real_notes/notes.csv`, so no older
   prices are needed.) Compare them with your own data and correct any that
   differ.

   Setup stops and lists any price that's blank. Extra history is fine.

2. **Set up:**

   ```bash
   python3 notes_desk.py setup
   ```

3. **The night's price file is already there:** `feeds/closes_2026-11-20.csv`
   holds the real 2026-11-20 closes. (For later dates, `price-template` makes a
   blank one; see below.)

4. **Run the night:**

   ```bash
   python3 notes_desk.py nightly 2026-11-20
   cat reports/morning_2026-11-20.txt
   ```

### Each later day

After the market closes (closes are final from 4:20 pm New York time):

```bash
python3 notes_desk.py fetch-prices 2026-11-23      # writes feeds/closes_2026-11-23.csv
python3 notes_desk.py nightly 2026-11-23
```

or both in one step: `python3 notes_desk.py nightly 2026-11-23 --fetch`.

No need to rerun setup. A run stops, is marked failed in `job_runs`, and raises
an alert if the price file is missing, any price is blank, an underlier is
missing or unknown. A close more than 20% from the previous one doesn't stop the
run, since real moves that size happen: it's loaded, an alert lists it, and the
morning report opens with a "Prices to double-check" section. If one turns out
to be wrong, fix the price file and rerun the date. Rerunning a date is safe: it
replaces that date's results, including re-deciding any call or maturity that
night (so a corrected price can undo a call that a bad one triggered). Notes
called or matured on a later night still appear in an earlier night's results.
Rerunning an older night also replays every later night automatically, from
the prices already loaded, and rewrites their reports: a corrected price can
move a call to an earlier night, and the later nights are decided again on top
of it.

A check date with no close (a market holiday) is taken at the next night's
close, as the filings specify: each run applies every call and maturity check
dated since the previous run.

Setup rebuilds the database, so it reloads only the prices in `my_history.csv`;
put any history you want to keep there. If setup says the database is open
somewhere else, disconnect pgAdmin (right-click the server, Disconnect) first.

### Fetching prices automatically

`fetch-prices` gets each underlier's close from Yahoo Finance's daily price
feed (free, no account or key) and writes `feeds/closes_<date>.csv`, then shows
what it got:

```
 underlier | yahoo_symbol |     close
-----------+--------------+-----------
 AAPL      | AAPL         |    342.56
 ...
 RTY       | ^RUT         |  2,581.79
 SPX       | ^GSPC        |  6,910.39
```

- **With no date** it uses the latest trading day that has closes.
- **Indices use Yahoo's symbols:** SPX is `^GSPC`, NDX is `^NDX`, RTY is
  `^RUT` (the index, not the futures). Stocks use their ticker. If you add
  notes on another index, add its Yahoo symbol to `YAHOO_SYMBOLS` at the top of
  `desk/fetch.py`.
- **Anything it can't get is left blank** and listed, so you can type it in.
  The nightly run refuses a blank price, so nothing is guessed.
- **On a weekend or holiday** it stops with "No closes for <date>".
- **It won't overwrite** a price file that already exists (so a file you've
  corrected by hand is safe); add `--force` to replace it.
- **Fetched prices get the same checks as typed ones**, including the flag for
  a move of more than 20% from the previous close.

`nightly --fetch` fetches first if the day's price file doesn't exist yet, then
runs. If the fetch fails, or leaves any price blank, the run is marked failed
and an alert is raised; fill in the blanks and run `nightly <date>` again.

Yahoo's feed isn't an official data service: Yahoo can change or block it
without notice, and its terms are for personal use. A desk in production would
use a licensed feed (the issuer's, Bloomberg, ICE, or a paid API). To switch,
replace `daily_closes()` in `desk/fetch.py`; it only has to return
`{date: close}` for one symbol.

Without the feed, `price-template <date>` writes a blank price file to fill in
by hand from any source (FRED's `SP500` series, or Yahoo or Nasdaq.com
historical data).

## The reports

Each run writes two files to `reports/`:

- **`morning_<date>.txt`**: the live book; notes by type; the 15 notes closest
  to losing principal; every check in the next 30 days (calls likely
  or not, coupons on track, at risk or missed, maturities) plus notes already below their buffer or barrier;
  and concentration breaches.
- **`notes_<date>.csv`**: every note's close, cushion, distance to its call
  trigger, value per $1,000 if it matured today, next check date and filing
  link. Opens in any spreadsheet. Columns are explained below.

### The columns in `notes_<date>.csv`

One row per note, sorted with the notes closest to losing principal first (the
morning report's "closest to losing principal" table is the top 15 rows).
Percentages are decimals: `0.2019` means 20.19%.

| Column | What it is |
|---|---|
| `cusip` | The note's 9-character ID from its SEC filing |
| `issuer` | The bank that issued it and owes the money |
| `note_type` | `income` (contingent coupon), `buffered`, `trigger_growth` or `protected` |
| `underlier` | What the note is linked to: SPX, NDX, RTY or a stock such as AAPL |
| `trade_date` | The day the note was priced and its starting level was set |
| `maturity_date` | The day it pays back (a few days after the final valuation date) |
| `initial_level` | The starting level from the filing; every percentage below is measured from it |
| `close` | The underlier's close that night, from `feeds/closes_<date>.csv` |
| `since_start` | close ÷ initial_level − 1: the plain move since the trade date |
| `cushion` | How much further the underlier can fall before principal is lost at maturity, measured to the barrier (income) or the buffer (buffered). Positive is safe, negative is underwater. Blank for protected notes, which can't lose principal |
| `coupon_cushion` | Income notes only: how far above the coupon barrier. Negative on a coupon date means no coupon |
| `vs_call_trigger` | Callable notes only: distance to the autocall level. Zero or positive means it would be called on a call date. Blank if the note can't be called |
| `payout_per_1000` | What each $1,000 would pay if that night were the final valuation date: principal plus any upside, not counting coupons |
| `next_check` | The next coupon, call or final valuation date |
| `source_url` | The SEC filing the terms came from |

Examples from the 2026-11-20 run:

- **46661MLN2** (JPM, buffered, NDX, 10% buffer): starts at 30,408.50 and
  closes at 25,028.98, so `since_start` is −0.1769. The buffer absorbs the
  first 10%, so `cushion` is −0.0855 (underwater). `payout_per_1000` is
  923.09, losing about the 7.69% beyond the buffer.
- **46661PYC5** (JPM, buffered, SPX, 10% buffer, downside rate 1.1111): starts
  at 7,704.13 and closes at 6,910.39, so `since_start` is −0.1030, just past
  the buffer, and `cushion` is −0.0034. The 0.30% past the buffer times 1.1111
  costs about 0.336%, so `payout_per_1000` is 996.64.
- **90266FEM4** (UBS, income, AAPL, 80% barrier): starts at 356.27 and closes
  at 342.56, so `since_start` is −0.0385. `cushion` and `coupon_cushion` are
  both 0.2019, since AAPL can fall another 20% before either barrier.
  `vs_call_trigger` is −0.0385: AAPL must get back to its starting level to
  be called at the next check, 2027-02-16. `payout_per_1000` is 1000.

## Adding new notes

1. **Make your file** from the template, which has only the heading row:

   ```bash
   cp real_notes/new_notes_template.csv my_new_notes.csv
   ```

   Or create a CSV any way you like with the same columns. Leaving out the
   copy step works too; the template just saves typing the headings.

2. **Fill in one row per note** in a spreadsheet or text editor, with the terms
   from the note's final filing (the 424B2 headed "Pricing supplement dated…").
   Save it as CSV.

3. **Add them:**

   ```bash
   python3 notes_desk.py add-notes my_new_notes.csv
   ```

   It checks every note the same way setup does and lists every problem with
   its CUSIP (nothing is added if any check fails), shows a preview of the
   check dates it built, and asks before adding. The notes go straight into the
   running database (every loaded price is kept) and into `real_notes/*.csv`,
   so a later setup keeps them.

A new-notes file has the `notes.csv` columns (below) plus four schedule columns
that describe the check dates instead of listing every one:

| Column | Meaning |
|---|---|
| `obs_frequency` | `monthly`, `quarterly`, `semiannual`, `annual`, or `none` |
| `first_obs_date` | The first coupon or call check |
| `first_call_date` | The first check that can call the note; blank means every check can |
| `final_valuation_date` | The date the payout is fixed (from the filing; a few days before maturity) |

How the dates are built: from `first_obs_date`, step by the frequency until
the final valuation date, keeping the same day of the month (month-end dates
stay at month-end) and moving weekend dates to the next Monday. Income notes
get a coupon check on every date; callable notes also get a call check from
`first_call_date` on. The final valuation date is the maturity check, and the
last coupon check for income notes.

| Note | `obs_frequency` | `first_obs_date` | `first_call_date` | `final_valuation_date` |
|---|---|---|---|---|
| Monthly income, callable after 3 months | `monthly` | `2026-12-13` | `2027-02-13` | `2029-11-13` |
| Quarterly income, callable from the 2nd check | `quarterly` | `2027-02-12` | `2027-05-12` | `2028-11-13` |
| Buffered note with one call date | `annual` | `2027-11-26` | | `2028-11-13` |
| Buffered or protected note, no calls | `none` | | | `2028-01-27` |

Market holidays aren't known, so always compare the preview's first and final
dates and check counts with the filing's schedule table before answering yes.

**Irregular schedules.** If a filing's dates don't follow a regular pattern,
set `obs_frequency` to `none` and list the coupon and call dates in a second
file with columns `cusip,obs_date,kind`, one row per check:

```bash
python3 notes_desk.py add-notes my_new_notes.csv my_extra_dates.csv
```

**New underliers.** A note on a new stock adds the stock automatically. The
next nightly price file needs its close (`price-template` includes it), and to
keep it across a setup rebuild, add a row for it to `my_history.csv`.

## Fixing notes

Edit the row in `real_notes/notes.csv` (or its dates in
`real_notes/observation_dates.csv`), then rerun `python3 notes_desk.py setup`.

`notes.csv` columns (enter percentages as decimals, e.g. 10% as `0.10`):

| Column | Meaning |
|---|---|
| `cusip`, `issuer`, `source_url` | Identity and the filing the terms came from |
| `underlier`, `underlier_name` | Ticker (`SPX`, `AAPL`) and full name |
| `note_type` | `income`, `buffered`, `trigger_growth` or `protected` |
| `trade_date`, `maturity_date` | Pricing date and the date money is repaid |
| `initial_level` | Starting level, from the filing (required). For an index note this is usually the index's close on `trade_date` |
| `coupon_rate`, `coupon_barrier_pct`, `memory_coupon` | Income notes: annual coupon, the level it's paid above, and whether missed coupons are paid later |
| `barrier_pct` | Income and trigger-growth notes: the level below which principal is lost from the start |
| `autocall_pct`, `call_premium` | Automatic call level, and for growth notes the fixed return paid if called |
| `buffer_pct`, `downside_rate` | Buffered notes: loss absorbed by the bank, and any multiplier on losses past it |
| `participation`, `cap_pct` | Upside multiple and maximum total gain |

`observation_dates.csv` has one row per check: `cusip`, `obs_date`, `kind`
(`coupon`, `call` or `maturity`). A date that is both a coupon and a call
check gets two rows. Each note needs exactly one `maturity` row, on its final
valuation date.

## What-if scenarios and tests

```bash
python3 notes_desk.py what-if
python3 notes_desk.py test
```

Neither changes your data: each runs inside a transaction that is undone at the end.

`what-if` uses five of the notes:

- **A. Payout at maturity by move.** What $1,000 returns if the underlier
  finishes up 80%, up 20%, down 20%, down 46% and so on, using the same payout
  function as the nightly job. (Barclays HPE: principal only; its coupons are
  paid on top.)
- **B. What the nightly job flags for the Barclays HPE note** on Jan 4, 2027,
  25 days before its first call and coupon check, at HPE prices of $58, $50,
  $32 and $29.

`test` runs the unit tests in `tests/test_*.py` (schedules built from real
filings, CSV checks; no database needed) and the database tests in
`tests/test_nightly.sql` (payouts against the filings, calls, reruns, missing
prices). The unit tests also run on their own with `python3 -m unittest`.

## Scheduling

```cron
CRON_TZ=America/Chicago
15 18 * * 1-5  cd /opt/notes_desk && python3 notes_desk.py nightly --fetch >> /var/log/notes_desk/nightly.log 2>&1
```

With no date, `nightly` runs for today, and `--fetch` downloads the closes
first, so nothing has to be typed. On a market holiday the fetch finds no
closes and the run is marked failed with that reason. Alerts are printed to the log. To post
them to Slack too, set `NOTES_DESK_SLACK_WEBHOOK` to a Slack incoming-webhook URL.

## Simplifications

- There's no holiday calendar: a check on a market holiday is applied at the
  next run, which is right as long as nights are run in order. Generated
  schedules (`add-notes`) roll weekends but not holidays, so compare them with
  the filing.
- Concentration limits use notional (the client money committed to each
  underlier), not market value, since there are no secondary-market prices.
- Notes on more than one underlier ("worst-of") aren't supported.
- Coupon payments, including memory coupons catching up, aren't tracked; the
  system flags coupon checks but doesn't record what was paid.
- Notes called for a fixed premium are marked called, but the premium isn't
  recorded as a payment.

## Filling in history (`backfill`)

```bash
python3 notes_desk.py backfill 2026-01-01                  # up to today
python3 notes_desk.py backfill 2026-01-01 --end 2026-11-25
python3 notes_desk.py backfill --no-download               # replay the price files already in feeds/
```

1. **Downloads** every underlier's daily closes for the period, one request per
   underlier, and writes one price file per trading day in `feeds/`. Price
   files already there are kept as they are (they're the record of what was
   loaded); add `--force` to replace them.
2. **Replays** every night in date order, so each coupon, call and maturity
   check is applied on its own date. A note only counts from its trade date,
   and a night only needs prices for the notes in the book that night. Each
   night gets a run-log entry; only the last gets report files.
3. **Checks every note's starting level** against the downloaded close on its
   trade date. Some filings set it a few days earlier, on a "strike date"; a
   match with a close in the 10 days before is listed separately as normal.
   Anything else is flagged: a typo in `real_notes/notes.csv`, or a stock split
   (downloaded history is split-adjusted, filings aren't).
4. **Summarizes:** notes called or matured during the period, nights with
   moves over 20%, and any night that failed, with the reason. Fix a failed
   night's price file and run backfill again; it's safe to repeat.

A full year (about 230 nights) takes seconds. The website's charts and the AI
tools' price history then cover the whole period. Docker's first start replays
every price file in `feeds/` the same way.

## The website

```bash
python3 notes_desk.py serve            # then open http://localhost:8000
python3 notes_desk.py serve --port 8080
```

A small website on top of the same database: a dashboard for each night, every
note with filters and sorting, a page per note (terms in plain English, price
chart, payout calculator, schedule, nightly history), and the run history with
a button to run the nightly job. It uses only Python's built-in web server, so
there's nothing extra to install.

It listens on 127.0.0.1, so only this computer can open it. The run button
carries a code that changes each time the server starts, so another website
can't trigger a run. If the button says "Form expired", reload the page.

It reads the database live, so it shows whatever the latest run wrote, whether
that run came from cron, the command line or the button.

### Sharing a snapshot

```bash
python3 notes_desk.py export             # the latest night
python3 notes_desk.py export 2026-11-20  # a particular night
```

writes the dashboard, the notes table and every note's page to `export/` as
plain HTML files, plus that night's two report files. Open `export/index.html`
in any browser: sorting, filters, charts and the payout calculator all work,
with no server or database. Zip the folder to share it. Each export replaces
the folder's contents. The code is in
`desk/web.py`.

## Ask Claude about the book (MCP)

`python3 notes_desk.py mcp` is an **MCP server**: it lets an AI assistant such
as Claude Desktop or Claude Code answer questions about the book in plain
English, using the app's own numbers. For example:

- "Which Nasdaq-100 notes are below their buffer, and how far?"
- "What does 46661MLN2 pay if the Nasdaq falls another 10% from here?"
- "Summarize last night for an advisor in three sentences."
- "Which notes have a coupon at risk in the next 30 days?"

**The tools** (in `desk/tools.py`): `book_summary`, `find_notes`, `get_note`,
`payout_at_moves`, `upcoming_events`, `price_history` and `run_history`, all
read-only. One more, `run_nightly`, is off unless you turn it on (below).

**Guardrails:**

- **Read-only by default:** every tool runs on a database connection where
  PostgreSQL itself refuses writes. The assistant can't change notes, prices or
  results unless you start the server with `--allow-runs`.
- **No invented numbers:** payouts come from `note_payout_per_1000()`, the same
  function the nightly job uses, and the server tells the assistant to take
  every number from a tool.
- **Sources:** `get_note` returns the note's SEC filing link, so answers can
  cite it.

The server speaks MCP over standard input and output with Python's standard
library, so there's nothing to install. Your MCP client starts it; you don't
run it yourself.

### Claude Code

```bash
claude mcp add --transport stdio notes-desk -- python3 /full/path/to/notes_desk/notes_desk.py mcp
claude mcp list        # should show notes-desk as connected
```

Then start `claude` and ask away (`/mcp` shows the server and its tools).

### Claude Desktop (Mac, Windows; Linux beta on Ubuntu and Debian only)

On Fedora and other Linux distributions, use Claude Code instead (above).

In Claude Desktop: **Settings → Developer → Edit Config**, and add (with your
own full path):

```json
{
  "mcpServers": {
    "notes-desk": {
      "command": "python3",
      "args": ["/full/path/to/notes_desk/notes_desk.py", "mcp"]
    }
  }
}
```

Quit Claude Desktop completely and start it again. The tools appear under
**Connectors** in the chat box's "+" menu.

### Using the Docker copy instead

With `docker compose up` running, point the client at the container. The
`-T` matters: it gives the server plain input and output instead of a terminal.

```json
{
  "mcpServers": {
    "notes-desk": {
      "command": "docker",
      "args": ["compose", "-f", "/full/path/to/notes-desk/compose.yaml",
               "exec", "-T", "app", "python3", "notes_desk.py", "mcp"]
    }
  }
}
```

On a Mac, if Claude Desktop says it can't find `docker`, use its full path,
`/usr/local/bin/docker`.

### Letting Claude run the nightly job (`--allow-runs`)

Start the server with `--allow-runs` and it also offers **`run_nightly`**: it
fetches a day's closes if there's no price file yet, runs the nightly job and
returns its log plus that night's summary. Then you can say *"fetch today's
closes, run the job and tell me what changed"*.

It's the ordinary nightly job, with the same price checks, lock, run log and
reports as `nightly DATE --fetch`, so a run from Claude shows up on the Runs
page and in `run_history` like any other. Rerunning a day is safe. Clients see
it marked as a tool that changes data, and ask before using it unless you've
told them not to. The other tools stay read-only either way.

To switch an existing Claude Code setup over:

```bash
claude mcp remove -s user notes-desk
claude mcp add -s user --transport stdio notes-desk -- python3 /full/path/to/notes_desk/notes_desk.py mcp --allow-runs
```

For Claude Desktop, add `"--allow-runs"` after `"mcp"` in the `args` list.

### Other agent frameworks

The tools don't depend on MCP: `desk/tools.py` has each tool's name,
description, JSON Schema and Python function in `TOOLS`, and `tools.call(name,
arguments)` runs one. A framework with its own tool system needs only a thin
wrapper around those.
