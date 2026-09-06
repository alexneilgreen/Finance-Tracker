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
# PAGE 1 - BUDGET
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


@app.route("/api/budget/copy_forward_preview")
def copy_forward_preview():
    """Whether there's anything to offer copying forward from last month,
    for the 'Copy last month's budget' prompt on the Budget page."""
    month = request.args.get("month")
    if not month:
        return jsonify({"error": "month is required"}), 400
    return jsonify(db.get_copy_forward_preview(month))


@app.route("/api/budget/copy_forward", methods=["POST"])
def copy_forward():
    """Non-destructive month-to-month copy: adds last month's line items
    that aren't already present this month, and leaves anything already
    entered for this month untouched. Unlike applying a preset (which
    clears the month first), this is safe to run even after starting to
    build out the month by hand."""
    body = request.get_json()
    month = body.get("month")
    if not month:
        return jsonify({"error": "month is required"}), 400
    return jsonify(db.copy_budget_forward(month)), 201


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


@app.route("/api/settings/<key>", methods=["GET", "PUT"])
def app_setting(key):
    if request.method == "PUT":
        body = request.get_json()
        db.set_setting(key, body.get("value"))
        return "", 204
    return jsonify({"key": key, "value": db.get_setting(key)})


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


@app.route("/api/pay_schedules", methods=["GET", "POST"])
def pay_schedules():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.create_pay_schedule(
            name=body.get("name"),
            annual_income=body.get("annual_income", 0),
            anchor_date=body.get("anchor_date"),
            payments_per_year=body.get("payments_per_year", 26),
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
        )
        return jsonify({"id": new_id}), 201
    return jsonify(db.get_pay_schedules())


@app.route("/api/pay_schedules/<int:schedule_id>", methods=["PUT", "DELETE"])
def pay_schedule_detail(schedule_id):
    if request.method == "DELETE":
        db.delete_pay_schedule(schedule_id)
        return "", 204
    body = request.get_json()
    db.update_pay_schedule(schedule_id, body)
    return "", 204


@app.route("/api/pay_schedules/<int:schedule_id>/deductions", methods=["POST"])
def add_pay_schedule_deduction(schedule_id):
    body = request.get_json()
    new_id = db.add_pay_schedule_deduction(schedule_id, body["name"], body.get("amount", 0))
    return jsonify({"id": new_id}), 201


@app.route("/api/pay_schedule_deductions/<int:ded_id>", methods=["PUT", "DELETE"])
def modify_pay_schedule_deduction(ded_id):
    if request.method == "DELETE":
        db.delete("pay_schedule_deductions", ded_id)
        return "", 204
    body = request.get_json()
    db.update("pay_schedule_deductions", ded_id, {"name": body["name"], "amount": body["amount"]})
    return "", 204


@app.route("/api/pay_schedules/<int:schedule_id>/investments", methods=["POST"])
def add_pay_schedule_investment(schedule_id):
    body = request.get_json()
    new_id = db.add_pay_schedule_investment(
        schedule_id, body["name"], body.get("amount", 0), body.get("is_match", False)
    )
    return jsonify({"id": new_id}), 201


@app.route("/api/pay_schedule_investments/<int:inv_id>", methods=["PUT", "DELETE"])
def modify_pay_schedule_investment(inv_id):
    if request.method == "DELETE":
        db.delete("pay_schedule_investments", inv_id)
        return "", 204
    body = request.get_json()
    db.update(
        "pay_schedule_investments",
        inv_id,
        {"name": body["name"], "amount": body["amount"], "is_match": 1 if body.get("is_match") else 0},
    )
    return "", 204


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
# PAGE 2 / TAB A - DAILY LEDGER
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


@app.route("/api/transactions/search")
def search_transactions():
    q = request.args.get("q") or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    line_item_id = request.args.get("line_item_id")
    tx_type = request.args.get("type") or None
    min_amount = request.args.get("min_amount")
    max_amount = request.args.get("max_amount")

    rows = db.search_transactions(
        q=q, date_from=date_from, date_to=date_to,
        line_item_id=int(line_item_id) if line_item_id else None,
        tx_type=tx_type,
        min_amount=float(min_amount) if min_amount not in (None, "") else None,
        max_amount=float(max_amount) if max_amount not in (None, "") else None,
    )
    return jsonify(rows)


