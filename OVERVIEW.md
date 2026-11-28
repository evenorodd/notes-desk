# Notes desk: overview

The app does one job: **every night, take closing prices, value every note, and
write a report of what needs attention.** Everything else supports that.
README.md has the full step-by-step guide, including getting and updating the
code from GitHub on Windows, Mac and Linux.

## How it works

```
  INPUTS (CSV files)              THE APP (Python + SQL)              OUTPUTS
  ------------------              ----------------------              -------
  real_notes/  the notes ────► setup ──────► PostgreSQL database
                                                   │
  feeds/closes_DATE.csv ◄── fetch-prices (Yahoo)   │
          │                                        ▼
          └──────────────────► nightly ──► values every note ──► reports/morning_DATE.txt
  feeds/rates_YEAR.csv ─────────────────► model value of every note (pricing)
                                                                 reports/notes_DATE.csv
                                                   │
                                                   ├──► serve ──► the same results in your browser
                                                   └──► mcp   ──► an AI assistant (Claude) answers questions
                                                                  about the book; with --allow-runs it can
                                                                  also run the nightly job
```

## The folders

| Folder / file | What's in it | Do you edit it? |
|---|---|---|
| `real_notes/` | The 75 notes' terms from SEC filings (`notes.csv`), their check dates (`observation_dates.csv`), and a template for adding notes | Only through `add-notes` |
| `feeds/` | One price file per trading day since January 2, 2026 (`closes_DATE.csv`): the price history and the record of what was loaded | Only to fix a bad price |
| `reports/` | The output: the morning report and the every-note CSV for each night | No, you read these |
| `desk/` | The Python code, one file per command (`tools.py` and `mcp_server.py` are the AI assistant's tools) | No |
| `sql/` | Builds the database (the tables) and holds the nightly valuation logic | No |
| `tests/` | Checks the logic still works | No |
| `notes_desk.py` | The one file you run | No |
| `Dockerfile`, `compose.yaml`, `docker/` | Run everything, database included, with one command: `docker compose up --build`. `docker/systemd/` has the nightly timer for Linux | No |

## The commands

| Command | When |
|---|---|
| `setup` | Once, or to rebuild from scratch. It builds the database from `real_notes/`; then `backfill --no-download` reloads `feeds/`. |
| `nightly DATE --fetch` | Every trading night. It gets prices, values the notes and writes the reports. |
| `catch-up` | What a schedule runs: every trading night since the last successful run, so missed nights and holidays take care of themselves. |
| `backfill START` | Downloads history and replays every night since START (e.g. `2026-01-01`), then checks each note's starting level against the close on its trade date. |
| `fetch-prices` / `price-template` | Get a night's prices on their own (downloaded, or a blank file to type in). |
| `add-notes FILE` | When new notes are bought. |
| `pricing-setup` | Once: downloads a year of earlier prices and the Treasury rates, then values every note on every night. After that, each nightly run prices itself. |
| `price DATE` / `price --all` | Reprices one night or all of them (normally done by nightly). |
| `serve` | Opens the notes desk in your web browser at http://localhost:8000: dashboard, every note, one page per note, run history. |
| `mcp` | Lets an AI assistant (Claude Desktop, Claude Code) answer questions about the book, read-only. Add `--allow-runs` to let it run the nightly job too. Started by the assistant, not by you. |
| `export` | Saves the website for one night as plain HTML files in `export/`, to share. |
| `what-if` / `test` | To explore payouts or check the logic. They change nothing. |

Run `python3 notes_desk.py COMMAND -h` to see a command's options (for example,
`nightly -h` shows `--fetch`).

## The tables, in five groups

**1. Reference: who and what (rarely changes)**
- `indices`: the 15 underliers (SPX, NDX, AAPL…)
- `issuers`: the banks (JPMorgan, Goldman…)
- `advisors` and `accounts`: who owns the notes (one example advisor and household for now)

**2. The notes and who owns them (changes when notes are added)**
- `notes`: each note's terms, such as type, start level, barrier, buffer, cap and coupon. This is the heart of the database.
- `observation_dates`: every coupon, call and final-valuation date for each note
- `holdings`: which account owns how much of which note

**3. Prices (one new set each night)**
- `market_data`: every close, for every underlier and date
- `feed_loads`: a record of each price file loaded

**4. Nightly results (written by the nightly job)**
- `note_daily_status`: each note's value each night, with cushion, distance to call and payout. **This is what the reports are built from.**
- `events`: every coupon, call and maturity check in the next 30 days with how it looks (likely call, coupon on track or at risk…), plus notes already below protection
- `concentration_breaches`: an advisor with too much money linked to one underlier
- `job_runs`: one row per run, saying whether it succeeded, failed or warned, and why

