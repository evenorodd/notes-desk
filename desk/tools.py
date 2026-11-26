"""Read-only questions about the book, as tools an AI assistant can call.

Each tool is a plain Python function plus a JSON Schema for its arguments, so
any agent framework can use them: desk/mcp_server.py serves them over MCP (for
Claude Desktop, Claude Code and other MCP clients), and another framework only
needs a thin wrapper around TOOLS.

Design rules:
  - Every number comes from the database or note_payout_per_1000(), the same
    function the nightly job uses. The assistant explains; it never computes.
  - Read-only: each call runs on a connection where PostgreSQL refuses writes.
    The one exception, run_nightly, is only offered when the server is started
    with --allow-runs (see available()), and it runs the ordinary nightly job:
    the same checks, lock, log and reports as the command line.
  - Percentages are returned in percent (-8.55 means -8.55%), money in dollars.
"""

import contextlib
import datetime
import io
from decimal import Decimal

from . import DeskError, db, market_today

TYPES = ("income", "buffered", "trigger_growth", "protected")


# --- helpers ---------------------------------------------------------------------

def read_only_connection():
    conn = db.connect(autocommit=True)
    db.run(conn, "set default_transaction_read_only = on")
    return conn


def _plain(v):
    """Database values to JSON-friendly ones."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _pct(v, places=2):
    return None if v is None else round(float(v) * 100, places)


def _records(conn, sql, params=None):
    headers, rows = db.query(conn, sql, params)
    return [{h: _plain(v) for h, v in zip(headers, r)} for r in rows]


def _night(conn, date=None):
    """The night to answer for: the one asked about, else the latest run."""
    nights = [r[0] for r in db.rows(conn, "select distinct run_date from note_daily_status order by 1 desc")]
    if not nights:
        raise DeskError("No nightly runs yet, so there are no results to report.")
    if date is None:
        return nights[0]
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        raise DeskError(f"'{date}' isn't a date; use YYYY-MM-DD.")
    if d not in nights:
        shown = ", ".join(str(n) for n in nights[:10])
        raise DeskError(f"No nightly results for {d}. Nights with results (latest first): {shown}")
    return d


def _note_id(conn, cusip):
    found = db.rows(conn, "select note_id from notes where cusip = %s", (cusip.strip().upper(),))
    if not found:
        raise DeskError(f"No note with CUSIP {cusip}. Use find_notes to see the CUSIPs in the book.")
    return found[0][0]


# --- the tools ---------------------------------------------------------------------

def book_summary(conn, date=None):
    d = _night(conn, date)
    k = db.rows(conn, """
        select count(*), sum(h.notional),
               count(*) filter (where s.cushion < 0),
               coalesce(sum(h.notional) filter (where s.cushion < 0), 0),
               count(*) filter (where s.cushion >= 0 and s.cushion < 0.10),
               sum(s.payout_per_1000 * h.notional / 1000)
        from note_daily_status s join holdings h using (note_id)
        where s.run_date = %s""", (d,))[0]
    by_type = _records(conn, """
        select n.note_type, count(*) as notes, count(*) filter (where s.cushion < 0) as below_protection,
               round(avg(s.payout_per_1000), 2) as avg_payout_per_1000
        from note_daily_status s join notes n using (note_id)
        where s.run_date = %s group by 1 order by 1""", (d,))
    underliers = _records(conn, """
        select n.index_code as underlier, count(*) as notes,
               count(*) filter (where s.cushion < 0) as below_protection,
               m.close, round((m.close / p.close - 1) * 100, 2) as day_move_pct
        from note_daily_status s join notes n using (note_id)
        join market_data m on m.index_code = n.index_code and m.price_date = s.run_date
        left join lateral (select close from market_data x where x.index_code = n.index_code
                           and x.price_date < s.run_date order by x.price_date desc limit 1) p on true
        where s.run_date = %s group by n.index_code, m.close, p.close
        order by count(*) desc, 1""", (d,))
    run = _records(conn, """select status, message from job_runs where run_date = %s
                            order by run_id desc limit 1""", (d,))
    return {
        "night": d.isoformat(),
        "latest_run": run[0] if run else None,
        "notes_valued": k[0],
        "total_notional": _plain(k[1]),
        "below_protection": {"notes": k[2], "notional": _plain(k[3])},
        "within_10pct_of_protection": k[4],
        "value_if_all_matured_today": round(float(k[5] or 0), 2),
        "by_type": by_type,
        "by_underlier": underliers,
        "note": "value_if_all_matured_today excludes coupons already paid.",
    }


def find_notes(conn, underlier=None, note_type=None, issuer=None, below_protection_only=False,
               max_cushion_pct=None, sort_by="cushion", limit=25, date=None):
    d = _night(conn, date)
    where, params = ["s.run_date = %s"], [d]
    if underlier:
        where.append("n.index_code = %s")
        params.append(underlier.strip().upper())
    if note_type:
        where.append("n.note_type = %s")
        params.append(note_type)
    if issuer:
        where.append("i.name ilike %s")
        params.append(f"%{issuer}%")
    if below_protection_only:
        where.append("s.cushion < 0")
    if max_cushion_pct is not None:
        where.append("s.cushion < %s")
        params.append(Decimal(str(max_cushion_pct)) / 100)
    order = {"cushion": "s.cushion nulls last", "payout": "s.payout_per_1000",
             "maturity": "n.maturity_date", "since_start": "s.perf"}[sort_by]
    params.append(max(1, min(int(limit), 100)))
    rows = _records(conn, f"""
        select n.cusip, i.name as issuer, n.note_type, n.index_code as underlier,
               n.trade_date, n.maturity_date, n.initial_level as start_level, s.index_level as close,
               round(s.perf * 100, 2) as since_start_pct, round(s.cushion * 100, 2) as cushion_pct,
               round(s.coupon_cushion * 100, 2) as coupon_cushion_pct,
               round(s.dist_to_call * 100, 2) as vs_call_trigger_pct,
               s.payout_per_1000, n.status
        from note_daily_status s join notes n using (note_id) join issuers i using (issuer_id)
        where {" and ".join(where)}
        order by {order}, n.cusip limit %s""", params)
    return {"night": d.isoformat(), "count": len(rows), "notes": rows,
            "fields": "cushion_pct: room before principal is lost at maturity (negative = past it); "
                      "payout_per_1000: what $1,000 pays if this night were the final valuation, excluding coupons."}


def get_note(conn, cusip, date=None):
    d = _night(conn, date)
    note_id = _note_id(conn, cusip)
    terms = _records(conn, """
        select n.cusip, i.name as issuer, n.note_type, n.index_code as underlier, x.name as underlier_name,
               n.trade_date, n.maturity_date, n.initial_level as start_level,
               round(n.coupon_rate * 100, 4) as coupon_rate_pct_per_year,
               round(coalesce(n.coupon_barrier_pct, n.barrier_pct) * 100, 2) as coupon_barrier_pct,
               n.memory_coupon, round(n.barrier_pct * 100, 2) as barrier_pct,
               round(n.buffer_pct * 100, 2) as buffer_pct, n.downside_rate,
               n.participation, round(n.cap_pct * 100, 4) as cap_pct,
               round(n.autocall_pct * 100, 2) as call_trigger_pct,
               round(n.call_premium * 100, 4) as call_premium_pct,
               n.status, n.closed_date, n.source_url as sec_filing
        from notes n join issuers i using (issuer_id) join indices x using (index_code)
        where n.note_id = %s""", (note_id,))[0]
    terms = {k: v for k, v in terms.items() if v is not None}
    status = _records(conn, """
        select index_level as close, round(perf * 100, 2) as since_start_pct,
               round(cushion * 100, 2) as cushion_pct, round(coupon_cushion * 100, 2) as coupon_cushion_pct,
               round(dist_to_call * 100, 2) as vs_call_trigger_pct, payout_per_1000
        from note_daily_status where note_id = %s and run_date = %s""", (note_id, d))
    upcoming = _records(conn, """
        select obs_date, string_agg(kind, ' + ' order by kind) as checks
        from observation_dates where note_id = %s and obs_date > %s
        group by obs_date order by obs_date limit 6""", (note_id, d))
    events = _records(conn, """select obs_date, kind, flag from events
                               where note_id = %s and run_date = %s order by obs_date""", (note_id, d))
    holdings = _records(conn, """select a.household, v.name as advisor, h.notional
                                 from holdings h join accounts a using (account_id) join advisors v using (advisor_id)
                                 where h.note_id = %s order by h.notional desc""", (note_id,))
    return {"night": d.isoformat(), "terms": terms,
            "status": status[0] if status else None,
            "next_checks": upcoming, "flags": events, "holdings": holdings}


def payout_at_moves(conn, cusip, moves_pct):
    note_id = _note_id(conn, cusip)
    if not moves_pct or len(moves_pct) > 50:
        raise DeskError("Give between 1 and 50 moves, in percent (e.g. [-30, -10, 0, 10]).")
    rows = []
    for m in moves_pct:
        if not -100 < float(m) <= 1000:
            raise DeskError(f"A move of {m}% isn't possible; use a number above -100.")
        level, pay = db.rows(conn, """
            select n.initial_level * (1 + %s::numeric / 100),
                   note_payout_per_1000(n, n.initial_level * (1 + %s::numeric / 100))
            from notes n where n.note_id = %s""", (str(m), str(m), note_id))[0]
        rows.append({"move_pct": float(m), "underlier_level": round(float(level), 2),
                     "payout_per_1000": _plain(pay)})
    note_type = db.scalar(conn, "select note_type from notes where note_id = %s", (note_id,))
    return {"cusip": cusip.upper(), "payouts": rows,
            "note": "Payout at maturity per $1,000, if the underlier finishes at that move from its start level. "
                    + ("Principal only: coupons paid along the way are extra." if note_type == "income" else "")}


def upcoming_events(conn, days=30, flag=None, date=None):
    d = _night(conn, date)
    params = [d, d, int(days)]
    extra = ""
    if flag:
        extra = " and e.flag = %s"
        params.append(flag)
    rows = _records(conn, f"""
        select e.obs_date, n.cusip, n.index_code as underlier, e.kind, e.flag, e.notional
        from events e join notes n using (note_id)
        where e.run_date = %s and (e.flag = 'below_protection' or e.obs_date <= %s::date + %s){extra}
        order by e.flag = 'below_protection', e.obs_date, e.notional desc""", params)
    return {"night": d.isoformat(), "count": len(rows), "events": rows,
            "flags": {"likely_call": "at or above the call trigger ahead of a call date",
                      "coupon_at_risk": "within 10% of the coupon barrier ahead of a coupon date",
                      "coupon_missed_if_today": "below the coupon barrier ahead of a coupon date",
                      "maturing": "final valuation coming up", "maturing_with_loss": "final valuation coming up, below protection",
                      "below_protection": "past its buffer or barrier, maturity more than 30 days away"}}


def price_history(conn, underlier, start=None, end=None):
    code = underlier.strip().upper()
    if not db.rows(conn, "select 1 from indices where index_code = %s", (code,)):
        known = ", ".join(r[0] for r in db.rows(conn, "select index_code from indices order by 1"))
        raise DeskError(f"Unknown underlier {underlier}. Known: {known}")
    rows = _records(conn, """
        select price_date, close from market_data
        where index_code = %s and (%s::date is null or price_date >= %s::date)
          and (%s::date is null or price_date <= %s::date)
        order by price_date""", (code, start, start, end, end))
    return {"underlier": code, "closes": rows}


def run_history(conn, limit=10):
    rows = _records(conn, """
        select run_id, run_date, status, to_char(started_at, 'YYYY-MM-DD HH24:MI') as started, message
        from job_runs order by run_id desc limit %s""", (max(1, min(int(limit), 100)),))
    return {"runs": rows}


def run_nightly(date=None, fetch=True):
    """Runs the nightly job exactly as `notes_desk.py nightly DATE [--fetch]`
    does, and returns its log, then that night's summary if it worked."""
    from .nightly import run
    try:
        d = datetime.date.fromisoformat(date) if date else market_today()
    except ValueError:
        raise DeskError(f"'{date}' isn't a date; use YYYY-MM-DD.")
    log = io.StringIO()
    ok = True
    with contextlib.redirect_stdout(log):
        try:
            run(d, fetch)
        except DeskError as e:
            ok = False
            print(str(e))
            for p in e.problems:
                print(f"  {p}")
    result = {"night": d.isoformat(), "ok": ok, "log": log.getvalue().strip()}
    if ok:
        conn = read_only_connection()
        try:
            result["summary"] = book_summary(conn, d.isoformat())
        finally:
            conn.close()
    return result


