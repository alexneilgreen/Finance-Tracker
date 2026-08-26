"""
db_manager.py
--------------
All SQLite access for the Personal Finance & Investment Tracker lives here.
No ORM - just sqlite3 + plain SQL, wrapped in small helper functions so
api.py never has to write raw queries.
"""

import sqlite3
import csv
import hashlib
import io
import calendar
import math
import statistics
from datetime import datetime, date, timedelta
from pathlib import Path
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Paths - resolved relative to this file so the app is fully portable
# (copy the whole finance_tracker/ folder anywhere and it still works).
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "finance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS budget_line_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    planned_amount  REAL NOT NULL DEFAULT 0,
    month           TEXT NOT NULL,          -- 'YYYY-MM'
    FOREIGN KEY (group_id) REFERENCES budget_groups(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS income (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    month         TEXT NOT NULL,            -- 'YYYY-MM'
    source        TEXT NOT NULL,
    gross_amount  REAL NOT NULL DEFAULT 0,
    pay_date      TEXT
);

CREATE TABLE IF NOT EXISTS deductions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    income_id  INTEGER NOT NULL,
    name       TEXT NOT NULL,
    amount     REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (income_id) REFERENCES income(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS investments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    income_id  INTEGER NOT NULL,
    name       TEXT NOT NULL,               -- e.g. 'TSP Contribution', 'Agency Match'
    amount     REAL NOT NULL DEFAULT 0,
    is_match   INTEGER NOT NULL DEFAULT 0,  -- 1 = employer match (not deducted from paycheck)
    FOREIGN KEY (income_id) REFERENCES income(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS transactions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    line_item_id       INTEGER,
    date               TEXT NOT NULL,            -- 'YYYY-MM-DD'
    description        TEXT,
    amount             REAL NOT NULL,
    type               TEXT NOT NULL DEFAULT 'expense',  -- 'expense' | 'income'
    import_hash        TEXT,                     -- dedupes re-imported CSV rows; NULL for manual entries
    import_category    TEXT,                     -- raw CSV "Category name", kept for mapping-learning
    import_subcategory TEXT,                     -- raw CSV "Sub-category name"
    import_merchant    TEXT,                    -- raw CSV "Merchant name"
    split_group_id     TEXT,                     -- shared by every row a split transaction was divided into; NULL otherwise
    FOREIGN KEY (line_item_id) REFERENCES budget_line_items(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sinking_funds (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    target_amount  REAL NOT NULL DEFAULT 0,
    target_date    TEXT
);

CREATE TABLE IF NOT EXISTS sinking_fund_contributions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id  INTEGER NOT NULL,
    date     TEXT NOT NULL,
    amount   REAL NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES sinking_funds(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,            -- e.g. 'Truist MMA', 'Vanguard Brokerage'
    account_type  TEXT NOT NULL DEFAULT 'Investment'
);

CREATE TABLE IF NOT EXISTS contributions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL,
    date         TEXT NOT NULL,
    amount       REAL NOT NULL,
    import_hash  TEXT,                     -- dedupes re-imported CSV rows; NULL for manual entries
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS valuations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL,
    date         TEXT NOT NULL,
    value        REAL NOT NULL,
    import_hash  TEXT,                     -- dedupes re-imported CSV rows; NULL for manual entries
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS debts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    debt_type             TEXT NOT NULL DEFAULT 'Loan',  -- Mortgage, Auto Loan, Student Loan, Credit Card, Personal Loan, Other
    is_revolving          INTEGER NOT NULL DEFAULT 0,     -- 1 = credit-card-style (no fixed term/payoff formula)
    current_balance       REAL NOT NULL DEFAULT 0,
    apr                   REAL NOT NULL DEFAULT 0,        -- e.g. 6.5 for 6.5%
    minimum_payment       REAL NOT NULL DEFAULT 0,
    original_principal    REAL,                            -- optional, for reference only
    original_term_months  INTEGER,                          -- optional, for reference only
    start_date            TEXT,                             -- optional, 'YYYY-MM-DD'
    escrow_amount         REAL NOT NULL DEFAULT 0          -- mortgage taxes/insurance folded into payment, excluded from amortization
);

CREATE TABLE IF NOT EXISTS debt_payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    debt_id    INTEGER NOT NULL,
    date       TEXT NOT NULL,
    amount     REAL NOT NULL,     -- total payment amount (principal + interest, excl. escrow)
    principal  REAL NOT NULL DEFAULT 0,
    interest   REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (debt_id) REFERENCES debts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pending_credits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT NOT NULL,
    description  TEXT,
    amount       REAL NOT NULL,
    merchant     TEXT,
    category     TEXT,
    subcategory  TEXT,
    import_hash  TEXT UNIQUE,
    resolved     INTEGER NOT NULL DEFAULT 0,  -- 1 once routed to a fund/category or dismissed
    resolution   TEXT                          -- 'fund' | 'category' | 'dismissed'
);

CREATE TABLE IF NOT EXISTS budget_presets (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS budget_preset_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id       INTEGER NOT NULL,
    group_name      TEXT NOT NULL,
    item_name       TEXT NOT NULL,
    planned_amount  REAL NOT NULL DEFAULT 0,
    sort_order      INTEGER DEFAULT 0,
    FOREIGN KEY (preset_id) REFERENCES budget_presets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS import_mappings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_key   TEXT NOT NULL UNIQUE,   -- normalized CSV category/sub-category/merchant text
    group_name  TEXT NOT NULL,
    item_name   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS description_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,   -- substring searched for anywhere in a transaction's description, case-insensitive
    group_name  TEXT NOT NULL,
    item_name   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pay_schedule (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL DEFAULT 'Pay Schedule',  -- e.g. a job name, so multiple schedules (job changes over time) can coexist
    annual_income      REAL NOT NULL DEFAULT 0,
    anchor_date        TEXT,                      -- a known pay date, 'YYYY-MM-DD'
    payments_per_year  INTEGER NOT NULL DEFAULT 26,
    start_date         TEXT,                      -- 'YYYY-MM-DD', optional
    end_date           TEXT                       -- 'YYYY-MM-DD', NULL = on-going
);

CREATE TABLE IF NOT EXISTS pay_schedule_deductions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_schedule_id  INTEGER NOT NULL DEFAULT 1 REFERENCES pay_schedule(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    amount           REAL NOT NULL DEFAULT 0     -- per-paycheck amount
);

CREATE TABLE IF NOT EXISTS pay_schedule_investments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_schedule_id  INTEGER NOT NULL DEFAULT 1 REFERENCES pay_schedule(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    amount           REAL NOT NULL DEFAULT 0,  -- per-paycheck amount
    is_match         INTEGER NOT NULL DEFAULT 0
);
"""


def _migrate_pay_schedule_singleton(conn):
    """The original pay_schedule table only ever allowed one row
    (`id INTEGER PRIMARY KEY CHECK (id = 1)`), which blocked logging more
    than one job/pay schedule over time -- e.g. one schedule for 2025 and a
    different one for 2026 after a job change. Rebuilds the table to drop
    that constraint the first time this runs against an older database,
    carrying the existing row forward as the first schedule (kept at id=1
    so its deductions/investments, which default to pay_schedule_id=1,
    stay attached to it automatically). Safe to call on every startup --
    it's a no-op once the table no longer has the old constraint, including
    on a brand new database that never had it in the first place."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='pay_schedule'"
    ).fetchone()
    if not row or "CHECK (id = 1)" not in (row["sql"] or ""):
        return

    conn.execute("ALTER TABLE pay_schedule RENAME TO pay_schedule_old")
    conn.execute("""
        CREATE TABLE pay_schedule (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT NOT NULL DEFAULT 'Pay Schedule',
            annual_income      REAL NOT NULL DEFAULT 0,
            anchor_date        TEXT,
            payments_per_year  INTEGER NOT NULL DEFAULT 26,
            start_date         TEXT,
            end_date           TEXT
        )
    """)
    old = conn.execute("SELECT * FROM pay_schedule_old WHERE id = 1").fetchone()
    if old:
        conn.execute(
            """INSERT INTO pay_schedule (id, name, annual_income, anchor_date, payments_per_year, start_date, end_date)
               VALUES (1, 'Pay Schedule', ?, ?, ?, ?, ?)""",
            (old["annual_income"], old["anchor_date"], old["payments_per_year"], old["start_date"], old["end_date"]),
        )
    conn.execute("DROP TABLE pay_schedule_old")
    conn.commit()


def init_db():
    """Create the database file and all tables if they don't already exist."""
    with get_conn() as conn:
        _migrate_pay_schedule_singleton(conn)
        conn.executescript(SCHEMA)
        _ensure_column(conn, "investments", "is_match", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "transactions", "import_hash", "TEXT")
        _ensure_column(conn, "transactions", "import_category", "TEXT")
        _ensure_column(conn, "transactions", "import_subcategory", "TEXT")
        _ensure_column(conn, "transactions", "import_merchant", "TEXT")
        _ensure_column(conn, "transactions", "split_group_id", "TEXT")
        _ensure_column(conn, "pending_credits", "resolved", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "pending_credits", "resolution", "TEXT")
        _ensure_column(conn, "pay_schedule", "start_date", "TEXT")
        _ensure_column(conn, "pay_schedule", "end_date", "TEXT")
        _ensure_column(conn, "pay_schedule", "name", "TEXT NOT NULL DEFAULT 'Pay Schedule'")
        _ensure_column(conn, "pay_schedule_deductions", "pay_schedule_id", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "pay_schedule_investments", "pay_schedule_id", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "contributions", "import_hash", "TEXT")
        _ensure_column(conn, "accounts", "risk_profile", "TEXT NOT NULL DEFAULT 'moderate'")
        _ensure_column(conn, "valuations", "import_hash", "TEXT")
        conn.commit()


def _ensure_column(conn, table, column, ddl_type):
    """Adds a column if it's missing - lets older databases (from before this
    column existed) upgrade in place instead of breaking on launch."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


@contextmanager
def get_conn():
    """Yields a sqlite3 connection with foreign keys enforced and Row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Generic helpers used by simple CRUD endpoints (groups, funds, accounts...)
# ---------------------------------------------------------------------------
def fetch_all(table, where="", params=()):
    with get_conn() as conn:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        rows = conn.execute(sql, params).fetchall()
        return rows_to_dicts(rows)


def insert(table, fields: dict):
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        conn.commit()
        return cur.lastrowid


def delete(table, row_id):
    with get_conn() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        conn.commit()


def update(table, row_id, fields: dict):
    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
    with get_conn() as conn:
        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE id = ?",
            tuple(fields.values()) + (row_id,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Domain-specific queries (joins / aggregates that generic helpers can't do)
# ---------------------------------------------------------------------------
def get_line_items_for_month(month):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT li.id, li.name, li.planned_amount, li.month,
                   g.id AS group_id, g.name AS group_name, g.sort_order
            FROM budget_line_items li
            JOIN budget_groups g ON g.id = li.group_id
            WHERE li.month = ?
            ORDER BY g.sort_order, g.name, li.name
            """,
            (month,),
        ).fetchall()
        return rows_to_dicts(rows)


def _previous_month(month):
    year, mo = (int(p) for p in month.split("-"))
    return f"{year - 1}-12" if mo == 1 else f"{year}-{mo - 1:02d}"


def get_copy_forward_preview(month):
    """What copy_budget_forward(month) would do, without doing it -- lets
    the frontend show 'Copy N line items from March 2025?' before the
    person commits. Returns None if the previous month has nothing to
    copy, or if this month already has every one of those line items
    (nothing would actually change)."""
    source_month = _previous_month(month)
    source_items = get_line_items_for_month(source_month)
    if not source_items:
        return None

    existing_names = {(li["group_name"], li["name"]) for li in get_line_items_for_month(month)}
    new_items = [li for li in source_items if (li["group_name"], li["name"]) not in existing_names]
    if not new_items:
        return None

    return {
        "source_month": source_month,
        "count": len(new_items),
        "total_planned": sum(li["planned_amount"] for li in new_items),
    }


def copy_budget_forward(month):
    """Copies every group/line item from the previous month into `month`,
    carrying over each line item's planned_amount as a starting point.
    Idempotent and additive: a line item already present this month (same
    group + name) is left untouched rather than duplicated or overwritten,
    so this is safe to run more than once (e.g. after manually adding a
    couple of this month's items first, then copying the rest forward)."""
    source_month = _previous_month(month)
    source_items = get_line_items_for_month(source_month)
    if not source_items:
        return {"copied": 0, "source_month": source_month}

    existing_names = {(li["group_name"], li["name"]) for li in get_line_items_for_month(month)}
    with get_conn() as conn:
        # Group ids are month-independent (budget_groups isn't scoped by
        # month), so a source item's group_id can be reused directly --
        # only budget_line_items rows need to be created for the new month.
        copied = 0
        for li in source_items:
            if (li["group_name"], li["name"]) in existing_names:
                continue
            conn.execute(
                "INSERT INTO budget_line_items (group_id, name, planned_amount, month) VALUES (?, ?, ?, ?)",
                (li["group_id"], li["name"], li["planned_amount"], month),
            )
            copied += 1
        conn.commit()
    return {"copied": copied, "source_month": source_month}


def get_spent_by_line_item(month):
    """Sum of expense transactions per line item, for a given month."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT line_item_id, SUM(amount) AS spent
            FROM transactions
            WHERE type = 'expense' AND strftime('%Y-%m', date) = ?
            GROUP BY line_item_id
            """,
            (month,),
        ).fetchall()
        return {r["line_item_id"]: r["spent"] for r in rows}


# ---------------------------------------------------------------------------
# Pay Schedule - for a fixed biweekly (or other N-payments-per-year) payroll,
# lets the person enter their ANNUAL income once instead of re-entering a
# paycheck every month. Deductions/investments are entered as PER-PAYCHECK
# amounts; each month's actual totals are however many paychecks land in
# that calendar month (biweekly means most months get 2, but ~2 months a
# year get a 3rd, since 26 x 14 days is a few days short of a full year and
# that drift eventually pushes a pay date across a month boundary).
# ---------------------------------------------------------------------------
def get_pay_schedules():
    """Every pay schedule ever logged (e.g. one job's schedule that ended,
    then a different job's that started) -- most recently started first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pay_schedule ORDER BY (start_date IS NULL), start_date DESC, id DESC"
        ).fetchall()
        schedules = rows_to_dicts(rows)
        for s in schedules:
            s["deductions"] = rows_to_dicts(
                conn.execute(
                    "SELECT * FROM pay_schedule_deductions WHERE pay_schedule_id = ? ORDER BY id", (s["id"],)
                ).fetchall()
            )
            s["investments"] = rows_to_dicts(
                conn.execute(
                    "SELECT * FROM pay_schedule_investments WHERE pay_schedule_id = ? ORDER BY id", (s["id"],)
                ).fetchall()
            )
        return schedules


def get_pay_schedule(schedule_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pay_schedule WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return None
        schedule = dict(row)
        schedule["deductions"] = rows_to_dicts(
            conn.execute(
                "SELECT * FROM pay_schedule_deductions WHERE pay_schedule_id = ? ORDER BY id", (schedule_id,)
            ).fetchall()
        )
        schedule["investments"] = rows_to_dicts(
            conn.execute(
                "SELECT * FROM pay_schedule_investments WHERE pay_schedule_id = ? ORDER BY id", (schedule_id,)
            ).fetchall()
        )
        return schedule


def create_pay_schedule(name=None, annual_income=0, anchor_date=None, payments_per_year=26, start_date=None, end_date=None):
    return insert("pay_schedule", {
        "name": name or "Pay Schedule",
        "annual_income": annual_income,
        "anchor_date": anchor_date,
        "payments_per_year": payments_per_year,
        "start_date": start_date,
        "end_date": end_date,
    })


def update_pay_schedule(schedule_id, fields):
    allowed = {
        k: v for k, v in fields.items()
        if k in ("name", "annual_income", "anchor_date", "payments_per_year", "start_date", "end_date")
    }
    if allowed:
        update("pay_schedule", schedule_id, allowed)


def delete_pay_schedule(schedule_id):
    delete("pay_schedule", schedule_id)  # ON DELETE CASCADE removes its deductions/investments too


def _pay_dates_in_month(anchor_date_str, interval_days, month_str):
    """Every pay date that falls in month_str, given a fixed cadence of
    interval_days starting from anchor_date_str (which can be any known
    pay date - past, present, or future - since the cadence is periodic)."""
    if not anchor_date_str or not interval_days:
        return []
    anchor = datetime.strptime(anchor_date_str, "%Y-%m-%d").date()
    year, month = (int(p) for p in month_str.split("-"))
    month_start = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

    offset = (month_start - anchor).days % interval_days
    first_pay_in_month = month_start + timedelta(days=(interval_days - offset) % interval_days)

    dates = []
    d = first_pay_in_month
    while d < next_month:
        dates.append(d)
        d += timedelta(days=interval_days)
    return [d.strftime("%Y-%m-%d") for d in dates]


# =========================================================================
# Tax withholding / pre-tax benefit classification - used to build the
# Tax Summary card's "Total Withheld for Taxes" figure and to estimate
# taxable income for the bracket-based tax estimate. Deduction line items
# only ever have a free-text `name` (e.g. "Federal Withholding", "Health
# Insurance") with no structured category, so this is a best-effort keyword
# match rather than something guaranteed correct - anything that doesn't
# match either list falls into "other" and is left out of both totals
# rather than guessed at.
# =========================================================================
_TAX_WITHHOLDING_KEYWORDS = (
    "federal", "fed tax", "fed income", "state tax", "state income",
    "local tax", "city tax", "income tax", "withholding", "fica",
    "social security", "medicare", "oasdi",
)
_PRETAX_BENEFIT_KEYWORDS = (
    "401k", "401(k)", "403b", "403(b)", "457", "hsa", "fsa",
    "health insurance", "medical", "dental", "vision", "pretax", "pre-tax",
)


def _classify_deduction(name):
    """Returns 'tax_withholding', 'pretax_benefit', or 'other' for a
    deduction's free-text name. See module note above for caveats."""
    n = (name or "").lower()
    if any(k in n for k in _TAX_WITHHOLDING_KEYWORDS):
        return "tax_withholding"
    if any(k in n for k in _PRETAX_BENEFIT_KEYWORDS):
        return "pretax_benefit"
    return "other"


def get_pay_schedule_summary(month):
    """Returns this month's combined pay-schedule-derived totals across
    every pay schedule whose active window overlaps `month` -- so a job
    change partway through the timeline (schedule A ends, schedule B
    starts) is handled by just logging a second schedule with its own
    start/end date, rather than needing one schedule to somehow cover
    both jobs. Returns None if no schedule contributes anything this month."""
    schedules = get_pay_schedules()
    year, mo = (int(p) for p in month.split("-"))
    month_start = f"{month}-01"
    next_month_start = f"{year + 1}-01-01" if mo == 12 else f"{year}-{mo + 1:02d}-01"

    payment_count = 0
    pay_dates = []
    gross = 0.0
    total_deductions = 0.0
    total_investments = 0.0
    total_match = 0.0
    total_tax_withheld = 0.0
    total_pretax_benefits = 0.0
    all_deductions = []
    all_investments = []
    per_schedule = []

    for schedule in schedules:
        if not schedule["anchor_date"] or schedule["annual_income"] <= 0:
            continue
        # Skip schedules whose active window can't possibly overlap this
        # month at all, before bothering to compute pay dates.
        if schedule.get("start_date") and schedule["start_date"] >= next_month_start:
            continue
        if schedule.get("end_date") and schedule["end_date"] < month_start:
            continue

        interval_days = round(365.25 / schedule["payments_per_year"])
        dates = _pay_dates_in_month(schedule["anchor_date"], interval_days, month)
        if schedule.get("start_date"):
            dates = [d for d in dates if d >= schedule["start_date"]]
        if schedule.get("end_date"):
            dates = [d for d in dates if d <= schedule["end_date"]]
        if not dates:
            continue

        count = len(dates)
        per_check_gross = schedule["annual_income"] / schedule["payments_per_year"]
        per_check_deductions = sum(d["amount"] for d in schedule["deductions"])
        per_check_investments = sum(i["amount"] for i in schedule["investments"] if not i["is_match"])
        per_check_match = sum(i["amount"] for i in schedule["investments"] if i["is_match"])
        per_check_tax_withheld = sum(
            d["amount"] for d in schedule["deductions"] if _classify_deduction(d["name"]) == "tax_withholding"
        )
        per_check_pretax_benefits = sum(
            d["amount"] for d in schedule["deductions"] if _classify_deduction(d["name"]) == "pretax_benefit"
        )

        schedule_gross = per_check_gross * count
        schedule_deductions = per_check_deductions * count
        schedule_investments = per_check_investments * count
        schedule_match = per_check_match * count
        schedule_tax_withheld = per_check_tax_withheld * count
        schedule_pretax_benefits = per_check_pretax_benefits * count

        payment_count += count
        pay_dates.extend(dates)
        gross += schedule_gross
        total_deductions += schedule_deductions
        total_investments += schedule_investments
        total_match += schedule_match
        total_tax_withheld += schedule_tax_withheld
        total_pretax_benefits += schedule_pretax_benefits
        all_deductions.extend(schedule["deductions"])
        all_investments.extend(schedule["investments"])
        per_schedule.append({
            "id": schedule["id"], "name": schedule["name"],
            "payment_count": count, "pay_dates": dates, "per_check_gross": per_check_gross,
            "gross": schedule_gross, "total_deductions": schedule_deductions,
            "total_investments": schedule_investments, "total_match": schedule_match,
            "total_tax_withheld": schedule_tax_withheld, "total_pretax_benefits": schedule_pretax_benefits,
        })

    if not per_schedule:
        return None

    pay_dates.sort()
    return {
        "payment_count": payment_count,
        "pay_dates": pay_dates,
        "gross": gross,
        "total_deductions": total_deductions,
        "total_investments": total_investments,
        "total_match": total_match,
        "total_tax_withheld": total_tax_withheld,
        "total_pretax_benefits": total_pretax_benefits,
        "net_take_home": gross - total_deductions - total_investments,
        "deductions": all_deductions,
        "investments": all_investments,
        "schedules": per_schedule,  # per-schedule breakdown, e.g. for a month that straddles a job change
    }


def add_pay_schedule_deduction(schedule_id, name, amount):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pay_schedule_deductions (pay_schedule_id, name, amount) VALUES (?, ?, ?)",
            (schedule_id, name, amount),
        )
        conn.commit()
        return cur.lastrowid


def add_pay_schedule_investment(schedule_id, name, amount, is_match):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pay_schedule_investments (pay_schedule_id, name, amount, is_match) VALUES (?, ?, ?, ?)",
            (schedule_id, name, amount, 1 if is_match else 0),
        )
        conn.commit()
        return cur.lastrowid


def get_income_summary(month):
    """Returns gross pay, total deductions, total EMPLOYEE pre-tax investments,
    total employer MATCH (informational only), and net take-home for a given
    month, plus each income row with its own nested deductions/investments
    so the Budget page can list, edit, and delete them individually.

    Employer match is intentionally excluded from the net-take-home math:
    it's money added to your investment account, not money that ever left
    your paycheck, so subtracting it would understate what you actually
    have to assign in the zero-based budget.

    If a Pay Schedule is configured (see get_pay_schedule_summary), its
    contribution for this month is folded into every total below, so
    everything downstream (budget assignment, Sankey diagram, reports)
    automatically reflects it with no separate code path.
    """
    with get_conn() as conn:
        income_rows = rows_to_dicts(
            conn.execute("SELECT * FROM income WHERE month = ?", (month,)).fetchall()
        )

        gross = 0.0
        total_deductions = 0.0
        total_investments = 0.0  # employee contributions only
        total_match = 0.0        # employer match, informational only
        total_tax_withheld = 0.0
        total_pretax_benefits = 0.0

        for r in income_rows:
            gross += r["gross_amount"]

            ded_rows = rows_to_dicts(
                conn.execute(
                    "SELECT * FROM deductions WHERE income_id = ? ORDER BY id", (r["id"],)
                ).fetchall()
            )
            inv_rows = rows_to_dicts(
                conn.execute(
                    "SELECT * FROM investments WHERE income_id = ? ORDER BY id", (r["id"],)
                ).fetchall()
            )

            r["deductions"] = ded_rows
            r["investments"] = inv_rows

            total_deductions += sum(d["amount"] for d in ded_rows)
            total_investments += sum(i["amount"] for i in inv_rows if not i["is_match"])
            total_match += sum(i["amount"] for i in inv_rows if i["is_match"])
            total_tax_withheld += sum(d["amount"] for d in ded_rows if _classify_deduction(d["name"]) == "tax_withholding")
            total_pretax_benefits += sum(d["amount"] for d in ded_rows if _classify_deduction(d["name"]) == "pretax_benefit")

        schedule_summary = get_pay_schedule_summary(month)
        if schedule_summary:
            gross += schedule_summary["gross"]
            total_deductions += schedule_summary["total_deductions"]
            total_investments += schedule_summary["total_investments"]
            total_match += schedule_summary["total_match"]
            total_tax_withheld += schedule_summary["total_tax_withheld"]
            total_pretax_benefits += schedule_summary["total_pretax_benefits"]

        net_take_home = gross - total_deductions - total_investments
        return {
            "income": income_rows,
            "gross": gross,
            "total_deductions": total_deductions,
            "total_investments": total_investments,
            "total_match": total_match,
            "total_tax_withheld": total_tax_withheld,
            "total_pretax_benefits": total_pretax_benefits,
            "net_take_home": net_take_home,
            "schedule": schedule_summary,
        }


def get_monthly_spending_report(month):
    """Group-level totals of planned vs spent, for the donut + table on Page 3."""
    line_items = get_line_items_for_month(month)
    spent_map = get_spent_by_line_item(month)

    groups = {}
    for li in line_items:
        g = groups.setdefault(
            li["group_name"], {"group": li["group_name"], "planned": 0.0, "spent": 0.0}
        )
        g["planned"] += li["planned_amount"]
        g["spent"] += spent_map.get(li["id"], 0.0)

    return list(groups.values())


def get_multi_year_trend(years):
    """Total monthly spend (all groups combined) for each requested year, so
    the frontend can overlay several years on one chart."""
    result = {}
    with get_conn() as conn:
        for year in years:
            rows = conn.execute(
                """
                SELECT strftime('%m', t.date) AS month_num, SUM(t.amount) AS total
                FROM transactions t
                WHERE t.type = 'expense' AND strftime('%Y', t.date) = ?
                GROUP BY month_num
                """,
                (str(year),),
            ).fetchall()
            totals_by_month = {r["month_num"]: r["total"] for r in rows}
            result[str(year)] = [round(totals_by_month.get(f"{m:02d}", 0.0), 2) for m in range(1, 13)]
    return result


def get_savings_rate_series(year):
    """Savings Rate for each month of `year` = (money that went to a budget
    group literally named 'Savings') / (Net Take-Home that month). A month
    with no income logged is returned with savings_rate_pct = None rather
    than a misleading 0%, since the rate is undefined without a denominator.
    """
    results = []
    for m in range(1, 13):
        month_str = f"{year}-{m:02d}"
        income = get_income_summary(month_str)
        net_take_home = income["net_take_home"]
        if net_take_home <= 0:
            results.append({
                "month": month_str, "net_take_home": 0.0,
                "savings_amount": 0.0, "savings_rate_pct": None,
            })
            continue

        groups = get_monthly_spending_report(month_str)
        savings_amount = sum(g["spent"] for g in groups if g["group"].strip().lower() == "savings")
        results.append({
            "month": month_str,
            "net_take_home": round(net_take_home, 2),
            "savings_amount": round(savings_amount, 2),
            "savings_rate_pct": round(savings_amount / net_take_home * 100, 2),
        })
    return results


# =========================================================================
# Federal income tax brackets, used only for the Report page's rough
# "estimated tax owed" figure. Sourced from IRS Revenue Procedures via the
# Tax Foundation's published tables:
#   2026: Rev. Proc. 2025-32 (tax year 2026 - most recent available)
#   2025: Rev. Proc. 2024-40, as amended by the OBBBA's standard-deduction
#         increase (signed July 4, 2025)
#   2024: Rev. Proc. 2023-34
# Each entry is (bracket floor, marginal rate %) for taxable income, in
# ascending order. Not tax advice - there's no way to know itemized
# deductions, credits, non-W2 income, etc. from what this app tracks, so
# this is a marginal-bracket estimate against the standard deduction only.
# =========================================================================
FEDERAL_TAX_BRACKETS = {
    2026: {
        "single": [(0, 10), (12400, 12), (50400, 22), (105700, 24), (201775, 32), (256225, 35), (640600, 37)],
        "mfj":    [(0, 10), (24800, 12), (100800, 22), (211400, 24), (403550, 32), (512450, 35), (768700, 37)],
        "hoh":    [(0, 10), (17700, 12), (67450, 22), (105700, 24), (201775, 32), (256200, 35), (640600, 37)],
    },
    2025: {
        "single": [(0, 10), (11925, 12), (48475, 22), (103350, 24), (197300, 32), (250525, 35), (626350, 37)],
        "mfj":    [(0, 10), (23850, 12), (96950, 22), (206700, 24), (394600, 32), (501050, 35), (751600, 37)],
        "hoh":    [(0, 10), (17000, 12), (64850, 22), (103350, 24), (197300, 32), (250500, 35), (626350, 37)],
    },
    2024: {
        "single": [(0, 10), (11600, 12), (47150, 22), (100525, 24), (191950, 32), (243725, 35), (609350, 37)],
        "mfj":    [(0, 10), (23200, 12), (94300, 22), (201050, 24), (383900, 32), (487450, 35), (731200, 37)],
        "hoh":    [(0, 10), (16550, 12), (63100, 22), (100500, 24), (191950, 32), (243700, 35), (609350, 37)],
    },
}

STANDARD_DEDUCTION = {
    2026: {"single": 16100, "mfj": 32200, "hoh": 24150},
    2025: {"single": 15750, "mfj": 31500, "hoh": 23625},
    2024: {"single": 14600, "mfj": 29200, "hoh": 21900},
}

FILING_STATUS_LABELS = {"single": "Single", "mfj": "Married Filing Jointly", "hoh": "Head of Household"}


def _closest_supported_tax_year(year):
    years = sorted(FEDERAL_TAX_BRACKETS.keys())
    if year in FEDERAL_TAX_BRACKETS:
        return year
    return min(years, key=lambda y: abs(y - year))


def estimate_federal_tax(taxable_income, year, filing_status="single"):
    """Marginal-bracket estimate of federal income tax owed on `taxable_income`
    for the given calendar year and filing status. Falls back to the
    nearest year we have brackets for if `year` isn't covered (flagged via
    `bracket_year` in the return value so the frontend can note it).
    """
    filing_status = filing_status if filing_status in FEDERAL_TAX_BRACKETS[2026] else "single"
    bracket_year = _closest_supported_tax_year(int(year))
    brackets = FEDERAL_TAX_BRACKETS[bracket_year][filing_status]
    taxable_income = max(0.0, taxable_income)

    tax = 0.0
    breakdown = []
    for i, (floor, rate) in enumerate(brackets):
        if taxable_income <= floor:
            break
        ceiling = brackets[i + 1][0] if i + 1 < len(brackets) else None
        top_of_slice = min(taxable_income, ceiling) if ceiling is not None else taxable_income
        amount_in_bracket = max(0.0, top_of_slice - floor)
        tax_in_bracket = amount_in_bracket * rate / 100
        tax += tax_in_bracket
        if amount_in_bracket > 0:
            breakdown.append({
                "rate": rate, "floor": floor, "ceiling": ceiling,
                "amount_taxed": round(amount_in_bracket, 2), "tax": round(tax_in_bracket, 2),
            })

    marginal_rate = breakdown[-1]["rate"] if breakdown else brackets[0][1]
    return {
        "bracket_year": bracket_year,
        "filing_status": filing_status,
        "filing_status_label": FILING_STATUS_LABELS[filing_status],
        "taxable_income": round(taxable_income, 2),
        "tax": round(tax, 2),
        "marginal_rate": marginal_rate,
        "effective_rate": round((tax / taxable_income) * 100, 2) if taxable_income > 0 else 0,
        "breakdown": breakdown,
    }


def get_year_end_tax_summary(year, filing_status="single"):
    """A year-end tax-prep summary: gross pay, pre-tax deductions, pre-tax
    investments, employer match, tax withheld, and an estimated federal
    tax liability (marginal-bracket estimate against the standard
    deduction), totaled for the whole year and broken down per income
    source (each pay schedule/job, plus any manually-logged income grouped
    together) so a multi-job year still reads sensibly rather than as one
    blended number.

    Returns None if there was no income at all logged for the year.
    """
    year = int(year)
    totals = {
        "gross": 0.0, "total_deductions": 0.0, "total_investments": 0.0, "total_match": 0.0,
        "total_tax_withheld": 0.0, "total_pretax_benefits": 0.0, "net_take_home": 0.0,
    }
    by_source = {}

    def bucket(key, label):
        return by_source.setdefault(key, {
            "label": label, "gross": 0.0, "total_deductions": 0.0,
            "total_investments": 0.0, "total_match": 0.0,
            "total_tax_withheld": 0.0, "total_pretax_benefits": 0.0,
        })

    for m in range(1, 13):
        month = f"{year}-{m:02d}"
        income = get_income_summary(month)
        totals["gross"] += income["gross"]
        totals["total_deductions"] += income["total_deductions"]
        totals["total_investments"] += income["total_investments"]
        totals["total_match"] += income["total_match"]
        totals["total_tax_withheld"] += income["total_tax_withheld"]
        totals["total_pretax_benefits"] += income["total_pretax_benefits"]
        totals["net_take_home"] += income["net_take_home"]

        for s in (income["schedule"] or {}).get("schedules", []):
            b = bucket(f"schedule_{s['id']}", s["name"])
            b["gross"] += s["gross"]
            b["total_deductions"] += s["total_deductions"]
            b["total_investments"] += s["total_investments"]
            b["total_match"] += s["total_match"]
            b["total_tax_withheld"] += s["total_tax_withheld"]
            b["total_pretax_benefits"] += s["total_pretax_benefits"]

        for row in income["income"]:
            b = bucket("manual", "Other Income")
            b["gross"] += row["gross_amount"]
            b["total_deductions"] += sum(d["amount"] for d in row["deductions"])
            b["total_investments"] += sum(i["amount"] for i in row["investments"] if not i["is_match"])
            b["total_match"] += sum(i["amount"] for i in row["investments"] if i["is_match"])
            b["total_tax_withheld"] += sum(d["amount"] for d in row["deductions"] if _classify_deduction(d["name"]) == "tax_withholding")
            b["total_pretax_benefits"] += sum(d["amount"] for d in row["deductions"] if _classify_deduction(d["name"]) == "pretax_benefit")

    if totals["gross"] <= 0:
        return None

    sources = sorted(by_source.values(), key=lambda b: -b["gross"])
    for s in sources:
        s["net"] = s["gross"] - s["total_deductions"] - s["total_investments"]
        for k in ("gross", "total_deductions", "total_investments", "total_match", "total_tax_withheld", "total_pretax_benefits", "net"):
            s[k] = round(s[k], 2)

    for k in totals:
        totals[k] = round(totals[k], 2)

    # Rough taxable-income estimate: gross pay, minus pre-tax retirement/HSA
    # investments, minus deductions classified as pre-tax benefits (health
    # insurance, etc.), minus the standard deduction for the filing status.
    # Ignores itemizing, credits, non-W2 income, and anything the app has
    # no way to know about - see estimate_federal_tax()'s docstring.
    bracket_year = _closest_supported_tax_year(year)
    standard_deduction = STANDARD_DEDUCTION[bracket_year][filing_status if filing_status in STANDARD_DEDUCTION[bracket_year] else "single"]
    taxable_income = totals["gross"] - totals["total_investments"] - totals["total_pretax_benefits"] - standard_deduction
    estimate = estimate_federal_tax(taxable_income, year, filing_status)
    estimate["standard_deduction"] = standard_deduction
    estimate["amount_withheld"] = totals["total_tax_withheld"]
    estimate["estimated_balance"] = round(totals["total_tax_withheld"] - estimate["tax"], 2)  # positive = refund, negative = owed

    return {"year": year, "sources": sources, "totals": totals, "estimate": estimate}


def get_annual_trend(year):
    """Monthly spend total per group across a calendar year."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT strftime('%m', t.date) AS month_num, g.name AS group_name,
                   SUM(t.amount) AS total
            FROM transactions t
            JOIN budget_line_items li ON li.id = t.line_item_id
            JOIN budget_groups g ON g.id = li.group_id
            WHERE t.type = 'expense' AND strftime('%Y', t.date) = ?
            GROUP BY month_num, g.name
            ORDER BY month_num
            """,
            (str(year),),
        ).fetchall()
        return rows_to_dicts(rows)


def get_budget_flow(month):
    """Nodes + links for the zero-based budget Sankey diagram:
    Gross Pay -> {Taxes & Deductions, Pre-Tax Investments, Net Take-Home}
    Employer Match -> Pre-Tax Investments (shown as its own income source,
      since it never passes through Gross Pay and isn't subtracted from
      Net Take-Home, but it does land in the same investments pool).
    Net Take-Home -> {each budget group, Unbudgeted remainder}

    When more than one pay schedule actually contributed pay this month
    (two jobs at once, or a job change that straddles the month), each
    schedule gets its own income-source node instead of being folded into
    one blended "Gross Pay" node -- so it's visible at a glance how much
    of this month's money came from which job. Any manually-logged income
    rows (not tied to a pay schedule) are grouped into their own "Other
    Income" node alongside them in that case. With 0 or 1 active schedule
    this collapses back to the original single "Gross Pay" node.
    """
    income = get_income_summary(month)
    groups = get_monthly_spending_report(month)
    schedules = (income["schedule"] or {}).get("schedules", [])

    nodes = []
    links = []

    if len(schedules) > 1:
        for s in schedules:
            node_id = f"gross_schedule_{s['id']}"
            nodes.append({"id": node_id, "label": s["name"], "column": 0})
            if s["total_deductions"] > 0.01:
                links.append({"source": node_id, "target": "deductions", "value": s["total_deductions"]})
            if s["total_investments"] > 0.01:
                links.append({"source": node_id, "target": "investments", "value": s["total_investments"]})
            schedule_net = s["gross"] - s["total_deductions"] - s["total_investments"]
            if schedule_net > 0.01:
                links.append({"source": node_id, "target": "net", "value": schedule_net})
            if s["total_match"] > 0.01:
                match_id = f"match_schedule_{s['id']}"
                nodes.append({"id": match_id, "label": f"{s['name']} Match", "column": 0})
                links.append({"source": match_id, "target": "investments", "value": s["total_match"]})

        manual_gross = sum(r["gross_amount"] for r in income["income"])
        if manual_gross > 0.01:
            manual_deductions = sum(d["amount"] for r in income["income"] for d in r["deductions"])
            manual_investments = sum(i["amount"] for r in income["income"] for i in r["investments"] if not i["is_match"])
            manual_match = sum(i["amount"] for r in income["income"] for i in r["investments"] if i["is_match"])

            nodes.append({"id": "gross_manual", "label": "Other Income", "column": 0})
            if manual_deductions > 0.01:
                links.append({"source": "gross_manual", "target": "deductions", "value": manual_deductions})
            if manual_investments > 0.01:
                links.append({"source": "gross_manual", "target": "investments", "value": manual_investments})
            manual_net = manual_gross - manual_deductions - manual_investments
            if manual_net > 0.01:
                links.append({"source": "gross_manual", "target": "net", "value": manual_net})
            if manual_match > 0.01:
                nodes.append({"id": "match_manual", "label": "Other Income Match", "column": 0})
                links.append({"source": "match_manual", "target": "investments", "value": manual_match})
    else:
        nodes.append({"id": "gross", "label": "Gross Pay", "column": 0})
        links.append({"source": "gross", "target": "deductions", "value": income["total_deductions"]})
        links.append({"source": "gross", "target": "investments", "value": income["total_investments"]})
        links.append({"source": "gross", "target": "net", "value": income["net_take_home"]})

        if income["total_match"] > 0.01:
            nodes.append({"id": "match", "label": "Employer Match", "column": 0})
            links.append({"source": "match", "target": "investments", "value": income["total_match"]})

    nodes.extend([
        {"id": "deductions", "label": "Taxes & Deductions", "column": 1},
        {"id": "investments", "label": "Pre-Tax Investments", "column": 1},
        {"id": "net", "label": "Net Take-Home", "column": 1},
    ])

    assigned = 0.0
    for g in groups:
        if g["planned"] <= 0:
            continue
        node_id = f"group_{g['group']}"
        nodes.append({"id": node_id, "label": g["group"], "column": 2})
        links.append({"source": "net", "target": node_id, "value": g["planned"]})
        assigned += g["planned"]

    unbudgeted = income["net_take_home"] - assigned
    if unbudgeted > 0.01:
        nodes.append({"id": "unbudgeted", "label": "Unbudgeted", "column": 2})
        links.append({"source": "net", "target": "unbudgeted", "value": unbudgeted})

    return {"nodes": nodes, "links": links}


def _xirr(cash_flows):
    """cash_flows: list of (datetime, amount) tuples -- negative for money
    going into the account (a contribution), positive for the terminal
    value being pulled back out (the account's current worth). Returns the
    annualized rate as a float (0.084 == 8.4%/yr), or None if it can't be
    solved (e.g. everything flows the same direction, so no real interest
    rate would reconcile them).

    Solved by bisection rather than Newton-Raphson: it needs no derivative,
    and given a bracket with a sign change it can't diverge, which matters
    more here than raw speed for a handful of cash flows.
    """
    if len(cash_flows) < 2:
        return None
    cash_flows = sorted(cash_flows, key=lambda cf: cf[0])
    t0 = cash_flows[0][0]

    def npv(rate):
        total = 0.0
        for d, amt in cash_flows:
            days = (d - t0).days
            total += amt / ((1 + rate) ** (days / 365.0))
        return total

    lo, hi = -0.999, 10.0  # -99.9% to +1000%/yr -- generous enough for any real account
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if (f_lo > 0) == (f_hi > 0):
        return None  # no sign change in range -> no root to bracket

    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def _compute_account_return_metrics(account):
    """Three "how well is this account actually doing" numbers, cheapest to
    most rigorous:
      - simple_return_pct: (current value - total contributed) / total
        contributed. Ignores timing entirely.
      - cagr_pct: annualizes that same growth over the account's elapsed
        history. Still ignores *when* money went in -- a lump sum from day
        one and 8 equal monthly deposits get treated identically -- so it's
        only a rough read, most useful for an account that started with one
        deposit and mostly just sat there.
      - xirr_pct: the cash-flow-timed version -- every contribution dated
        individually against the current value -- so a big deposit made
        last month isn't credited with a full year of growth the way CAGR
        would credit it.
    """
    contributions = account.get("contributions") or []
    valuations = account.get("valuations") or []
    if not contributions and not valuations:
        return None

    total_contributed = sum(c["amount"] for c in contributions)
    if valuations:
        latest_date, latest_value = valuations[-1]["date"], valuations[-1]["value"]
    elif contributions:
        latest_date, latest_value = contributions[-1]["date"], total_contributed
    else:
        return None

    if total_contributed <= 0:
        return None

    simple_return_pct = (latest_value - total_contributed) / total_contributed * 100

    dates = sorted({c["date"] for c in contributions} | {v["date"] for v in valuations})
    first_date = dates[0]
    years = (datetime.strptime(latest_date, "%Y-%m-%d") - datetime.strptime(first_date, "%Y-%m-%d")).days / 365.0

    cagr_pct = None
    if years >= 0.08:  # under ~a month is too short to annualize meaningfully
        try:
            cagr_pct = ((latest_value / total_contributed) ** (1 / years) - 1) * 100
        except (ZeroDivisionError, ValueError):
            cagr_pct = None

    cash_flows = [(datetime.strptime(c["date"], "%Y-%m-%d"), -c["amount"]) for c in contributions]
    cash_flows.append((datetime.strptime(latest_date, "%Y-%m-%d"), latest_value))
    xirr = _xirr(cash_flows)

    return {
        "total_contributed": round(total_contributed, 2),
        "current_value": round(latest_value, 2),
        "as_of": latest_date,
        "simple_return_pct": round(simple_return_pct, 2),
        "cagr_pct": round(cagr_pct, 2) if cagr_pct is not None else None,
        "xirr_pct": round(xirr * 100, 2) if xirr is not None else None,
    }


def _twrr_subperiod_returns(contributions, valuations):
    """Splits an account's history into sub-periods bounded by consecutive
    valuations, and computes the return of each sub-period with
    contributions backed out -- the standard building block behind
    Time-Weighted Rate of Return. Any contributions that landed inside a
    sub-period are treated as if they arrived right before the period's
    ending valuation; that's the standard simplification apps make when
    they don't have a fresh valuation logged at every single cash-flow
    date (the fully precise version, Modified Dietz / true TWRR, needs a
    valuation *at* each cash flow, which manually-logged accounts won't
    usually have).

    Returns a list of {date, days, return} dicts, one per valuation after
    the first -- `date` is the period's end date, `days` is the sub-period
    length, `return` is that period's contribution-adjusted return.
    """
    if len(valuations) < 2:
        return []
    vals = sorted(valuations, key=lambda v: v["date"])
    periods = []
    for i in range(1, len(vals)):
        start_date, start_val = vals[i - 1]["date"], vals[i - 1]["value"]
        end_date, end_val = vals[i]["date"], vals[i]["value"]
        if start_val <= 0:
            continue
        days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
        if days <= 0:
            continue
        cf = sum(c["amount"] for c in contributions if start_date < c["date"] <= end_date)
        r = (end_val - cf - start_val) / start_val
        periods.append({"date": end_date, "days": days, "return": r})
    return periods


def _compute_risk_metrics(account):
    """Four risk/performance numbers built on top of the same sub-period
    return series:
      - twrr_pct: Time-Weighted Rate of Return, annualized. Unlike XIRR,
        this is deliberately blind to *how much* was contributed each
        period -- it only measures how well the money that was invested
        performed, which is the standard way to judge investment
        selection independent of your own deposit behavior.
      - annualized_volatility_pct: how much those periodic returns swing
        around their average, annualized.
      - max_drawdown_pct / drawdown_peak_date / drawdown_trough_date: the
        worst peak-to-trough decline in a synthetic "growth of $1" index
        built from the same TWRR sub-period returns (so, like TWRR, it
        isolates investment performance from new money flowing in).
      - drawdown_duration_days: peak to trough, in days.
      - recovery_date / recovery_days / recovered: trough back to that
        same peak level, or None/False if it hasn't happened yet.
    """
    contributions = account.get("contributions") or []
    valuations = account.get("valuations") or []
    periods = _twrr_subperiod_returns(contributions, valuations)
    if not periods:
        return None

    total_days = sum(p["days"] for p in periods)
    if total_days <= 0:
        return None

    # ---- Time-Weighted Rate of Return ----
    cumulative = 1.0
    for p in periods:
        cumulative *= (1 + p["return"])
    twrr_cumulative_pct = (cumulative - 1) * 100
    years = total_days / 365.0
    twrr_annualized_pct = ((cumulative ** (1 / years)) - 1) * 100 if years > 0 else None

    # ---- Annualized Volatility (needs at least 2 sub-periods for a stdev) ----
    annualized_volatility_pct = None
    if len(periods) >= 2:
        try:
            period_stdev = statistics.stdev(p["return"] for p in periods)
            avg_period_years = (total_days / len(periods)) / 365.0
            if avg_period_years > 0:
                annualized_volatility_pct = period_stdev * math.sqrt(1 / avg_period_years) * 100
        except statistics.StatisticsError:
            annualized_volatility_pct = None

    # ---- Growth-of-$1 index, for Max Drawdown / Duration / Recovery ----
    vals_sorted = sorted(valuations, key=lambda v: v["date"])
    index_points = [{"date": vals_sorted[0]["date"], "index": 1.0}]
    running = 1.0
    for p in periods:
        running *= (1 + p["return"])
        index_points.append({"date": p["date"], "index": running})

    peak_value = index_points[0]["index"]
    peak_date = index_points[0]["date"]
    max_dd = 0.0
    dd_peak_date = peak_date
    dd_trough_date = peak_date
    for pt in index_points[1:]:
        if pt["index"] > peak_value:
            peak_value = pt["index"]
            peak_date = pt["date"]
        dd = (pt["index"] / peak_value) - 1
        if dd < max_dd:
            max_dd = dd
            dd_peak_date = peak_date
            dd_trough_date = pt["date"]

    drawdown_duration_days = None
    recovery_date = None
    recovery_days = None
    recovered = None
    if max_dd < 0:
        drawdown_duration_days = (
            datetime.strptime(dd_trough_date, "%Y-%m-%d") - datetime.strptime(dd_peak_date, "%Y-%m-%d")
        ).days
        peak_index_value = next(pt["index"] for pt in index_points if pt["date"] == dd_peak_date)
        past_trough = False
        for pt in index_points:
            if pt["date"] == dd_trough_date:
                past_trough = True
                continue
            if past_trough and pt["index"] >= peak_index_value:
                recovery_date = pt["date"]
                recovery_days = (
                    datetime.strptime(recovery_date, "%Y-%m-%d") - datetime.strptime(dd_trough_date, "%Y-%m-%d")
                ).days
                recovered = True
                break
        if recovery_date is None:
            recovered = False

    return {
        "twrr_pct": round(twrr_annualized_pct, 2) if twrr_annualized_pct is not None else None,
        "twrr_cumulative_pct": round(twrr_cumulative_pct, 2),
        "annualized_volatility_pct": round(annualized_volatility_pct, 2) if annualized_volatility_pct is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "drawdown_peak_date": dd_peak_date if max_dd < 0 else None,
        "drawdown_trough_date": dd_trough_date if max_dd < 0 else None,
        "drawdown_duration_days": drawdown_duration_days,
        "recovery_date": recovery_date,
        "recovery_days": recovery_days,
        "recovered": recovered,
        "num_return_periods": len(periods),
    }


# ---------------------------------------------------------------------------
# Investment Insights - plain-language "is this actually a good investment,
# or does it just look like one" flags, built on top of the same metrics
# dict _compute_account_return_metrics()/_compute_risk_metrics() already
# produce. Thresholds are banded by a per-account `risk_profile` (Low /
# Medium / High tolerance) rather than one fixed number for every account,
# since a 25% drawdown is unremarkable for a growth account and alarming
# for a cash-like one.
# ---------------------------------------------------------------------------
RISK_PROFILES = ("conservative", "moderate", "aggressive")

RISK_PROFILE_THRESHOLDS = {
    # vol_high / dd_concerning: annualized volatility / max drawdown (in
    # percentage points, e.g. 25.0 == 25%) past which the ride is "a lot"
    # for that risk profile.
    # calmar_poor / calmar_excellent: CAGR / |Max DD| bands.
    # timing_gap_notable: |XIRR - TWRR| gap (percentage points) worth flagging.
    "conservative": {"vol_high": 12.0, "dd_concerning": 15.0, "calmar_poor": 0.5, "calmar_excellent": 1.5, "timing_gap_notable": 2.0},
    "moderate":     {"vol_high": 20.0, "dd_concerning": 25.0, "calmar_poor": 0.4, "calmar_excellent": 1.2, "timing_gap_notable": 3.0},
    "aggressive":   {"vol_high": 35.0, "dd_concerning": 45.0, "calmar_poor": 0.33, "calmar_excellent": 1.0, "timing_gap_notable": 5.0},
}

_INSIGHT_SEVERITY_ORDER = {"warning": 0, "good": 1, "neutral": 2}


def generate_investment_insights(metrics, risk_profile="moderate"):
    """Evaluates one merged metrics dict (return metrics + risk metrics, the
    same shape stored on acc["metrics"]) and returns a list of:
        {"severity": "good" | "warning" | "neutral", "code": str, "label": str, "detail": str}
    ordered warnings-first. `code` is a stable identifier so a renderer can
    style each insight type consistently without string-matching `label`.
    """
    if not metrics:
        return []
    t = RISK_PROFILE_THRESHOLDS.get(risk_profile, RISK_PROFILE_THRESHOLDS["moderate"])
    insights = []

    cagr = metrics.get("cagr_pct")
    xirr = metrics.get("xirr_pct")
    twrr = metrics.get("twrr_pct")
    vol = metrics.get("annualized_volatility_pct")
    max_dd = metrics.get("max_drawdown_pct")  # 0 or negative
    recovery_days = metrics.get("recovery_days")
    recovered = metrics.get("recovered")
    periods = metrics.get("num_return_periods") or 0

    if periods == 0:
        # No valuation-to-valuation sub-period exists yet at all (fewer
        # than 2 valuations logged), so none of the checks below have
        # anything to work with.
        insights.append({
            "severity": "neutral", "code": "insufficient_history",
            "label": "Limited history",
            "detail": "Log at least one more valuation to unlock these insights for this account.",
        })
        return insights
    if periods < 2:
        # A single sub-period is enough for a timing (XIRR vs. TWRR) read,
        # but not for volatility/drawdown, which need a return series.
        insights.append({
            "severity": "neutral", "code": "insufficient_history",
            "label": "Limited history",
            "detail": "Log one more valuation to unlock volatility and drawdown insights for this account.",
        })

    # 1. Timing fluke: XIRR vs. TWRR gap
    if xirr is not None and twrr is not None:
        gap = xirr - twrr
        if gap > t["timing_gap_notable"]:
            insights.append({
                "severity": "good", "code": "timing_luck",
                "label": "Favorable cash-flow timing",
                "detail": f"Your deposit timing added about {gap:.1f} points above the asset's own performance (XIRR {xirr:+.1f}% vs. TWRR {twrr:+.1f}%). The asset itself isn't performing quite as well as your balance suggests.",
            })
        elif gap < -t["timing_gap_notable"]:
            insights.append({
                "severity": "warning", "code": "timing_drag",
                "label": "Timing drag",
                "detail": f"Poor deposit timing cost you about {abs(gap):.1f} points versus just holding the asset (TWRR {twrr:+.1f}% vs. XIRR {xirr:+.1f}%). The underlying investment is doing better than your own return.",
            })

    # 2. Calmar ratio: CAGR per unit of worst-case pain
    if cagr is not None and cagr > 0 and max_dd is not None and max_dd < 0:
        calmar = cagr / abs(max_dd)
        if calmar >= t["calmar_excellent"]:
            insights.append({
                "severity": "good", "code": "calmar_excellent",
                "label": "Strong risk-adjusted return",
                "detail": f"A Calmar ratio of {calmar:.2f} means the annual return ({cagr:+.1f}%) comfortably outweighs the worst historical drawdown ({max_dd:.1f}%).",
            })
        elif calmar < t["calmar_poor"]:
            insights.append({
                "severity": "warning", "code": "calmar_poor",
                "label": "Weak risk-adjusted return",
                "detail": f"A Calmar ratio of {calmar:.2f} means you're enduring disproportionate drawdown ({max_dd:.1f}%) for the return this account delivers ({cagr:+.1f}%).",
            })

    # 3. The "Wild Ride" trap: strong return riding on extreme vol + deep DD
    if (cagr is not None and cagr > 0 and vol is not None and max_dd is not None
            and vol > t["vol_high"] and abs(max_dd) > t["dd_concerning"]):
        insights.append({
            "severity": "warning", "code": "wild_ride",
            "label": "Return may be luck-driven",
            "detail": f"The {cagr:+.1f}% return is riding on extreme volatility ({vol:.1f}%) and a deep drawdown ({max_dd:.1f}%) for this account's risk profile. This return pattern is unstable and could reverse.",
        })

    # 4. Consistently negative, but not a wild ride -- a poor fit, not bad luck
    if cagr is not None and cagr <= 0 and (vol is None or vol <= t["vol_high"]):
        insights.append({
            "severity": "warning", "code": "steady_underperformer",
            "label": "Consistent underperformance",
            "detail": f"This account is losing money ({cagr:+.1f}% CAGR) without especially high volatility -- this looks like a genuinely weak asset fit rather than a rough patch.",
        })

    # 5. Resilience: drawdown depth vs. recovery time
    if max_dd is not None and abs(max_dd) >= t["dd_concerning"]:
        if recovered is False or (recovery_days is not None and recovery_days > 730):
            days_note = f"{recovery_days} days" if recovery_days is not None else "still ongoing"
            insights.append({
                "severity": "warning", "code": "low_resilience",
                "label": "Slow to recover",
                "detail": f"A {max_dd:.1f}% drawdown has taken a long time to recover from ({days_note}). Consider whether you're comfortable seeing this account underwater for that long.",
            })
        elif recovered and recovery_days is not None and recovery_days < 180:
            insights.append({
                "severity": "good", "code": "high_resilience",
                "label": "Bounces back quickly",
                "detail": f"Despite a steep {max_dd:.1f}% drawdown, this account historically recovered in about {recovery_days} days.",
            })

    insights.sort(key=lambda i: _INSIGHT_SEVERITY_ORDER.get(i["severity"], 3))
    return insights


def _account_value_at(account, date):
    """Value of one account as of `date`: latest valuation on/before it, or
    the running contribution total if no valuation has been logged yet.
    Mirrors accountValueAt() in main.js, used there for the same
    carry-forward logic when charting."""
    applicable = [v for v in account.get("valuations", []) if v["date"] <= date]
    if applicable:
        return sorted(applicable, key=lambda v: v["date"])[-1]["value"]
    return sum(c["amount"] for c in account.get("contributions", []) if c["date"] <= date)


def _build_portfolio_series(accounts):
    """Pools every account into one synthetic "portfolio account" -- a single
    valuation series (portfolio value on every date any account has a
    valuation, each account's contribution carried forward via
    _account_value_at) and a single flat contributions list. Feeding this
    into the same _compute_account_return_metrics()/_compute_risk_metrics()
    used per-account gives a true blended growth-of-$1 for the whole
    portfolio -- correctly reflecting that accounts moving differently from
    each other can lower overall volatility and drawdown -- rather than a
    weighted average of each account's separately-computed metrics, which
    would miss that diversification effect entirely.
    """
    all_dates = set()
    for acc in accounts:
        for v in acc.get("valuations", []):
            all_dates.add(v["date"])
    if not all_dates:
        return [], []

    dates = sorted(all_dates)
    pooled_valuations = [
        {"date": d, "value": sum(_account_value_at(acc, d) for acc in accounts)}
        for d in dates
    ]
    pooled_contributions = [
        {"date": c["date"], "amount": c["amount"]}
        for acc in accounts for c in acc.get("contributions", [])
    ]
    return pooled_contributions, pooled_valuations


def compute_portfolio_metrics(accounts, risk_profile="moderate"):
    """Return + risk metrics (and insights) for the whole portfolio blended
    together, using the same growth-of-$1 machinery as a single account.
    Returns None if there's no valuation history anywhere yet."""
    contributions, valuations = _build_portfolio_series(accounts)
    if not valuations:
        return None

    pooled = {"contributions": contributions, "valuations": valuations}
    metrics = _compute_account_return_metrics(pooled)
    risk_metrics = _compute_risk_metrics(pooled)
    if risk_metrics:
        metrics = metrics or {}
        metrics.update(risk_metrics)
    if not metrics:
        return None

    metrics["insights"] = generate_investment_insights(metrics, risk_profile)
    return metrics


def get_portfolio_metrics():
    """Convenience wrapper: loads every account fresh from the DB and
    blends them into portfolio-level metrics, using the saved
    'portfolio_risk_profile' app setting (defaults to 'moderate')."""
    accounts = get_net_worth_history()
    risk_profile = get_setting("portfolio_risk_profile") or "moderate"
    return compute_portfolio_metrics(accounts, risk_profile)


def get_net_worth_history():
    """For every account, returns contribution running-total and the latest
    valuation per date, so the frontend can chart 'money in' vs 'market value'.
    """
    with get_conn() as conn:
        accounts = rows_to_dicts(conn.execute("SELECT * FROM accounts").fetchall())
        for acc in accounts:
            contribs = conn.execute(
                "SELECT id, date, amount FROM contributions WHERE account_id = ? ORDER BY date",
                (acc["id"],),
            ).fetchall()
            vals = conn.execute(
                "SELECT id, date, value FROM valuations WHERE account_id = ? ORDER BY date",
                (acc["id"],),
            ).fetchall()
            acc["contributions"] = rows_to_dicts(contribs)
            acc["valuations"] = rows_to_dicts(vals)

            metrics = _compute_account_return_metrics(acc)
            risk_metrics = _compute_risk_metrics(acc)
            if risk_metrics:
                metrics = metrics or {}
                metrics.update(risk_metrics)
            acc["metrics"] = metrics
            acc["insights"] = generate_investment_insights(metrics, acc.get("risk_profile") or "moderate")
        return accounts


# ---------------------------------------------------------------------------
# Budget Presets - save the current month's Groups + Line Items + planned
# amounts as a reusable named template, and apply it to any month later.
# ---------------------------------------------------------------------------
def get_budget_presets():
    with get_conn() as conn:
        presets = rows_to_dicts(
            conn.execute("SELECT * FROM budget_presets ORDER BY name").fetchall()
        )
        for p in presets:
            items = conn.execute(
                "SELECT * FROM budget_preset_items WHERE preset_id = ? ORDER BY sort_order, id",
                (p["id"],),
            ).fetchall()
            p["items"] = rows_to_dicts(items)
        return presets


def save_budget_preset(name, month):
    """Snapshots every group/line item/planned amount for `month` into a new
    named preset. Raises ValueError on a duplicate name or an empty month."""
    line_items = get_line_items_for_month(month)
    if not line_items:
        raise ValueError("There are no budget line items this month to save.")

    with get_conn() as conn:
        try:
            cur = conn.execute("INSERT INTO budget_presets (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            raise ValueError(f'A preset named "{name}" already exists.')

        preset_id = cur.lastrowid
        for i, li in enumerate(line_items):
            conn.execute(
                """INSERT INTO budget_preset_items
                   (preset_id, group_name, item_name, planned_amount, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (preset_id, li["group_name"], li["name"], li["planned_amount"], i),
            )
        conn.commit()
        return preset_id


def delete_budget_preset(preset_id):
    delete("budget_presets", preset_id)


def apply_budget_preset(preset_id, month):
    """Applies a saved preset to `month`: clears that month's existing line
    items (so re-applying a preset gives a clean slate rather than piling
    up duplicates), then recreates each preset item - reusing any existing
    group with a matching name, or creating a new group if needed.
    Returns the number of line items created. Raises ValueError if the
    preset doesn't exist or has no items.
    """
    with get_conn() as conn:
        preset_items = conn.execute(
            "SELECT * FROM budget_preset_items WHERE preset_id = ? ORDER BY sort_order, id",
            (preset_id,),
        ).fetchall()
        if not preset_items:
            raise ValueError("That preset could not be found.")

        conn.execute("DELETE FROM budget_line_items WHERE month = ?", (month,))

        existing_groups = {
            r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM budget_groups").fetchall()
        }
        max_sort = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM budget_groups"
        ).fetchone()["m"]

        for item in preset_items:
            group_name = item["group_name"]
            if group_name not in existing_groups:
                max_sort += 1
                cur = conn.execute(
                    "INSERT INTO budget_groups (name, sort_order) VALUES (?, ?)",
                    (group_name, max_sort),
                )
                existing_groups[group_name] = cur.lastrowid

            conn.execute(
                """INSERT INTO budget_line_items (group_id, name, planned_amount, month)
                   VALUES (?, ?, ?, ?)""",
                (existing_groups[group_name], item["item_name"], item["planned_amount"], month),
            )

        conn.commit()
        return len(preset_items)


# ---------------------------------------------------------------------------
# App settings - small generic key/value store. Currently just holds the
# selected color theme, but kept generic for any future simple preference.
# ---------------------------------------------------------------------------
def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO app_settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Transaction CSV import (Truist "individual line items" export) + the
# self-improving category-mapping system that auto-assigns a Line Item.
#
# How the auto-assignment learns: every imported row's raw CSV
# Sub-category/Category/Merchant text is kept on the transaction. Whenever
# the user assigns (or bulk-assigns) a Line Item to a transaction that has
# that raw text, we remember "this CSV text -> this Line Item" in
# import_mappings. Future imports check that table first, so the more you
# correct it, the more it gets right automatically.
# ---------------------------------------------------------------------------
CSV_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%m/%d/%y")


def _normalize_match_key(text):
    return (text or "").strip().lower()


def _parse_csv_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in CSV_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_csv_amount(raw):
    cleaned = (raw or "").replace("$", "").replace(",", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned) if cleaned else 0.0


def _compute_import_hash(date, amount, description):
    raw = f"{date}|{amount:.2f}|{(description or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_or_create_group(conn, group_name):
    row = conn.execute("SELECT id FROM budget_groups WHERE name = ?", (group_name,)).fetchone()
    if row:
        return row["id"]
    max_sort = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM budget_groups").fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO budget_groups (name, sort_order) VALUES (?, ?)", (group_name, max_sort + 1)
    )
    return cur.lastrowid


def _find_or_create_line_item(conn, group_name, item_name, month):
    group_id = _find_or_create_group(conn, group_name)
    row = conn.execute(
        "SELECT id FROM budget_line_items WHERE group_id = ? AND name = ? AND month = ?",
        (group_id, item_name, month),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO budget_line_items (group_id, name, planned_amount, month) VALUES (?, ?, 0, ?)",
        (group_id, item_name, month),
    )
    return cur.lastrowid


def _lookup_mapping(conn, category, subcategory, merchant):
    """Tries Sub-category, then Category, then Merchant (most to least
    specific) against the learned mapping table; returns the first hit."""
    for candidate in (subcategory, category, merchant):
        key = _normalize_match_key(candidate)
        if not key:
            continue
        row = conn.execute("SELECT * FROM import_mappings WHERE match_key = ?", (key,)).fetchone()
        if row:
            return dict(row)
    return None


# ---------------------------------------------------------------------------
# Automate tab -- manual, user-authored "if this text appears anywhere in
# the description, categorize it as..." rules. Distinct from import_mappings
# above: that table is auto-learned from an exact match on a bank export's
# Category/Sub-category/Merchant columns, whereas these are deliberately
# created substring rules against the free-text description itself, so a
# merchant like "CHICK-FIL-A #04821" still matches a rule for "CHICK-FIL-A"
# even when the exact merchant string varies transaction to transaction.
# ---------------------------------------------------------------------------
def get_description_rules():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM description_rules ORDER BY LENGTH(pattern) DESC, created_at"
        ).fetchall()
        return rows_to_dicts(rows)


def create_description_rule(pattern, group_name, item_name):
    pattern = (pattern or "").strip()
    if not pattern:
        raise ValueError("Pattern can't be empty.")
    return insert("description_rules", {
        "pattern": pattern, "group_name": group_name, "item_name": item_name,
    })


def update_description_rule(rule_id, fields):
    allowed = {k: v for k, v in fields.items() if k in ("pattern", "group_name", "item_name")}
    if "pattern" in allowed:
        allowed["pattern"] = (allowed["pattern"] or "").strip()
        if not allowed["pattern"]:
            raise ValueError("Pattern can't be empty.")
    if not allowed:
        return
    update("description_rules", rule_id, allowed)


def delete_description_rule(rule_id):
    delete("description_rules", rule_id)


def _lookup_description_rule(rules, description):
    """Case-insensitive substring search across every rule's pattern
    against one transaction's description text. `rules` is already sorted
    longest-pattern-first (see get_description_rules) so a more specific
    rule wins over a shorter, more general one when both would match."""
    desc_lower = (description or "").lower()
    if not desc_lower:
        return None
    for rule in rules:
        if rule["pattern"].lower() in desc_lower:
            return rule
    return None


def apply_description_rule_to_existing(rule_id, only_unassigned=True):
    """Bulk-applies one rule to already-imported transactions whose
    description contains its pattern -- used when a rule is first created,
    so it doesn't only affect future imports. Returns how many rows changed."""
    with get_conn() as conn:
        rule = conn.execute("SELECT * FROM description_rules WHERE id = ?", (rule_id,)).fetchone()
        if not rule:
            return 0
        pattern = rule["pattern"].lower()
        query = "SELECT id, date, description FROM transactions WHERE LOWER(description) LIKE ?"
        params = [f"%{pattern}%"]
        if only_unassigned:
            query += " AND line_item_id IS NULL"
        rows = conn.execute(query, params).fetchall()

        updated = 0
        for row in rows:
            line_item_id = _find_or_create_line_item(conn, rule["group_name"], rule["item_name"], row["date"][:7])
            conn.execute("UPDATE transactions SET line_item_id = ? WHERE id = ?", (line_item_id, row["id"]))
            updated += 1
        conn.commit()
        return updated


def import_account_csv(account_id, csv_text):
    """Imports contributions/valuations for one Net Worth Aggregator account.
    Expected columns: Date, Type, Amount -- Type is 'Contribution' or
    'Valuation' (case-insensitive). For a Contribution row, Amount is the
    amount deposited on that date; for a Valuation row, Amount (or a
    'Value' column, accepted as an alias) is the account's total value as
    of that date. Duplicate rows (matched by account + type + date +
    amount) are skipped so re-importing an overlapping export is safe.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    imported_contributions = 0
    imported_valuations = 0
    skipped_duplicate = 0
    errors = []

    with get_conn() as conn:
        for row_num, row in enumerate(reader, start=2):  # header is row 1
            try:
                date = _parse_csv_date(row.get("Date"))
                if not date:
                    errors.append(f"Row {row_num}: couldn't parse a date, skipped.")
                    continue

                type_raw = (row.get("Type") or "").strip().lower()
                if type_raw not in ("contribution", "valuation"):
                    errors.append(f"Row {row_num}: Type must be 'Contribution' or 'Valuation', skipped.")
                    continue

                amount = _parse_csv_amount(row.get("Amount") or row.get("Value"))
                table = "contributions" if type_raw == "contribution" else "valuations"
                value_col = "amount" if type_raw == "contribution" else "value"
                import_hash = _compute_import_hash(date, amount, f"{account_id}|{type_raw}")

                existing = conn.execute(
                    f"SELECT id FROM {table} WHERE import_hash = ?", (import_hash,)
                ).fetchone()
                if existing:
                    skipped_duplicate += 1
                    continue

                conn.execute(
                    f"INSERT INTO {table} (account_id, date, {value_col}, import_hash) VALUES (?, ?, ?, ?)",
                    (account_id, date, amount, import_hash),
                )
                if type_raw == "contribution":
                    imported_contributions += 1
                else:
                    imported_valuations += 1
            except Exception as e:
                errors.append(f"Row {row_num}: {e}")

        conn.commit()

    return {
        "imported_contributions": imported_contributions,
        "imported_valuations": imported_valuations,
        "skipped_duplicate": skipped_duplicate,
        "errors": errors,
    }


def import_transactions_csv(csv_text):
    """Parses a Truist-style transaction export and inserts each row as a
    Daily Ledger transaction. Columns expected:
    Posted Date, Transaction Date, Transaction Type, Check/Serial #,
    Full description, Merchant name, Category name, Sub-category name,
    Amount, Daily Posted Balance.

    Special handling by Transaction Type, checked before anything else --
    but ONLY when this file actually has a Transaction Type column at all.
    Not every export variant includes it, and the column is matched by a
    normalized (trimmed, case-insensitive) header comparison rather than an
    exact string match, since a slightly different header ("transaction
    type", " Transaction Type ", etc.) would otherwise silently fail to
    match and fall through to the no-column behavior below anyway --
    exactly the kind of mismatch that made every row default to 'expense'
    regardless of its real Transaction Type in a prior version of this
    import. When the column isn't present in this file, every row is
    imported as an ordinary transaction exactly as it would be if this
    feature didn't exist, since there's no type information to route on:
    - "Deposit" / "Interest": skipped entirely, not logged anywhere. These
      represent money landing in the account that's already accounted for
      via Income / Pay Schedule on the Budget page, so logging them as
      Daily Ledger transactions too would double-count them.
    - "Credit": not inserted as a transaction. Instead held as a "pending
      credit" (see the pending_credits table) for the person to manually
      route into a Sinking Fund / Goal contribution on the Daily Ledger
      tab, or dismiss outright, since money like a Zelle payment received
      is often a deliberate fund deposit rather than ordinary income.
    - Everything else (Debit and any unrecognized label): a normal Daily
      Ledger transaction, defaulting to type='expense' unless a negative
      Amount signals otherwise -- Transaction Type is authoritative over
      Amount's sign wherever it's recognized, since some real-world bank
      exports list every Amount as positive regardless of direction.

    - Duplicate rows (matched by date + amount + description) are skipped
      in whichever of transactions/pending_credits they'd land in, so
      re-importing an overlapping date range is safe either way.
    - A Line Item is auto-assigned first from any Automate rule whose
      pattern appears anywhere in the row's description (see
      description_rules / the Automate tab), then falls back to a
      previously learned Category/Sub-category/Merchant mapping; otherwise
      it's left unassigned for manual review.
    - Rows categorized "Transfers & Payments" are still imported (nothing
      is silently dropped) but counted separately, since money moving
      between your own accounts or a credit card bill payment usually
      isn't "spending" you want counted in budget reports - the returned
      `transfer_count` lets the UI flag them for optional bulk deletion.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    # Match the Transaction Type header loosely (trimmed, case-insensitive)
    # instead of requiring an exact "Transaction Type" string, and only
    # engage the skip/Credit-routing logic below when this file's header
    # row actually contains something recognizable as that column.
    transaction_type_col = next(
        (f for f in (reader.fieldnames or []) if (f or "").strip().lower() == "transaction type"),
        None,
    )

    imported = 0
    skipped_duplicate = 0
    unassigned = 0
    transfer_count = 0
    skipped_deposit_interest = 0
    pending_credit_count = 0
    months = set()
    errors = []

    with get_conn() as conn:
        description_rules = get_description_rules()
        for row_num, row in enumerate(reader, start=2):  # header is row 1
            try:
                date_raw = row.get("Transaction Date") or row.get("Posted Date") or ""
                date = _parse_csv_date(date_raw)
                if not date:
                    errors.append(f"Row {row_num}: couldn't parse a date ('{date_raw}'), skipped.")
                    continue

                amount_signed = _parse_csv_amount(row.get("Amount"))
                amount = abs(amount_signed)
                txn_type_raw = (
                    (row.get(transaction_type_col) or "").strip().lower()
                    if transaction_type_col
                    else ""
                )

                merchant = (row.get("Merchant name") or "").strip()
                full_desc = (row.get("Full description") or row.get("Description") or "").strip()
                category = (row.get("Category name") or "").strip()
                subcategory = (row.get("Sub-category name") or "").strip()
                description = merchant or full_desc or "Imported transaction"
                import_hash = _compute_import_hash(date, amount, full_desc or merchant)

                # "Deposit" / "Interest" -- skip entirely, not logged anywhere.
                # Gated on transaction_type_col so this only ever fires for
                # files that actually carry a Transaction Type column.
                if transaction_type_col and txn_type_raw in ("deposit", "interest"):
                    skipped_deposit_interest += 1
                    continue

                # "Credit" -- hold as a pending credit for manual fund
                # assignment instead of inserting as a transaction.
                if transaction_type_col and txn_type_raw == "credit":
                    dup = conn.execute(
                        "SELECT id FROM pending_credits WHERE import_hash = ?", (import_hash,)
                    ).fetchone()
                    if dup:
                        skipped_duplicate += 1
                        continue
                    conn.execute(
                        """INSERT INTO pending_credits
                           (date, description, amount, merchant, category, subcategory, import_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (date, description, amount, merchant, category, subcategory, import_hash),
                    )
                    pending_credit_count += 1
                    months.add(date[:7])
                    continue

                # Everything else: an ordinary Daily Ledger transaction.
                # Transaction Type is authoritative over Amount's sign
                # wherever it's a recognized label -- some real-world bank
                # exports list every Amount as positive regardless of
                # direction, so trusting the sign first would silently
                # mislabel debits as income with no way to fix it later
                # (there's no "type" field for a row that was never even
                # inserted -- distinct from the transactions-table case).
                EXPENSE_TYPES = ("debit", "withdrawal", "payment", "purchase", "fee", "check")
                if txn_type_raw in EXPENSE_TYPES:
                    txn_type = "expense"
                elif amount_signed != 0:
                    txn_type = "income" if amount_signed > 0 else "expense"
                else:
                    txn_type = "expense"

                dup = conn.execute(
                    "SELECT id FROM transactions WHERE import_hash = ?", (import_hash,)
                ).fetchone()
                if dup:
                    skipped_duplicate += 1
                    continue

                if category.lower() == "transfers & payments":
                    transfer_count += 1

                rule_match = _lookup_description_rule(description_rules, description)
                mapping = rule_match or _lookup_mapping(conn, category, subcategory, merchant)
                line_item_id = None
                if mapping:
                    line_item_id = _find_or_create_line_item(
                        conn, mapping["group_name"], mapping["item_name"], date[:7]
                    )
                else:
                    unassigned += 1

                conn.execute(
                    """INSERT INTO transactions
                       (line_item_id, date, description, amount, type,
                        import_hash, import_category, import_subcategory, import_merchant)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (line_item_id, date, description, amount, txn_type,
                     import_hash, category, subcategory, merchant),
                )
                imported += 1
                months.add(date[:7])
            except Exception as e:
                errors.append(f"Row {row_num}: {e}")

        conn.commit()

    return {
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "unassigned": unassigned,
        "transfer_count": transfer_count,
        "skipped_deposit_interest": skipped_deposit_interest,
        "pending_credits": pending_credit_count,
        "months": sorted(months),
        "errors": errors,
    }


def _learn_mapping_from_transactions(conn, ids, line_item_id):
    """After assigning line_item_id to these transactions, remembers the
    CSV category/sub-category/merchant -> Line Item association for any of
    them that came from an import, so future imports auto-match it."""
    if not ids or not line_item_id:
        return
    li = conn.execute(
        """SELECT bli.name AS item_name, bg.name AS group_name
           FROM budget_line_items bli JOIN budget_groups bg ON bg.id = bli.group_id
           WHERE bli.id = ?""",
        (line_item_id,),
    ).fetchone()
    if not li:
        return

    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT import_category, import_subcategory, import_merchant
            FROM transactions WHERE id IN ({placeholders})""",
        ids,
    ).fetchall()
    for r in rows:
        key_source = r["import_subcategory"] or r["import_category"] or r["import_merchant"]
        if not key_source:
            continue
        match_key = _normalize_match_key(key_source)
        conn.execute(
            """INSERT INTO import_mappings (match_key, group_name, item_name) VALUES (?, ?, ?)
               ON CONFLICT(match_key) DO UPDATE SET group_name=excluded.group_name, item_name=excluded.item_name""",
            (match_key, li["group_name"], li["item_name"]),
        )


def search_transactions(q=None, date_from=None, date_to=None, line_item_id=None,
                         tx_type=None, min_amount=None, max_amount=None, limit=500):
    """Cross-month transaction search -- the Daily Ledger tab only ever shows
    one Ledger Month at a time, so this is the escape hatch for "when did I
    buy that" / "show me everything from Amazon this year" style questions
    that span months.
    """
    clauses = []
    params = []

    if q:
        clauses.append("(t.description LIKE ? OR t.import_merchant LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if date_from:
        clauses.append("t.date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("t.date <= ?")
        params.append(date_to)
    if line_item_id:
        clauses.append("t.line_item_id = ?")
        params.append(line_item_id)
    if tx_type:
        clauses.append("t.type = ?")
        params.append(tx_type)
    if min_amount is not None:
        clauses.append("t.amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        clauses.append("t.amount <= ?")
        params.append(max_amount)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, g.name AS group_name, li.name AS line_item_name
            FROM transactions t
            LEFT JOIN budget_line_items li ON li.id = t.line_item_id
            LEFT JOIN budget_groups g ON g.id = li.group_id
            {where}
            ORDER BY t.date DESC, t.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return rows_to_dicts(rows)


def update_transaction(tx_id, fields):
    """Edits a transaction (date/description/amount/line_item_id) and, if a
    Line Item was assigned, teaches the mapping system from it."""
    with get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
        conn.execute(
            f"UPDATE transactions SET {set_clause} WHERE id = ?",
            (*fields.values(), tx_id),
        )
        if "line_item_id" in fields and fields["line_item_id"]:
            _learn_mapping_from_transactions(conn, [tx_id], fields["line_item_id"])
        conn.commit()


def split_transaction(tx_id, splits):
    """Splits one transaction into multiple, one per {line_item_id, amount,
    description} dict in `splits`. The original row is removed and
    replaced by len(splits) new rows sharing a split_group_id (so they
    can be shown together and undone as a unit), each carrying the
    original's date and type.

    The original's import_hash, if it had one (i.e. it came from a CSV
    import), is preserved on the first new row only -- so a future
    re-import of that same bank export row is still recognized as a
    duplicate and skipped, rather than silently recreating the original
    unsplit transaction alongside the split it was divided into.

    Raises ValueError if there are fewer than 2 splits, any split amount
    isn't positive, or the amounts don't sum to the original transaction's
    amount (within a cent, to tolerate rounding).
    """
    if len(splits) < 2:
        raise ValueError("A split needs at least 2 parts.")
    if any(s.get("amount", 0) <= 0 for s in splits):
        raise ValueError("Each split amount must be positive.")

    with get_conn() as conn:
        original = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not original:
            raise ValueError("Transaction not found.")
        original = dict(original)

        total = sum(s["amount"] for s in splits)
        if abs(total - original["amount"]) > 0.01:
            raise ValueError(
                f"Split amounts total {total:.2f}, which doesn't match the original amount of {original['amount']:.2f}."
            )

        split_group_id = f"split_{tx_id}"
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))

        new_ids = []
        for i, s in enumerate(splits):
            cur = conn.execute(
                """INSERT INTO transactions
                   (line_item_id, date, description, amount, type, import_hash,
                    import_category, import_subcategory, import_merchant, split_group_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s.get("line_item_id"),
                    original["date"],
                    (s.get("description") or original["description"] or "").strip(),
                    s["amount"],
                    original["type"],
                    original["import_hash"] if i == 0 else None,
                    original["import_category"],
                    original["import_subcategory"],
                    original["import_merchant"],
                    split_group_id,
                ),
            )
            new_ids.append(cur.lastrowid)

        for new_id, s in zip(new_ids, splits):
            if s.get("line_item_id"):
                _learn_mapping_from_transactions(conn, [new_id], s["line_item_id"])

        conn.commit()

    return {"split_group_id": split_group_id, "transaction_ids": new_ids}


def unsplit_transaction(split_group_id):
    """Merges every transaction sharing a split_group_id back into a
    single transaction (their amounts summed). The merged row is left
    unassigned (no Line Item) since undoing a split means the individual
    per-part assignments no longer apply to the combined amount."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE split_group_id = ? ORDER BY id", (split_group_id,)
        ).fetchall()
        if not rows:
            raise ValueError("No split transactions found for that group.")
        rows = [dict(r) for r in rows]
        total = sum(r["amount"] for r in rows)
        first = rows[0]

        conn.execute("DELETE FROM transactions WHERE split_group_id = ?", (split_group_id,))
        cur = conn.execute(
            """INSERT INTO transactions
               (line_item_id, date, description, amount, type, import_hash,
                import_category, import_subcategory, import_merchant, split_group_id)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                first["date"], first["description"], total, first["type"],
                first["import_hash"], first["import_category"], first["import_subcategory"], first["import_merchant"],
            ),
        )
        conn.commit()
        return {"transaction_id": cur.lastrowid}


