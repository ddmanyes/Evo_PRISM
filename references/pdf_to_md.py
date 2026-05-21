"""
Convert downloaded reference PDFs to markdown files.
Usage: python pdf_to_md.py
"""

import fitz  # PyMuPDF
import re
from pathlib import Path

PDF_DIR = Path(__file__).parent / "pdfs"
OUT_DIR = Path(__file__).parent

PAPERS = {
    "llmlingua_emnlp2023.pdf": "llmlingua.md",
    "deepseek_ocr_2025.pdf": "deepseek_ocr.md",
    "agent_first_data_systems_2025.pdf": "agent_first_data_systems.md",
    "memgpt_2023.pdf": "memgpt.md",
    "duckdb_sigmod2019.pdf": "duckdb.md",
}


def clean_text(text: str) -> str:
    # Remove excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove hyphenation at line breaks (common in PDFs)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Normalize whitespace within lines
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_pdf(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        # Use "text" mode with proper reading order
        text = page.get_text("text", sort=True)
        pages.append(f"<!-- Page {i + 1} -->\n{text}")
    doc.close()
    return "\n\n".join(pages)


def pdf_to_markdown(pdf_path: Path, md_path: Path):
    print(f"  Extracting: {pdf_path.name} ...", end=" ")
    raw = extract_pdf(pdf_path)
    cleaned = clean_text(raw)

    # Add a header banner
    header = (
        f"> **Source PDF:** `references/pdfs/{pdf_path.name}`\n"
        f"> Extracted with PyMuPDF. Equations and figures may be incomplete.\n\n---\n\n"
    )

    content = header + cleaned
    md_path.write_text(content, encoding="utf-8")
    size_kb = md_path.stat().st_size / 1024
    print(f"OK ({size_kb:.0f} KB) -> {md_path.name}")


if __name__ == "__main__":
    print(f"Output directory: {OUT_DIR}\n")
    for pdf_name, md_name in PAPERS.items():
        pdf_path = PDF_DIR / pdf_name
        md_path = OUT_DIR / md_name
        if not pdf_path.exists():
            print(f"  SKIP (not found): {pdf_name}")
            continue
        if md_path.exists():
            print(f"  SKIP (already exists): {md_name}")
            continue
        pdf_to_markdown(pdf_path, md_path)

    print("\nDone.")
