"""Legacy font-name normalisation and Sheja / TB-Youtso conversion."""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_cmap_fix.glyph_outline_id import identify_candidates
from pdf_cmap_fix.pytiblegenc_tables import convert_text, normalize_font_name, table_for

REPO = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
JAN = DATA / "sheja-jan-excerpt.pdf"
MAR = DATA / "sheja-mar-excerpt.pdf"


def test_normalize_strips_hyphen_normal_weight() -> None:
    assert normalize_font_name("TB-Youtso-Normal") == "TB-Youtso"
    assert normalize_font_name("TB2-Youtso-Normal") == "TB2-Youtso"
    assert normalize_font_name("TB-Youtso-Bold") == "TB-Youtso-Bold"


def test_normalize_still_strips_space_normal() -> None:
    assert normalize_font_name("TCRC Youtso Normal") == "TCRC Youtso"


def test_table_for_tb_youtso_normal_resolves() -> None:
    name, table = table_for("TB-Youtso-Normal")
    assert name == "TB-Youtso"
    assert table is not None
    assert table["z"] == "\u0f56"
    assert table["\u00d9"] == "\u0f7c"
    assert table["\u00e6"] == "\u0f63"
    assert table["<"] == "\u0f40\u0fb1"
    assert table["="] == "\u0f40\u0fb2"
    name2, table2 = table_for("TB2-Youtso-Normal")
    assert name2 == "TB2-Youtso"
    assert table2 is not None
    name_b, table_b = table_for("TB-Youtso-Bold")
    assert name_b == "TB-Youtso-Bold"
    assert table_b is not None
    assert table_b["<"] == "\u0f40\u0fb1"


def test_convert_sheja_masthead_through_tb_youtso() -> None:
    assert convert_text("TB-Youtso", "z\u00d9h-M\u00e6-\u00e6\u00d9-") == (
        "\u0f56\u0f7c\u0f51\u0f0b\u0f62\u0f92\u0fb1\u0f63\u0f0b\u0f63\u0f7c\u0f0b"
    )


def test_table_for_tcrc_youtso_normal_still_resolves() -> None:
    name, table = table_for("TCRC Youtso Normal")
    assert name == "TCRC Youtso"
    assert table is not None


def test_reviewed_ttyoutso_bold_tables_and_tcrc_aliases() -> None:
    assert normalize_font_name("TB-TTYoutso-Bold") == "TB-TTYoutso-Bold"
    assert normalize_font_name("TB2-TTYoutso-Bold") == "TB2-TTYoutso-Bold"
    assert normalize_font_name("TCRCYoutso") == "TCRC Youtso"
    assert normalize_font_name("TCRC-Bod-Yig") == "TCRC Bod-Yig"
    name, table = table_for("TB-TTYoutso-Bold")
    assert name == "TB-TTYoutso-Bold"
    assert table is not None
    assert table["<"] == "\u0f40\u0fb1"
    assert table[chr(164)] == "\u0f58"
    assert table[chr(184)] == "\u0f5f"
    name2, table2 = table_for("TB2-TTYoutso-Bold")
    assert name2 == "TB2-TTYoutso-Bold"
    assert table2 is not None
    assert table2["D"] == "\u0f50"
    name_c, table_c = table_for("TCRCYoutso")
    assert name_c == "TCRC Youtso"
    assert table_c is not None


def test_chosgyal_and_mangala_normal_use_distinct_reviewed_tables() -> None:
    assert normalize_font_name("TibetanChosGyalSkt2") == "TibetanChosGyalSkt2"
    name, table = table_for("TibetanChosGyalSkt1")
    assert name == "TibetanChosGyalSkt1"
    assert table is not None
    assert table[chr(1)] == "\u0f4a\u0f9a"
    assert table[chr(20)] == "\u0f4a\u0fa4"
    # Exact table lookup wins before -Normal suffix normalisation.
    name_m, table_m = table_for("PJGNCA+TibetanMangala-Normal")
    assert name_m == "TibetanMangala-Normal"
    assert table_m is not None
    assert table_m[chr(1)] == "\u0f58"
    assert table_m[chr(3)] == "\u0f0b"
    # The generic encoding remains untouched and materially different.
    generic_name, generic = table_for("TibetanMangala")
    assert generic_name == "TibetanMangala"
    assert generic is not None
    assert generic[chr(17)] != table_m[chr(17)]


