"""
pdf_report.py
-------------
Builds the multi-page "Annual Report" PDF: one page per month (spending
donut, budget adherence table, zero-based budget Sankey flow) followed by
a final page with the Annual Trend and Net Worth history charts.

Uses matplotlib's non-interactive Agg backend + PdfPages so the whole
document is assembled in memory (no temp files) and handed back as raw
PDF bytes for Flask to stream as a download.

The Sankey layout math here is a direct port of the client-side version
in frontend/js/report.js (same node-stacking + padding-aware scale fix),
so the PDF page matches what's on screen in the app.
"""

import io
import calendar
import textwrap
from datetime import datetime

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.path import Path
from matplotlib.backends.backend_pdf import PdfPages

from backend import db_manager as db

# ---------------------------------------------------------------------------
# Print-friendly palette - light background, legible on paper, echoing the
# app's ink/brass ledger theme without dumping ink across a whole printed page.
# ---------------------------------------------------------------------------
PALETTE = ["#B08D3E", "#4C7A63", "#4C6E8C", "#A14B36", "#7C6592", "#3F8C8F", "#A9822E", "#5D7086"]
MUTED = "#C7CCD3"
TEXT_DARK = "#1C2530"
TEXT_MUTED = "#5B6572"
GRID = "#E3DFD2"
NODE_COLOR = "#B08D3E"


def _fmt(n):
    return f"${n:,.2f}"


def _year_dates(accounts, year):
    """Same logic as the frontend's computeYearDates(): every contribution/
    valuation date actually within `year`, plus a synthetic Jan 1 point (if
    there's data from before the year) so lines start from the correct
    carried-forward value instead of jumping from zero."""
    all_dates = sorted(
        {c["date"] for a in accounts for c in a["contributions"]}
        | {v["date"] for a in accounts for v in a["valuations"]}
    )
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    in_year = [d for d in all_dates if year_start <= d <= year_end]
    has_prior = any(d < year_start for d in all_dates)

    dates = list(in_year)
    if has_prior and year_start not in dates:
        dates.append(year_start)
    return sorted(dates)


def _account_value_at(account, d):
    """Value of one account as of date d: latest valuation on/before d, or
    the running contribution total if no valuation has been logged yet."""
    applicable = [v for v in account["valuations"] if v["date"] <= d]
    if applicable:
        return applicable[-1]["value"]
    return sum(c["amount"] for c in account["contributions"] if c["date"] <= d)


def _smooth_series(y, samples_per_seg=10):
    """Catmull-Rom smoothing over evenly-spaced integer x positions, so the
    PDF line charts curve the same way the frontend's Chart.js lines do
    (tension 0.2-0.25) instead of drawing straight point-to-point segments.
    Returns (x, y) arrays ready to plot; original categorical tick labels
    still line up at the original integer positions."""
    n = len(y)
    if n == 0:
        return [], []
    if n < 3:
        return list(range(n)), list(y)

    pts = [y[0]] + list(y) + [y[-1]]
    xs, ys = [], []
    for i in range(n - 1):
        p0, p1, p2, p3 = pts[i], pts[i + 1], pts[i + 2], pts[i + 3]
        ts = np.linspace(0, 1, samples_per_seg, endpoint=(i == n - 2))
        for t in ts:
            t2, t3 = t * t, t * t * t
            yv = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                        + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
            xs.append(i + t)
            ys.append(yv)
    return xs, ys


def _style_axes(ax):
    ax.set_facecolor("white")
    ax.grid(color=GRID, linewidth=0.6)
    ax.tick_params(colors=TEXT_MUTED, labelsize=7)
    for spine in ax.spines.values():
        spine.set_color(GRID)


def _draw_page_chrome(fig, year, kicker, page_num, total_pages):
    """Small-caps section label above the title, plus a running footer --
    applied to every page after the cover/TOC so the whole document reads
    as one designed report instead of a stack of independently-built
    pages. `kicker` may be None (used for the TOC, which still gets a
    footer but no section label)."""
    if kicker:
        fig.text(0.5, 0.995, kicker.upper(), fontsize=7, color=PALETTE[0], ha="center", va="top",
                  family="sans-serif", weight="bold")
    fig.text(0.06, 0.015, f"Personal Finance Annual Report - {year}", fontsize=7.5, color=TEXT_MUTED, ha="left", va="bottom")
    fig.text(0.94, 0.015, f"Page {page_num} of {total_pages}", fontsize=7.5, color=TEXT_MUTED, ha="right", va="bottom")


def _compute_year_in_review_stats(year, accounts):
    """Headline year-in-review numbers for the cover page -- computed from
    the same data sources (and the same _year_dates/_account_value_at
    carry-forward logic) the Annual Summary charts already use, so the
    cover's numbers always agree with the charts later in the document."""
    dates = _year_dates(accounts, year)
    year_start, year_end = f"{year}-01-01", f"{year}-12-31"

    if dates:
        start_net_worth = sum(_account_value_at(a, dates[0]) for a in accounts)
        end_net_worth = sum(_account_value_at(a, dates[-1]) for a in accounts)
        net_worth_change = end_net_worth - start_net_worth
    else:
        end_net_worth = net_worth_change = None

    total_contributed = sum(
        c["amount"] for a in accounts for c in a["contributions"]
        if year_start <= c["date"] <= year_end
    )

    savings_rows = db.get_savings_rate_series(year)
    rates = [r["savings_rate_pct"] for r in savings_rows if r["savings_rate_pct"] is not None]
    avg_savings_rate = sum(rates) / len(rates) if rates else None

    return {
        "end_net_worth": end_net_worth,
        "net_worth_change": net_worth_change,
        "total_contributed": total_contributed,
        "avg_savings_rate": avg_savings_rate,
    }


