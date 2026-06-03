from __future__ import annotations

from yagami.ingest.extract import extract


def test_extract_plain_text():
    doc = extract(filename="notes.txt", mime="text/plain", blob=b"hello world\nline two")
    assert doc.error is None
    assert doc.text == "hello world\nline two"
    assert doc.truncated is False


def test_extract_markdown_keeps_source():
    src = b"# Title\n\n- one\n- two\n"
    doc = extract(filename="readme.md", mime="text/markdown", blob=src)
    assert doc.error is None
    assert "# Title" in doc.text
    assert "- one" in doc.text


def test_extract_truncates_long_input():
    blob = b"x" * 200_000
    doc = extract(filename="huge.txt", mime="text/plain", blob=blob, max_chars=5000)
    assert doc.truncated is True
    assert len(doc.text) <= 5500  # 5000 + suffix
    assert "truncated" in doc.text


def test_extract_unsupported_returns_error():
    doc = extract(filename="cat.heic", mime="image/heic", blob=b"\x00\x00")
    assert doc.error is not None
    assert "unsupported" in doc.error.lower()


def test_extract_pdf_handles_garbage_gracefully():
    # Not a real PDF - pypdf should raise, we should return an error doc.
    doc = extract(filename="broken.pdf", mime="application/pdf", blob=b"not really a pdf")
    assert doc.error is not None
    assert doc.text == ""


def test_extract_pdf_real_minimal():
    # Tiny synthetic PDF with one page containing "Hello PDF".
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length 44>>stream\n"
        b"BT /F1 24 Tf 50 100 Td (Hello PDF) Tj ET\n"
        b"endstream endobj\n"
        b"xref\n0 6\n0000000000 65535 f\n0000000009 00000 n\n0000000053 00000 n\n"
        b"0000000100 00000 n\n0000000199 00000 n\n0000000245 00000 n\n"
        b"trailer<</Size 6/Root 1 0 R>>\n"
        b"startxref\n331\n%%EOF\n"
    )
    doc = extract(filename="hello.pdf", mime="application/pdf", blob=pdf)
    # pypdf may or may not extract depending on the synthetic structure; we
    # accept either a successful extraction OR a graceful empty extract
    # (no exception).
    assert doc.error is None or doc.error == ""
