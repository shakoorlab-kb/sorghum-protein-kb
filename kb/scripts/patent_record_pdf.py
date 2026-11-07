#!/usr/bin/env python3
"""Build a PDF record of a patent from its Patentscope text and drawing images, for ingest into the KB.

  .venv/bin/python scripts/patent_record_pdf.py papers/patent_sources/<NUMBER> OUT.pdf

Use this when the official publication PDF cannot be downloaded by script (Patentscope and Google Patents
serve WO publications through browser-only pages). The folder holds:
  patentscope_text.txt   bibliographic data, abstract, description and claims, copied verbatim from Patentscope
                         (a block starting "Table N." with "a | b | c" rows is rendered as a table)
  drawings_*.png         the publication's drawing sheets, in file-name order
The PDF keeps the text as given and says on its first page where it came from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
BODY = ParagraphStyle("body", fontName="DejaVu", fontSize=9.5, leading=13, spaceAfter=5)
HEAD = ParagraphStyle("head", fontName="DejaVu-Bold", fontSize=11, leading=15, spaceBefore=8, spaceAfter=5)
NOTE = ParagraphStyle("note", fontName="DejaVu", fontSize=8, leading=11, textColor=colors.HexColor("#555555"), spaceAfter=8)
HEADINGS = {"ABSTRACT", "DESCRIPTION", "CLAIMS", "WHAT IS CLAIMED IS", "BACKGROUND", "SUMMARY", "DRAWINGS",
            "DETAILED DESCRIPTION", "CROSS-REFERENCE TO RELATED APPLICATIONS", "SEQUENCE DISCLOSURE STATEMENT", "TITLE"}


def build(folder: Path, out: Path) -> None:
    lines = (folder / "patentscope_text.txt").read_text().splitlines()
    story, i = [], 0
    while i < len(lines):
        line = lines[i].rstrip()
        if i < 3 and line.startswith(("SOURCE:", "The description", "Table 2 is")):
            story.append(Paragraph(escape(line), NOTE))
        elif line.startswith("Table ") and any(" | " in l for l in lines[i + 1:i + 3]):  # a table block follows its caption
            story.append(Paragraph(escape(line), HEAD))
            rows, i = [], i + 1
            while i < len(lines) and lines[i].strip():
                cells = [c.strip() for c in lines[i].split(" | ")]
                rows.append(cells if len(cells) > 1 else [cells[0], "", ""])
                i += 1
            t = Table([["", "Wild type", "kaf-23"]] + rows, colWidths=[3.6 * inch, 1.2 * inch, 1.2 * inch])
            t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "DejaVu", 9), ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 9),
                                   ("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story += [t, Spacer(1, 8)]
            continue
        elif line.strip() in HEADINGS or (line.isupper() and 12 < len(line) < 90 and not line.startswith("[")):
            story.append(Paragraph(escape(line), HEAD))
        elif line.strip():
            story.append(Paragraph(escape(line), BODY))
        i += 1
    for png in sorted(folder.glob("drawings_*.png")):
        story += [PageBreak(), Paragraph(escape(f"Drawings: {png.stem.replace('_', ' ')}"), HEAD),
                  Image(str(png), width=6.4 * inch, height=8.6 * inch, kind="proportional")]
    SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                      topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                      title=lines[4] if len(lines) > 4 else folder.name).build(story)


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
    print("wrote", sys.argv[2])