# --- the catalogue ------------------------------------------------------------------

DATE = {"type": "string", "description": "A night with results, YYYY-MM-DD. Default: the latest night."}
CUSIP = {"type": "string", "description": "The note's 9-character CUSIP, e.g. 46661MLN2."}

TOOLS = [
    {
        "name": "book_summary",
        "description": "Headline numbers for one night of the structured-note book: notes valued, notional, "
                       "how many are below their protection (buffer or barrier), value if everything matured "
                       "that night, and breakdowns by note type and underlier with that day's index moves. "
                       "Start here for any overview question.",
        "input_schema": {"type": "object", "properties": {"date": DATE}, "additionalProperties": False},
        "handler": book_summary,
    },
    {
        "name": "find_notes",
        "description": "List notes with their status for one night, filtered and sorted. Use it for questions "
                       "like 'which Nasdaq notes are below their buffer', 'notes within 5% of losing "
                       "principal', or 'all JPMorgan income notes'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "underlier": {"type": "string", "description": "Underlier code: SPX, NDX, RTY or a stock ticker such as AAPL."},
                "note_type": {"type": "string", "enum": list(TYPES)},
                "issuer": {"type": "string", "description": "Part of the issuer's name, e.g. 'JPMorgan', 'Goldman', 'UBS'."},
                "below_protection_only": {"type": "boolean", "description": "Only notes already past their buffer or barrier."},
                "max_cushion_pct": {"type": "number", "description": "Only notes with cushion below this, in percent (5 means within 5% of losing principal)."},
                "sort_by": {"type": "string", "enum": ["cushion", "payout", "maturity", "since_start"], "description": "Default: cushion, lowest first."},
                "limit": {"type": "integer", "description": "Maximum notes to return, 1 to 100. Default 25."},
                "date": DATE,
            },
            "additionalProperties": False,
        },
        "handler": find_notes,
    },
    {
        "name": "get_note",
        "description": "Everything about one note: its terms from the SEC filing (with the filing link), its "
                       "status that night, its next coupon/call/maturity checks, any flags, and who holds it.",
        "input_schema": {"type": "object", "properties": {"cusip": CUSIP, "date": DATE},
                         "required": ["cusip"], "additionalProperties": False},
        "handler": get_note,
    },
    {
        "name": "payout_at_moves",
        "description": "What $1,000 of a note pays at maturity if its underlier finishes at given moves from the "
                       "note's start level. Uses the same payout function as the nightly job: always call this "
                       "rather than working a payout out yourself.",
        "input_schema": {
            "type": "object",
            "properties": {"cusip": CUSIP,
                           "moves_pct": {"type": "array", "items": {"type": "number"},
                                         "description": "Moves in percent from the start level, e.g. [-40, -20, -10, 0, 10, 30]."}},
            "required": ["cusip", "moves_pct"], "additionalProperties": False,
        },
        "handler": payout_at_moves,
    },
    {
        "name": "upcoming_events",
        "description": "Flags from one night's run: likely calls, coupons at risk or missed, maturities in the "
                       "next N days, plus notes already below protection.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "Look-ahead in days, default 30 (the job flags up to 30)."},
                           "flag": {"type": "string", "enum": ["likely_call", "coupon_at_risk", "coupon_missed_if_today",
                                                               "maturing", "maturing_with_loss", "below_protection"]},
                           "date": DATE},
            "additionalProperties": False,
        },
        "handler": upcoming_events,
    },
    {
        "name": "price_history",
        "description": "Closing prices loaded for one underlier, optionally between two dates.",
        "input_schema": {"type": "object",
                         "properties": {"underlier": {"type": "string", "description": "SPX, NDX, RTY or a stock ticker."},
                                        "start": {"type": "string", "description": "YYYY-MM-DD"},
                                        "end": {"type": "string", "description": "YYYY-MM-DD"}},
                         "required": ["underlier"], "additionalProperties": False},
        "handler": price_history,
    },
    {
        "name": "run_history",
        "description": "Recent nightly runs: date, whether each succeeded, failed or warned (e.g. prices that moved over 20%), and why.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Default 10."}},
                         "additionalProperties": False},
        "handler": run_history,
    },
]

