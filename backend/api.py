"""
api.py
------
A small local-only Flask app. Every route is prefixed /api/... and talks
to db_manager.py. This is bound to 127.0.0.1 only (see main.py) so nothing
outside the machine can ever reach it.
"""

import io
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory
import pandas as pd

from backend import db_manager as db
from backend import pdf_report

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------------------
# Serve the SPA itself
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/css/<path:filename>")
def css(filename):
    return send_from_directory(FRONTEND_DIR / "css", filename)


@app.route("/js/<path:filename>")
def js(filename):
    return send_from_directory(FRONTEND_DIR / "js", filename)


# ---------------------------------------------------------------------------
# PAGE 1 — BUDGET
# ---------------------------------------------------------------------------
@app.route("/api/budget/groups", methods=["GET", "POST"])
def budget_groups():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "budget_groups",
            {"name": body["name"], "sort_order": body.get("sort_order", 0)},
        )
        return jsonify({"id": new_id}), 201
    return jsonify(db.fetch_all("budget_groups", "1=1 ORDER BY sort_order, name"))


@app.route("/api/budget/groups/<int:group_id>", methods=["DELETE"])
def delete_budget_group(group_id):
    db.delete("budget_groups", group_id)
    return "", 204


@app.route("/api/budget/line_items", methods=["GET", "POST"])
def budget_line_items():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "budget_line_items",
            {
                "group_id": body["group_id"],
                "name": body["name"],
                "planned_amount": body.get("planned_amount", 0),
                "month": body["month"],
            },
        )
        return jsonify({"id": new_id}), 201
    month = request.args.get("month")
    return jsonify(db.get_line_items_for_month(month))


@app.route("/api/budget/line_items/<int:item_id>", methods=["PUT", "DELETE"])
def modify_line_item(item_id):
    if request.method == "DELETE":
        db.delete("budget_line_items", item_id)
        return "", 204
    body = request.get_json()
    db.update("budget_line_items", item_id, {"planned_amount": body["planned_amount"]})
    return "", 204


@app.route("/api/budget/copy_month", methods=["POST"])
def copy_month():
    """Duplicate all line items from one month to another (keeps planning fast)."""
    body = request.get_json()
    src, dst = body["from_month"], body["to_month"]
    items = db.get_line_items_for_month(src)
    for li in items:
        db.insert(
            "budget_line_items",
            {
                "group_id": li["group_id"],
                "name": li["name"],
                "planned_amount": li["planned_amount"],
                "month": dst,
            },
        )
    return jsonify({"copied": len(items)}), 201


@app.route("/api/budget/presets", methods=["GET", "POST"])
def budget_presets():
    if request.method == "POST":
        body = request.get_json()
        name = (body.get("name") or "").strip()
        month = body.get("month")
        if not name:
            return jsonify({"error": "Preset name is required."}), 400
        try:
            new_id = db.save_budget_preset(name, month)
        except ValueError as e:
            return jsonify({"error": str(e)}), 409
        return jsonify({"id": new_id}), 201
    return jsonify(db.get_budget_presets())


@app.route("/api/budget/presets/<int:preset_id>", methods=["DELETE"])
def delete_budget_preset(preset_id):
    db.delete_budget_preset(preset_id)
    return "", 204


@app.route("/api/budget/presets/<int:preset_id>/apply", methods=["POST"])
def apply_budget_preset(preset_id):
    body = request.get_json()
    month = body.get("month")
    if not month:
        return jsonify({"error": "A month is required."}), 400
    try:
        applied = db.apply_budget_preset(preset_id, month)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"applied_items": applied}), 200


# ---------------------------------------------------------------------------
# Income / Deductions / Investments (feeds the Net Take-Home calculation)
# ---------------------------------------------------------------------------
@app.route("/api/income", methods=["GET", "POST"])
def income():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "income",
            {
                "month": body["month"],
                "source": body["source"],
                "gross_amount": body["gross_amount"],
                "pay_date": body.get("pay_date"),
            },
        )
        return jsonify({"id": new_id}), 201
    month = request.args.get("month")
    return jsonify(db.get_income_summary(month))


