"""Regression tests for pytiblegenc-table conversion of legacy simple fonts.

The fixture is page 1 of ``JKW-KABAB-Volume-01.pdf``, which embeds several
legacy non-Unicode Tibetan fonts (``Dedris-*``, i.e. Ededris) as symbolic
simple TrueType subsets plus a ``TimesNewRomanPSMT`` decoy.

These fonts have no usable GSUB/cmap, so the GSUB-derived lookups can't help.
Instead each code's *existing* (wrong, Latin-ish) ToUnicode character is
re-mapped through pytiblegenc's curated per-font table. ``Dedris-a`` resolves to
the ``Ededris-a`` table via pytiblegenc's ``Dedris -> Ededris`` alias.
"""
from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from pdf_cmap_fix.gid.extractor import collect_font_merges

DATA = Path(__file__).resolve().parent / "data"
EXCERPT = DATA / "jkw-kabab-excerpt.pdf"
# Same page with the Dedris-a font's BaseFont, FontName and embedded name table
# scrubbed to an obfuscated name, so only outline identification can recover it.
OBFUSCATED = DATA / "jkw-kabab-obfuscated.pdf"
# A Type0/Identity-H legacy font (TibetanChogyal) whose ToUnicode uses a 2-byte
# codespace -- must still be re-mapped through the table, not mis-matched by the
# GID tier to a sparse same-named lookup.
CHOGYAL_TYPE0 = DATA / "swift-path-chogyal-type0.pdf"
# Page 1 of a real BDRC corpus document (Thrangu Rinpoche's collected works).
# Its legacy Ededris fonts are embedded as Type1/CFF subsets with a real
# /Encoding but NO ToUnicode -- the from-scratch encoding route must rebuild it.
THRANGU_CFF = DATA / "thrangu-sungtsom-excerpt.pdf"

pytestmark = pytest.mark.skipif(
    not EXCERPT.is_file(), reason="JKW-KABAB excerpt fixture not present"
)


def _records_by_font_name(path: Path = EXCERPT) -> dict[str, dict]:
    doc = fitz.open(str(path))
    try:
        records, _ = collect_font_merges(doc)
    finally:
        doc.close()
    return {r["pdf_font_name"]: r for r in records}


def test_dedris_a_maps_codes_to_tibetan_via_existing_tounicode() -> None:
    rec = _records_by_font_name()["KSWSGG+Dedris-a"]

    # Resolved through the Dedris -> Ededris alias.
    assert rec["db_name_matched"] == "Ededris-a"
    assert rec["changed"] > 0

    merged = rec["merged"]
    # The mapping keys on the *existing* ToUnicode char, not the raw byte:
    # code 34's existing '$' -> table[36] = GA (raw byte 34 has no table row).
    assert merged[33] == "\u0f40"          # KA
    assert merged[34] == "\u0f42"          # GA
    assert merged[40] == "\u0f62\u0fab"    # RA + subjoined DZA (stack)
    assert merged[48] == "\u0f56\u0fb1"    # BA + subjoined YA (stack)


def test_simple_dedris_vowa_is_converted() -> None:
    rec = _records_by_font_name()["PLHYWC+Dedris-vowa"]
    assert rec["db_name_matched"] == "Ededris-vowa"
    assert rec["changed"] > 0
    # head mark (yig mgo) -- the leading ornament of the page
    assert rec["merged"][34] == "\u0f04\u0f05"


def test_times_new_roman_is_not_converted() -> None:
    rec = _records_by_font_name()["JJGQVW+TimesNewRomanPSMT"]
    assert rec["db_name_matched"] is None
    assert rec["db_key_matched"] is None
    assert rec["changed"] == 0


@pytest.mark.skipif(
    not OBFUSCATED.is_file(), reason="obfuscated-name fixture not present"
)
def test_obfuscated_font_identified_via_glyph_outlines() -> None:
    # The font name is fully obfuscated (no table resolves by name), so this
    # can only match by hashing the embedded glyph outlines against glyph_db.csv.
    rec = _records_by_font_name(OBFUSCATED)["ZQXJVK+Obf00001"]

    assert rec["db_name_matched"] == "Ededris-a"
    assert rec["changed"] > 0
    merged = rec["merged"]
    assert merged[33] == "\u0f40"          # KA
    assert merged[34] == "\u0f42"          # GA
    assert merged[40] == "\u0f62\u0fab"    # RA + subjoined DZA


