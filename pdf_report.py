"""
PDF report generator — builds a client-style PDF (blue stat cards, black
section bars, trend charts, and full data tables) from the same GSC/GA4
data the dashboard uses.
"""

import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

import gsc_client
import ga4_client

BLUE = colors.HexColor("#2f8fd6")
DARK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
ROW_ALT = colors.HexColor("#f2f4f7")
GREEN = colors.HexColor("#33c17a")
RED = colors.HexColor("#d84343")

_styles = getSampleStyleSheet()
_stat_para_style = ParagraphStyle("stat", parent=_styles["Normal"], textColor=colors.white, leading=14)
_cell_style = ParagraphStyle("cell", parent=_styles["Normal"], fontSize=8, leading=10)
_head_style = ParagraphStyle("head", parent=_styles["Normal"], fontSize=8, leading=10,
                              textColor=colors.white)


def _band(text):
    style = ParagraphStyle("band", parent=_styles["Normal"], textColor=colors.white,
                            backColor=DARK, fontSize=11, leading=18, spaceBefore=4, spaceAfter=10,
                            leftIndent=8, borderPadding=6)
    return Paragraph(text.upper(), style)


def _sub_title(text):
    style = ParagraphStyle("subtitle", parent=_styles["Normal"], fontSize=10, leading=14,
                            spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2b2f36"))
    return Paragraph(f"<b>{text}</b>", style)


def _chunk(pairs, size=4):
    for i in range(0, len(pairs), size):
        yield pairs[i:i + size]


def _stat_table(pairs):
    """One row of solid-blue metric boxes, like the dashboard's stat cards."""
    data = [[
        Paragraph(f"<font size=7 color='white'>{label.upper()}</font><br/>"
                  f"<font size=15 color='white'><b>{value}</b></font>", _stat_para_style)
        for label, value in pairs
    ]]
    col_width = 170 * mm / len(pairs)
    t = Table(data, colWidths=[col_width] * len(pairs))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEAFTER", (0, 0), (-2, -1), 1, colors.white),
    ]))
    return t


def _line_chart_image(x_labels, series, labels, hex_colors):
    fig, ax = plt.subplots(figsize=(6.7, 2.3), dpi=150)
    for vals, label, c in zip(series, labels, hex_colors):
        ax.plot(x_labels, vals, label=label, color=c, linewidth=1.5)
    ax.legend(fontsize=7, frameon=False, loc="upper left")
    ax.tick_params(labelsize=6, rotation=45)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _donut_image(labels, values, hex_colors):
    fig, ax = plt.subplots(figsize=(3.4, 3.0), dpi=150)
    ax.pie(values, labels=labels, colors=hex_colors, autopct="%1.0f%%",
           wedgeprops=dict(width=0.42), textprops={"fontsize": 7})
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _data_table(headers, rows, col_widths, row_colors=None):
    """Generic small data table: header row (dark) + striped body rows.
    row_colors: optional list of colors.Color, one per row, for the last
    numeric column (used for gainers/losers +/- coloring)."""
    header_row = [Paragraph(h, _head_style) for h in headers]
    data = [header_row]
    for row in rows:
        data.append([Paragraph(str(c), _cell_style) for c in row])

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e5ea")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def _movers_table(rows, improved):
    headers = ["Query", "Prev.", "Now", "Change"]
    widths = [95 * mm, 25 * mm, 25 * mm, 25 * mm]
    color_hex = "#33c17a" if improved else "#d84343"
    table_rows = []
    for r in rows:
        change = r["position_change"]
        sign = "+" if change > 0 else ""
        table_rows.append([
            r["query"][:55],
            r["previous_position"],
            r["current_position"],
            f'<font color="{color_hex}">{sign}{change}</font>',
        ])
    return _data_table(headers, table_rows, widths)


