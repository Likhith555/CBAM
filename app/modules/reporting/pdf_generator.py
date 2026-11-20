"""Auditable CBAM Emissions Passport PDF Generator using ReportLab."""

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


STATUS_COLORS = {
    "draft": ("#fff3e0", "#e65100"),
    "user-confirmed": ("#e3f2fd", "#0d47a1"),
    "expert-reviewed": ("#e8f5e9", "#1b5e20"),
    "externally-verified": ("#ede7f6", "#4527a0"),
}
STATUS_WORDING = {
    "draft": "Not reviewed. Not for submission.",
    "user-confirmed": "Confirmed by the exporter. Not independently reviewed.",
    "expert-reviewed": "Reviewed by a named expert. Not externally verified.",
    "externally-verified": "Recorded by the user; not verified by this tool.",
}
INCOMPLETE_TEXT = "INCOMPLETE \u2013 NOT FOR SUBMISSION"


def _norm_status(status: str) -> str:
    return (status or "Draft").strip().lower().replace("_", "-").replace(" ", "-")


def _legacy_calc_from_priority1(p1: Dict[str, Any]) -> Dict[str, Any]:
    r = p1["result"]
    unc = p1.get("uncertainty") or {}
    return {
        "specific_embedded_emissions_see": r["see_tco2_per_t"],
        "facility_gross_scope1_tco2": r["scope1_raw_tco2"],
        "allocated_scope1_direct_tco2": r["scope1_allocated_tco2"],
        "facility_gross_scope2_tco2": r["scope2_raw_tco2"],
        "allocated_scope2_indirect_tco2": r["scope2_counted_tco2"],
        "precursor_emissions_tco2": r["precursor_tco2"],
        "total_embedded_emissions_tco2": r["total_tco2"],
        "net_target_production_tonnes": r["target_production_t"],
        "allocation_factor_alpha": r["alpha"],
        "data_quality_rating": f"{r['data_quality_label']} (fallback share {r['fallback_share'] * 100:.1f}% of emissions)",
        "uncertainty": unc,
    }