@pytest.mark.skipif(
    not CHOGYAL_TYPE0.is_file(), reason="Type0 Chogyal fixture not present"
)
def test_type0_legacy_font_converted_via_existing_tounicode() -> None:
    # Regression: a Type0/Identity-H legacy font with a 2-byte/4-hex ToUnicode
    # codespace used to fall through to the GID tier (sparse garbage match).
    # It must instead be re-mapped through the pytiblegenc table.
    rec = _records_by_font_name(CHOGYAL_TYPE0)["OZVHIQ+TibetanChogyal"]

    assert rec["pdf_font_type"] == "Type0"
    assert rec["db_name_matched"] == "TibetanChogyal"
    assert rec["changed"] > 0
    merged = rec["merged"]
    # Codes are 2-byte (== GID under Identity-H); the existing ToUnicode maps
    # them to Latin, which the table turns into Tibetan.
    assert merged[4] == "\u0f42"  # GA
    assert merged[5] == "\u0f44"  # NGA


@pytest.mark.skipif(
    not THRANGU_CFF.is_file(), reason="Thrangu CFF excerpt fixture not present"
)
def test_cff_type1_font_without_tounicode_rebuilt_via_encoding() -> None:
    # Real-world case: Type1/CFF Ededris subsets with a /Encoding but no
    # ToUnicode at all. The from-scratch encoding route maps each code's glyph
    # name through the Adobe Glyph List, then the pytiblegenc table.
    rec = _records_by_font_name(THRANGU_CFF)["LEMGRM+Ededris-a"]

    assert rec["pdf_font_type"] == "Type1"
    assert rec["db_name_matched"] == "Ededris-a"
    assert rec["to_unicode_xref"] is None  # synthesised from scratch
    assert rec["changed"] > 0

    merged = rec["merged"]
    # /Encoding maps code 33 -> glyph 'I' -> table -> GA + subjoined YA, etc.
    assert merged[33] == "གྱ"  # GA + subjoined YA
    assert merged[40] == "ཕྲ"  # PHA + subjoined RA
    assert merged[48] == "རྗ"  # RA + subjoined JA


@pytest.mark.skipif(
    not THRANGU_CFF.is_file(), reason="Thrangu CFF excerpt fixture not present"
)
def test_cff_type1_decoys_stay_unconverted() -> None:
    by_name = _records_by_font_name(THRANGU_CFF)
    # Latin Type1/CFF fonts on the page are not legacy Tibetan faces: outline
    # identification and name lookup both fail, so no record is emitted.
    assert "SCOWDF+TimesNewRomanPSMT" not in by_name
    assert "DKFVEI+NewBaskervilleStd-Roman" not in by_name


@pytest.mark.skipif(
    not THRANGU_CFF.is_file(), reason="Thrangu CFF excerpt fixture not present"
)
def test_cff_type1_page_extracts_tibetan_after_apply() -> None:
    from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

    doc = fitz.open(str(THRANGU_CFF))
    try:
        records, _ = collect_font_merges(doc)
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        text = reopened.load_page(0).get_text()
    finally:
        reopened.close()

    # The document title -- "gsung rtsom thor bu" (collected miscellaneous
    # writings) -- extracts as correct Tibetan; before the fix the page yielded
    # zero Tibetan code points.
    assert "གསུང་རྩོམ་ཐོར་བུ" in text
    assert sum(1 for ch in text if "ༀ" <= ch <= "࿿") > 500


def _records_from_stripped_tounicode(path: Path = EXCERPT) -> tuple[dict[str, dict], "fitz.Document", list]:
    """Open ``path``, delete every font's /ToUnicode, then collect merges.

    Returns ``(records_by_font_name, doc, records)`` -- the doc and record list
    are returned so a caller can run ``apply_font_merges_to_doc`` and inspect the
    result. The caller owns closing the doc.
    """
    import re

    doc = fitz.open(str(path))
    for f in doc[0].get_fonts(full=True):
        if re.search(r"/ToUnicode (\d+) 0 R", doc.xref_object(f[0])):
            doc.xref_set_key(f[0], "ToUnicode", "null")
    records, _ = collect_font_merges(doc)
    return {r["pdf_font_name"]: r for r in records}, doc, records