def _build_cover_page(pdf, year, stats, generated_on):
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    ax.axhline(0.80, color=PALETTE[0], linewidth=1.2, xmin=0.1, xmax=0.9)
    ax.axhline(0.28, color=PALETTE[0], linewidth=1.2, xmin=0.1, xmax=0.9)

    ax.text(0.5, 0.71, "Personal Finance", fontsize=15, color=TEXT_MUTED, family="serif", ha="center")
    ax.text(0.5, 0.65, "Annual Report", fontsize=32, color=TEXT_DARK, family="serif", weight="bold", ha="center")
    ax.text(0.5, 0.585, str(year), fontsize=20, color=PALETTE[0], family="serif", ha="center")

    stat_items = [
        ("Ending Net Worth", _fmt(stats["end_net_worth"]) if stats["end_net_worth"] is not None else "N/A", None),
        (
            "Net Worth Change",
            f"{'+' if stats['net_worth_change'] >= 0 else ''}{_fmt(stats['net_worth_change'])}" if stats["net_worth_change"] is not None else "N/A",
            (PALETTE[1] if stats["net_worth_change"] >= 0 else PALETTE[3]) if stats["net_worth_change"] is not None else None,
        ),
        ("Total Contributed", _fmt(stats["total_contributed"]), None),
        ("Avg. Savings Rate", f"{stats['avg_savings_rate']:.1f}%" if stats["avg_savings_rate"] is not None else "N/A", None),
    ]
    col_width = 0.8 / len(stat_items)
    for i, (label, value, color) in enumerate(stat_items):
        cx = 0.1 + col_width * (i + 0.5)
        ax.text(cx, 0.50, value, fontsize=15, color=color or TEXT_DARK, family="serif", weight="bold", ha="center")
        ax.text(cx, 0.455, label, fontsize=8.5, color=TEXT_MUTED, ha="center")

    ax.text(0.5, 0.22, f"Generated {generated_on}", fontsize=9, color=TEXT_MUTED, ha="center")

    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_toc_page(pdf, year, entries, total_pages):
    """`entries` is a list of (section_title, page_num) tuples. Rows are
    built as monospaced dot-leader lines (title, then '.' padding, then a
    right-aligned page number) since matplotlib has no native "leader"
    primitive -- a fixed-width font keeps the dots and numbers lined up
    cleanly without needing to measure rendered text width."""
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle("Table of Contents", fontsize=18, color=TEXT_DARK, family="serif", y=0.90)
    ax = fig.add_axes([0.12, 0.15, 0.76, 0.65])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    line_width = 54
    for i, (title, page_num) in enumerate(entries):
        y = 0.97 - i * 0.07
        num_str = str(page_num)
        dots = "." * max(3, line_width - len(title) - len(num_str))
        ax.text(0, y, f"{title}{dots}{num_str}", fontsize=12, color=TEXT_DARK, family="monospace", va="top")

    _draw_page_chrome(fig, year, None, 2, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _draw_donut(ax, groups, net_take_home):
    ax.set_title("Monthly Spending", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if net_take_home <= 0:
        ax.text(0.5, 0.5, "No income logged", ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        ax.axis("off")
        return

    labels = [g["group"] for g in groups]
    # A group whose only transactions this month were refunds/credits can
    # have a negative net "Spent" (see the app's refund convention) - and a
    # negative "Planned" is possible too if the person entered one. Neither
    # is a valid pie wedge size, so clamp per-group values to zero here;
    # this only affects the donut's shape, not the raw numbers shown in the
    # Budget Adherence table below it.
    budgeted = [max(g["planned"], 0.0) for g in groups]
    spent = [max(g["spent"], 0.0) for g in groups]
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(groups))]

    unbudgeted = max(net_take_home - sum(budgeted), 0)
    unspent = max(net_take_home - sum(spent), 0)
    budgeted_ring = budgeted + [unbudgeted]
    spent_ring = spent + [unspent]
    ring_colors = colors + [MUTED]

    # Outer ring = spent, inner ring = budgeted (same convention as the app).
    # Kept compact (radius <1) so this panel doesn't run noticeably taller
    # than the Budget Adherence table sharing this row.
    ax.pie(spent_ring, radius=0.85, colors=ring_colors,
           wedgeprops=dict(width=0.24, edgecolor="white", linewidth=1))
    ax.pie(budgeted_ring, radius=0.61, colors=ring_colors,
           wedgeprops=dict(width=0.24, edgecolor="white", linewidth=1))
    ax.text(0, 0, f"{_fmt(net_take_home)}\nNet Take-Home", ha="center", va="center",
            fontsize=7.5, color=TEXT_DARK)
    ax.axis("equal")

    handles = [patches.Patch(color=colors[i], label=labels[i]) for i in range(len(labels))]
    handles.append(patches.Patch(color=MUTED, label="Unbudgeted / Unspent"))
    if handles:
        ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.02),
                  fontsize=6.5, frameon=False, ncol=2)


