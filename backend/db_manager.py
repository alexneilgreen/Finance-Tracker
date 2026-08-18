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
from datetime import datetime, date, timedelta
from pathlib import Path
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Paths — resolved relative to this file so the app is fully portable
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
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    date        TEXT NOT NULL,
    amount      REAL NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS valuations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL,
    date        TEXT NOT NULL,
    value       REAL NOT NULL,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS pay_schedule (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton row
    annual_income      REAL NOT NULL DEFAULT 0,
    anchor_date        TEXT,                      -- a known pay date, 'YYYY-MM-DD'
    payments_per_year  INTEGER NOT NULL DEFAULT 26
);

CREATE TABLE IF NOT EXISTS pay_schedule_deductions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    amount  REAL NOT NULL DEFAULT 0     -- per-paycheck amount
);

CREATE TABLE IF NOT EXISTS pay_schedule_investments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    amount    REAL NOT NULL DEFAULT 0,  -- per-paycheck amount
    is_match  INTEGER NOT NULL DEFAULT 0
);
"""


def init_db():
    """Create the database file and all tables if they don't already exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "investments", "is_match", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "transactions", "import_hash", "TEXT")
        _ensure_column(conn, "transactions", "import_category", "TEXT")
        _ensure_column(conn, "transactions", "import_subcategory", "TEXT")
        _ensure_column(conn, "transactions", "import_merchant", "TEXT")
        _ensure_column(conn, "pending_credits", "resolved", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "pending_credits", "resolution", "TEXT")
        conn.commit()


def _ensure_column(conn, table, column, ddl_type):
    """Adds a column if it's missing — lets older databases (from before this
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
# Pay Schedule — for a fixed biweekly (or other N-payments-per-year) payroll,
# lets the person enter their ANNUAL income once instead of re-entering a
# paycheck every month. Deductions/investments are entered as PER-PAYCHECK
# amounts; each month's actual totals are however many paychecks land in
# that calendar month (biweekly means most months get 2, but ~2 months a
# year get a 3rd, since 26 x 14 days is a few days short of a full year and
# that drift eventually pushes a pay date across a month boundary).
# ---------------------------------------------------------------------------
def get_pay_schedule():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pay_schedule WHERE id = 1").fetchone()
        if not row:
            return None
        schedule = dict(row)
        schedule["deductions"] = rows_to_dicts(
            conn.execute("SELECT * FROM pay_schedule_deductions ORDER BY id").fetchall()
        )
        schedule["investments"] = rows_to_dicts(
            conn.execute("SELECT * FROM pay_schedule_investments ORDER BY id").fetchall()
        )
        return schedule


def save_pay_schedule(annual_income, anchor_date, payments_per_year=26):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO pay_schedule (id, annual_income, anchor_date, payments_per_year)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   annual_income = excluded.annual_income,
                   anchor_date = excluded.anchor_date,
                   payments_per_year = excluded.payments_per_year""",
            (annual_income, anchor_date, payments_per_year),
        )
        conn.commit()


def _pay_dates_in_month(anchor_date_str, interval_days, month_str):
    """Every pay date that falls in month_str, given a fixed cadence of
    interval_days starting from anchor_date_str (which can be any known
    pay date — past, present, or future — since the cadence is periodic)."""
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


def get_pay_schedule_summary(month):
    """Returns this month's pay-schedule-derived totals, or None if no
    schedule has been configured yet."""
    schedule = get_pay_schedule()
    if not schedule or not schedule["anchor_date"] or schedule["annual_income"] <= 0:
        return None

    interval_days = round(365.25 / schedule["payments_per_year"])
    pay_dates = _pay_dates_in_month(schedule["anchor_date"], interval_days, month)
    count = len(pay_dates)

    per_check_gross = schedule["annual_income"] / schedule["payments_per_year"]
    per_check_deductions = sum(d["amount"] for d in schedule["deductions"])
    per_check_investments = sum(i["amount"] for i in schedule["investments"] if not i["is_match"])
    per_check_match = sum(i["amount"] for i in schedule["investments"] if i["is_match"])

    gross = per_check_gross * count
    total_deductions = per_check_deductions * count
    total_investments = per_check_investments * count
    total_match = per_check_match * count

    return {
        "payment_count": count,
        "pay_dates": pay_dates,
        "per_check_gross": per_check_gross,
        "per_check_deductions": per_check_deductions,
        "per_check_investments": per_check_investments,
        "per_check_match": per_check_match,
        "gross": gross,
        "total_deductions": total_deductions,
        "total_investments": total_investments,
        "total_match": total_match,
        "net_take_home": gross - total_deductions - total_investments,
        "deductions": schedule["deductions"],
        "investments": schedule["investments"],
    }