def generate_pdf(site_url, property_id, start_date, end_date):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=15 * mm,
                             leftMargin=15 * mm, rightMargin=15 * mm)
    elems = []

    title_style = ParagraphStyle("title", parent=_styles["Title"], fontSize=18)
    sub_style = ParagraphStyle("sub", parent=_styles["Normal"], textColor=MUTED, fontSize=9)

    elems.append(Paragraph("SEO &amp; Analytics Report", title_style))
    elems.append(Paragraph(f"{site_url} &nbsp;|&nbsp; {start_date} to {end_date}", sub_style))
    elems.append(Spacer(1, 8 * mm))

    # ==================== Google Analytics ====================
    if property_id:
        ga = ga4_client.get_summary(property_id, start_date, end_date)
        mins, secs = divmod(int(ga["avg_session_duration"]), 60)
        pairs = [
            ("New Users", ga["new_users"]), ("Total Users", ga["total_users"]),
            ("Sessions", ga["sessions"]), ("Views", ga["views"]),
            ("Bounce Rate", f'{ga["bounce_rate"]}%'), ("Engagement Rate", f'{ga["engagement_rate"]}%'),
            ("Events/Session", ga["events_per_session"]), ("Avg. Engagement", f"{mins}:{secs:02d}"),
        ]
        elems.append(_band("Google Analytics"))
        for chunk in _chunk(pairs, 4):
            elems.append(_stat_table(chunk))
            elems.append(Spacer(1, 3 * mm))

        trend = ga4_client.get_trend(property_id, start_date, end_date)
        if trend:
            dates = [r["date"][5:] for r in trend]
            users = [r["active_users"] for r in trend]
            sessions = [r["sessions"] for r in trend]
            img = _line_chart_image(dates, [users, sessions], ["Active Users", "Sessions"],
                                     ["#2f8fd6", "#f5a623"])
            elems.append(Image(img, width=170 * mm, height=58 * mm))
        elems.append(Spacer(1, 4 * mm))

        traffic = ga4_client.get_traffic_sources(property_id, start_date, end_date)
        if traffic:
            elems.append(_sub_title("Traffic Sources"))
            img = _donut_image(
                [r["channel"] for r in traffic], [r["sessions"] for r in traffic],
                ["#2f8fd6", "#33c17a", "#f5a623", "#e05d5d", "#a566ff", "#26c6da"][:len(traffic)]
            )
            elems.append(Image(img, width=90 * mm, height=78 * mm))
        elems.append(Spacer(1, 4 * mm))

        pages = ga4_client.get_top_pages(property_id, start_date, end_date, limit=15)
        if pages:
            elems.append(_sub_title("Top Pages (GA4)"))
            rows = [[p["page"][:70], p["views"], p["users"]] for p in pages]
            elems.append(_data_table(["Page", "Views", "Users"], rows,
                                      [130 * mm, 20 * mm, 20 * mm]))
        elems.append(Spacer(1, 6 * mm))

    # ==================== Google Search Console ====================
    gsc = gsc_client.get_summary(site_url, start_date, end_date)
    pairs = [
        ("Clicks", gsc["clicks"]), ("CTR", f'{gsc["ctr"]}%'),
        ("Avg Position", gsc["position"]), ("Impressions", gsc["impressions"]),
    ]
    elems.append(_band("Google Search Console"))
    elems.append(_stat_table(pairs))
    elems.append(Spacer(1, 3 * mm))

    trend = gsc_client.get_trend(site_url, start_date, end_date)
    if trend:
        dates = [r["date"][5:] for r in trend]
        clicks = [r["clicks"] for r in trend]
        impressions = [r["impressions"] for r in trend]
        img = _line_chart_image(dates, [clicks, impressions], ["Clicks", "Impressions"],
                                 ["#2f8fd6", "#33c17a"])
        elems.append(Image(img, width=170 * mm, height=58 * mm))
    elems.append(Spacer(1, 4 * mm))

    queries = gsc_client.get_queries(site_url, start_date, end_date, limit=20)
    if queries:
        elems.append(_sub_title("Top Queries"))
        rows = [[q["query"][:45], q["clicks"], q["impressions"], f'{q["ctr"]}%', q["position"]]
                for q in queries]
        elems.append(_data_table(["Query", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    pages = gsc_client.get_pages(site_url, start_date, end_date, limit=20)
    if pages:
        elems.append(_sub_title("Top Pages"))
        rows = [[p["page"][:55], p["clicks"], p["impressions"], f'{p["ctr"]}%', p["position"]]
                for p in pages]
        elems.append(_data_table(["Page", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    movers = gsc_client.get_movers(site_url, start_date, end_date, limit=10)
    if movers["gainers"]:
        elems.append(_sub_title("📈 Biggest Gainers (position improved)"))
        elems.append(_movers_table(movers["gainers"], improved=True))
        elems.append(Spacer(1, 3 * mm))
    if movers["losers"]:
        elems.append(_sub_title("📉 Biggest Losers (position dropped)"))
        elems.append(_movers_table(movers["losers"], improved=False))
    elems.append(Spacer(1, 4 * mm))

    devices = gsc_client.get_devices(site_url, start_date, end_date)
    if devices:
        elems.append(_sub_title("Devices"))
        rows = [[d["device"], d["clicks"], d["impressions"], f'{d["ctr"]}%', d["position"]]
                for d in devices]
        elems.append(_data_table(["Device", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))
    elems.append(Spacer(1, 4 * mm))

    countries = gsc_client.get_countries(site_url, start_date, end_date, limit=15)
    if countries:
        elems.append(_sub_title("Top Countries"))
        rows = [[c["country"], c["clicks"], c["impressions"], f'{c["ctr"]}%', c["position"]]
                for c in countries]
        elems.append(_data_table(["Country", "Clicks", "Impr.", "CTR", "Pos."], rows,
                                  [80 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]))

    doc.build(elems)
    buf.seek(0)
    return buf