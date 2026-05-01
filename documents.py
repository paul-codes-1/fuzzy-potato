"""Document text extraction: PDF (pdfplumber + OCR fallback) and HTML.

Pulled out of main.py to dedupe the two near-identical pdfplumber blocks
that lived inside download_agenda and download_minutes. Pure functions —
no pipeline state required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pdfplumber
import pytesseract
from bs4 import BeautifulSoup
from pdf2image import convert_from_path


def _noop(*_args, **_kwargs) -> None:
    pass


def extract_pdf_text(
    pdf_path: Path,
    *,
    log_fn: Callable = _noop,
    progress_fn: Callable = _noop,
    ocr_max_pages: int = 5,
) -> Optional[str]:
    """Extract text from a PDF. Tries pdfplumber first; if no text comes out
    (scanned PDF), falls back to OCR on the first ``ocr_max_pages`` pages.

    Returns the extracted text, or None if both paths failed.
    """
    text_parts: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        log_fn(f"pdfplumber extraction error for {pdf_path.name}: {e}", "WARNING")

    if text_parts:
        return "\n\n".join(text_parts)

    progress_fn("PDF appears scanned, attempting OCR...")
    return ocr_pdf(pdf_path, max_pages=ocr_max_pages, log_fn=log_fn, progress_fn=progress_fn)


def ocr_pdf(
    pdf_path: Path,
    *,
    max_pages: int = 5,
    log_fn: Callable = _noop,
    progress_fn: Callable = _noop,
) -> Optional[str]:
    """OCR the first ``max_pages`` of a PDF via tesseract. Returns extracted
    text or None on failure / no text."""
    try:
        images = convert_from_path(pdf_path, first_page=1, last_page=max_pages)
        if not images:
            return None

        text_parts: list[str] = []
        for i, image in enumerate(images):
            progress_fn(f"OCR processing page {i + 1}/{len(images)}...")
            page_text = pytesseract.image_to_string(image)
            if page_text and page_text.strip():
                text_parts.append(page_text.strip())

        if text_parts:
            return "\n\n".join(text_parts)
        return None
    except Exception as e:
        log_fn(f"OCR error: {e}", "WARNING")
        return None


def extract_html_text(html_content: str) -> Optional[str]:
    """Strip script/style/chrome from HTML, return plain text. Returns None
    if the result is too short to be a real document body (under 100 chars).
    """
    soup = BeautifulSoup(html_content, "lxml")
    for element in soup(["script", "style", "nav", "header", "footer"]):
        element.decompose()
    text = soup.get_text(separator="\n", strip=True)
    if text and len(text) > 100:
        return text
    return None