def bulk_update_transaction_line_item(ids, line_item_id):
    """Reassigns the Line Item on multiple transactions in one action and
    teaches the mapping system from the batch."""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE transactions SET line_item_id = ? WHERE id IN ({placeholders})",
            (line_item_id, *ids),
        )
        _learn_mapping_from_transactions(conn, ids, line_item_id)
        conn.commit()


def bulk_delete_transactions(ids):
    """Deletes multiple transactions at once - handy for clearing out
    imported rows that turn out to be internal transfers rather than
    real spending."""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", ids)
        conn.commit()


# ---------------------------------------------------------------------------
# Pending Credits - "Credit"-type CSV import rows (Zelle received, refunds,
# reimbursements, etc.) that aren't auto-logged as Daily Ledger transactions,
# since they're often a deliberate deposit toward a Sinking Fund / Goal
# rather than ordinary income. They sit here until the person routes each
# one into a fund (creating a contribution) or dismisses it outright.
# ---------------------------------------------------------------------------
def get_pending_credits():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_credits WHERE resolved = 0 ORDER BY date DESC"
        ).fetchall()
        return rows_to_dicts(rows)


def assign_pending_credit_to_fund(pending_id, fund_id):
    """Logs the pending credit as a contribution to the chosen Sinking Fund
    and marks it resolved. Raises ValueError if it's already been handled
    (e.g. actioned from another window)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_credits WHERE id = ? AND resolved = 0", (pending_id,)
        ).fetchone()
        if not row:
            raise ValueError("That credit has already been handled.")
        conn.execute(
            "INSERT INTO sinking_fund_contributions (fund_id, date, amount) VALUES (?, ?, ?)",
            (fund_id, row["date"], row["amount"]),
        )
        conn.execute(
            "UPDATE pending_credits SET resolved = 1, resolution = 'fund' WHERE id = ?",
            (pending_id,),
        )
        conn.commit()


def assign_pending_credit_to_line_item(pending_id, line_item_id):
    """Logs the pending credit as a NEGATIVE expense transaction against the
    chosen budget Line Item, so it reduces that category's Spent total --
    the standard treatment for money coming back to you against a category
    (a refund, reimbursement, etc.), rather than counting as unrelated
    income. Marks the pending credit resolved. Raises ValueError if it's
    already been handled."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_credits WHERE id = ? AND resolved = 0", (pending_id,)
        ).fetchone()
        if not row:
            raise ValueError("That credit has already been handled.")
        conn.execute(
            """INSERT INTO transactions
               (line_item_id, date, description, amount, type,
                import_hash, import_category, import_subcategory, import_merchant)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (line_item_id, row["date"], row["description"], -abs(row["amount"]), "expense",
             row["import_hash"], row["category"], row["subcategory"], row["merchant"]),
        )
        conn.execute(
            "UPDATE pending_credits SET resolved = 1, resolution = 'category' WHERE id = ?",
            (pending_id,),
        )
        conn.commit()


def dismiss_pending_credit(pending_id):
    """Marks a pending credit dismissed without logging it anywhere -- for
    credits that turn out not to need tracking at all. Kept in the table
    (not deleted) so re-importing the same file recognizes it by its
    import_hash and doesn't resurface it."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_credits SET resolved = 1, resolution = 'dismissed' WHERE id = ?",
            (pending_id,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Debt Payoff Tracker - mirror image of the Net Worth Aggregator: balances
# go down instead of up, driven by an interest formula instead of manual
# valuations. Covers both standard amortizing loans (mortgage, auto,
# student, personal) and revolving debt (credit cards).
# ---------------------------------------------------------------------------
def get_debts():
    with get_conn() as conn:
        debts = rows_to_dicts(conn.execute("SELECT * FROM debts ORDER BY id").fetchall())
        for d in debts:
            payments = conn.execute(
                "SELECT * FROM debt_payments WHERE debt_id = ? ORDER BY date", (d["id"],)
            ).fetchall()
            d["payments"] = rows_to_dicts(payments)
        return debts


def add_debt_payment(debt_id, pay_date, amount):
    """Logs one payment and automatically splits it into interest/principal
    based on the debt's current balance and APR (same math a real loan
    servicer uses), then reduces current_balance by the principal portion --
    so unlike Net Worth accounts, you don't separately "update the value";
    logging a payment IS what moves the balance.
    """
    with get_conn() as conn:
        debt = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
        if not debt:
            raise ValueError("Debt not found.")
        debt = dict(debt)

        monthly_rate = (debt["apr"] / 100) / 12
        interest_portion = round(debt["current_balance"] * monthly_rate, 2)
        principal_portion = round(amount - interest_portion, 2)
        if principal_portion < 0:
            # Payment didn't even cover this period's interest -- balance
            # actually grows; still record it honestly rather than pretending
            # negative principal is zero.
            new_balance = round(debt["current_balance"] - principal_portion, 2)
        else:
            new_balance = round(max(debt["current_balance"] - principal_portion, 0.0), 2)

        conn.execute(
            "INSERT INTO debt_payments (debt_id, date, amount, principal, interest) VALUES (?, ?, ?, ?, ?)",
            (debt_id, pay_date, amount, principal_portion, interest_portion),
        )
        conn.execute("UPDATE debts SET current_balance = ? WHERE id = ?", (new_balance, debt_id))
        conn.commit()
        return {"interest": interest_portion, "principal": principal_portion, "new_balance": new_balance}


def _add_months(d, months):
    """Adds `months` calendar months to date `d`, clamping the day so e.g.
    Jan 31 + 1 month lands on Feb 28/29 instead of overflowing into March."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _amortization_schedule(balance, apr, payment, max_months=600):
    """Simulates paying down `balance` at `apr`% APR with a fixed monthly
    `payment` (works for both a standard installment loan and a revolving
    balance paid at a fixed amount each month). Returns a list of
    {month, interest, principal, balance} rows, stopping when the balance
    hits 0 or max_months (50 years) is reached as a safety valve.

    If `payment` doesn't even cover the first month's interest, the
    schedule comes back empty -- the balance would never shrink, which the
    caller surfaces as a warning rather than looping pointlessly.
    """
    monthly_rate = (apr / 100) / 12
    schedule = []
    bal = balance
    month = 0
    while bal > 0.01 and month < max_months:
        interest = bal * monthly_rate
        if payment <= interest:
            break
        principal = min(payment - interest, bal)
        bal = round(bal - principal, 2)
        month += 1
        schedule.append({"month": month, "interest": round(interest, 2), "principal": round(principal, 2), "balance": bal})
    return schedule


def get_debt_summary(debt_id):
    """Payoff projection for one debt at its current minimum_payment:
    months remaining, total interest still to be paid, and a projected
    payoff date -- or a warning if the minimum payment can't even cover
    the monthly interest."""
    with get_conn() as conn:
        debt = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    if not debt:
        raise ValueError("Debt not found.")
    debt = dict(debt)

    if debt["current_balance"] <= 0.01:
        return {"months_to_payoff": 0, "total_interest": 0.0, "payoff_date": date.today().isoformat(), "warning": None, "schedule": []}

    schedule = _amortization_schedule(debt["current_balance"], debt["apr"], debt["minimum_payment"])
    if not schedule:
        return {
            "months_to_payoff": None, "total_interest": None, "payoff_date": None,
            "warning": "The minimum payment doesn't cover this month's interest, so the balance won't shrink at this payment amount.",
            "schedule": [],
        }

    total_interest = round(sum(r["interest"] for r in schedule), 2)
    months = len(schedule)
    payoff_date = _add_months(date.today(), months).isoformat()
    return {"months_to_payoff": months, "total_interest": total_interest, "payoff_date": payoff_date, "warning": None, "schedule": schedule}


def get_debt_payoff_plan(strategy="avalanche", extra_monthly=0.0, max_months=600):
    """Simulates paying off every debt in parallel: each keeps getting its own
    minimum payment every month, and `extra_monthly` is funneled entirely
    into ONE target debt at a time, chosen by `strategy`:
      - "avalanche": highest APR first (minimizes total interest paid)
      - "snowball":  smallest balance first (clears individual debts fastest)
    The instant a debt hits $0, its minimum payment rolls into the extra
    pool for the next target -- the "snowball" effect either strategy relies
    on to accelerate over time.
    """
    debts = get_debts()
    balances = {d["id"]: d["current_balance"] for d in debts if d["current_balance"] > 0.01}
    aprs = {d["id"]: d["apr"] for d in debts}
    mins = {d["id"]: d["minimum_payment"] for d in debts}
    names = {d["id"]: d["name"] for d in debts}

    if not balances:
        return {"strategy": strategy, "months_to_debt_free": 0, "total_interest": 0.0, "payoff_order": [], "monthly_balance_totals": []}

    def sort_key(debt_id):
        return balances[debt_id] if strategy == "snowball" else -aprs[debt_id]

    total_interest = 0.0
    month = 0
    payoff_order = []
    monthly_totals = []
    extra_pool = extra_monthly

    while balances and month < max_months:
        month += 1

        for debt_id in list(balances.keys()):
            interest = balances[debt_id] * (aprs[debt_id] / 100) / 12
            total_interest += interest
            balances[debt_id] += interest

        for debt_id in list(balances.keys()):
            pay = min(mins[debt_id], balances[debt_id])
            balances[debt_id] -= pay

        pool = extra_pool
        for debt_id in sorted(balances.keys(), key=sort_key):
            if pool <= 0:
                break
            pay = min(pool, balances[debt_id])
            balances[debt_id] -= pay
            pool -= pay

        for debt_id in list(balances.keys()):
            if balances[debt_id] <= 0.01:
                payoff_order.append({"id": debt_id, "name": names[debt_id], "month": month})
                extra_pool += mins[debt_id]
                del balances[debt_id]

        monthly_totals.append(round(sum(balances.values()), 2))

    return {
        "strategy": strategy,
        "months_to_debt_free": month if not balances else None,
        "total_interest": round(total_interest, 2),
        "payoff_order": payoff_order,
        "monthly_balance_totals": monthly_totals,
    }


# ---------------------------------------------------------------------------
# Full-database Excel backup / restore - one sheet per table, covering
# essentially every piece of user data across all three pages (Budget,
# Track, Report). BACKUP_TABLES order matters: parents before children for
# insert-on-restore, since every child table's foreign key needs its
# parent row to already exist. Kept pandas-free on purpose (api.py owns
# turning this into/from an actual .xlsx) so this module's only dependency
# stays sqlite3.
# ---------------------------------------------------------------------------
BACKUP_TABLES = [
    ("Budget Groups", "budget_groups", ["id", "name", "sort_order"]),
    ("Budget Line Items", "budget_line_items", ["id", "group_id", "name", "month", "planned_amount"]),
    ("Budget Presets", "budget_presets", ["id", "name"]),
    ("Budget Preset Items", "budget_preset_items", ["id", "preset_id", "group_name", "item_name", "planned_amount", "sort_order"]),
    ("Income", "income", ["id", "month", "source", "gross_amount", "pay_date"]),
    ("Income Deductions", "deductions", ["id", "income_id", "name", "amount"]),
    ("Income Investments", "investments", ["id", "income_id", "name", "amount", "is_match"]),
    ("Pay Schedule", "pay_schedule", ["id", "name", "annual_income", "anchor_date", "payments_per_year", "start_date", "end_date"]),
    ("Pay Schedule Deductions", "pay_schedule_deductions", ["id", "pay_schedule_id", "name", "amount"]),
    ("Pay Schedule Investments", "pay_schedule_investments", ["id", "pay_schedule_id", "name", "amount", "is_match"]),
    ("Transactions", "transactions", ["id", "line_item_id", "date", "description", "amount", "type", "import_hash", "import_category", "import_subcategory", "import_merchant"]),
    ("Pending Credits", "pending_credits", ["id", "date", "description", "amount", "merchant", "category", "subcategory", "import_hash", "resolved", "resolution"]),
    ("Sinking Funds", "sinking_funds", ["id", "name", "target_amount", "target_date"]),
    ("Fund Contributions", "sinking_fund_contributions", ["id", "fund_id", "date", "amount"]),
    ("Accounts", "accounts", ["id", "name", "account_type", "risk_profile"]),
    ("Account Contributions", "contributions", ["id", "account_id", "date", "amount", "import_hash"]),
    ("Account Valuations", "valuations", ["id", "account_id", "date", "value", "import_hash"]),
    ("Debts", "debts", ["id", "name", "debt_type", "is_revolving", "current_balance", "apr", "minimum_payment", "original_principal", "original_term_months", "start_date", "escrow_amount"]),
    ("Debt Payments", "debt_payments", ["id", "debt_id", "date", "amount", "principal", "interest"]),
    ("Import Mappings", "import_mappings", ["id", "match_key", "group_name", "item_name"]),
    ("Automate Rules", "description_rules", ["id", "pattern", "group_name", "item_name", "created_at"]),
    ("App Settings", "app_settings", ["key", "value"]),
]

# Human-readable column headers for the exported sheets - presentation
# only; import maps these back to the raw column name via BACKUP_TABLES.
_COLUMN_LABELS = {
    "id": "ID", "name": "Name", "sort_order": "Sort Order", "group_id": "Group ID",
    "planned_amount": "Planned Amount", "month": "Month", "preset_id": "Preset ID",
    "group_name": "Group Name", "item_name": "Item Name", "source": "Source",
    "gross_amount": "Gross Amount", "pay_date": "Pay Date", "income_id": "Income ID",
    "amount": "Amount", "is_match": "Is Employer Match", "annual_income": "Annual Income",
    "pay_schedule_id": "Pay Schedule ID",
    "anchor_date": "Anchor Pay Date", "payments_per_year": "Payments Per Year",
    "start_date": "Start Date", "end_date": "End Date", "line_item_id": "Line Item ID",
    "date": "Date", "description": "Description", "type": "Type",
    "import_hash": "Import Hash", "import_category": "Import Category",
    "import_subcategory": "Import Subcategory", "import_merchant": "Import Merchant",
    "merchant": "Merchant", "category": "Category", "subcategory": "Subcategory",
    "resolved": "Resolved", "resolution": "Resolution", "fund_id": "Fund ID",
    "account_id": "Account ID", "account_type": "Account Type", "risk_profile": "Risk Profile", "value": "Value",
    "debt_id": "Debt ID", "debt_type": "Debt Type", "is_revolving": "Is Revolving",
    "current_balance": "Current Balance", "apr": "APR %", "minimum_payment": "Minimum Payment",
    "original_principal": "Original Principal", "original_term_months": "Original Term (Months)",
    "escrow_amount": "Escrow Amount", "principal": "Principal", "interest": "Interest",
    "match_key": "Match Key", "key": "Key", "pattern": "Pattern", "created_at": "Created At",
}


def get_full_backup_data():
    """Returns [(sheet_name, [headers], [[row values], ...]), ...] covering
    every table in BACKUP_TABLES. api.py turns this into an actual multi-
    sheet .xlsx via pandas.
    """
    sheets = []
    with get_conn() as conn:
        for sheet_name, table, columns in BACKUP_TABLES:
            rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            headers = [_COLUMN_LABELS.get(c, c.replace("_", " ").title()) for c in columns]
            data_rows = [[r[c] for c in columns] for r in rows]
            sheets.append((sheet_name, headers, data_rows))
    return sheets


def import_full_backup_data(sheets):
    """Restores the database from a full backup produced by
    get_full_backup_data() and round-tripped through Excel. This is a
    DESTRUCTIVE full replace: every sheet name in `sheets` that matches a
    known table has that table's existing rows cleared and replaced with
    exactly what's in the sheet - primary keys included, so every other
    table's foreign keys stay pointing at the right row. Any sheet/table
    not present in the upload is left completely untouched.

    `sheets`: dict of {sheet_name: [ {raw_column_name: value, ...}, ... ]}
    -- api.py is responsible for parsing the uploaded .xlsx into this shape
    (mapping the human-readable headers back to raw column names).
    """
    known = {name: (table, columns) for name, table, columns in BACKUP_TABLES}
    matched = [(name, known[name][0], known[name][1]) for name in sheets if name in known]
    if not matched:
        raise ValueError("No recognized sheets found in this file - is it a backup exported from this app?")

    conn = sqlite3.connect(DB_PATH)
    try:
        # Off for the duration of this one atomic swap only: deleting a
        # parent before its (about-to-be-deleted-anyway) children would
        # otherwise trip a constraint mid-flight even though the end state
        # is fully consistent.
        conn.execute("PRAGMA foreign_keys = OFF")
        for _name, table, _columns in reversed(matched):
            conn.execute(f"DELETE FROM {table}")
        for name, table, columns in matched:
            for row in sheets[name]:
                values = []
                for c in columns:
                    v = row.get(c)
                    if v is None or (isinstance(v, float) and v != v):  # v != v catches NaN
                        v = None
                    values.append(v)
                placeholders = ", ".join(["?"] * len(columns))
                conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"tables_restored": [name for name, _, _ in matched]}