def generate_passport_pdf(passport_data: Dict[str, Any], output_path: Optional[Path] = None) -> bytes:
    """
    Generates a professional, multi-section auditable 'CBAM Emissions Passport' PDF report.
    Complies with EU CBAM evidence requirements (Regulation 2023/956 & 2025/2621).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1a237e"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#455a64"),
    )
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0d47a1"),
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#263238"),
    )
    bold_cell_style = ParagraphStyle(
        "BoldCell",
        parent=body_style,
        fontName="Helvetica-Bold",
    )
    disclaimer_style = ParagraphStyle(
        "DisclaimerText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#b71c1c"),
    )

    story = []

    raw_review_status = str(passport_data.get("review_status", "Draft")).strip() or "Draft"
    status_key = _norm_status(raw_review_status)
    version_number = passport_data.get("passport_version")
    p1 = passport_data.get("priority1_result")
    is_priority1 = "calc_status" in passport_data
    incomplete = is_priority1 and not p1
    badge_bg, badge_fg = STATUS_COLORS.get(status_key, STATUS_COLORS["draft"])
    badge_text = raw_review_status.upper() + (f"  |  Passport v{version_number}" if version_number else "")
    if incomplete:
        badge_text = INCOMPLETE_TEXT + "  |  " + badge_text

    def _page_header(canvas, document):
        canvas.saveState()
        width, height = letter
        fill = "#ffebee" if incomplete else badge_bg
        ink = "#b71c1c" if incomplete else badge_fg
        canvas.setFillColor(colors.HexColor(fill))
        canvas.setStrokeColor(colors.HexColor(ink))
        canvas.roundRect(width - 36 - 300, height - 30, 300, 18, 4, stroke=1, fill=1)
        canvas.setFillColor(colors.HexColor(ink))
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(width - 36 - 150, height - 24, badge_text[:80])
        canvas.setFillColor(colors.HexColor("#78909c"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(36, height - 24, "CBAM Emissions Passport")
        canvas.drawRightString(width - 36, 20, f"Page {document.page}")
        canvas.restoreState()

    doc.topMargin = 44

    # 1. Header & Title Banner
    story.append(Paragraph("CBAM-READY EMISSIONS PASSPORT", title_style))
    story.append(
        Paragraph(
            "Verifiable Decision-Support Evidence Pack | European Union Regulation (EU) 2023/956 Definitive Regime",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a237e"), spaceAfter=10))

    # 2. Metadata Grid
    ws = passport_data.get("workspace", {})
    calc = passport_data.get("calculation", {})
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    review_status_label = raw_review_status + (f" (v{version_number})" if version_number else "")

    meta_table_data = [
        [
            Paragraph("<b>Exporting Company:</b>", body_style),
            Paragraph(escape(str(ws.get("company_name") or "")), body_style),
            Paragraph("<b>Report Date:</b>", body_style),
            Paragraph(gen_time, body_style),
        ],
        [
            Paragraph("<b>Installation / Plant:</b>", body_style),
            Paragraph(escape(f"{ws.get('installation_name') or ''} ({ws.get('installation_id') or ''})"), body_style),
            Paragraph("<b>Country of Origin:</b>", body_style),
            Paragraph(escape(str(ws.get("country") or "")), body_style),
        ],
        [
            Paragraph("<b>Target CN Code:</b>", body_style),
            Paragraph(f"<b>{escape(str(ws.get('cn_code') or ''))}</b>", bold_cell_style),
            Paragraph("<b>Reporting Period:</b>", body_style),
            Paragraph(escape(str(ws.get("reporting_period") or "")), body_style),
        ],
        [
            Paragraph("<b>Product Family:</b>", body_style),
            Paragraph(escape(str(ws.get("target_product_family") or "")), body_style),
            Paragraph("<b>Review Status:</b>", body_style),
            Paragraph(f"<b>{escape(review_status_label)}</b>", bold_cell_style),
        ],
    ]

    t_meta = Table(meta_table_data, colWidths=[1.4 * inch, 2.3 * inch, 1.4 * inch, 2.3 * inch])
    t_meta.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f9fa")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#eceff1")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    story.append(t_meta)
    story.append(Spacer(1, 8))

    # Status badge on the first page, with the wording for that status.
    wording = passport_data.get("status_wording") or STATUS_WORDING.get(status_key, "")
    badge_rows = [[Paragraph(
        f"<font size='13'><b>{escape(raw_review_status.upper())}</b></font>"
        + (f"&nbsp;&nbsp;<font size='9'>Passport version {version_number}</font>" if version_number else "")
        + f"<br/><font size='8.5'>{escape(wording)}</font>",
        ParagraphStyle("Badge", parent=body_style, textColor=colors.HexColor(badge_fg), leading=14),
    )]]
    verifier = passport_data.get("verifier_details")
    if status_key == "externally-verified" and verifier:
        badge_rows.append([Paragraph(
            "<b>Verifier details entered by the user:</b> "
            + escape(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in verifier.items() if v))
            + "<br/><b>Recorded by the user; not verified by this tool.</b>",
            body_style,
        )])
    t_badge = Table(badge_rows, colWidths=[7.4 * inch])
    t_badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(badge_bg)),
        ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor(badge_fg)),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(t_badge)
    story.append(Spacer(1, 10))

    # 3. Legal Disclaimer Box
    disclaimer_text = (
        "<b>LEGAL & COMPLIANCE DISCLAIMER:</b> This Emissions Passport is an auditable decision-support calculation evidence pack "
        "designed for Indian SME manufacturers supplying EU importers. It applies official deterministic formulas under Commission "
        "Implementing Regulation (EU) 2025/2621 and IPCC 2006 guidelines. <i>This document does NOT replace statutory verification "
        "by an accredited independent verifier under Commission Implementing Regulation (EU) 2024/2493, nor does it legally certify emissions.</i>"
    )
    t_disc = Table([[Paragraph(disclaimer_text, disclaimer_style)]], colWidths=[7.4 * inch])
    t_disc.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffebee")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#e57373")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(t_disc)
    story.append(Spacer(1, 10))

    if incomplete:
        _append_incomplete(story, passport_data, section_heading, body_style, bold_cell_style)
        _append_status_history(story, passport_data, section_heading, body_style, bold_cell_style)
        doc.build(story, onFirstPage=_page_header, onLaterPages=_page_header)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        if output_path:
            output_path.write_bytes(pdf_bytes)
        return pdf_bytes

    if p1:
        calc = _legacy_calc_from_priority1(p1)

    # 4. Embedded Emissions Summary Box (SEE)
    story.append(Paragraph("1. Specific Embedded Emissions (SEE) Summary", section_heading))

    see_val = calc["specific_embedded_emissions_see"]
    alloc_scope1 = calc["allocated_scope1_direct_tco2"]
    alloc_scope2 = calc["allocated_scope2_indirect_tco2"]
    precursor_val = calc["precursor_emissions_tco2"]
    total_embedded = calc["total_embedded_emissions_tco2"]
    target_tonnes = calc["net_target_production_tonnes"]
    alpha_p = calc["allocation_factor_alpha"]
    quality = calc["data_quality_rating"]

    summary_table_data = [
        [
            Paragraph("<b>Emission Component</b>", bold_cell_style),
            Paragraph("<b>Gross Facility</b>", bold_cell_style),
            Paragraph("<b>Allocation (alpha_p)</b>", bold_cell_style),
            Paragraph("<b>Attributed to Product</b>", bold_cell_style),
            Paragraph("<b>Unit Specific (tCO2e/t)</b>", bold_cell_style),
        ],
        [
            Paragraph("Direct Scope 1 (Furnace Oil / Fuel)", body_style),
            Paragraph(f"{calc['facility_gross_scope1_tco2']:.2f} tCO2", body_style),
            Paragraph(f"alpha_p = {alpha_p:.4f}", body_style),
            Paragraph(f"{alloc_scope1:.4f} tCO2", body_style),
            Paragraph(f"{alloc_scope1 / target_tonnes:.4f}", body_style),
        ],
        [
            Paragraph("Indirect Scope 2 (Grid Electricity)", body_style),
            Paragraph(f"{calc['facility_gross_scope2_tco2']:.2f} tCO2", body_style),
            Paragraph(f"alpha_p = {alpha_p:.4f} (delta=1)", body_style),
            Paragraph(f"{alloc_scope2:.4f} tCO2", body_style),
            Paragraph(f"{alloc_scope2 / target_tonnes:.4f}", body_style),
        ],
        [
            Paragraph("Precursor Materials (Alloy Steel Wire Rod)", body_style),
            Paragraph(f"{precursor_val:.2f} tCO2", body_style),
            Paragraph("100% Direct Attribution", body_style),
            Paragraph(f"{precursor_val:.4f} tCO2", body_style),
            Paragraph(f"{precursor_val / target_tonnes:.4f}", body_style),
        ],
        [
            Paragraph("<b>TOTAL SPECIFIC EMBEDDED EMISSIONS (SEE)</b>", bold_cell_style),
            Paragraph("—", body_style),
            Paragraph(f"Net Output: {target_tonnes:.1f} t", body_style),
            Paragraph(f"<b>{total_embedded:.4f} tCO2</b>", bold_cell_style),
            Paragraph(f"<b>{see_val:.4f} tCO2e/t</b>", bold_cell_style),
        ],
    ]

    t_summary = Table(
        summary_table_data,
        colWidths=[2.5 * inch, 1.2 * inch, 1.4 * inch, 1.3 * inch, 1.0 * inch],
    )
    t_summary.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaf6")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#c5cae9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#b0bec5")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ])
    )
    story.append(t_summary)
    story.append(Spacer(1, 10))
    if p1:
        _append_breakdown(story, p1, section_heading, body_style, bold_cell_style)

    # 5. Monte Carlo Statistical Uncertainty Box
    unc = calc.get("uncertainty") or {}
    p5 = unc.get("p5")
    p50 = unc.get("p50")
    p95 = unc.get("p95")
    interval_text = f"90% [{p5:.4f} - {p95:.4f}]" if p5 is not None and p95 is not None else "Not available"
    percentile_text = (
        [f"{p5:.4f}", f"{p50:.4f}", f"{p95:.4f}"]
        if p5 is not None and p50 is not None and p95 is not None
        else ["Not available", "Not available", "Not available"]
    )

    story.append(Paragraph("2. Uncertainty Quantification & Data Quality Rating", section_heading))
    unc_data = [
        [
            Paragraph(f"<b>Data Quality Rating:</b> {quality}", bold_cell_style),
            Paragraph(
                f"<b>Monte Carlo Iterations:</b> {unc.get('iterations', 'Not available')}"
                + (f" (seed {p1.get('mc_seed')})" if p1 else ""),
                body_style,
            ),
            Paragraph(f"<b>Confidence Interval:</b> {interval_text}", body_style),
        ],
        [
            Paragraph(f"<b>P5 (Conservative Lower Bound):</b> {percentile_text[0]} tCO2e/t", body_style),
            Paragraph(f"<b>P50 (Simulated Median):</b> {percentile_text[1]} tCO2e/t", bold_cell_style),
            Paragraph(f"<b>P95 (Conservative Upper Bound):</b> {percentile_text[2]} tCO2e/t", body_style),
        ],
    ]
    t_unc = Table(unc_data, colWidths=[2.5 * inch, 2.4 * inch, 2.5 * inch])
    t_unc.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e0f2f1")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#80cbc4")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#b2dfdb")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(t_unc)
    story.append(Spacer(1, 10))

    if p1:
        _append_assumptions(story, p1, section_heading, body_style, bold_cell_style)
        _append_factors(story, p1, section_heading, body_style, bold_cell_style)

    # 6. Activity Data & Evidence Provenance Table
    story.append(Paragraph("3. Activity Data Provenance & Traceability Ledger", section_heading))
    provenance_data = [
        [
            Paragraph("<b>Stream / Source</b>", bold_cell_style),
            Paragraph("<b>Raw Value</b>", bold_cell_style),
            Paragraph("<b>Effective Value</b>", bold_cell_style),
            Paragraph("<b>Override Reason</b>", bold_cell_style),
            Paragraph("<b>Source File & Snippet Reference</b>", bold_cell_style),
        ],
    ]

    overrides = passport_data.get("overrides") or {}
    provenance_rows = []
    for doc_hash, document in (passport_data.get("uploaded_documents") or {}).items():
        for field in document.get("extracted_fields", []):
            field_name = str(field.get("field_name", ""))
            override = overrides.get(f"{doc_hash}_{field_name}") or {}
            raw_value = f"{field.get('extracted_value', '')} {field.get('unit', '')}".strip()
            if override:
                effective_value = (
                    f"{override.get('override_value', '')} "
                    f"{override.get('override_unit') or field.get('unit', '')}"
                ).strip()
                override_reason = override.get("override_reason") or "Human override"
            else:
                effective_value = raw_value
                override_reason = "No override"

            page_reference = (
                f" [p. {field.get('evidence_page')}]" if field.get("evidence_page") else ""
            )
            snippet = (field.get("evidence_snippet") or "").strip()
            if len(snippet) > 90:
                snippet = snippet[:87] + "..."
            source_reference = (
                f"{document.get('filename', 'document')}{page_reference} "
                f"(SHA-256: {doc_hash[:12]}...)"
            )
            if snippet:
                source_reference += f" - {snippet}"

            provenance_rows.append([
                Paragraph(escape(field_name), body_style),
                Paragraph(escape(raw_value), body_style),
                Paragraph(escape(effective_value), body_style),
                Paragraph(escape(str(override_reason)), body_style),
                Paragraph(escape(source_reference), body_style),
            ])

    if provenance_rows:
        provenance_data.extend(provenance_rows)
    else:
        provenance_data.append([
            Paragraph("No activity data attached", body_style),
            Paragraph("-", body_style),
            Paragraph("-", body_style),
            Paragraph("-", body_style),
            Paragraph("Upload source documents to populate this ledger.", body_style),
        ])
    t_prov = Table(
        provenance_data,
        colWidths=[1.4 * inch, 1.0 * inch, 1.0 * inch, 1.5 * inch, 2.5 * inch],
    )
    t_prov.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceff1")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    story.append(t_prov)
    story.append(Spacer(1, 10))

    # 7. Cryptographic Fingerprints & Audit Ledger
    story.append(Paragraph("4. Cryptographic Document Signatures (SHA-256)", section_heading))
    docs_ledger = passport_data.get("uploaded_documents", {})
    ledger_rows = [
        [
            Paragraph("<b>File Name</b>", bold_cell_style),
            Paragraph("<b>Size</b>", bold_cell_style),
            Paragraph("<b>SHA-256 Cryptographic Hash Digest</b>", bold_cell_style),
        ]
    ]

    if docs_ledger:
        for doc_hash, doc_info in list(docs_ledger.items())[:5]:
            ledger_rows.append([
                Paragraph(doc_info.get("filename", "document.pdf"), body_style),
                Paragraph(f"{doc_info['file_size_bytes'] / 1024:.1f} KB", body_style),
                Paragraph(f"<font face='Courier' size='7'>{doc_hash}</font>", body_style),
            ])
    else:
        ledger_rows.append([
            Paragraph("No source documents uploaded", body_style),
            Paragraph("", body_style),
            Paragraph("", body_style),
        ])

    t_ledger = Table(ledger_rows, colWidths=[2.2 * inch, 0.9 * inch, 4.3 * inch])
    t_ledger.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ])
    )
    story.append(t_ledger)
    story.append(Spacer(1, 14))

    # 8. Signature Block
    sig_data = [
        [
            Paragraph("<b>Prepared By:</b>", body_style),
            Paragraph("<b>Approved By Plant Head:</b>", body_style),
            Paragraph("<b>Third-Party Verifier Acknowledgement:</b>", body_style),
        ],
        [
            Paragraph("Signature: ______________________", body_style),
            Paragraph("Signature: ______________________", body_style),
            Paragraph("Verification Body: ________________", body_style),
        ],
        [
            Paragraph("Date: _________________________", body_style),
            Paragraph("Date: _________________________", body_style),
            Paragraph("Accreditation ID: ________________", body_style),
        ],
    ]
    t_sig = Table(sig_data, colWidths=[2.4 * inch, 2.5 * inch, 2.5 * inch])
    t_sig.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story.append(t_sig)

    if is_priority1 or passport_data.get("status_history"):
        _append_status_history(story, passport_data, section_heading, body_style, bold_cell_style)

    # Build PDF
    doc.build(story, onFirstPage=_page_header, onLaterPages=_page_header)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    if output_path:
        output_path.write_bytes(pdf_bytes)

    return pdf_bytes



# ---------------------------------------------------------------------
# Priority 1 sections
# ---------------------------------------------------------------------

def _grid_table(rows, widths, header_bg="#eceff1"):
    table = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_bg)),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd8dc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _p(text, style):
    return Paragraph(escape(str(text if text is not None else "")), style)


def _append_incomplete(story, data, heading, body, bold):
    banner = Table([[Paragraph(
        f"<font size='16'><b>{INCOMPLETE_TEXT}</b></font><br/>"
        "Required inputs are missing and no approved fallback has been applied. "
        "No specific embedded emissions (SEE) value is produced.",
        ParagraphStyle("Inc", parent=body, textColor=colors.HexColor("#b71c1c"), leading=18),
    )]], colWidths=[7.4 * inch])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffebee")),
        ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#c62828")),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(banner)
    story.append(Spacer(1, 10))
    story.append(Paragraph("Missing inputs checklist", heading))
    rows = [[Paragraph("<b>Input</b>", bold), Paragraph("<b>What is missing</b>", bold),
             Paragraph("<b>Fallback</b>", bold)]]
    for gap in data.get("gaps") or []:
        rows.append([
            Paragraph(f"<font face='Courier' size='7'>{escape(gap['input_key'])}</font>", body),
            _p(gap["description"], body),
            _p("Available - apply explicitly" if gap.get("fallback_available") else "None - value required", body),
        ])
    if len(rows) == 1:
        rows.append([_p("-", body), _p("No open gaps listed.", body), _p("-", body)])
    story.append(_grid_table(rows, [2.2, 3.7, 1.5], "#ffcdd2"))


def _append_breakdown(story, p1, heading, body, bold):
    r = p1["result"]
    story.append(Paragraph("1a. Direct emissions breakdown (Q x NCV x EF x OF + Process)", heading))
    rows = [[Paragraph(f"<b>{h}</b>", bold) for h in ("Term", "Raw (tCO2)", "Allocated (tCO2)", "Basis")]]
    for term in r["fuels"]:
        rows.append([_p(f"Fuel: {term['name']}", body), _p(f"{term['raw_tco2']:.4f}", body),
                     _p(f"{term['allocated_tco2']:.4f}", body),
                     _p("fallback-based" if term["fallback_based"] else "measured", body)])
    for term in r["process"]:
        rows.append([_p(f"Process: {term['name']}", body), _p(f"{term['raw_tco2']:.4f}", body),
                     _p(f"{term['allocated_tco2']:.4f}", body),
                     _p("fallback-based" if term["fallback_based"] else "measured / direct", body)])
    if not r["process"]:
        rows.append([_p("Process emissions", body), _p("0.0000", body), _p("0.0000", body),
                     _p("Declared: no process emissions", body)])
    rows.append([Paragraph("<b>Direct total</b>", bold), _p(f"{r['scope1_raw_tco2']:.4f}", body),
                 _p(f"{r['scope1_allocated_tco2']:.4f}", body), _p(f"alpha = {r['alpha']:.4f}", body)])
    story.append(_grid_table(rows, [2.9, 1.3, 1.4, 1.8]))
    for note in r.get("notes") or []:
        story.append(Paragraph(f"<i>{escape(note)}</i>", body))
    story.append(Spacer(1, 8))


def _append_assumptions(story, p1, heading, body, bold):
    title = Paragraph("Assumptions (fallback values applied by the user)", heading)
    rows = [[Paragraph(f"<b>{h}</b>", bold) for h in ("Input", "Value used", "Source", "Set / year", "Reason / applied by")]]
    for a in p1.get("assumptions") or []:
        rows.append([
            Paragraph(f"<font face='Courier' size='7'>{escape(a['input_key'])}</font>", body),
            _p(f"{a['value_used']} {a['unit']}", body),
            _p(f"{a.get('publisher', '')} - see 'Emission factors used' for the full citation", body),
            _p(f"{a['factor_set_version']} / {a.get('year', '')}", body),
            _p(f"{a['reason']} - {a['applied_by']}", body),
        ])
    if len(rows) == 1:
        rows.append([_p("None", body), _p("-", body), _p("All inputs are measured or document-backed.", body),
                     _p("-", body), _p("-", body)])
    story.append(KeepTogether([title, _grid_table(rows, [1.5, 0.9, 2.6, 0.8, 1.6], "#fff8e1")]))
    story.append(Spacer(1, 8))


def _append_factors(story, p1, heading, body, bold):
    story.append(Paragraph("Emission factors used", heading))
    rows = [[Paragraph(f"<b>{h}</b>", bold) for h in ("Key", "Value", "Unit", "Source", "Year", "Factor set")]]
    for f in p1.get("factors_used") or []:
        rows.append([
            Paragraph(f"<font face='Courier' size='7'>{escape(f['key'])}</font>", body),
            _p(f["value"], body), _p(f["unit"], body),
            Paragraph(f"{escape(f['source_title'])} - {escape(f['publisher'])}<br/>"
                      f"<font size='6.5' color='#546e7a'>{escape(f['source_url'])}</font>", body),
            _p(f["year"], body), _p(f["factor_set_version"], body),
        ])
    if len(rows) == 1:
        rows.append([_p("None", body), _p("-", body), _p("-", body),
                     _p("No register factors used; all factors are document-backed inputs.", body),
                     _p("-", body), _p("-", body)])
    story.append(_grid_table(rows, [1.4, 0.6, 0.7, 3.4, 0.5, 0.8]))
    story.append(Paragraph(
        f"Factor set version for this passport: <b>{escape(str(p1.get('factor_set_version')))}</b>. "
        "Re-running this passport uses the same version, so the result is reproducible.", body))
    story.append(Spacer(1, 8))


def _append_status_history(story, data, heading, body, bold):
    story.append(PageBreak())
    story.append(Paragraph("Status history", heading))
    rows = [[Paragraph(f"<b>{h}</b>", bold) for h in ("When (UTC)", "Version", "From", "To", "Who / role", "Note")]]
    for h in data.get("status_history") or []:
        rows.append([
            _p(str(h.get("timestamp", ""))[:19], body), _p(h.get("version_number"), body),
            _p(h.get("from") or "(new)", body), _p(h.get("to"), body),
            _p(f"{h.get('actor')} ({h.get('role')})", body), _p(h.get("note") or "", body),
        ])
    if len(rows) == 1:
        rows.append([_p("-", body)] * 6)
    story.append(_grid_table(rows, [1.1, 0.6, 1.0, 1.1, 1.4, 2.2]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<i>Roles are recorded through a prototype 'acting as' selector with a typed name; real authentication is "
        "future work. 'Externally Verified' is recorded by the user from a document they hold and is not checked "
        "by this tool.</i>", body))
