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


# ── Word minutes (Granicus serves .docx / legacy .doc for some years) ────────
import zipfile


def _make_docx(path):
    xml = ('<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
           '<w:p><w:r><w:t>MINUTES</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>I.</w:t></w:r><w:tab/><w:r><w:t>CALL TO ORDER &amp; roll</w:t></w:r></w:p>'
           '</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def test_sniff_office_kind():
    assert documents.sniff_office_kind(b"PK\x03\x04rest") == "docx"
    assert documents.sniff_office_kind(b"\xd0\xcf\x11\xe0rest") == "doc"
    assert documents.sniff_office_kind(b"%PDF-1.4") is None
    assert documents.sniff_office_kind(b"") is None


def test_extract_docx_text(tmp_path):
    p = tmp_path / "m.docx"; _make_docx(p)
    assert documents.extract_office_text(p, "docx") == "MINUTES\nI.\tCALL TO ORDER & roll"


def test_extract_doc_text_without_antiword_warns(tmp_path):
    p = tmp_path / "m.doc"; p.write_bytes(b"\xd0\xcf\x11\xe0")
    logs = []
    with patch.object(documents.shutil, "which", return_value=None):
        assert documents.extract_office_text(p, "doc", log_fn=lambda m, lvl="INFO": logs.append((lvl, m))) is None
    assert any("antiword" in m for _, m in logs)


def test_extract_doc_text_via_antiword(tmp_path):
    p = tmp_path / "m.doc"; p.write_bytes(b"\xd0\xcf\x11\xe0")
    fake = MagicMock(stdout="BOARD OF ADJUSTMENT\nSeptember 25, 2015\n", returncode=0)
    with patch.object(documents.shutil, "which", return_value="/usr/bin/antiword"), \
         patch.object(documents.subprocess, "run", return_value=fake) as run:
        assert documents.extract_office_text(p, "doc") == "BOARD OF ADJUSTMENT\nSeptember 25, 2015"
    assert run.call_args[0][0][:3] == ["antiword", "-w", "0"]