# Tools that change data. Offered only when the server is started with --allow-runs.
WRITE_TOOLS = [
    {
        "name": "run_nightly",
        "description": "Run the nightly job for one trading day: download that day's closing prices if there's "
                       "no price file for it yet, check them, value every note and write the reports. Rerunning "
                       "a day is safe (it replaces that day's results). Prices are final from 4:20 pm New York "
                       "time; weekends and market holidays have no prices. Always report any lines in the log "
                       "about prices to double-check (moves over 20%) and any failure, in full.",
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "Trading day, YYYY-MM-DD. Default: today in New York."},
                           "fetch": {"type": "boolean", "description": "Download the closes if there's no price file yet. Default true."}},
            "additionalProperties": False,
        },
        "handler": run_nightly,
        "writes": True,
    },
]

BY_NAME = {t["name"]: t for t in TOOLS + WRITE_TOOLS}


def available(allow_runs=False):
    """The tools a server offers: the read-only ones, plus run_nightly if allowed."""
    return TOOLS + (WRITE_TOOLS if allow_runs else [])


def check_arguments(schema, args):
    """A small JSON Schema check, enough for these tools: required keys, no
    unknown keys, basic types and enums. Returns a list of problems."""
    problems = []
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            problems.append(f"missing required argument '{key}'")
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list}
    for key, value in args.items():
        if key not in props:
            problems.append(f"unknown argument '{key}'")
            continue
        spec = props[key]
        want = kinds[spec["type"]]
        if value is None:
            continue
        if isinstance(value, bool) and spec["type"] in ("integer", "number"):
            problems.append(f"'{key}' should be a {spec['type']}")
        elif not isinstance(value, want):
            problems.append(f"'{key}' should be a {spec['type']}")
        elif "enum" in spec and value not in spec["enum"]:
            problems.append(f"'{key}' must be one of {', '.join(spec['enum'])}")
        elif spec["type"] == "array" and not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            problems.append(f"'{key}' should be a list of numbers")
    return problems


def call(name, args, connect=read_only_connection):
    """Runs one tool. Raises KeyError for an unknown tool, DeskError for a
    problem the caller should see (bad arguments, unknown CUSIP, no data)."""
    tool = BY_NAME[name]
    problems = check_arguments(tool["input_schema"], args)
    if problems:
        raise DeskError("Invalid arguments: " + "; ".join(problems))
    if tool.get("writes"):
        return tool["handler"](**{k: v for k, v in args.items() if v is not None})
    conn = connect()
    try:
        return tool["handler"](conn, **{k: v for k, v in args.items() if v is not None})
    finally:
        conn.close()
