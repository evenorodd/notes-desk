"""Notes desk: nightly monitoring of structured notes in PostgreSQL."""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_DIR / "sql"
NOTES_DIR = PROJECT_DIR / "real_notes"
FEEDS_DIR = PROJECT_DIR / "feeds"
REPORTS_DIR = PROJECT_DIR / "reports"
NOTES_CSV = NOTES_DIR / "notes.csv"
OBS_CSV = NOTES_DIR / "observation_dates.csv"
HISTORY_CSV = PROJECT_DIR / "my_history.csv"


class DeskError(Exception):
    """A problem to report to the person running the command (no traceback)."""

    def __init__(self, message, problems=()):
        super().__init__(message)
        self.problems = list(problems)


def market_today():
    """Today's date in New York, where the markets are. A computer or container
    on UTC (Docker's default) has already moved to tomorrow by 7 pm New York
    time, which is exactly when the nightly job runs."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:   # no time-zone data available: fall back to the local date
        return datetime.date.today()
