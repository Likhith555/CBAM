import docx
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import parse_xml, OxmlElement
from docx.oxml.ns import nsdecls, qn
from pathlib import Path

def set_cell_background(cell, hex_color):
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    cell._tc.get_or_add_tcPr().append(shading_elm)

def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('w:top', top), ('w:bottom', bottom), ('w:left', left), ('w:right', right)]:
        node = OxmlElement(m)
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)

def create_document():
    doc = Document()

    # Page Margins
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Styles & Colors
    # Primary Emerald: #047857, Charcoal: #1E293B, Slate: #64748B, Border: #E2E8F0
    PRIMARY_COLOR = RGBColor(4, 120, 87)
    TEXT_COLOR = RGBColor(30, 41, 59)
    MUTED_COLOR = RGBColor(100, 116, 139)

    # Document Title
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(4)
    run_title = p_title.add_run("CBAM-Ready Emissions Passport")
    run_title.font.name = "Arial"
    run_title.font.size = Pt(24)
    run_title.font.bold = True
    run_title.font.color.rgb = PRIMARY_COLOR

    # Subtitle
    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(18)
    run_sub = p_sub.add_run("Full Architecture, Data Flow, Regulatory Engine & Operational Specification")
    run_sub.font.name = "Arial"
    run_sub.font.size = Pt(12)
    run_sub.font.color.rgb = MUTED_COLOR

    # Metadata Box (Callout table)
    meta_table = doc.add_table(rows=2, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_table.autofit = False

    meta_data = [
        [("Target Sector", "Iron & Steel (Steel Fasteners - CN 7318)"), ("Pilot Exporter", "Precision Fasteners India Pvt Ltd (IN-DEL-01)")],
        [("Compliance Scope", "EU CBAM (Reg 2023/956, Implementing Reg 2025/2621)"), ("Architecture", "FastAPI + Streamlit/React + SQLite + ReportLab RAG")]
    ]

    for r_idx, row in enumerate(meta_table.rows):
        for c_idx, cell in enumerate(row.cells):
            label, val = meta_data[r_idx][c_idx]
            cell.width = Inches(3.25)
            set_cell_background(cell, "F8FAFC")
            set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            r_label = p.add_run(f"{label}: ")
            r_label.font.name = "Arial"
            r_label.font.size = Pt(9.5)
            r_label.font.bold = True
            r_label.font.color.rgb = PRIMARY_COLOR
            r_val = p.add_run(val)
            r_val.font.name = "Arial"
            r_val.font.size = Pt(9.5)
            r_val.font.color.rgb = TEXT_COLOR

    doc.add_paragraph().paragraph_format.space_after = Pt(12)

    def add_heading_1(text):
        h = doc.add_paragraph()
        h.paragraph_format.space_before = Pt(16)
        h.paragraph_format.space_after = Pt(6)
        h.paragraph_format.keep_with_next = True
        r = h.add_run(text)
        r.font.name = "Arial"
        r.font.size = Pt(15)
        r.font.bold = True
        r.font.color.rgb = PRIMARY_COLOR
        return h

    def add_heading_2(text):
        h = doc.add_paragraph()
        h.paragraph_format.space_before = Pt(12)
        h.paragraph_format.space_after = Pt(4)
        h.paragraph_format.keep_with_next = True
        r = h.add_run(text)
        r.font.name = "Arial"
        r.font.size = Pt(12)
        r.font.bold = True
        r.font.color.rgb = RGBColor(15, 23, 42)
        return h

    def add_body_p(text, bold_prefix=None):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(5)
        p.paragraph_format.line_spacing = 1.15
        if bold_prefix:
            rb = p.add_run(bold_prefix)
            rb.font.name = "Arial"
            rb.font.size = Pt(10)
            rb.font.bold = True
            rb.font.color.rgb = TEXT_COLOR
        r = p.add_run(text)
        r.font.name = "Arial"
        r.font.size = Pt(10)
        r.font.color.rgb = TEXT_COLOR
        return p

    def add_bullet(text, bold_prefix=None):
        p = doc.add_paragraph(style='List Bullet')
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.15
        if bold_prefix:
            rb = p.add_run(bold_prefix)
            rb.font.name = "Arial"
            rb.font.size = Pt(10)
            rb.font.bold = True
            rb.font.color.rgb = TEXT_COLOR
        r = p.add_run(text)
        r.font.name = "Arial"
        r.font.size = Pt(10)
        r.font.color.rgb = TEXT_COLOR
        return p

    # --- Section 1 ---
    add_heading_1("1. Executive Summary & Problem Context")
    add_body_p(
        "The European Union's Carbon Border Adjustment Mechanism (CBAM) under Regulation (EU) 2023/956 and Implementing Regulation (EU) 2025/2621 imposes carbon tariffs on imports of emissions-intensive goods entering the EU. Foreign exporters must submit verified declarations detailing the Specific Embedded Emissions (SEE, expressed in tCO2e per tonne of goods)."
    )
    add_body_p(
        "While multinational primary steelmakers have dedicated ESG departments, Indian Micro, Small, and Medium Enterprises (MSMEs)—which forge, machine, and export high-tensile steel fasteners, flanges, and machined components—cannot afford Big-4 environmental audit retainers (costing €15,000–€30,000 annually). They confront three critical bottlenecks:"
    )
    add_bullet(" Electric utility bills from state Discoms (e.g., DHBVN, PSPCL), fuel delivery challans (IOCL, BPCL), piped gas bills (IGL), and mill test certificates (JSPL, Tata Steel) arrive in disconnected PDF, scanned image, CSV, and Excel formats.", "1. Scattered, Multi-Format Records:")
    add_bullet(" Factories run multiple product lines through shared 11kV grid connections and annealing furnaces without dedicated sub-metering for export goods.", "2. Unmetered Shared Utilities:")
    add_bullet(" Generic Large Language Models (LLMs) cannot perform carbon arithmetic because probabilistic generation produces hallucinations that are immediately rejected by accredited third-party verifiers. Every number requires reproducible deterministic math anchored to cryptographic evidence.", "3. Regulatory Non-Compliance of Black-Box AI:")

    # --- Section 2 ---
    add_heading_1("2. Core Architectural Principles")
    add_bullet(" AI/OCR models are restricted exclusively to unstructured document parsing and semantic regulatory retrieval. All emissions arithmetic is executed via 100% deterministic, auditable Python code.", "Strict Separation of Extraction and Arithmetic:")
    add_bullet(" Every ingested document is hashed using SHA-256 upon arrival. Extracted operational metrics maintain page coordinates, line references, and verbatim snippet context.", "Cryptographic Chain-of-Custody:")
    add_bullet(" Shared utility consumption is partitioned using the mass allocation factor alpha_p = (Net Target Product Output) / (Total Facility Output).", "Mass-Balance Allocation (alpha_p):")
    add_bullet(" A 2,000-run vectorized NumPy Monte Carlo engine models physical measurement errors (±2% electricity meter tolerance, ±5% fuel dipstick tolerance, ±3% precursor lab certification variance) to produce P5, Mean, and P95 confidence envelopes.", "Stochastic Uncertainty Quantification:")
    add_bullet(" Generates official ReportLab PDF certificates containing cryptographic evidence ledgers alongside CBAM-compliant CSV tables for EU importer filing.", "Verifier-Ready Output:")

    # --- Section 3 ---
    add_heading_1("3. Technology Stack & Implementation Architecture")
    add_bullet(" Python 3.12, FastAPI (Modular Monolith architecture), Pydantic v2 validation contracts, SQLAlchemy ORM, SQLite embedded database.", "Backend & API:")
    add_bullet(" PyMuPDF (fitz), pdfplumber, Tesseract OCR (with graceful fallback), pandas, openpyxl.", "Document AI & Ingestion:")
    add_bullet(" Pure Python deterministic math engine, NumPy for Monte Carlo vectorized stochastic simulations.", "Calculation Engine:")
    add_bullet(" Sentence-transformers embeddings, FAISS vector store with NumPy cosine similarity fallback, indexing official EU regulations with strict anti-hallucination guardrails.", "Regulatory RAG:")
    add_bullet(" ReportLab PDF engine (tables, badges, cryptoledger formatting) and pandas CSV serialization.", "Audit Reporting:")
    add_bullet(" Streamlit multi-page wizard (frontend/app.py) with guided 5-step workflow; React / Vite / Tailwind web app (web/) with TypeScript client bindings.", "Dual Frontend:")

    # --- Section 4 ---
    add_heading_1("4. Repository Structure & Key File Map")

    file_table = doc.add_table(rows=1, cols=3)
    file_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    file_table.autofit = False

    headers = [("Directory / File", Inches(2.2)), ("Role / Module", Inches(1.8)), ("Description", Inches(2.5))]
    hdr_cells = file_table.rows[0].cells
    for idx, (title, w) in enumerate(headers):
        hdr_cells[idx].width = w
        set_cell_background(hdr_cells[idx], "047857")
        set_cell_margins(hdr_cells[idx], top=80, bottom=80, left=100, right=100)
        p = hdr_cells[idx].paragraphs[0]
        r = p.add_run(title)
        r.font.name = "Arial"
        r.font.size = Pt(9.5)
        r.font.bold = True
        r.font.color.rgb = RGBColor(255, 255, 255)

    file_rows = [
        ("app/modules/calculation/engine.py", "Calculation Engine", "Pure deterministic Scope 1, Scope 2, and precursor emissions math."),
        ("app/modules/document_ai/parser.py", "Document AI Ingestion", "SHA-256 calculation, PDF parsing, OCR, and spreadsheet ingestion."),
        ("app/modules/document_ai/extractor.py", "Field Extractor", "Schema-constrained regex/heuristic extractor with verbatim snippet context."),
        ("app/modules/document_ai/generate_samples.py", "Synthetic Generator", "Generates 10 realistic Indian industrial utility bills, challans, and MTCs."),
        ("app/modules/uncertainty/monte_carlo.py", "Uncertainty Engine", "NumPy-vectorized Monte Carlo engine (P5, Mean, P95)."),
        ("app/modules/reporting/pdf_generator.py", "PDF Generator", "ReportLab auditable PDF passport with embedded cryptographic evidence ledger."),
        ("app/modules/rag/assistant.py", "Regulatory RAG", "FAISS vector assistant grounded in official EU CBAM regulations."),
        ("app/db/models.py & seed.py", "Database & Registry", "SQLite ORM tables for audit logs, workspaces, and seeded factors."),
        ("frontend/views/dashboard.py", "Streamlit Dashboard", "Passport calculation UI, confidence band charts, and exports."),
        ("web/src/features/cbam/passport/", "React Web App", "Modern TypeScript React dashboard with missing-inputs checklist.")
    ]

    for fpath, frole, fdesc in file_rows:
        row_cells = file_table.add_row().cells
        for c_idx, text in enumerate([fpath, frole, fdesc]):
            row_cells[c_idx].width = headers[c_idx][1]
            set_cell_background(row_cells[c_idx], "FFFFFF" if len(file_table.rows) % 2 == 0 else "F8FAFC")
            set_cell_margins(row_cells[c_idx], top=60, bottom=60, left=80, right=80)
            p = row_cells[c_idx].paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(text)
            r.font.name = "Arial"
            r.font.size = Pt(8.5)
            r.font.color.rgb = TEXT_COLOR
            if c_idx == 0:
                r.font.name = "Courier New"

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # --- Section 5 ---
    add_heading_1("5. The 5-Step Operational Pipeline")
    add_bullet(" Exporter details, UN/LOCODE (IN-DEL-01), reporting window (2026-10-01 to 2026-10-31), and target CN code (7318 15 82 - Steel Fasteners). Sets delta_CN = 1 (mandatory Scope 2 indirect emissions inclusion).", "Step 1 · Workspace Setup:")
    add_bullet(" Ingestion of scattered records (DHBVN power bill, IOCL furnace oil challan, JSPL wire rod MTC). Computes SHA-256 hashes and captures verbatim snippet anchors.", "Step 2 · Ingestion & Document AI:")
    add_bullet(" Side-by-side verification of extracted metrics vs. source evidence. Human operators can override values with mandatory audit log justification.", "Step 3 · Review & Audit Trail:")
    add_bullet(" User enters target production (500 t) and total installation production (625 t). Sets utility coverage to 'Shared installation use' to establish alpha_p = 0.8000.", "Step 4 · Mass Allocation:")
    add_bullet(" Deterministic execution of Scope 1, Scope 2, and precursor arithmetic; 2,000-iteration Monte Carlo simulation; download of auditable PDF and CSV emissions passport.", "Step 5 · Passport Dashboard & Export:")

    # --- Section 6 ---
    add_heading_1("6. Deterministic Benchmark Scenario & Mathematical Proof")
    add_body_p(
        "The calculation engine was validated using a real-world benchmark scenario representing an Indian steel fastener exporter:"
    )

    calc_table = doc.add_table(rows=1, cols=4)
    calc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    calc_table.autofit = False

    calc_hdrs = [("Emission Source", Inches(1.6)), ("Raw Activity Data", Inches(1.8)), ("Formula & Allocation (alpha_p = 0.80)", Inches(1.8)), ("Allocated Emissions", Inches(1.3))]
    hdr_cells2 = calc_table.rows[0].cells
    for idx, (title, w) in enumerate(calc_hdrs):
        hdr_cells2[idx].width = w
        set_cell_background(hdr_cells2[idx], "047857")
        set_cell_margins(hdr_cells2[idx], top=80, bottom=80, left=100, right=100)
        p = hdr_cells2[idx].paragraphs[0]
        r = p.add_run(title)
        r.font.name = "Arial"
        r.font.size = Pt(9.5)
        r.font.bold = True
        r.font.color.rgb = RGBColor(255, 255, 255)

    calc_data = [
        ("Scope 1 Fuel", "15,000 L Furnace Oil\nNCV: 0.0402 GJ/L\nEF: 0.0774 tCO2/GJ", "15,000 * 0.0402 * 0.0774 = 46.672 tCO2\nAllocated: 46.672 * 0.80", "37.338 tCO2"),
        ("Scope 2 Electricity", "250,000 kWh = 250 MWh\nGrid EF: 0.716 tCO2/MWh\ndelta_CN = 1 (included)", "250 * 0.716 = 179.000 tCO2\nAllocated: 179.000 * 0.80", "143.200 tCO2"),
        ("Precursor Material", "520 t Steel Wire Rod (10B21)\nSupplier SEE: 1.85 tCO2/t", "520 t * 1.85 tCO2/t\n(Direct material input)", "962.000 tCO2"),
        ("Total Embedded Carbon", "Sum of Scope 1, 2, and Precursor", "37.338 + 143.200 + 962.000", "1,142.538 tCO2"),
        ("Specific Embedded Emissions (SEE)", "Net Finished Product:\n500.0 tonnes", "1,142.538 tCO2 / 500.0 tonnes", "2.2851 tCO2/t")
    ]

    for src, raw, form, res in calc_data:
        row_cells = calc_table.add_row().cells
        for c_idx, text in enumerate([src, raw, form, res]):
            row_cells[c_idx].width = calc_hdrs[c_idx][1]
            set_cell_background(row_cells[c_idx], "FFFFFF" if len(calc_table.rows) % 2 == 0 else "F8FAFC")
            set_cell_margins(row_cells[c_idx], top=60, bottom=60, left=80, right=80)
            p = row_cells[c_idx].paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(text)
            r.font.name = "Arial"
            r.font.size = Pt(8.5)
            r.font.color.rgb = TEXT_COLOR
            if c_idx == 3 or "SEE" in src:
                r.font.bold = True

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # --- Section 7 ---
    add_heading_1("7. Future Execution & Research Roadmap")
    add_bullet(" Collect and annotate 5,000 Indian Discom bills and transport challans to fine-tune open-source LayoutLMv3 or Donut models, replacing heuristic regex with localized vision-language weights.", "Phase 1 · Specialized Document AI:")
    add_bullet(" Direct telemetry integration with industrial smart energy meters (Schneider Electric, Secure Meters, L&T) using Modbus TCP / MQTT protocols for automated sub-metering.", "Phase 2 · Live IoT & SCADA Pipeline:")
    add_bullet(" Automated serialization matching the official European Commission CBAM Transitional Registry XML Schema Definition (XSD) for direct portal submission.", "Phase 3 · Official EU XML Registry Schema:")
    add_bullet(" Implementation of ZKP algorithms to mathematically demonstrate emissions compliance (e.g., SEE <= 2.28 tCO2/t) without exposing proprietary commercial invoice details (electricity tariffs, production volumes, and supplier costs) to EU buyers.", "Phase 4 · Zero-Knowledge Proofs for Trade Privacy:")
    add_bullet(" Expanding registry configurations and emission factors to Aluminium (CN 7601-7616), Cement (CN 2523), and Fertilizers (CN 3102).", "Phase 5 · Expansion to Other CBAM Sectors:")

    # Save to disk
    output_path = Path("C:/Users/haris/CBAM/CBAM_Ready_Emissions_Passport_Documentation.docx")
    doc.save(str(output_path))
    print(f"Document successfully created at: {output_path}")

if __name__ == "__main__":
    create_document()
