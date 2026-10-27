"""Regression coverage for the Dedris-vowa empty spacing glyph.

The fixture is page 2 of ``白度母光輪.pdf``. Its positive-width empty GID 3
has an existing U+00A0 ToUnicode mapping. The old sibling fallback confused the
shared ``visiblespace`` pseudo-hash with several Tibetan vowels.
"""
from pathlib import Path

import fitz
import pytest

from pdf_cmap_fix.gid.extractor import collect_font_merges
from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

FIXTURE = Path(__file__).resolve().parent / "data" / "dedris-vowa-spacing-page2.pdf"

pytestmark = pytest.mark.skipif(
    not FIXTURE.is_file(), reason="Dedris-vowa page fixture not present"
)


def test_positive_width_empty_glyph_stays_spacing() -> None:
    doc = fitz.open(str(FIXTURE))
    try:
        before = doc[0].get_pixmap().samples
        records, _ = collect_font_merges(doc)
        target = next(
            r
            for r in records
            if r["pdf_font_name"].endswith("+Dedris-vowa")
            and r["existing"].get(3) == "\u00a0"
        )
        assert target["db_name_matched"] == "Ededris-vowa"
        assert target["merged"][3] == " "

        apply_font_merges_to_doc(doc, records)
        repaired = doc.tobytes()
    finally:
        doc.close()

    reopened = fitz.open(stream=repaired, filetype="pdf")
    try:
        assert reopened[0].get_pixmap().samples == before
        text = reopened[0].get_text()
    finally:
        reopened.close()

    assert "\u00a0" not in text
    assert "ུུ" not in text
    assert "ཚེ་ དཔག་ མེད" in text