@app.route("/api/income/<int:income_id>", methods=["PUT", "DELETE"])
def modify_income(income_id):
    if request.method == "DELETE":
        db.delete("income", income_id)
        return "", 204
    body = request.get_json()
    db.update("income", income_id, {"gross_amount": body["gross_amount"]})
    return "", 204


@app.route("/api/income/<int:income_id>/deductions", methods=["POST"])
def add_deduction(income_id):
    body = request.get_json()
    new_id = db.insert(
        "deductions", {"income_id": income_id, "name": body["name"], "amount": body["amount"]}
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/deductions/<int:deduction_id>", methods=["PUT", "DELETE"])
def modify_deduction(deduction_id):
    if request.method == "DELETE":
        db.delete("deductions", deduction_id)
        return "", 204
    body = request.get_json()
    db.update("deductions", deduction_id, {"name": body["name"], "amount": body["amount"]})
    return "", 204


@app.route("/api/income/<int:income_id>/investments", methods=["POST"])
def add_investment(income_id):
    body = request.get_json()
    new_id = db.insert(
        "investments",
        {
            "income_id": income_id,
            "name": body["name"],
            "amount": body["amount"],
            "is_match": 1 if body.get("is_match") else 0,
        },
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/investments/<int:investment_id>", methods=["PUT", "DELETE"])
def modify_investment(investment_id):
    if request.method == "DELETE":
        db.delete("investments", investment_id)
        return "", 204
    body = request.get_json()
    db.update(
        "investments",
        investment_id,
        {
            "name": body["name"],
            "amount": body["amount"],
            "is_match": 1 if body.get("is_match") else 0,
        },
    )
    return "", 204


# ---------------------------------------------------------------------------
# PAGE 2 / TAB A — DAILY LEDGER
# ---------------------------------------------------------------------------
@app.route("/api/transactions", methods=["GET", "POST"])
def transactions():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "transactions",
            {
                "line_item_id": body.get("line_item_id"),
                "date": body["date"],
                "description": body.get("description", ""),
                "amount": body["amount"],
                "type": body.get("type", "expense"),
            },
        )
        return jsonify({"id": new_id}), 201
    month = request.args.get("month")
    where = "strftime('%Y-%m', date) = ?" if month else "1=1"
    params = (month,) if month else ()
    rows = db.fetch_all("transactions", where + " ORDER BY date DESC", params)
    return jsonify(rows)


@app.route("/api/transactions/<int:tx_id>", methods=["DELETE"])
def delete_transaction(tx_id):
    db.delete("transactions", tx_id)
    return "", 204


@app.route("/api/ledger_summary")
def ledger_summary():
    """Planned / Spent / Remaining per line item for the Daily Ledger tab."""
    month = request.args.get("month")
    line_items = db.get_line_items_for_month(month)
    spent_map = db.get_spent_by_line_item(month)
    for li in line_items:
        li["spent"] = spent_map.get(li["id"], 0.0)
        li["remaining"] = li["planned_amount"] - li["spent"]
    return jsonify(line_items)


# ---------------------------------------------------------------------------
# PAGE 2 / TAB B — SINKING FUNDS & GOALS
# ---------------------------------------------------------------------------
@app.route("/api/sinking_funds", methods=["GET", "POST"])
def sinking_funds():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "sinking_funds",
            {
                "name": body["name"],
                "target_amount": body["target_amount"],
                "target_date": body.get("target_date"),
            },
        )
        return jsonify({"id": new_id}), 201

    funds = db.fetch_all("sinking_funds")
    for f in funds:
        contribs = db.fetch_all(
            "sinking_fund_contributions", "fund_id = ?", (f["id"],)
        )
        f["current_amount"] = sum(c["amount"] for c in contribs)
        f["contributions"] = contribs
    return jsonify(funds)


@app.route("/api/sinking_funds/<int:fund_id>", methods=["DELETE"])
def delete_fund(fund_id):
    db.delete("sinking_funds", fund_id)
    return "", 204


