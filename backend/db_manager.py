"""
db_manager.py
--------------
All SQLite access for the Personal Finance & Investment Tracker lives here.
No ORM - just sqlite3 + plain SQL, wrapped in small helper functions so
api.py never has to write raw queries.
"""

import sqlite3
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
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    line_item_id  INTEGER,
    date          TEXT NOT NULL,            -- 'YYYY-MM-DD'
    description   TEXT,
    amount        REAL NOT NULL,
    type          TEXT NOT NULL DEFAULT 'expense',  -- 'expense' | 'income'
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
"""


def init_db():
    """Create the database file and all tables if they don't already exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "investments", "is_match", "INTEGER NOT NULL DEFAULT 0")
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

        net_take_home = gross - total_deductions - total_investments
        return {
            "income": income_rows,
            "gross": gross,
            "total_deductions": total_deductions,
            "total_investments": total_investments,
            "total_match": total_match,
            "net_take_home": net_take_home,
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