**5. Pricing (written after the nightly job; see README.md, "Model values")**
- `treasury_rates`: Treasury yields by maturity, each day
- `note_calibrations`: each note's spread over Treasuries, solved on its trade date to match the issuer's estimated value
- `note_valuations`: each note's model value each night, with how it moves per 1% and per volatility point
- `pricing_input_files`: which rate and history files have been loaded

The first two groups describe what you own. The third is what the market did.
The fourth is what the job concluded, and the fifth what the notes are worth.

## How the notes are priced

Each night, every note gets a **model value**: what $1,000 of it is worth
today, before maturity. A note is a loan to the issuing bank with options built
into its payout, so its value is:

> **the average of what it might pay, across all the ways the underlier could
> move, discounted back to today at the Treasury rate plus a spread for the
> bank's risk**

1. **Imagine the possible futures.** How widely the underlier could move
   depends on its volatility, measured from the past year of its prices.
2. **Apply the note's own rules** to each future: coupons, calls, buffer,
   barrier, cap.
3. **Average, then discount.** A dollar later is worth less than a dollar now.
4. **Match the bank's own number.** Each filing states the bank's estimated
   value on the trade date (e.g. $966.85 per $1,000). The spread is set so the
   model agrees with it that day, then kept, so the model moves each night the
   way the bank's own method would.

| Note type | Notes | Method | More volatility... |
|---|---|---|---|
| Buffered | 48 | Exact formula: only the final price matters, so it's payout × probability, summed over every final price (the same answer as a bond plus calls and puts priced with Black–Scholes) | lowers the value |
| Principal-protected | 10 | Exact formula: a bond plus a call | raises the value |
| Income (coupons, calls) | 15 | Simulation: 20,000 possible paths, the rules applied on every check date, then averaged | lowers it, strongly |
| Buffered with a call | 2 | Simulation, as above | lowers the value |

Each note also gets how its value moves per 1% move in the underlier and per
volatility point. The website's **Pricing** page shows every input.

**Limits:** volatility comes from past prices, where banks use option-market
(implied) volatility; where the two differ, the calibrated spread looks odd
and the Pricing page marks it **check**. Dividend yields are approximate. A
model value isn't a price a dealer would pay. README.md, "Model values",
has the details.

## The website (`serve`)

`python3 notes_desk.py serve`, then open http://localhost:8000. It reads the
database live, so it always shows the latest run. Only your own computer can
open it. Leave the terminal window open while you use it; Ctrl+C stops it.

- **Dashboard:** one night at a glance. Prices to double-check, headline
  numbers, the notes closest to losing principal, each underlier's close and
  exposure, and what's coming up in the next 30 days.
- **Notes:** every note in one table. Filter by type or underlier, search,
  and click any column to sort.
- **One note** (click a CUSIP): its terms in plain English, a chart of the
  underlier against the note's start, barrier or buffer and call levels, a
  payout calculator (drag to see what $1,000 pays at any move), the schedule,
  and the note's history night by night.
- **Pricing:** how model values are worked out, and every input: volatility
  and dividends per underlier, the Treasury curve, and each note's calibrated
  spread and value.
- **Runs:** every nightly run and whether it worked, the price files loaded,
  and a button that runs the nightly job (with the price download if needed).

## The AI assistant (`mcp`)

`notes_desk.py mcp` is an MCP server: the standard way to give an AI assistant
tools. Once it's registered with Claude Code or Claude Desktop (README.md,
"Ask Claude about the book"), you can ask about the book in plain English.

- **Seven read-only tools:** a night's summary, finding notes, one note in
  full, payouts at any move, upcoming events, price history and run history.
- **Every number comes from the app:** payouts use the same SQL function as
  the nightly job; Claude explains, it doesn't calculate.
- **Read-only by default:** PostgreSQL itself refuses writes on the tools'
  connection. With `--allow-runs`, one more tool, `run_nightly`, runs the
  ordinary nightly job, logged like any other run.
- **Framework-neutral:** the tools live in `desk/tools.py` as plain
  functions; `desk/mcp_server.py` just serves them.

## The two reports

- **`morning_DATE.txt`**: the one-page summary to read first. It has the book
  size, prices to double-check, notes closest to losing principal, upcoming
  events, likely calls, coupons at risk and concentration breaches.
- **`notes_DATE.csv`**: every note on one row (the columns are explained in
  README.md), for sorting and filtering in a spreadsheet.

## If you only remember three things

Notes go in through `real_notes/`, prices go in through `feeds/`, and answers
come out in `reports/`. The database in the middle keeps the history.