def test_dzongkha_calligraphic_alias() -> None:
    assert normalize_font_name("DzongkhaCalligraphic") == "TibetanCalligraphic"
    assert normalize_font_name("DzongkhaCalligraphicSkt2") == "TibetanCalligraphicSkt2"
    name_d, table_d = table_for("DzongkhaCalligraphic")
    assert name_d == "TibetanCalligraphic"
    assert table_d is not None
    assert table_d["!"] == "\u0f40"
    # Precomposed stacks, not attu's isolated subjoined letters.
    assert table_d["D"] == "\u0f62\u0f9f"
    assert table_d["i"] == "\u0f42\u0fb2"
    assert table_d["e"] == "\u0f56\u0fb1"
    assert table_d[chr(166)] == "\u0f74"
    assert table_d[chr(8363)] == "\u0f66\u0fa4\u0fb1"


def test_identify_candidates_corrupt_cmap_returns_empty() -> None:
    class _BrokenCmap:
        def __contains__(self, key: str) -> bool:
            return key == "glyf"

        def __getitem__(self, key: str):
            raise AssertionError(
                "corrupt cmap table format 4 (data length: 42, header length: 48)"
            )

    assert identify_candidates(_BrokenCmap()) == []


def _records(path: Path) -> list[dict]:
    import fitz

    from pdf_cmap_fix.gid.extractor import collect_font_merges

    doc = fitz.open(str(path))
    try:
        records, _ = collect_font_merges(doc)
    finally:
        doc.close()
    return records


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


def _by_matched(records: list[dict]) -> dict[str, dict]:
    return {r["db_name_matched"]: r for r in records if r.get("db_name_matched")}


@pytest.mark.skipif(not JAN.is_file(), reason="Sheja January excerpt not present")
def test_sheja_jan_excerpt_covers_regular_and_tb2() -> None:
    """Page 10 of Sheja Jan 2001: TB-Youtso + TB2-Youtso body text."""
    records = _records(JAN)
    by = _by_matched(records)
    assert "TB-Youtso" in by
    assert "TB2-Youtso" in by
    assert by["TB-Youtso"]["changed"] > 0
    assert by["TB2-Youtso"]["changed"] > 0
    assert by["TB-Youtso"]["merged"][60] == "\u0f40\u0fb1"
    assert by["TB2-Youtso"]["merged"][102] == "\u0fa8"
    assert by["TB2-Youtso"]["merged"][123] == "\u0f51"
    text = _patched_text(JAN)
    assert "\u0f56\u0f7c\u0f51\u0f0b\u0f51\u0f44\u0f0b\u0f56\u0f7c\u0f51\u0f0b\u0f58\u0f72" in text
    assert "\u0f63\u0f7c\u0f0b\u0f62\u0f92\u0fb1\u0f74\u0f66\u0f0b\u0f40\u0fb1\u0f72" in text
    assert "\u0f63\u0f7c\u0f0b\u0f62\u0f92\u0fb1\u0f74\u0f66\u0f0b\u0fb1\u0f72" not in text


@pytest.mark.skipif(not MAR.is_file(), reason="Sheja March excerpt not present")
def test_sheja_mar_excerpt_covers_regular_bold_and_tb2() -> None:
    """Page 22 of Sheja Mar 2001: Regular + Bold + TB2 (TB1 Latin is skipped)."""
    records = _records(MAR)
    by = _by_matched(records)
    assert set(by) >= {"TB-Youtso", "TB-Youtso-Bold", "TB2-Youtso"}
    assert by["TB-Youtso"]["changed"] > 0
    assert by["TB-Youtso-Bold"]["changed"] > 0
    assert by["TB2-Youtso"]["changed"] > 0
    assert by["TB-Youtso"]["merged"][60] == "\u0f40\u0fb1"
    assert by["TB-Youtso-Bold"]["merged"][60] == "\u0f40\u0fb1"
    assert by["TB2-Youtso"]["merged"][117] == "\u0f9c"
    tb1 = [r for r in records if "TB1-Youtso" in (r.get("pdf_font_name") or "")]
    assert tb1
    assert all(r.get("db_name_matched") is None and r.get("changed", 0) == 0 for r in tb1)
    text = _patched_text(MAR)
    assert "\u0f56\u0f7c\u0f51\u0f0b\u0f51\u0f44\u0f0b\u0f56\u0f7c\u0f51\u0f0b\u0f58\u0f72" in text
    assert "\u0f56\u0f7c\u0f51\u0f0b\u0f62\u0f92\u0fb1\u0f63\u0f0b\u0f63\u0f7c\u0f0b" in text
    assert "\u0f40\u0fb1\u0f72" in text
