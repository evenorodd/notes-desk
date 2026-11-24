"""Builds a note's check dates from a short schedule description.

    obs_frequency         monthly, quarterly, semiannual, annual, or none
    first_obs_date        first coupon or call check
    first_call_date       first check that can call the note (blank = first_obs_date)
    final_valuation_date  the date the payout is fixed

Dates step from first_obs_date by the frequency, always counted from the
first date so the day of the month doesn't drift (Jan 31 monthly gives
Feb 28, Mar 31, ...). Weekend dates move to the next Monday. Market holidays
aren't known, so compare the result with the filing's schedule table.
"""

import calendar
import datetime

FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


def add_months(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return datetime.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_weekday(d):
    """Saturday or Sunday moves to the following Monday."""
    return d + datetime.timedelta(days=7 - d.weekday()) if d.weekday() >= 5 else d


def schedule_problems(note):
    cusip = note.get("cusip") or "(blank CUSIP)"
    freq = note.get("obs_frequency") or "none"
    out = []
    if freq != "none" and freq not in FREQUENCY_MONTHS:
        out.append(f"{cusip}: obs_frequency must be monthly, quarterly, semiannual, annual or none")
    if note.get("final_valuation_date") is None:
        out.append(f"{cusip}: final_valuation_date is required")
    if freq != "none" and note.get("first_obs_date") is None:
        out.append(f"{cusip}: first_obs_date is required when obs_frequency is set")
    if (note.get("first_obs_date") and note.get("final_valuation_date")
            and note["first_obs_date"] >= note["final_valuation_date"]):
        out.append(f"{cusip}: first_obs_date must be before final_valuation_date")
    return out


def build_dates(note):
    """Returns [(date, kind), ...] with kind coupon, call or maturity."""
    freq = note.get("obs_frequency") or "none"
    final = note["final_valuation_date"]
    income = note.get("note_type") == "income"
    callable_ = note.get("autocall_pct") is not None
    out = []

    if freq != "none":
        first = note["first_obs_date"]
        first_call = note.get("first_call_date") or first
        step = FREQUENCY_MONTHS[freq]
        g = 0
        while True:
            d = next_weekday(add_months(first, g * step))
            if d >= final:   # compared after the weekend move, so a Sunday just before
                break        # a Monday final valuation isn't counted twice
            if income:
                out.append((d, "coupon"))
            if callable_ and d >= first_call:
                out.append((d, "call"))
            g += 1

    out.append((final, "maturity"))
    if income:
        out.append((final, "coupon"))
    return out
