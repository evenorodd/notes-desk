# Notes desk: overview

The app does one job: **every night, take closing prices, value every note, and
write a report of what needs attention.** Everything else supports that.
README.md has the full step-by-step guide.

## How it works

```
  INPUTS (CSV files)              THE APP (Python + SQL)              OUTPUTS
  ------------------              ----------------------              -------
  real_notes/  the notes ──┐
  my_history.csv          ─┼─► setup ──────► PostgreSQL database
                           │                       │
  feeds/closes_DATE.csv ◄── fetch-prices (Yahoo)   │
          │                                        ▼
          └──────────────────► nightly ──► values every note ──► reports/morning_DATE.txt
                                                                 reports/notes_DATE.csv
                                                   │
                                                   └──► serve ──► the same results in your browser
```

## The folders

| Folder / file | What's in it | Do you edit it? |
|---|---|---|
| `real_notes/` | The 75 notes' terms from SEC filings (`notes.csv`), their check dates (`observation_dates.csv`), and a template for adding notes | Only through `add-notes` |
| `my_history.csv` | One earlier close per underlier, so the first night has something to compare against | Rarely |
| `feeds/` | One price file per night, `closes_DATE.csv`, which also serves as a record of what was loaded | Only to fix a bad price |
| `reports/` | The output: the morning report and the every-note CSV for each night | No, you read these |
| `desk/` | The Python code, one file per command | No |
| `sql/` | Builds the database (the tables) and holds the nightly valuation logic | No |
| `tests/` | Checks the logic still works | No |
| `notes_desk.py` | The one file you run | No |
| `Dockerfile`, `compose.yaml`, `docker/` | Run everything, database included, with one command: `docker compose up --build` | No |

## The commands

| Command | When |
|---|---|
| `setup` | Once, or to rebuild from scratch. It builds the database from `real_notes/` and `my_history.csv`. |
| `nightly DATE --fetch` | Every trading night. It gets prices, values the notes and writes the reports. |
| `fetch-prices` / `price-template` | Get a night's prices on their own (downloaded, or a blank file to type in). |
| `add-notes FILE` | When new notes are bought. |
| `serve` | Opens the notes desk in your web browser at http://localhost:8000: dashboard, every note, one page per note, run history. |
| `export` | Saves the website for one night as plain HTML files in `export/`, to share. |
| `what-if` / `test` | To explore payouts or check the logic. They change nothing. |

Run `python3 notes_desk.py COMMAND -h` to see a command's options (for example,
`nightly -h` shows `--fetch`).

## The 13 tables, in four groups

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
- `events`: things coming up or already wrong, such as likely calls, coupons at risk and notes below protection
- `concentration_breaches`: an advisor with too much money linked to one underlier
- `job_runs`: one row per run, saying whether it succeeded, failed or warned, and why

The first two groups describe what you own. The third is what the market did.
The fourth is what the job concluded.

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
- **Runs:** every nightly run and whether it worked, the price files loaded,
  and a button that runs the nightly job (with the price download if needed).

## The two reports

- **`morning_DATE.txt`**: the one-page summary to read first. It has the book
  size, prices to double-check, notes closest to losing principal, upcoming
  events, likely calls, coupons at risk and concentration breaches.
- **`notes_DATE.csv`**: every note on one row (the columns are explained in
  README.md), for sorting and filtering in a spreadsheet.

## If you only remember three things

Notes go in through `real_notes/`, prices go in through `feeds/`, and answers
come out in `reports/`. The database in the middle keeps the history.
