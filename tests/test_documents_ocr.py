"""OCR cap regression: the old 5-page default silently truncated scanned
minutes (the June 11 2026 PC adoption vote sat on p.10 of an 11-page scan)."""
import inspect
from pathlib import Path
from unittest.mock import patch, MagicMock

import documents


def test_default_ocr_cap_covers_long_minutes():
    sig = inspect.signature(documents.extract_pdf_text)
    assert sig.parameters["ocr_max_pages"].default >= 40
    assert inspect.signature(documents.ocr_pdf).parameters["max_pages"].default >= 40


def test_ocr_warns_when_cap_truncates(tmp_path):
    pdf = tmp_path / "minutes.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    fake_pdf = MagicMock(); fake_pdf.pages = [object()] * 11
    fake_pdf.__enter__.return_value = fake_pdf
    logs = []
    with patch.object(documents.pdfplumber, "open", return_value=fake_pdf), \
         patch.object(documents, "convert_from_path", return_value=["img"] * 3) as conv, \
         patch.object(documents.pytesseract, "image_to_string", return_value="text"):
        out = documents.ocr_pdf(pdf, max_pages=3, log_fn=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert out == "text\n\ntext\n\ntext"
    conv.assert_called_once_with(pdf, first_page=1, last_page=3)
    assert any(lvl == "WARNING" and "OCR truncated" in m and "11 pages" in m for lvl, m in logs)


def test_ocr_no_warning_when_within_cap(tmp_path):
    pdf = tmp_path / "m.pdf"; pdf.write_bytes(b"%PDF")
    fake_pdf = MagicMock(); fake_pdf.pages = [object()] * 4
    fake_pdf.__enter__.return_value = fake_pdf
    logs = []
    with patch.object(documents.pdfplumber, "open", return_value=fake_pdf), \
         patch.object(documents, "convert_from_path", return_value=["img"] * 4), \
         patch.object(documents.pytesseract, "image_to_string", return_value="t"):
        documents.ocr_pdf(pdf, max_pages=40, log_fn=lambda m, lvl="INFO": logs.append((lvl, m)))
    assert not any("OCR truncated" in m for _, m in logs)
