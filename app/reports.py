"""PDF generation for school lists (ReportLab: pure Python, no native dependencies)."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .tuition import is_out_of_state, tuition_for

LOGO = Path(__file__).resolve().parent.parent / "static" / "jinx-logo.jpg"
HEADERS = ["College", "Division", "State", "Tuition", "Coach email", "Matching need", "Fit"]


def money(amount: float | None) -> str:
    return f"${amount:,.0f}" if amount else "—"


def tuition_display(college, player) -> str:
    """Tuition string for the PDF; bold with a * when the out-of-state rate applies."""
    amount, oos = tuition_for(college, player)
    text = money(amount)
    return f"<b>{text} *</b>" if oos else text


def school_list_pdf(player, matches, filter_summary: str) -> bytes:
    """Render the generated school list as a PDF document and return its bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), title=f"School List - {player.name}",
                            author="Jinx Recruiting", leftMargin=36, rightMargin=36, topMargin=32, bottomMargin=32)
    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell"); cell.fontSize = 8.5; cell.leading = 11
    flow = []
    if LOGO.exists():
        flow += [Image(str(LOGO), width=1.5 * inch, height=0.75 * inch, kind="proportional"), Spacer(1, 8)]
    flow.append(Paragraph(f"School List: {player.name}", styles["Title"]))
    flow.append(Paragraph(f"Class of {player.grad_year} &middot; {player.primary_position} &middot; generated {date.today():%B %d, %Y}", styles["Normal"]))
    flow.append(Paragraph(filter_summary, styles["Normal"]))
    home = (getattr(player, "home_state", "") or "").strip()
    if home:
        flow.append(Paragraph(f"Home state: <b>{home}</b>", styles["Normal"]))
    flow.append(Spacer(1, 14))

    data = [[Paragraph(f"<b>{h}</b>", cell) for h in HEADERS]]
    any_oos = False
    for college, need, score in matches:
        any_oos = any_oos or is_out_of_state(player, college)
        data.append([Paragraph(str(v), cell) for v in (
            college.name, college.division or "—", college.state or "—", tuition_display(college, player),
            college.coach_emails or "—", f"{need.position} &middot; {need.class_year}", score)])
    if len(data) == 1:
        data.append([Paragraph("No matching colleges for the selected filters.", cell)] + [""] * 6)

    table = Table(data, colWidths=[2.1 * inch, 1.15 * inch, 0.6 * inch, 0.9 * inch, 2.3 * inch, 1.5 * inch, 0.5 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C7D0DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    flow.append(table)
    if any_oos:
        flow.append(Spacer(1, 8))
        flow.append(Paragraph(
            "<b>Bold *</b> tuition = out-of-state rate (college is outside the player's home state).",
            cell))
    doc.build(flow)
    return buffer.getvalue()
