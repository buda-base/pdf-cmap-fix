"""HuaGuang Windows encoding: UTFC table + Founder / Distiller PDFs.

Founder PDFGenerator splits the 2-byte slot across Type1 plane fonts
(``JBHGZWHZ.B6`` + glyph ``LA1``). Acrobat Distiller writes the GB pair
into ToUnicode as a CJK ideograph (U+554A = B0 A1 = U+0F40).
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from pdf_cmap_fix import huaguang as hg
from pdf_cmap_fix.gid.extractor import collect_font_merges
from pdf_cmap_fix.tounicode_core import (
    _map_has_tibetan,
    apply_font_merges_to_doc,
)

DATA = Path(__file__).resolve().parent / "data"
RONGTHA = DATA / "huaguang-rongtha-excerpt.pdf"
TSONGKHAPA = DATA / "huaguang-tsongkhapa-excerpt.pdf"
_needs_rongtha = pytest.mark.skipif(
    not RONGTHA.is_file(), reason="HuaGuang Rongtha excerpt fixture not present"
)
_needs_tsong = pytest.mark.skipif(
    not TSONGKHAPA.is_file(), reason="HuaGuang Tsongkhapa excerpt fixture not present"
)

KA = "\u0f40"
KHA = "\u0f41"
GA = "\u0f42"
BA = "\u0f56"
TSEK = "\u0f0b"
CJK_A = "\u554a"  # GBK B0A1
CJK_AI = "\u57c3"  # GBK B0A3
YIG_MGO = "\u0f04\u0f05"


def test_table_windows_slots() -> None:
    assert hg.lookup(0xB0, 0xA1) == KA
    assert hg.lookup(0xB0, 0xA2) == KHA
    assert hg.lookup(0xB0, 0xA3) == GA
    # DOS trail 0x21 is the same slot as Windows 0xA1.
    assert hg.lookup(0xB0, 0x21) == KA


def test_cjk_ideograph_is_gb_pair() -> None:
    assert hg.from_cjk_char(CJK_A) == KA
    assert hg.from_cjk_char(CJK_AI) == GA
    assert hg.from_cjk_char("A") is None
    assert hg.from_cjk_char(KA) is None


def test_font_name_and_plane() -> None:
    assert hg.is_huaguang_font("IECGCE+JBHGZWHZ.B6")
    assert hg.is_huaguang_font("INDKPI+JBHGZWHZ")
    assert hg.is_huaguang_font("INDLBJ+ZWBZ")
    assert hg.is_huaguang_font("QHZGIC+FHYW1-B0")
    assert hg.is_huaguang_font("INDLCK+FHZW1")
    assert not hg.is_huaguang_font("RPGOWK+Optima-Regular")
    assert not hg.is_huaguang_font("BTQSWF+TibetanClassic")
    assert hg.plane_from_font_name("IECGCE+JBHGZWHZ.B6") == 0xB6
    assert hg.plane_from_font_name("JBHGZWHZ_BA") == 0xBA
    assert hg.plane_from_font_name("FHYW1-B0") == 0xB0
    assert hg.plane_from_font_name("JBHGZWHZ.C0") == 0xC0
    assert hg.plane_from_font_name("JBHGZWHZ") is None
    assert hg.trail_from_glyph_name("LA1") == 0xA1
    assert hg.trail_from_glyph_name("LF4") == 0xF4
    assert hg.trail_from_glyph_name("Lslash") is None
    assert hg.trail_from_glyph_name("space") is None


def test_plane_encoding_builds_tounicode() -> None:
    enc = {2: "LA1", 3: "LAF", 4: "space"}
    got = hg.tounicode_from_plane_encoding("JBHGZWHZ.B0", enc)
    assert got == {2: KA, 3: BA}


def test_cjk_map_skips_non_gb() -> None:
    existing = {1190: CJK_A, 10: "A", 11: KA}
    got = hg.tounicode_from_cjk_map(existing)
    assert got == {1190: KA}


def test_symbol_fonts_are_not_hg2uni() -> None:
    assert hg.is_huaguang_symbol_font("INDLBK+FHYW1")
    assert hg.is_huaguang_symbol_font("QHZGIC+FHYW1-B0")
    assert hg.is_huaguang_symbol_font("INDLCK+FHZW1")
    assert not hg.is_huaguang_symbol_font("INDKPI+JBHGZWHZ")
    # Page-number slots: B0D1..B0DA = 0-9, B0C9/B0CA = (), B0CE = -.
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xD1) == "0"
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xD2) == "1"
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xDA) == "9"
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xC9) == "("
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xCA) == ")"
    assert hg.lookup_symbol("FHYW1", 0xB0, 0xCE) == "-"
    assert hg.lookup_symbol("FHZW1", 0xB0, 0xA5) == "\u00b7"
    # Must not fall back to the Tibetan body table (B0A5 = U+0F45).
    assert hg.lookup_symbol("FHZW1", 0xB0, 0xA5) != hg.lookup(0xB0, 0xA5)
    # 哎 (GBK B0A5) through the symbol font is a leader, not ཅ.
    assert hg.from_cjk_char("\u54ce", "FHZW1") == "\u00b7"
    assert hg.from_cjk_char("\u54ce", "JBHGZWHZ") == "\u0f45"


def test_map_has_tibetan_rejects_cjk_only_gid_dump() -> None:
    assert _map_has_tibetan({1: KA, 2: TSEK}) is True
    assert _map_has_tibetan({1: CJK_A, 2: "\u51b2", 3: "\ue27f"}) is False
    assert _map_has_tibetan({}) is False


def _patched_page_text(path: Path) -> tuple[str, list[dict]]:
    doc = fitz.open(str(path))
    try:
        assert len(doc) == 1
        records, _ = collect_font_merges(doc)
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()
    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        text = reopened.load_page(0).get_text()
    finally:
        reopened.close()
    return text, records


@_needs_tsong
def test_tsongkhapa_page_recovers_table_of_contents() -> None:
    """One-page Distiller excerpt: CJK ToUnicode -> Tibetan via GBK + Hg2Uni."""
    text, records = _patched_page_text(TSONGKHAPA)
    assert records
    assert all(r["db_name_matched"] == "huaguang" for r in records)
    # Distiller laid དཀར་ / ཆག on two lines.
    assert "\u0f51\u0f40\u0f62\u0f0b" in text
    assert "\u0f46\u0f42" in text
    assert "\u0f59\u0f7c\u0f44\u0f0b\u0f41\u0f0b\u0f54" in text  # tsong kha pa
    assert "\u0f56\u0fb3\u0f7c\u0f0b\u0f56\u0f5f\u0f44\u0f0b\u0f42\u0fb2\u0f42\u0f66" in text
    # FHYW1 page numbers and FHZW1 leaders, not Hg2Uni letter soup.
    assert "(1)" in text
    assert "(11)" in text
    assert "(44)" in text
    assert "\u00b7\u00b7" in text
    assert "\u0f51\u0f7a\u0f61\u0f7a\u0f53\u0f7a" not in text
    assert CJK_A not in text
    assert "\u77ee" not in text  # 矮


@_needs_rongtha
def test_rongtha_page_recovers_volume_title() -> None:
    """One-page Founder excerpt: plane fonts JBHGZWHZ.B* + Lxx names."""
    text, records = _patched_page_text(RONGTHA)
    assert records
    assert all(r["db_name_matched"] == "huaguang" for r in records)
    assert all("Ededris" not in (r.get("db_key_matched") or "") for r in records)
    assert YIG_MGO in text
    assert "\u0f62\u0f7c\u0f44\u0f0b\u0f50\u0f0b" in text  # rong tha
    assert "\u0f56\u0fb3\u0f7c\u0f0b\u0f56\u0f5f\u0f44\u0f0b\u0f46\u0f7c\u0f66\u0f0b\u0f60\u0f56\u0fb1\u0f7c\u0f62" in text
    assert "\u0f42\u0f66\u0f74\u0f44\u0f0b\u0f60\u0f56\u0f74\u0f58" in text  # gsung 'bum