def add_pay_schedule_deduction(name, amount):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pay_schedule_deductions (name, amount) VALUES (?, ?)", (name, amount)
        )
        conn.commit()
        return cur.lastrowid


def add_pay_schedule_investment(name, amount, is_match):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pay_schedule_investments (name, amount, is_match) VALUES (?, ?, ?)",
            (name, amount, 1 if is_match else 0),
        )
        conn.commit()
        return cur.lastrowid


def get_income_summary(month):
    """
    Returns gross pay, total deductions, total EMPLOYEE pre-tax investments,
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

        schedule_summary = get_pay_schedule_summary(month)
        if schedule_summary:
            gross += schedule_summary["gross"]
            total_deductions += schedule_summary["total_deductions"]
            total_investments += schedule_summary["total_investments"]
            total_match += schedule_summary["total_match"]

        net_take_home = gross - total_deductions - total_investments
        return {
            "income": income_rows,
            "gross": gross,
            "total_deductions": total_deductions,
            "total_investments": total_investments,
            "total_match": total_match,
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
    """
    Nodes + links for the zero-based budget Sankey diagram:
    Gross Pay -> {Taxes & Deductions, Pre-Tax Investments, Net Take-Home}
    Employer Match -> Pre-Tax Investments (shown as its own income source,
      since it never passes through Gross Pay and isn't subtracted from
      Net Take-Home, but it does land in the same investments pool).
    Net Take-Home -> {each budget group, Unbudgeted remainder}
    """
    income = get_income_summary(month)
    groups = get_monthly_spending_report(month)

    nodes = [
        {"id": "gross", "label": "Gross Pay", "column": 0},
        {"id": "deductions", "label": "Taxes & Deductions", "column": 1},
        {"id": "investments", "label": "Pre-Tax Investments", "column": 1},
        {"id": "net", "label": "Net Take-Home", "column": 1},
    ]
    links = [
        {"source": "gross", "target": "deductions", "value": income["total_deductions"]},
        {"source": "gross", "target": "investments", "value": income["total_investments"]},
        {"source": "gross", "target": "net", "value": income["net_take_home"]},
    ]

    if income["total_match"] > 0.01:
        nodes.append({"id": "match", "label": "Employer Match", "column": 0})
        links.append({"source": "match", "target": "investments", "value": income["total_match"]})

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


def get_net_worth_history():
    """
    For every account, returns contribution running-total and the latest
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
        return accounts


# ---------------------------------------------------------------------------
# Budget Presets — save the current month's Groups + Line Items + planned
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
    """
    Applies a saved preset to `month`: clears that month's existing line
    items (so re-applying a preset gives a clean slate rather than piling
    up duplicates), then recreates each preset item — reusing any existing
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
# App settings — small generic key/value store. Currently just holds the
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


def import_transactions_csv(csv_text):
    """
    Parses a Truist-style transaction export and inserts each row as a
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
    - A Line Item is auto-assigned when the row's Category/Sub-category/
      Merchant matches a previously learned mapping; otherwise it's left
      unassigned for manual review.
    - Rows categorized "Transfers & Payments" are still imported (nothing
      is silently dropped) but counted separately, since money moving
      between your own accounts or a credit card bill payment usually
      isn't "spending" you want counted in budget reports — the returned
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

                mapping = _lookup_mapping(conn, category, subcategory, merchant)
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
    """Deletes multiple transactions at once — handy for clearing out
    imported rows that turn out to be internal transfers rather than
    real spending."""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", ids)
        conn.commit()


# ---------------------------------------------------------------------------
# Pending Credits — "Credit"-type CSV import rows (Zelle received, refunds,
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