"""Reviewed TCRC Youtso / Bod-Yig mappings from the ancient-history book."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_cmap_fix.pytiblegenc_tables import convert_char, table_for

DATA = Path(__file__).resolve().parent / "data"
EXCERPT = DATA / "tcrc-youtso-excerpt.pdf"


def test_tcrc_youtso_reviewed_overrides() -> None:
    name, table = table_for("TCRCYoutso")
    assert name == "TCRC Youtso"
    assert table is not None
    # User-reviewed slots that differ from vendored utfc.
    assert table[" "] == " "
    assert table["&"] == "\u0f38"
    assert table["("] == "("
    assert table[")"] == ")"
    assert table[chr(182)] == "\u0f5d"
    assert table[chr(8211)] == "_"
    assert table[chr(8216)] == "'"
    assert table[chr(8217)] == "'"
    assert table[chr(8224)] == "\u0fb7\u0fb1"
    assert convert_char("TCRC Youtso", "&") == "\u0f38"
    assert convert_char("TCRC Youtso", chr(182)) == "\u0f5d"
    assert convert_char("TCRC Youtso", chr(8224)) == "\u0fb7\u0fb1"


def test_tcrc_bod_yig_keeps_reviewed_ka_subscript() -> None:
    name, table = table_for("TCRC-Bod-Yig")
    assert name == "TCRC Bod-Yig"
    assert table is not None
    assert table["+"] == "\u0f90"
    assert table["F"] == "\u0f41\u0fb2"
    assert table["G"] == "\u0f42"


def _patched_text(path: Path) -> str:
    import fitz

    from pdf_cmap_fix.gid.extractor import collect_font_merges
    from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

    doc = fitz.open(str(path))
    try:
        records, _ = collect_font_merges(doc)
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()
    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        return reopened.load_page(0).get_text()
    finally:
        reopened.close()


@pytest.mark.skipif(not EXCERPT.is_file(), reason="TCRC excerpt not present")
def test_tcrc_excerpt_recovers_gri_gum_heading() -> None:
    import fitz

    from pdf_cmap_fix.gid.extractor import collect_font_merges

    doc = fitz.open(str(EXCERPT))
    try:
        records, _ = collect_font_merges(doc)
    finally:
        doc.close()
    by = {r["db_name_matched"]: r for r in records if r.get("db_name_matched")}
    assert "TCRC Youtso" in by
    assert "TCRC Bod-Yig" in by
    assert by["TCRC Youtso"]["changed"] > 0
    text = _patched_text(EXCERPT)
    assert "\u0f51\u0f58\u0f0b\u0f46\u0f7c\u0f66\u0f0b\u0f58\u0f5b\u0f51\u0f0b\u0f54\u0f60\u0f72" in text
    assert "\u0f56\u0f7c\u0f51\u0f0b\u0f62\u0f92\u0fb1\u0f63" in text
    assert "\u0f42\u0fb2\u0f72\u0f0b\u0f42\u0f74\u0f58\u0f0b\u0f56\u0f59\u0f53\u0f0b\u0f54\u0f7c" in text