@app.route("/api/transactions/<int:tx_id>", methods=["PUT", "DELETE"])
def modify_transaction(tx_id):
    if request.method == "DELETE":
        db.delete("transactions", tx_id)
        return "", 204
    body = request.get_json()
    fields = {}
    for key in ("date", "description", "amount", "type"):
        if key in body:
            fields[key] = body[key]
    if "line_item_id" in body:
        fields["line_item_id"] = body["line_item_id"]
    if not fields:
        return jsonify({"error": "No fields to update."}), 400
    db.update_transaction(tx_id, fields)
    return "", 204


@app.route("/api/transactions/<int:tx_id>/split", methods=["POST"])
def split_transaction(tx_id):
    body = request.get_json()
    splits = body.get("splits") or []
    try:
        result = db.split_transaction(tx_id, splits)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 201


@app.route("/api/transactions/split_groups/<split_group_id>/unsplit", methods=["POST"])
def unsplit_transaction(split_group_id):
    try:
        result = db.unsplit_transaction(split_group_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 201


@app.route("/api/transactions/bulk_line_item", methods=["PUT"])
def bulk_update_transaction_line_item():
    body = request.get_json()
    ids = body.get("ids") or []
    line_item_id = body.get("line_item_id")
    if not ids:
        return jsonify({"error": "No transactions selected."}), 400
    if not line_item_id:
        return jsonify({"error": "A Line Item is required."}), 400
    db.bulk_update_transaction_line_item(ids, line_item_id)
    return jsonify({"updated": len(ids)}), 200


@app.route("/api/transactions/bulk_delete", methods=["POST"])
def bulk_delete_transactions():
    body = request.get_json()
    ids = body.get("ids") or []
    if not ids:
        return jsonify({"error": "No transactions selected."}), 400
    db.bulk_delete_transactions(ids)
    return jsonify({"deleted": len(ids)}), 200


@app.route("/api/transactions/clear/count")
def count_clear_transactions():
    """Preview count for the Clear panel - lets the frontend show 'Delete
    N transactions?' before the person confirms the actual delete below."""
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    return jsonify({"count": db.count_transactions_in_range(date_from, date_to)})


@app.route("/api/transactions/clear", methods=["POST"])
def clear_transactions():
    """Removes every transaction in [date_from, date_to], or all of them if
    both are omitted ('All time'). Body: { date_from, date_to } (either or
    both may be null/omitted)."""
    body = request.get_json() or {}
    date_from = body.get("date_from") or None
    date_to = body.get("date_to") or None
    deleted = db.delete_transactions_in_range(date_from, date_to)
    return jsonify({"deleted": deleted}), 200


@app.route("/api/transactions/import", methods=["POST"])
def import_transactions():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    raw_bytes = request.files["file"].read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    result = db.import_transactions_csv(csv_text)
    return jsonify(result), 200


@app.route("/api/automate/rules", methods=["GET", "POST"])
def automate_rules():
    if request.method == "POST":
        body = request.get_json()
        try:
            new_id = db.create_description_rule(
                body.get("pattern"), body.get("group_name"), body.get("item_name")
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        applied = 0
        if body.get("apply_to_existing"):
            applied = db.apply_description_rule_to_existing(new_id, only_unassigned=bool(body.get("only_unassigned", True)))
        return jsonify({"id": new_id, "applied": applied}), 201
    return jsonify(db.get_description_rules())


@app.route("/api/automate/rules/<int:rule_id>", methods=["PUT", "DELETE"])
def automate_rule_detail(rule_id):
    if request.method == "DELETE":
        db.delete_description_rule(rule_id)
        return "", 204
    body = request.get_json()
    try:
        db.update_description_rule(rule_id, body)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return "", 204


@app.route("/api/automate/rules/<int:rule_id>/apply", methods=["POST"])
def automate_rule_apply(rule_id):
    body = request.get_json(silent=True) or {}
    updated = db.apply_description_rule_to_existing(rule_id, only_unassigned=bool(body.get("only_unassigned", True)))
    return jsonify({"updated": updated})


@app.route("/api/automate/rules/apply_to_month", methods=["POST"])
def automate_rules_apply_to_month():
    """Runs every existing Automate rule against one Ledger Month's
    transactions at once. Body: { month: 'YYYY-MM', only_unassigned }."""
    body = request.get_json(silent=True) or {}
    month = body.get("month")
    if not month:
        return jsonify({"error": "A month is required."}), 400
    updated = db.apply_all_rules_to_month(month, only_unassigned=bool(body.get("only_unassigned", True)))
    return jsonify({"updated": updated})


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
# Pending Credits - "Credit"-type import rows waiting to be routed into a
# Sinking Fund or dismissed (see db.import_transactions_csv).
# ---------------------------------------------------------------------------
@app.route("/api/pending_credits", methods=["GET"])
def pending_credits():
    return jsonify(db.get_pending_credits())


@app.route("/api/pending_credits/<int:pending_id>/assign_to_fund", methods=["POST"])
def assign_pending_credit(pending_id):
    body = request.get_json()
    fund_id = body.get("fund_id")
    if not fund_id:
        return jsonify({"error": "A Sinking Fund is required."}), 400
    try:
        db.assign_pending_credit_to_fund(pending_id, fund_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return "", 204


@app.route("/api/pending_credits/<int:pending_id>/assign_to_line_item", methods=["POST"])
def assign_pending_credit_to_category(pending_id):
    body = request.get_json()
    line_item_id = body.get("line_item_id")
    if not line_item_id:
        return jsonify({"error": "A budget category (Line Item) is required."}), 400
    try:
        db.assign_pending_credit_to_line_item(pending_id, line_item_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return "", 204


@app.route("/api/pending_credits/<int:pending_id>", methods=["DELETE"])
def dismiss_pending_credit(pending_id):
    db.dismiss_pending_credit(pending_id)
    return "", 204


# ---------------------------------------------------------------------------
# PAGE 2 / TAB B - SINKING FUNDS & GOALS
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
# PAGE 2 / TAB C - NET WORTH AGGREGATOR
# ---------------------------------------------------------------------------
@app.route("/api/accounts", methods=["GET", "POST"])
def accounts():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "accounts",
            {
                "name": body["name"],
                "account_type": body.get("account_type", "Investment"),
                "risk_profile": body.get("risk_profile", "moderate"),
            },
        )
        return jsonify({"id": new_id}), 201
    return jsonify(db.get_net_worth_history())


@app.route("/api/accounts/<int:account_id>", methods=["PUT", "DELETE"])
def account_detail(account_id):
    if request.method == "DELETE":
        db.delete("accounts", account_id)
        return "", 204
    body = request.get_json()
    fields = {k: body[k] for k in ("name", "account_type", "risk_profile") if k in body}
    if not fields:
        return jsonify({"error": "No editable fields provided."}), 400
    db.update("accounts", account_id, fields)
    return "", 204


@app.route("/api/portfolio/metrics")
def portfolio_metrics():
    # Reuses the existing generic /api/settings/<key> endpoint for the
    # 'portfolio_risk_profile' preference (same pattern as color theme) --
    # no dedicated settings route needed.
    return jsonify(db.get_portfolio_metrics())


@app.route("/api/accounts/<int:account_id>/import_csv", methods=["POST"])
def import_account_csv(account_id):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    raw_bytes = request.files["file"].read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    result = db.import_account_csv(account_id, csv_text)
    return jsonify(result), 200


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
# PAGE 2 / TAB D - DEBT PAYOFF TRACKER
# ---------------------------------------------------------------------------
@app.route("/api/debts", methods=["GET", "POST"])
def debts():
    if request.method == "POST":
        body = request.get_json()
        new_id = db.insert(
            "debts",
            {
                "name": body["name"],
                "debt_type": body.get("debt_type", "Loan"),
                "is_revolving": 1 if body.get("is_revolving") else 0,
                "current_balance": body.get("current_balance", 0),
                "apr": body.get("apr", 0),
                "minimum_payment": body.get("minimum_payment", 0),
                "original_principal": body.get("original_principal"),
                "original_term_months": body.get("original_term_months"),
                "start_date": body.get("start_date"),
                "escrow_amount": body.get("escrow_amount", 0),
            },
        )
        return jsonify({"id": new_id}), 201
    return jsonify(db.get_debts())


@app.route("/api/debts/<int:debt_id>", methods=["PUT", "DELETE"])
def modify_debt(debt_id):
    if request.method == "DELETE":
        db.delete("debts", debt_id)
        return "", 204
    body = request.get_json()
    fields = {}
    for key in ("name", "debt_type", "current_balance", "apr", "minimum_payment",
                "original_principal", "original_term_months", "start_date", "escrow_amount"):
        if key in body:
            fields[key] = body[key]
    if "is_revolving" in body:
        fields["is_revolving"] = 1 if body["is_revolving"] else 0
    if not fields:
        return jsonify({"error": "No fields to update."}), 400
    db.update("debts", debt_id, fields)
    return "", 204


@app.route("/api/debts/<int:debt_id>/payments", methods=["POST"])
def add_debt_payment(debt_id):
    body = request.get_json()
    try:
        result = db.add_debt_payment(debt_id, body["date"], body["amount"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result), 201


@app.route("/api/debt_payments/<int:payment_id>", methods=["DELETE"])
def delete_debt_payment(payment_id):
    db.delete("debt_payments", payment_id)
    return "", 204


@app.route("/api/debts/<int:debt_id>/summary")
def debt_summary(debt_id):
    try:
        return jsonify(db.get_debt_summary(debt_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


@app.route("/api/debt_payoff_plan")
def debt_payoff_plan():
    strategy = request.args.get("strategy", "avalanche")
    extra_monthly = float(request.args.get("extra_monthly", 0) or 0)
    return jsonify(db.get_debt_payoff_plan(strategy=strategy, extra_monthly=extra_monthly))


# ---------------------------------------------------------------------------
# PAGE 3 - REPORTS
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


@app.route("/api/report/multi_year_trend")
def report_multi_year_trend():
    years_param = request.args.get("years", "")
    years = [y.strip() for y in years_param.split(",") if y.strip()]
    if not years:
        return jsonify({"error": "Provide at least one year, e.g. ?years=2025,2026"}), 400
    return jsonify(db.get_multi_year_trend(years))


@app.route("/api/report/savings_rate")
def report_savings_rate():
    year = request.args.get("year")
    return jsonify(db.get_savings_rate_series(year))


@app.route("/api/report/tax_summary")
def report_tax_summary():
    year = request.args.get("year")
    if not year:
        return jsonify({"error": "year is required"}), 400
    # Filing status has no dedicated table - it's just another key in the
    # generic app_settings store, same as the color theme.
    filing_status = db.get_setting("tax_filing_status", "single")
    return jsonify(db.get_year_end_tax_summary(year, filing_status))


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
# EXPORTS - Pandas -> Excel / CSV
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


@app.route("/api/backup/export")
def export_full_backup():
    """The comprehensive "Export to Excel" - one workbook, one sheet per table,
    covering Budget + Track + Report data (everything BACKUP_TABLES lists).
    This exact file format is also what /api/backup/import expects back.
    """
    sheets = db.get_full_backup_data()
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, headers, rows in sheets:
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"ledger_full_backup_{stamp}.xlsx",
    )


@app.route("/api/backup/import", methods=["POST"])
def import_full_backup():
    """Restores the database from a workbook produced by /api/backup/export.
    Destructive for any sheet/table it recognizes - see
    db.import_full_backup_data() for exactly what that means."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    raw_bytes = request.files["file"].read()

    try:
        all_sheets_df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None, engine="openpyxl")
    except Exception as e:
        return jsonify({"error": f"Couldn't read that file as an Excel workbook: {e}"}), 400

    # Map each table's human-readable headers back to raw column names.
    header_lookup = {}
    for sheet_name, table, columns in db.BACKUP_TABLES:
        labels = [db._COLUMN_LABELS.get(c, c.replace("_", " ").title()) for c in columns]
        header_lookup[sheet_name] = dict(zip(labels, columns))

    sheets = {}
    for sheet_name, sheet_df in all_sheets_df.items():
        if sheet_name not in header_lookup:
            continue
        sheet_df = sheet_df.rename(columns=header_lookup[sheet_name])
        sheets[sheet_name] = sheet_df.where(pd.notnull(sheet_df), None).to_dict("records")

    try:
        result = db.import_full_backup_data(sheets)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(result)


@app.route("/api/export/generic", methods=["POST"])
def export_generic():
    """Same Excel/CSV export as /api/export/<table>, but for computed report
    tables that don't exist as a single raw DB table (Budget Adherence,
    Multi-Year Comparison, Savings Rate, Debt Payoff Plan, etc.) -- the
    frontend already has the rendered rows in hand, so it just posts them
    here rather than the backend re-deriving each report a second time.
    Body: { headers: [...], rows: [[...], ...], filename, sheet_name, format }
    """
    body = request.get_json()
    headers = body.get("headers") or None
    rows = body.get("rows") or []
    filename = (body.get("filename") or "export").strip() or "export"
    sheet_name = (body.get("sheet_name") or "Sheet1")[:31]
    fmt = body.get("format", "xlsx")

    df = pd.DataFrame(rows, columns=headers) if headers else pd.DataFrame(rows)

    buffer = io.BytesIO()
    if fmt == "csv":
        df.to_csv(buffer, index=False)
        buffer.seek(0)
        return send_file(buffer, mimetype="text/csv", as_attachment=True,
                          download_name=f"{filename}.csv")

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{filename}.xlsx",
    )