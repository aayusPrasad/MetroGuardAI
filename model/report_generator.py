
"""PDF Inspection Certificate Generator for MetroGuard AI."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def generate_inspection_certificate(
    report_data: Mapping[str, Any],
    output_path: str | Path = "metroguard_inspection_certificate.pdf",
) -> str:
    """Generate a formal Legal Metrology Compliance PDF report from evaluation output."""
    output_path = Path(output_path)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=colors.HexColor('#1A365D'),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        textColor=colors.HexColor('#4A5568'),
        spaceAfter=12,
    )
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=12,
        textColor=colors.HexColor('#2B6CB0'),
        spaceBefore=10,
        spaceAfter=6,
    )
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        textColor=colors.HexColor('#2D3748'),
    )
    cell_bold = ParagraphStyle(
        'CellBold',
        parent=cell_style,
        fontName='Helvetica-Bold',
    )
    
    status_compliant = ParagraphStyle('SC', parent=cell_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#276749'))
    status_failed = ParagraphStyle('SF', parent=cell_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#9B2C2C'))
    status_review = ParagraphStyle('SR', parent=cell_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#D69E2E'))

    elements: list[Any] = []

    # Header
    elements.append(Paragraph("METROGUARD AI — COMPLIANCE INSPECTION CERTIFICATE", title_style))
    elements.append(Paragraph("Legal Metrology (Packaged Commodities) Rules, 2011 Enforcement Suite", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1A365D'), spaceBefore=2, spaceAfter=10))

    # Metadata Block
    raw_status = report_data.get("overall_status", "unknown").lower()
    if raw_status == "compliant":
        display_status, stat_color = "COMPLIANT", colors.HexColor('#276749')
    elif raw_status == "non_compliant":
        display_status, stat_color = "NON-COMPLIANT (Violations Detected)", colors.HexColor('#9B2C2C')
    else:
        display_status, stat_color = "REVIEW REQUIRED (Unverified/Missing Declarations)", colors.HexColor('#D69E2E')
    
    meta_data = [
        [Paragraph("<b>Inspection Timestamp:</b>", cell_style), Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cell_style)],
        [Paragraph("<b>Evaluation Mode:</b>", cell_style), Paragraph("E-Commerce Listing" if report_data.get("is_ecommerce") else "Physical Package Capture", cell_style)],
        [Paragraph("<b>Overall Verdict:</b>", cell_style), Paragraph(f"<b>{display_status}</b>", ParagraphStyle('OStat', parent=cell_style, textColor=stat_color))],
    ]
    meta_table = Table(meta_data, colWidths=[140, 400])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F7FAFC')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(meta_table)
    elements.append(Spacer(1, 10))

    # Preprocessing / Calibration & Concrete Statutory Context
    preprocessing = report_data.get("preprocessing", {})
    usp_context = report_data.get("usp_context", {})
    if preprocessing:
        elements.append(Paragraph("Physical Calibration & Statutory Context", section_heading))
        
        area_str = f"{preprocessing.get('pdp_area_cm2', 'N/A'):.2f} cm²" if preprocessing.get('pdp_area_cm2') else "N/A"
        usp_note = usp_context.get("reason", "Evaluated against standard statutory bounds.")
        
        prep_data = [
            [Paragraph("Calibration Method", cell_bold), Paragraph("Pixels per CM", cell_bold), Paragraph("PDP Surface Area", cell_bold)],
            [
                Paragraph(str(preprocessing.get("calibration_method", "N/A")), cell_style),
                Paragraph(str(preprocessing.get("pixels_per_cm", "N/A")), cell_style),
                Paragraph(area_str, cell_style),
            ],
            [Paragraph("<b>Statutory USP Context:</b>", cell_bold), Paragraph(usp_note, cell_style), Paragraph("", cell_style)]
        ]
        prep_table = Table(prep_data, colWidths=[160, 120, 260])
        prep_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EDF2F7')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E0')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('SPAN', (1, 2), (2, 2)),
            ('PADDING', (0, 0), (-1, -1), 5),
        ]))
        elements.append(prep_table)
        elements.append(Spacer(1, 10))

    # Field-Level Audit Table with Precise Rule References & Guaranteed USP Row
    elements.append(Paragraph("Mandatory Declaration Audit Trail (LMPC Rules, 2011)", section_heading))
    field_rows = [[Paragraph("Declaration Field", cell_bold), Paragraph("Rule Ref", cell_bold), Paragraph("Status", cell_bold), Paragraph("Extracted Value / Reason", cell_bold), Paragraph("Confidence / Height", cell_bold)]]
    
    fields = report_data.get("fields", {})
    for field_name, details in fields.items():
        f_status = details.get("status", "not_detected")
        if f_status == "compliant":
            status_para = Paragraph("COMPLIANT", status_compliant)
        elif f_status == "non_compliant":
            status_para = Paragraph("VIOLATION", status_failed)
        else:
            status_para = Paragraph("NOT DETECTED", status_review)
            
        rule_reference = str(details.get("rule_ref") or "Rule 6")
        val_or_reason = str(details.get("value") or details.get("reason") or "N/A")
        if isinstance(details.get("value"), dict):
            val_or_reason = ", ".join(f"{k}: {v}" for k, v in details["value"].items())
        elif isinstance(details.get("value"), list):
            val_or_reason = ", ".join(str(x) for x in details["value"])
            
        conf = details.get("confidence")
        height = details.get("bbox_height_mm")
        metrics_str = f"Conf: {conf:.2f}<br/>Height: {height:.2f}mm" if conf and height else "N/A"

        field_rows.append([
            Paragraph(field_name.replace("_", " ").title(), cell_style),
            Paragraph(rule_reference, cell_style),
            status_para,
            Paragraph(val_or_reason, cell_style),
            Paragraph(metrics_str, cell_style),
        ])

    audit_table = Table(field_rows, colWidths=[100, 75, 75, 174, 116])
    audit_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EDF2F7')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E0')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(audit_table)
    elements.append(Spacer(1, 15))

    # Footer / Disclaimer
    footer_text = (
        "<b>System Generated Report:</b> This document is automatically generated by MetroGuard AI "
        "pursuant to the Legal Metrology (Packaged Commodities) Rules, 2011. Fields marked as 'Not Detected' "
        "indicate algorithmic absence or low OCR confidence requiring secondary human inspector review."
    )
    elements.append(Paragraph(footer_text, ParagraphStyle('Footer', parent=styles['Normal'], fontName='Helvetica-Oblique', fontSize=8, textColor=colors.HexColor('#718096'))))

    doc.build(elements)
    return str(output_path)



def generate_inspection_docx(
    report_data: Mapping[str, Any],
    output_path: str | Path = "metroguard_inspection_certificate.docx",
) -> str:
    """Generate a formal Legal Metrology Compliance DOCX report from evaluation output."""
    output_path = Path(output_path)
    doc = Document()

    title = doc.add_heading("METROGUARD AI — COMPLIANCE INSPECTION CERTIFICATE", level=1)
    subtitle = doc.add_paragraph("Legal Metrology (Packaged Commodities) Rules, 2011 Enforcement Suite")
    subtitle.runs[0].italic = True

    raw_status = report_data.get("overall_status", "unknown").lower()
    display_status = {
        "compliant": "COMPLIANT",
        "non_compliant": "NON-COMPLIANT (Violations Detected)",
    }.get(raw_status, "REVIEW REQUIRED (Unverified/Missing Declarations)")

    meta = doc.add_table(rows=3, cols=2)
    meta.style = "Light Grid Accent 1"
    meta.cell(0, 0).text = "Inspection Timestamp:"
    meta.cell(0, 1).text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta.cell(1, 0).text = "Evaluation Mode:"
    meta.cell(1, 1).text = "E-Commerce Listing" if report_data.get("is_ecommerce") else "Physical Package Capture"
    meta.cell(2, 0).text = "Overall Verdict:"
    meta.cell(2, 1).text = display_status

    doc.add_paragraph()
    preprocessing = report_data.get("preprocessing", {})
    usp_context = report_data.get("usp_context", {})
    if preprocessing:
        doc.add_heading("Physical Calibration & Statutory Context", level=2)
        prep = doc.add_table(rows=2, cols=3)
        prep.style = "Light Grid Accent 1"
        prep.cell(0, 0).text = "Calibration Method"
        prep.cell(0, 1).text = "Pixels per CM"
        prep.cell(0, 2).text = "PDP Surface Area"
        prep.cell(1, 0).text = str(preprocessing.get("calibration_method", "N/A"))
        prep.cell(1, 1).text = str(preprocessing.get("pixels_per_cm", "N/A"))
        area = preprocessing.get("pdp_area_cm2")
        prep.cell(1, 2).text = f"{area:.2f} cm²" if area else "N/A"
        doc.add_paragraph(f"Statutory USP Context: {usp_context.get('reason', 'N/A')}")

    doc.add_paragraph()
    doc.add_heading("Mandatory Declaration Audit Trail (LMPC Rules, 2011)", level=2)
    fields = report_data.get("fields", {})
    table = doc.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, label in enumerate(["Declaration Field", "Rule Ref", "Status", "Extracted Value / Reason", "Confidence / Height"]):
        hdr[i].text = label

    for field_name, details in fields.items():
        row = table.add_row().cells
        f_status = details.get("status", "not_detected")
        status_label = {"compliant": "COMPLIANT", "non_compliant": "VIOLATION"}.get(f_status, "NOT DETECTED")

        val = details.get("value") or details.get("reason") or "N/A"
        if isinstance(details.get("value"), dict):
            val = ", ".join(f"{k}: {v}" for k, v in details["value"].items())
        elif isinstance(details.get("value"), list):
            val = ", ".join(str(x) for x in details["value"])

        conf = details.get("confidence")
        height = details.get("bbox_height_mm")
        metrics = f"Conf: {conf:.2f} / Height: {height:.2f}mm" if conf and height else "N/A"

        row[0].text = field_name.replace("_", " ").title()
        row[1].text = str(details.get("rule_ref") or "Rule 6")
        row[2].text = status_label
        row[3].text = str(val)
        row[4].text = metrics

    doc.add_paragraph()
    footer = doc.add_paragraph(
        "System Generated Report: This document is automatically generated by MetroGuard AI "
        "pursuant to the Legal Metrology (Packaged Commodities) Rules, 2011. Fields marked as "
        "'Not Detected' indicate algorithmic absence or low OCR confidence requiring secondary "
        "human inspector review."
    )
    footer.runs[0].italic = True
    footer.runs[0].font.size = Pt(8)

    doc.save(str(output_path))
    return str(output_path)
