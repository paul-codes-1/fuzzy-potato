"""Document text extraction: PDF (pdfplumber + OCR fallback) and HTML.

Pulled out of main.py to dedupe the two near-identical pdfplumber blocks
that lived inside download_agenda and download_minutes. Pure functions —
no pipeline state required.
"""

from __future__ import annotations

import html as _html
import re
import shutil
import subprocess
import zipfile
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
    ocr_max_pages: int = 40,
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
    max_pages: int = 40,
    log_fn: Callable = _noop,
    progress_fn: Callable = _noop,
) -> Optional[str]:
    """OCR the first ``max_pages`` of a PDF via tesseract. Returns extracted
    text or None on failure / no text."""
    try:
        # The old 5-page default silently dropped the back half of scanned
        # Planning Commission minutes (Action/vote lines sit at the END of
        # each item) — a 9-0 adoption vote on p.10 vanished from the archive.
        try:
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
        except Exception:
            total_pages = 0
        if total_pages > max_pages:
            log_fn(f"OCR truncated: {pdf_path.name} has {total_pages} pages, "
                   f"only the first {max_pages} will be OCR'd", "WARNING")
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


# ── Word documents ────────────────────────────────────────────────────────────
# Granicus serves some LFUCG minutes as Word files instead of PDF: .docx for
# 2024 Planning Commission subdivision minutes, legacy binary .doc for
# 2007–2015 BOA/PC minutes. The MinutesViewer redirect lands on
# DocumentViewer.php?file=lfucg_<hash>.doc[x] with content-type
# application/msword either way, so sniff magic bytes, not the header.

DOCX_MAGIC = b"PK\x03\x04"
DOC_MAGIC = b"\xd0\xcf\x11\xe0"


def sniff_office_kind(content: bytes) -> Optional[str]:
    """'docx' / 'doc' / None from the first bytes of a download."""
    if content[:4] == DOCX_MAGIC:
        return "docx"
    if content[:4] == DOC_MAGIC:
        return "doc"
    return None


def extract_docx_text(path: Path) -> Optional[str]:
    """Stdlib .docx → text: paragraphs become lines, tabs preserved. No
    python-docx dependency (the box's uv env doesn't ship it)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<w:br[^>]*/>", "\n", xml)
    text = _html.unescape(re.sub(r"<[^>]+>", "", xml))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def extract_doc_text(path: Path, *, log_fn: Callable = _noop) -> Optional[str]:
    """Legacy binary .doc → text via ``antiword`` (apt package on the boxes;
    see deploy/lightsail/SETUP.md). Returns None if antiword is missing."""
    if not shutil.which("antiword"):
        log_fn("antiword not installed — cannot extract legacy .doc minutes "
               "(sudo apt-get install -y antiword)", "WARNING")
        return None
    try:
        out = subprocess.run(["antiword", "-w", "0", str(path)],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        log_fn(f"antiword failed for {path.name}: {e}", "WARNING")
        return None
    text = out.stdout.strip()
    return text or None


def extract_office_text(path: Path, kind: str, *, log_fn: Callable = _noop) -> Optional[str]:
    """Dispatch on ``kind`` from :func:`sniff_office_kind`."""
    try:
        if kind == "docx":
            return extract_docx_text(path)
        if kind == "doc":
            return extract_doc_text(path, log_fn=log_fn)
    except Exception as e:
        log_fn(f"Word extraction error for {path.name}: {e}", "WARNING")
    return None