@app.route("/api/sinking_funds/<int:fund_id>/contributions", methods=["POST"])
def fund_contribution(fund_id):
    body = request.get_json()
    new_id = db.insert(
        "sinking_fund_contributions",
        {"fund_id": fund_id, "date": body["date"], "amount": body["amount"]},
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/sinking_fund_contributions/<int:contribution_id>", methods=["PUT", "DELETE"])
def modify_fund_contribution(contribution_id):
    if request.method == "DELETE":
        db.delete("sinking_fund_contributions", contribution_id)
        return "", 204
    body = request.get_json()
    db.update("sinking_fund_contributions", contribution_id, {"date": body["date"], "amount": body["amount"]})
    return "", 204


# ---------------------------------------------------------------------------
# PAGE 2 / TAB C — NET WORTH AGGREGATOR
# ---------------------------------------------------------------------------
@app.route("/api/accounts", methods=["GET", "POST"])
def accounts():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "accounts", {"name": body["name"], "account_type": body.get("account_type", "Investment")}
        )
        return jsonify({"id": new_id}), 201
    return jsonify(db.get_net_worth_history())


@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    db.delete("accounts", account_id)
    return "", 204


@app.route("/api/accounts/<int:account_id>/contributions", methods=["POST"])
def add_contribution(account_id):
    body = request.get_json()
    new_id = db.insert(
        "contributions",
        {"account_id": account_id, "date": body["date"], "amount": body["amount"]},
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/contributions/<int:contribution_id>", methods=["PUT", "DELETE"])
def modify_contribution(contribution_id):
    if request.method == "DELETE":
        db.delete("contributions", contribution_id)
        return "", 204
    body = request.get_json()
    db.update("contributions", contribution_id, {"date": body["date"], "amount": body["amount"]})
    return "", 204


@app.route("/api/accounts/<int:account_id>/valuations", methods=["POST"])
def add_valuation(account_id):
    body = request.get_json()
    new_id = db.insert(
        "valuations",
        {"account_id": account_id, "date": body["date"], "value": body["value"]},
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/valuations/<int:valuation_id>", methods=["PUT", "DELETE"])
def modify_valuation(valuation_id):
    if request.method == "DELETE":
        db.delete("valuations", valuation_id)
        return "", 204
    body = request.get_json()
    db.update("valuations", valuation_id, {"date": body["date"], "value": body["value"]})
    return "", 204


# ---------------------------------------------------------------------------
# PAGE 3 — REPORTS
# ---------------------------------------------------------------------------
@app.route("/api/report/monthly_spending")
def report_monthly_spending():
    month = request.args.get("month")
    return jsonify(db.get_monthly_spending_report(month))


@app.route("/api/report/budget_flow")
def report_budget_flow():
    """Nodes/links describing Gross Pay -> Deductions/Investments/Groups,
    consumed by the Sankey diagram on the Report page."""
    month = request.args.get("month")
    return jsonify(db.get_budget_flow(month))


@app.route("/api/report/annual_trend")
def report_annual_trend():
    year = request.args.get("year")
    return jsonify(db.get_annual_trend(year))


@app.route("/api/report/net_worth_history")
def report_net_worth_history():
    return jsonify(db.get_net_worth_history())


@app.route("/api/report/annual_pdf")
def annual_pdf():
    """Multi-page PDF: one page per month (spending donut, budget adherence,
    Sankey flow), then a final page with the annual trend and net worth charts."""
    year = request.args.get("year", str(datetime.now().year))
    buf = pdf_report.generate_annual_report(year)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"Annual_Report_{year}.pdf",
    )


# ---------------------------------------------------------------------------
# EXPORTS — Pandas -> Excel / CSV
# ---------------------------------------------------------------------------
EXPORTABLE_TABLES = {
    "transactions", "budget_groups", "budget_line_items", "income",
    "deductions", "investments", "sinking_funds", "sinking_fund_contributions",
    "accounts", "contributions", "valuations",
}


@app.route("/api/export/<table>")
def export_table(table):
    if table not in EXPORTABLE_TABLES:
        return jsonify({"error": "Unknown or non-exportable table"}), 400

    fmt = request.args.get("format", "xlsx")
    rows = db.fetch_all(table)
    df = pd.DataFrame(rows)

    buffer = io.BytesIO()
    if fmt == "csv":
        df.to_csv(buffer, index=False)
        buffer.seek(0)
        return send_file(buffer, mimetype="text/csv", as_attachment=True,
                          download_name=f"{table}.csv")

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=table[:31])
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{table}.xlsx",
    )