def test_dedris_a_tounicode_built_from_scratch_when_absent() -> None:
    # No ToUnicode and no resolvable glyph names: the map is rebuilt purely from
    # the embedded glyph outlines, and must match the existing-ToUnicode path.
    by_name, doc, _ = _records_from_stripped_tounicode()
    try:
        rec = by_name["KSWSGG+Dedris-a"]
        assert rec["db_name_matched"] == "Ededris-a"
        # to_unicode_xref is None => the font had no ToUnicode to re-map; the
        # stream is created from scratch on apply.
        assert rec["to_unicode_xref"] is None
        assert rec["changed"] > 0

        merged = rec["merged"]
        assert merged[33] == "ཀ"          # KA
        assert merged[34] == "ག"          # GA
        assert merged[40] == "རྫ"    # RA + subjoined DZA (stack)
        assert merged[48] == "བྱ"    # BA + subjoined YA (stack)
    finally:
        doc.close()


def test_from_scratch_tounicode_stream_is_attached_and_extracts_tibetan() -> None:
    import re

    from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

    by_name, doc, records = _records_from_stripped_tounicode()
    try:
        apply_font_merges_to_doc(doc, records)

        # A fresh /ToUnicode object was created and wired into the font dict.
        xref = next(
            f[0] for f in doc[0].get_fonts(full=True) if f[3].endswith("+Dedris-a")
        )
        assert re.search(r"/ToUnicode (\d+) 0 R", doc.xref_object(xref))

        data = doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        text = reopened.load_page(0).get_text()
    finally:
        reopened.close()
    tibetan = sum(1 for ch in text if "ༀ" <= ch <= "࿿")
    # The page is almost entirely Tibetan; before the fix it extracted ~0.
    assert tibetan > 500


def test_times_decoy_stays_unconverted_with_no_tounicode() -> None:
    by_name, doc, _ = _records_from_stripped_tounicode()
    try:
        # The Latin decoy is not a legacy font: outline identification bails, so
        # no from-scratch map is built and no record is emitted for it.
        assert "JJGQVW+TimesNewRomanPSMT" not in by_name
    finally:
        doc.close()


def test_glyph_lookup_rejects_ambiguous_empty_glyph_hash() -> None:
    # The old glyph DB gives every empty glyph named "visiblespace" the same
    # name-derived hash. Its sibling tables disagree (space, U, long-A, and
    # long-A+U), so selecting the first candidate would invent a character.
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    assert " " not in ptg._base().get("Ededris-d1", {})
    assert ptg.convert_char("Ededris-d1", " ") is None


def test_dedris_vowa_nbsp_is_explicitly_a_space() -> None:
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    # This positive-width empty glyph is U+00A0 in the PDF's original
    # ToUnicode map. Keep it as spacing without consulting sibling hashes.
    assert ptg.convert_char("Ededris-vowa", "\u00a0") == " "


def test_direct_table_hit_still_resolves() -> None:
    from pdf_cmap_fix import pytiblegenc_tables as ptg

    # A direct table hit still resolves without the fallback.
    assert ptg.convert_char("Ededris-a", "!") == "\u0f40"


def test_identify_candidates_resolves_subset_to_source_font() -> None:
    import io

    from fontTools.ttLib import TTFont

    from pdf_cmap_fix import pytiblegenc_tables as ptg
    from pdf_cmap_fix.glyph_outline_id import identify_candidates

    doc = fitz.open(str(EXCERPT))
    try:
        xref = next(
            f[0]
            for f in doc[0].get_fonts(full=True)
            if f[3].endswith("+Dedris-a")
        )
        ttfont = TTFont(io.BytesIO(bytes(doc.extract_font(xref)[3])), lazy=False)
    finally:
        doc.close()

    candidates = identify_candidates(ttfont)
    # Tightest (smallest superset) match first.
    assert candidates[0] == "Dedris-a"
    name, table = ptg.table_for_candidates(candidates)
    assert name == "Ededris-a"
    assert table is not None
