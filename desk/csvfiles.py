"""Reading and writing the CSV files: prices, notes and observation dates.

Every reader checks the file as it goes and raises one DeskError listing
every problem with its line number, so a file can be fixed in one pass.
"""

import csv
import datetime
from decimal import Decimal, InvalidOperation

from . import DeskError

PRICE_COLUMNS = ["index_code", "price_date", "close"]

NOTE_COLUMNS = [
    "cusip", "underlier", "underlier_name", "issuer", "note_type",
    "trade_date", "maturity_date", "initial_level",
    "coupon_rate", "coupon_barrier_pct", "barrier_pct",
    "buffer_pct", "downside_rate", "participation", "cap_pct",
    "autocall_pct", "call_premium", "memory_coupon", "source_url",
]
SCHEDULE_COLUMNS = ["obs_frequency", "first_obs_date", "first_call_date", "final_valuation_date"]
OBS_COLUMNS = ["cusip", "obs_date", "kind"]

_DATE_FIELDS = {"trade_date", "maturity_date", "price_date", "obs_date",
                "first_obs_date", "first_call_date", "final_valuation_date"}
_NUMBER_FIELDS = {"initial_level", "coupon_rate", "coupon_barrier_pct", "barrier_pct",
                  "buffer_pct", "downside_rate", "participation", "cap_pct",
                  "autocall_pct", "call_premium", "close"}


def _parse(field, text):
    """Converts one cell. Blank cells become None. Raises ValueError with a readable message."""
    text = (text or "").strip()
    if text == "":
        return None
    if field in _DATE_FIELDS:
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{field} '{text}' isn't a date like 2026-11-20")
    if field in _NUMBER_FIELDS:
        try:
            return Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{field} '{text}' isn't a plain number (no $ or commas)")
    if field == "memory_coupon":
        if text.lower() in ("true", "yes", "y", "1"):
            return True
        if text.lower() in ("false", "no", "n", "0"):
            return False
        raise ValueError(f"memory_coupon '{text}' should be true or false")
    return text


def read_table(path, columns, what):
    """Reads a CSV whose header must contain `columns`. Returns a list of dicts."""
    if not path.exists():
        raise DeskError(f"File not found: {path}")
    problems, out = [], []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in columns if c not in header]
        if missing:
            raise DeskError(f"{path.name} is missing these columns: {', '.join(missing)}",
                            [f"the first line must be: {','.join(columns)}"])
        reader.fieldnames = header
        for line_no, raw in enumerate(reader, start=2):
            if not any((v or "").strip() for k, v in raw.items() if k in columns):
                continue  # blank line
            row, label = {}, raw.get("cusip") or raw.get("index_code") or ""
            for c in columns:
                try:
                    row[c] = _parse(c, raw.get(c))
                except ValueError as e:
                    problems.append(f"line {line_no} {label}: {e}")
            row["_line"] = line_no
            out.append(row)
    if problems:
        raise DeskError(f"{path.name} has problems. Fix these and run it again:", problems)
    if not out:
        raise DeskError(f"{path.name} has no {what} in it.")
    return out


def read_prices(path):
    return read_table(path, PRICE_COLUMNS, "prices")


def read_notes(path, with_schedule=False):
    cols = NOTE_COLUMNS + (SCHEDULE_COLUMNS if with_schedule else [])
    return read_table(path, cols, "notes")


def read_observations(path):
    return read_table(path, OBS_COLUMNS, "observation dates")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral_value() else str(v)
    return str(v)


def append_rows(path, rows):
    """Appends rows (lists of values) to an existing CSV file."""
    needs_newline = path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n")
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        csv.writer(fh, lineterminator="\n").writerows([[_cell(v) for v in r] for r in rows])


def write_rows(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows([[_cell(v) for v in r] for r in rows])