def _draw_adherence_table(ax, groups):
    ax.set_title("Budget Adherence", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    ax.axis("off")
    if not groups:
        ax.text(0.5, 0.5, "No budget groups", ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        return

    col_labels = ["Group", "Planned", "Spent", "% Used", "Status"]
    rows = []
    for g in groups:
        planned, spent = g["planned"], g["spent"]
        pct = (spent / planned * 100) if planned > 0 else (100 if spent > 0 else 0)
        status = "Over" if pct > 100 else ("Near limit" if pct >= 90 else "On track")
        rows.append([g["group"], _fmt(planned), _fmt(spent), f"{pct:.0f}%", status])

    # Stretched to fill roughly the same vertical footprint as the donut
    # chart it shares a row with, rather than a compact, mostly-empty box.
    # Capped so a preset with many groups can't overflow the axes.
    n_rows = len(rows) + 1
    height_frac = min(0.16 * n_rows, 0.92)
    y0 = max(1.0 - height_frac, 0.02)
    table = ax.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left", colLoc="left",
                      bbox=[0.0, y0, 1.0, height_frac],
                      colWidths=[0.30, 0.18, 0.18, 0.15, 0.19])
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#EDE7D8")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        else:
            cell.set_facecolor("white")
            cell.set_text_props(color=TEXT_DARK)


def _draw_sankey(ax, flow):
    ax.set_title("Zero-Based Budget Flow", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    ax.axis("off")
    nodes, links = flow["nodes"], flow["links"]
    if not nodes or not links:
        ax.text(0.5, 0.5, "Add income and budget line items to see the flow.",
                ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        return

    width, height = 100.0, 55.0
    node_width, node_padding = 1.4, 2.8
    left_margin, right_margin = 2.0, 32.0

    columns = sorted({n["column"] for n in nodes})
    col_x = {}
    for i, col in enumerate(columns):
        span = width - left_margin - right_margin
        col_x[col] = left_margin + (i * span / (len(columns) - 1) if len(columns) > 1 else 0)

    node_by_id = {n["id"]: {**n, "in": [], "out": []} for n in nodes}
    for l in links:
        node_by_id[l["source"]]["out"].append(l)
        node_by_id[l["target"]]["in"].append(l)
    for n in node_by_id.values():
        in_sum = sum(l["value"] for l in n["in"])
        out_sum = sum(l["value"] for l in n["out"])
        n["value"] = max(in_sum, out_sum, 0.01)

    # Same padding-aware scale as the frontend: the tightest column (most
    # nodes relative to its total value) sets the scale so nothing overflows.
    usable_height = height - 4
    scale = min(
        (usable_height - max(len([n for n in node_by_id.values() if n["column"] == col]) - 1, 0) * node_padding)
        / max(sum(n["value"] for n in node_by_id.values() if n["column"] == col), 0.01)
        for col in columns
    )

    min_label_span = 3.4  # vertical room a node's two-line label needs, regardless of its bar's own thickness
    for col in columns:
        col_nodes = [n for n in node_by_id.values() if n["column"] == col]
        y = 2.0
        for n in col_nodes:
            n["x"] = col_x[col]
            n["y"] = y
            n["h"] = max(n["value"] * scale, 0.3)
            n["label_span"] = max(n["h"], min_label_span)
            y += n["label_span"] + node_padding

    for n in node_by_id.values():
        out_offset = n["y"]
        for l in n["out"]:
            l["_sy"] = out_offset
            l["_sh"] = max(l["value"] * scale, 0.15)
            out_offset += l["_sh"]
        in_offset = n["y"]
        for l in n["in"]:
            l["_ty"] = in_offset
            l["_th"] = max(l["value"] * scale, 0.15)
            in_offset += l["_th"]

    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.invert_yaxis()

    for i, l in enumerate(links):
        s, t = node_by_id[l["source"]], node_by_id[l["target"]]
        x0, x1 = s["x"] + node_width, t["x"]
        xm = (x0 + x1) / 2
        y0top, y0bot = l["_sy"], l["_sy"] + l["_sh"]
        y1top, y1bot = l["_ty"], l["_ty"] + l["_th"]
        verts = [(x0, y0top), (xm, y0top), (xm, y1top), (x1, y1top),
                 (x1, y1bot), (xm, y1bot), (xm, y0bot), (x0, y0bot), (x0, y0top)]
        codes = [Path.MOVETO, Path.CURVE4, Path.CURVE4, Path.CURVE4,
                 Path.LINETO, Path.CURVE4, Path.CURVE4, Path.CURVE4, Path.CLOSEPOLY]
        ax.add_patch(patches.PathPatch(Path(verts, codes), facecolor=PALETTE[i % len(PALETTE)],
                                        edgecolor="none", alpha=0.45))

    for n in node_by_id.values():
        ax.add_patch(patches.Rectangle((n["x"], n["y"]), node_width, n["h"],
                                        facecolor=NODE_COLOR, edgecolor="none"))
        label_x = n["x"] + node_width + 0.6
        ax.text(label_x, n["y"] + n["label_span"] / 2 - 1.0, n["label"], fontsize=6.5, color=TEXT_DARK, va="center")
        ax.text(label_x, n["y"] + n["label_span"] / 2 + 1.3, _fmt(n["value"]), fontsize=6, color=TEXT_MUTED, va="center")


def _build_month_page(pdf, year, month, page_num, total_pages, kicker):
    month_str = f"{year}-{month:02d}"
    month_name = calendar.month_name[month]

    income = db.get_income_summary(month_str)
    groups = db.get_monthly_spending_report(month_str)
    flow = db.get_budget_flow(month_str)

    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{month_name} {year}", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)

    gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], width_ratios=[0.5875, 1.4125],
                           hspace=0.55, wspace=0.35,
                           left=0.06, right=0.96, top=0.88, bottom=0.06)
    ax_donut = fig.add_subplot(gs[0, 0])
    ax_table = fig.add_subplot(gs[0, 1])
    ax_sankey = fig.add_subplot(gs[1, :])

    _draw_donut(ax_donut, groups, income["net_take_home"])
    _draw_adherence_table(ax_table, groups)

    if income["gross"] > 0:
        _draw_sankey(ax_sankey, flow)
    else:
        ax_sankey.set_title("Zero-Based Budget Flow", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
        ax_sankey.axis("off")
        ax_sankey.text(0.5, 0.5, "No income logged for this month.",
                        ha="center", va="center", color=TEXT_MUTED, fontsize=9)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_annual_charts_page(pdf, year, accounts, page_num, total_pages, kicker):
    rows = db.get_annual_trend(year)
    months = [f"{m:02d}" for m in range(1, 13)]
    month_labels = [calendar.month_abbr[m] for m in range(1, 13)]
    group_names = sorted({r["group_name"] for r in rows})

    dates = _year_dates(accounts, year)

    # Portrait rather than the month pages' landscape orientation - three
    # stacked charts need more vertical room than a landscape page gives.
    fig, (ax_trend, ax_net_worth, ax_by_account) = plt.subplots(3, 1, figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{year} - Annual Trend & Net Worth", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    fig.subplots_adjust(left=0.10, right=0.94, top=0.91, bottom=0.09, hspace=0.6)

    # ---- Annual Trend (spending by group, across all 12 months) ----
    ax_trend.set_title("Annual Trend", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if group_names:
        for i, name in enumerate(group_names):
            series = []
            for m in months:
                match = next((r for r in rows if r["month_num"] == m and r["group_name"] == name), None)
                series.append(match["total"] if match else 0)
            sx, sy = _smooth_series(series)
            ax_trend.plot(sx, sy, label=name, color=PALETTE[i % len(PALETTE)], linewidth=1.8)
        ax_trend.set_xticks(range(len(month_labels)))
        ax_trend.set_xticklabels(month_labels)
        ax_trend.legend(fontsize=6.5, frameon=False, ncol=3)
    else:
        ax_trend.text(0.5, 0.5, "No spending recorded this year", ha="center", va="center", color=TEXT_MUTED)
    _style_axes(ax_trend)

    # ---- Net Worth: Contributions vs. Market Value (aggregate, this year) ----
    ax_net_worth.set_title("Net Worth: Contributions vs. Market Value", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if dates and accounts:
        contributed_series = [
            sum(c["amount"] for a in accounts for c in a["contributions"] if c["date"] <= d)
            for d in dates
        ]
        sx_c, sy_c = _smooth_series(contributed_series)

        # Each account's share of the total is stacked as its own colored
        # band (same PALETTE used elsewhere) so the composition of the
        # total Market Value at any point in time is visible at a glance,
        # e.g. Fund 1 25% / Fund 2 40% / Fund 3 5% / Fund 4 30% at a given
        # date shows as four correspondingly-sized colored bands there.
        account_series = [[_account_value_at(a, d) for d in dates] for a in accounts]
        smoothed = [_smooth_series(s) for s in account_series]
        sx_v = smoothed[0][0] if smoothed else []
        stacked_ys = [sy for _sx, sy in smoothed]

        ax_net_worth.stackplot(
            sx_v, *stacked_ys,
            labels=[a["name"] for a in accounts],
            colors=[PALETTE[i % len(PALETTE)] for i in range(len(accounts))],
            alpha=0.6, edgecolor="white", linewidth=0.4,
            zorder=1,
        )

        # Gray diagonal-hatched overlay marking the Contributed (principal)
        # portion, layered on top of the colored stack (explicit zorder,
        # not just draw-call order) so it's clear how much of the total at
        # any date is growth vs. money actually put in.
        ax_net_worth.fill_between(
            sx_c, sy_c, facecolor="none", edgecolor=TEXT_MUTED,
            hatch="////", linewidth=0.0, alpha=0.75, zorder=2,
        )
        ax_net_worth.plot(sx_c, sy_c, label="Contributed", color=TEXT_MUTED, linestyle="--", linewidth=1.5, zorder=3)

        ax_net_worth.set_xticks(range(len(dates)))
        ax_net_worth.set_xticklabels(dates)
        ax_net_worth.legend(fontsize=6.5, frameon=False, ncol=2)
        ax_net_worth.tick_params(axis="x", rotation=30, labelsize=6)
    else:
        ax_net_worth.text(0.5, 0.5, f"No account activity in {year}", ha="center", va="center", color=TEXT_MUTED)
    _style_axes(ax_net_worth)

    # ---- Value Over Time by Account (per-account, this year) ----
    ax_by_account.set_title("Value Over Time by Account", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    if dates and accounts:
        for i, a in enumerate(accounts):
            series = [_account_value_at(a, d) for d in dates]
            sx, sy = _smooth_series(series)
            ax_by_account.plot(sx, sy, label=a["name"], color=PALETTE[i % len(PALETTE)], linewidth=1.8)
        ax_by_account.set_xticks(range(len(dates)))
        ax_by_account.set_xticklabels(dates)
        ax_by_account.legend(fontsize=6.5, frameon=False, ncol=2)
        ax_by_account.tick_params(axis="x", rotation=30, labelsize=6)
    else:
        ax_by_account.text(0.5, 0.5, f"No account activity in {year}", ha="center", va="center", color=TEXT_MUTED)
    _style_axes(ax_by_account)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_comparison_page(pdf, year, page_num, total_pages, kicker):
    """Multi-Year Comparison (total monthly spend, several years overlaid)
    and Savings Rate Over Time (% of Net Take-Home saved each month) --
    the same two charts the Report page shows live, added here so the PDF
    covers what the app tracks beyond just the month-by-month and
    net-worth pages."""
    years = [str(y) for y in range(year - 4, year + 1)]
    trend_by_year = db.get_multi_year_trend(years)
    savings_rows = db.get_savings_rate_series(year)
    month_labels = [calendar.month_abbr[m] for m in range(1, 13)]

    fig, (ax_multi, ax_savings) = plt.subplots(2, 1, figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{year} - Multi-Year Comparison & Savings Rate", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    fig.subplots_adjust(left=0.10, right=0.94, top=0.91, bottom=0.08, hspace=0.35)

    # ---- Multi-Year Comparison ----
    ax_multi.set_title("Multi-Year Comparison - Total Monthly Spending", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    has_any_spending = any(any(v for v in series) for series in trend_by_year.values())
    if has_any_spending:
        for i, y in enumerate(years):
            series = trend_by_year.get(y, [0] * 12)
            sx, sy = _smooth_series(series)
            ax_multi.plot(sx, sy, label=y, color=PALETTE[i % len(PALETTE)], linewidth=1.8)
        ax_multi.set_xticks(range(12))
        ax_multi.set_xticklabels(month_labels)
        ax_multi.legend(fontsize=7, frameon=False, ncol=len(years))
    else:
        ax_multi.text(0.5, 0.5, f"No spending recorded {years[0]}\u2013{years[-1]}", ha="center", va="center", color=TEXT_MUTED)
    _style_axes(ax_multi)

    # ---- Savings Rate Over Time ----
    ax_savings.set_title("Savings Rate Over Time", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    rates = [r["savings_rate_pct"] for r in savings_rows]
    if any(r is not None for r in rates):
        # Same loose benchmark tiers as the live Report page chart: green
        # 20%+, gold 10-20%, red under 10%, muted where no income was
        # logged that month (rate is undefined, not zero).
        bar_colors, heights = [], []
        for r in rates:
            if r is None:
                bar_colors.append(MUTED)
                heights.append(0)
            elif r >= 20:
                bar_colors.append(PALETTE[1])
                heights.append(r)
            elif r >= 10:
                bar_colors.append(PALETTE[0])
                heights.append(r)
            else:
                bar_colors.append(PALETTE[3])
                heights.append(r)
        ax_savings.bar(month_labels, heights, color=bar_colors)
        ax_savings.set_ylabel("% of Net Take-Home", fontsize=8, color=TEXT_MUTED)
    else:
        ax_savings.text(0.5, 0.5, f"No income logged in {year}", ha="center", va="center", color=TEXT_MUTED)
    _style_axes(ax_savings)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _draw_risk_metrics_table(ax, accounts_with_metrics):
    ax.axis("off")
    if not accounts_with_metrics:
        ax.text(0.5, 0.5, "Not enough valuation history yet to compute these metrics\n(each account needs at least two logged valuations).",
                 ha="center", va="center", color=TEXT_MUTED, fontsize=9)
        return

    # Ordered the way you'd actually read them when sizing up an account:
    # "how well did this do" first (Simple Return -> CAGR -> XIRR -> TWRR,
    # each a progressively more rigorous take on the same question), then
    # "how bumpy was the ride" (Volatility, Max Drawdown, Drawdown
    # Duration, Recovery Time -- the last three describing the same single
    # worst decline, so they stay adjacent).
    col_labels = ["Account", "Simple Ret.", "CAGR", "XIRR", "TWRR (Ann.)", "Volatility", "Max DD", "DD Duration", "Recovery"]
    rows = []
    for name, m in accounts_with_metrics:
        def pct_or_na(key, sign=False):
            v = m.get(key)
            if v is None:
                return "N/A"
            return f"{v:+.1f}%" if sign else f"{v:.1f}%"

        duration = f"{m['drawdown_duration_days']} days" if m.get("drawdown_duration_days") else "-"
        if m.get("recovery_days") is not None:
            recovery = f"{m['recovery_days']} days"
        elif m.get("recovered") is False:
            recovery = "Not yet"
        else:
            recovery = "-"

        rows.append([
            name,
            pct_or_na("simple_return_pct", sign=True),
            pct_or_na("cagr_pct", sign=True),
            pct_or_na("xirr_pct", sign=True),
            pct_or_na("twrr_pct", sign=True),
            pct_or_na("annualized_volatility_pct"),
            f"{m['max_drawdown_pct']:.1f}%" if m.get("max_drawdown_pct") else "0.0%",
            duration,
            recovery,
        ])

    n_rows = len(rows) + 1
    height_frac = min(0.11 * n_rows, 0.6)
    table = ax.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left", colLoc="left",
                      bbox=[0.0, 1.0 - height_frac, 1.0, height_frac],
                      colWidths=[0.16, 0.105, 0.09, 0.09, 0.11, 0.105, 0.09, 0.12, 0.11])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.6)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#EDE7D8")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        else:
            cell.set_facecolor("white")
            cell.set_text_props(color=TEXT_DARK)

    ax.text(0, 1.0 - height_frac - 0.06,
             "Simple Return, CAGR, and XIRR all describe your money's growth; TWRR isolates the investment's own performance from your "
             "deposit timing. Volatility, Max Drawdown, Drawdown Duration, and Recovery Time together describe how bumpy the ride was. "
             "See the following pages for a full explanation and the formula behind each.",
             fontsize=9, color=TEXT_MUTED, va="top", wrap=True)


def _draw_metric_explanation_block(ax, y, title, desc, formula, width=92):
    """Draws one title + wrapped description + wrapped formula block
    starting at fractional y, returns the y position just below it.
    `formula` may contain an embedded '\\n' (used for the combined
    Max Drawdown / Duration / Recovery block, which has two formula
    lines) -- each segment is wrapped independently."""
    ax.text(0, y, title, fontsize=11, color=TEXT_DARK, family="serif", weight="bold", va="top")
    y -= 0.032
    for line in textwrap.wrap(desc, width=width):
        ax.text(0, y, line, fontsize=9, color=TEXT_MUTED, va="top")
        y -= 0.024
    y -= 0.008
    for segment in formula.split("\n"):
        for line in textwrap.wrap(segment, width=width):
            ax.text(0, y, line, fontsize=9, color=TEXT_DARK, family="monospace", va="top")
            y -= 0.024
    return y - 0.03


def _build_metrics_table_page(pdf, year, accounts, page_num, total_pages, kicker):
    """Landscape page: the Return & Risk Metrics summary table (per
    account, over each account's full history -- these are lifetime
    performance metrics, not scoped to the report's year)."""
    accounts_with_metrics = [
        (a["name"], a["metrics"]) for a in accounts
        if a.get("metrics") and a["metrics"].get("num_return_periods")
    ]

    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor("white")
    fig.suptitle("Investment Return & Risk Metrics", fontsize=18, color=TEXT_DARK, family="serif", y=0.95)

    ax = fig.add_axes([0.05, 0.08, 0.9, 0.8])
    _draw_risk_metrics_table(ax, accounts_with_metrics)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_return_metrics_explanations_page(pdf, year, page_num, total_pages, kicker):
    """Portrait page: Simple Return, CAGR, XIRR, and TWRR -- the four ways
    of asking "how well did this investment do," each more rigorous about
    cash-flow timing than the last."""
    explanations = [
        ("Simple Return",
         "Growth as a plain percentage of what's been put in: how much more (or less) the account is worth today than the total of every "
         "contribution, with no adjustment for how long any of that money has been invested.",
         "Simple Return = (Current Value \u2212 Total Contributed) / Total Contributed"),
        ("CAGR (naive)",
         "Annualizes that same growth over the account's elapsed history. It still ignores *when* money went in -- a lump sum from day one "
         "and a dozen equal monthly deposits get treated identically -- so it's a rough read, most useful for an account funded mostly by "
         "one initial deposit.",
         "CAGR = (Current Value / Total Contributed)^(1 / Years) \u2212 1"),
        ("XIRR",
         "The cash-flow-timed version of CAGR: every contribution is dated individually against the current value, so a deposit made "
         "last month isn't credited with a full year of growth the way CAGR would credit it.",
         "\u03a3 CF\u1d62 / (1 + r)^((Date\u1d62 \u2212 Date\u2080) / 365) = 0  -  solved for r"),
        ("Time-Weighted Rate of Return (TWRR)",
         "Links together the return from each valuation to the next, backing out any contributions made in between -- so the number "
         "reflects how well the money performed, not how much (or when) was added to the account. This is the one that isolates the "
         "investment's own performance, independent of your deposit behavior.",
         "TWRR = \u220f (1 + r\u1d62) \u2212 1,   r\u1d62 = (End\u1d62 \u2212 Contrib\u1d62 \u2212 Start\u1d62) / Start\u1d62,  annualized over the full history"),
    ]

    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle("Return Metric Definitions", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    ax = fig.add_axes([0.08, 0.04, 0.86, 0.89])
    ax.axis("off")

    y = 0.99
    for title, desc, formula in explanations:
        y = _draw_metric_explanation_block(ax, y, title, desc, formula)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _build_risk_metrics_explanations_page(pdf, year, page_num, total_pages, kicker):
    """Portrait page: Annualized Volatility, plus Max Drawdown / Duration /
    Recovery Time combined into one block since all three describe the
    same single worst historical decline -- together these answer "how
    bumpy was the ride.\""""
    explanations = [
        ("Annualized Volatility",
         "How much an investment's periodic returns swing around their average, scaled up to a yearly figure. Higher means a bumpier ride; "
         "lower means steadier, more predictable performance period to period.",
         "\u03c3(annual) = \u03c3(per period) \u00d7 \u221a(periods per year)"),
        ("Maximum Drawdown, Duration & Recovery Time",
         "These three numbers describe the single worst decline in the account's history together: Max Drawdown is the worst percentage "
         "drop from any high point to the low point that followed it, measured on a contribution-adjusted \"growth of $1\" index built from "
         "the same per-period returns as TWRR -- so a big drop isn't hidden just because new money happened to land around the same time. "
         "Duration is how many days it took to fall from that peak to the trough. Recovery Time is how many days after the trough it took "
         "to climb back to that same peak level -- shown as \"Not yet recovered\" if it hasn't happened yet.",
         "Max Drawdown = min\u1d62[ Index\u1d62 \u00f7 max(Index\u2080...Index\u1d62) \u2212 1 ]\n"
         "Duration = Trough Date \u2212 Peak Date      Recovery Time = Recovery Date \u2212 Trough Date"),
    ]

    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle("Risk Metric Definitions", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    ax = fig.add_axes([0.08, 0.04, 0.86, 0.89])
    ax.axis("off")

    y = 0.99
    for title, desc, formula in explanations:
        y = _draw_metric_explanation_block(ax, y, title, desc, formula)

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _severity_color(severity):
    return {"warning": PALETTE[3], "good": PALETTE[1]}.get(severity, TEXT_MUTED)


def _draw_insight_group(ax, y, heading, insights, width=92):
    """Draws one heading followed by each insight as a colored bullet label
    + wrapped detail line (color keyed to severity: warning/good/neutral).
    Returns the y position just below the block."""
    ax.text(0, y, heading, fontsize=12, color=TEXT_DARK, family="serif", weight="bold", va="top")
    y -= 0.03
    if not insights:
        ax.text(0.02, y, "No flags at the current risk profile.", fontsize=9, color=TEXT_MUTED, va="top")
        return y - 0.04
    for ins in insights:
        color = _severity_color(ins["severity"])
        ax.text(0.02, y, f"\u25CF {ins['label']}", fontsize=10, color=color, weight="bold", va="top")
        y -= 0.022
        for line in textwrap.wrap(ins["detail"], width=width):
            ax.text(0.045, y, line, fontsize=9, color=TEXT_MUTED, va="top")
            y -= 0.02
        y -= 0.012
    return y - 0.015


def _compute_insights_context(accounts):
    """Computes the blended-portfolio insights plus the list of accounts
    that have any insights of their own -- shared by generate_annual_report
    (which needs to know ahead of time whether this page will render at
    all, to get the Table of Contents page numbers right) and the page
    builder itself, so it's only computed once per report."""
    risk_profile = db.get_setting("portfolio_risk_profile") or "moderate"
    portfolio = db.compute_portfolio_metrics(accounts, risk_profile)
    accounts_with_insights = [a for a in accounts if a.get("insights")]
    has_page = bool(portfolio or accounts_with_insights)
    return portfolio, accounts_with_insights, has_page


def _build_investment_insights_page(pdf, year, portfolio, accounts_with_insights, page_num, total_pages, kicker):
    """Portrait page: plain-language "is this actually a good investment, or
    does it just look like one" flags -- for the blended portfolio (a true
    growth-of-$1 pool across every account, not a weighted average of
    per-account metrics) and for each account with enough history. See
    generate_investment_insights() in db_manager.py."""
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle("Investment Insights", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    ax = fig.add_axes([0.08, 0.04, 0.86, 0.89])
    ax.axis("off")

    y = 0.99
    if portfolio:
        y = _draw_insight_group(ax, y, "Portfolio (blended across all accounts)", portfolio.get("insights") or [])
    for a in accounts_with_insights:
        if y < 0.08:
            break  # page is full; the rest are still summarized in the metrics table
        y = _draw_insight_group(ax, y, a["name"], a["insights"])

    disclaimer = "Thresholds are banded by each account's Risk Profile (Low/Medium/High) and are heuristics meant to prompt a closer look, not investment advice."
    dy = 0.05
    for line in textwrap.wrap(disclaimer, width=100):
        ax.text(0, dy, line, fontsize=8, color=TEXT_MUTED, va="bottom", style="italic")
        dy -= 0.018

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def _fmt_range(floor, ceiling):
    """Formats a bracket floor-ceiling range for a single table cell.
    Dollar signs are escaped here because matplotlib treats a *pair* of
    literal '$' in one text string as a matched mathtext span - with two
    formatted dollar amounts in the same cell, that silently swallows both
    '$' characters and collapses the surrounding whitespace. A lone '$'
    (as used everywhere else in this module) isn't paired, so it renders
    fine as-is; this helper only exists for the two-amount case."""
    lo = f"\\${floor:,.2f}"
    hi = f"\\${ceiling:,.2f}" if ceiling is not None else "and up"
    return f"{lo} \u2013 {hi}"


def _draw_tax_source_table(ax, sources, totals):
    """Same columns as the Report page's Tax tab table: gross pay,
    deductions, tax withheld, pre-tax investments, employer match, and net
    -- per income source, plus a totals row. Returns the y position just
    below the table (axes-fraction coordinates, like the other page
    builders in this module)."""
    ax.set_title("Income & Withholding by Source", fontsize=11, color=TEXT_DARK, family="serif", loc="left")
    ax.axis("off")

    col_labels = ["Source", "Gross Pay", "Deductions", "Tax\nWithheld", "Pre-Tax\nInvestments", "Employer\nMatch", "Net"]
    rows = [[s["label"], _fmt(s["gross"]), _fmt(s["total_deductions"]), _fmt(s["total_tax_withheld"]),
             _fmt(s["total_investments"]), _fmt(s["total_match"]), _fmt(s["net"])] for s in sources]
    rows.append(["Total", _fmt(totals["gross"]), _fmt(totals["total_deductions"]), _fmt(totals["total_tax_withheld"]),
                 _fmt(totals["total_investments"]), _fmt(totals["total_match"]), _fmt(totals["net_take_home"])])

    n_rows = len(rows) + 1
    height_frac = min(0.13 * n_rows, 0.44)
    y0 = 1.0 - height_frac
    table = ax.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left", colLoc="left",
                      bbox=[0.0, y0, 1.0, height_frac],
                      colWidths=[0.19, 0.12, 0.13, 0.14, 0.16, 0.14, 0.12])
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1, 1.5)  # extra row height so the two-line headers have room to breathe
    last_row = len(rows)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#EDE7D8")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        elif row == last_row:
            cell.set_facecolor("#F3EFE2")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        else:
            cell.set_facecolor("white")
            cell.set_text_props(color=TEXT_DARK)
    return y0 - 0.05


def _draw_tax_estimate_stats(ax, y, estimate):
    """Six-figure stat readout (taxable income, marginal/effective rate,
    estimated tax, amount withheld, refund/owed) as a plain label/value
    grid -- the print equivalent of the stat cards on the Report page's
    Tax tab, since matplotlib has no card-grid primitive to reuse.
    Returns the y position just below the block."""
    balance = estimate["estimated_balance"]
    balance_label = "Estimated Refund" if balance >= 0 else "Estimated Amount Owed"
    balance_color = PALETTE[1] if balance >= 0 else PALETTE[3]

    stats = [
        ("Taxable Income (est.)", _fmt(estimate["taxable_income"]), TEXT_DARK),
        ("Marginal Bracket", f"{estimate['marginal_rate']}%", TEXT_DARK),
        ("Effective Rate", f"{estimate['effective_rate']}%", TEXT_DARK),
        ("Estimated Federal Tax", _fmt(estimate["tax"]), TEXT_DARK),
        ("Already Withheld", _fmt(estimate["amount_withheld"]), TEXT_DARK),
        (balance_label, _fmt(abs(balance)), balance_color),
    ]
    col_x = [0.0, 0.35, 0.70]
    row_height = 0.075
    for i, (label, value, color) in enumerate(stats):
        x = col_x[i % 3]
        row_y = y - (i // 3) * row_height
        ax.text(x, row_y, label.upper(), fontsize=7, color=TEXT_MUTED, va="top")
        ax.text(x, row_y - 0.028, value, fontsize=13, color=color, family="serif", weight="bold", va="top")
    rows_used = -(-len(stats) // 3)  # ceil division
    return y - rows_used * row_height - 0.03


def _draw_tax_bracket_table(ax, y, estimate):
    """The marginal bracket breakdown (rate, bracket range, amount taxed at
    that rate, tax owed on that slice) for the report year's estimate.
    Returns the y position just below the table."""
    ax.text(0, y, f"{estimate['bracket_year']} Federal Bracket Breakdown \u2013 {estimate['filing_status_label']}",
             fontsize=11, color=TEXT_DARK, family="serif", weight="bold", va="top")
    y -= 0.045

    if not estimate["breakdown"]:
        ax.text(0, y, "No taxable income estimated for this year.", fontsize=9, color=TEXT_MUTED, va="top")
        return y - 0.04

    col_labels = ["Rate", "Bracket", "Amount Taxed", "Tax"]
    rows = []
    for b in estimate["breakdown"]:
        rows.append([f"{b['rate']}%", _fmt_range(b["floor"], b["ceiling"]), _fmt(b["amount_taxed"]), _fmt(b["tax"])])

    n_rows = len(rows) + 1
    height_frac = min(0.075 * n_rows, max(y - 0.06, 0.08))
    y0 = y - height_frac
    table = ax.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left", colLoc="left",
                      bbox=[0.0, y0, 1.0, height_frac],
                      colWidths=[0.14, 0.42, 0.22, 0.22])
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0:
            cell.set_facecolor("#EDE7D8")
            cell.set_text_props(color=TEXT_DARK, weight="bold")
        else:
            cell.set_facecolor("white")
            cell.set_text_props(color=TEXT_DARK)
    return y0 - 0.04


def _build_tax_summary_page(pdf, year, tax_data, page_num, total_pages, kicker):
    """Portrait page: the same Year-End Tax Summary + estimated-federal-tax
    breakdown shown live on the Report page's Tax tab (source table,
    taxable-income/estimated-tax stats, and the marginal bracket
    breakdown). generate_annual_report() skips this page entirely when
    tax_data is None (no income logged for the year), same condition
    get_year_end_tax_summary() itself uses."""
    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    fig.suptitle(f"{year} - Year-End Tax Summary", fontsize=18, color=TEXT_DARK, family="serif", y=0.97)
    ax = fig.add_axes([0.08, 0.05, 0.86, 0.87])
    ax.axis("off")

    y = _draw_tax_source_table(ax, tax_data["sources"], tax_data["totals"])
    estimate = tax_data["estimate"]
    y = _draw_tax_estimate_stats(ax, y, estimate)
    y = _draw_tax_bracket_table(ax, y, estimate)

    disclaimer = (
        f"Estimate only, based on {estimate['bracket_year']} IRS federal tax brackets and the {estimate['bracket_year']} "
        f"standard deduction for {estimate['filing_status_label']}. Doesn't account for credits, itemizing, or income "
        "outside what's tracked in this app - not a substitute for a real tax preparer."
    )
    dy = max(y, 0.05)
    for line in textwrap.wrap(disclaimer, width=110):
        ax.text(0, dy, line, fontsize=8, color=TEXT_MUTED, va="top", style="italic")
        dy -= 0.02

    _draw_page_chrome(fig, year, kicker, page_num, total_pages)
    pdf.savefig(fig, facecolor="white")
    plt.close(fig)


def generate_annual_report(year):
    """Returns a BytesIO containing the full multi-page PDF for the given year."""
    year = int(year)
    accounts = db.get_net_worth_history()

    # Whether the Investment Insights page will render is data-dependent
    # (it's skipped entirely if there's nothing to show), so this is
    # computed once, up front, to get the Table of Contents' page numbers
    # right without a two-pass render. Same idea for the Tax Summary page:
    # get_year_end_tax_summary() returns None when no income was logged
    # for the year at all.
    portfolio, accounts_with_insights, has_insights_page = _compute_insights_context(accounts)
    filing_status = db.get_setting("tax_filing_status", "single")
    tax_data = db.get_year_end_tax_summary(year, filing_status)
    has_tax_page = tax_data is not None

    page_months_start = 3
    page_metrics = page_months_start + 12
    page_annual_summary = page_metrics + 1
    page_tax = page_annual_summary + 2
    page_insights = page_tax + (1 if has_tax_page else 0)
    page_appendix = page_insights + (1 if has_insights_page else 0)
    total_pages = page_appendix + 2 - 1

    toc_entries = [
        ("Monthly Detail (January - December)", page_months_start),
        ("Investment Metrics Table", page_metrics),
        ("Annual Summary", page_annual_summary),
    ]
    if has_tax_page:
        toc_entries.append(("Year-End Tax Summary", page_tax))
    if has_insights_page:
        toc_entries.append(("Investment Insights", page_insights))
    toc_entries.append(("Appendix: Metric Definitions", page_appendix))

    stats = _compute_year_in_review_stats(year, accounts)
    now = datetime.now()
    generated_on = f"{now:%B} {now.day}, {now:%Y}"

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _build_cover_page(pdf, year, stats, generated_on)
        _build_toc_page(pdf, year, toc_entries, total_pages)

        page = page_months_start
        for month in range(1, 13):
            _build_month_page(pdf, year, month, page, total_pages, "MONTHLY DETAIL")
            page += 1

        _build_metrics_table_page(pdf, year, accounts, page, total_pages, "INVESTMENT METRICS")
        page += 1

        _build_annual_charts_page(pdf, year, accounts, page, total_pages, "ANNUAL SUMMARY")
        page += 1
        _build_comparison_page(pdf, year, page, total_pages, "ANNUAL SUMMARY")
        page += 1

        if has_tax_page:
            _build_tax_summary_page(pdf, year, tax_data, page, total_pages, "TAX SUMMARY")
            page += 1

        if has_insights_page:
            _build_investment_insights_page(pdf, year, portfolio, accounts_with_insights, page, total_pages, "INVESTMENT INSIGHTS")
            page += 1

        _build_return_metrics_explanations_page(pdf, year, page, total_pages, "APPENDIX: METRIC DEFINITIONS")
        page += 1
        _build_risk_metrics_explanations_page(pdf, year, page, total_pages, "APPENDIX: METRIC DEFINITIONS")
        page += 1

        info = pdf.infodict()
        info["Title"] = f"Personal Finance Annual Report {year}"
        info["Author"] = "Ledger"

    buf.seek(0)
    return buf