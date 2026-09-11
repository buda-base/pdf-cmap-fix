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


def test_map_has_tibetan_rejects_cjk_only_gid_dump() -> None:
    assert _map_has_tibetan({1: KA, 2: TSEK}) is True
    assert _map_has_tibetan({1: CJK_A, 2: "\u51b2", 3: "\ue27f"}) is False
    assert _map_has_tibetan({}) is False


@_needs_tsong
def test_tsongkhapa_distiller_cjk_becomes_tibetan() -> None:
    doc = fitz.open(str(TSONGKHAPA))
    try:
        records, stats = collect_font_merges(doc)
        assert stats["patched"] > 0
        by_name = {r["pdf_font_name"]: r for r in records}
        jbhg = next(r for n, r in by_name.items() if "JBHGZWHZ" in n)
        assert jbhg["db_name_matched"] == "huaguang"
        assert jbhg["changed"] > 0
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        text = reopened.load_page(0).get_text()
    finally:
        reopened.close()

    # Distiller laid དཀར་ / ཆག on two lines.
    assert "\u0f51\u0f40\u0f62\u0f0b" in text
    assert "\u0f46\u0f42" in text
    assert "\u0f59\u0f7c\u0f44\u0f0b\u0f41\u0f0b\u0f54" in text  # tsong kha pa
    assert "\u0f56\u0fb3\u0f7c\u0f0b\u0f56\u0f5f\u0f44" in text  # blo bzang
    assert CJK_A not in text
    assert "\u77ee" not in text  # 矮


@_needs_rongtha
def test_rongtha_founder_planes_are_not_ededris() -> None:
    doc = fitz.open(str(RONGTHA))
    try:
        records, stats = collect_font_merges(doc)
        assert stats["patched"] > 0
        for r in records:
            assert r["db_name_matched"] == "huaguang"
            assert "Ededris" not in (r.get("db_key_matched") or "")
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        text = reopened.load_page(1).get_text()
    finally:
        reopened.close()

    assert YIG_MGO in text
    assert ("\u0f56\u0f40\u0f60" in text) or ("\u0f62\u0f9b\u0f7a" in text)
    assert "\ue000" not in text
