"""Regression tests for bugs4 PDFs that now extract cleanly.

Fixtures are page 1 of the MonlamUniOuChan2 Kagyu namthar (gname fallback
from the default gid collector), the Himalaya-G Palmo namthar
(embedded-GID map against the PUA-free lookup), and page 1 of the
DzongkhaCalligraphic skull-cup text (full-stack Calligraphic table).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_cmap_fix.gid.extractor import FONT_LOOKUP_DIR, collect_font_merges
from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

DATA = Path(__file__).resolve().parent / "data"
MONLAM = DATA / "monlam-ouchan2-excerpt.pdf"
HIMALAYA = DATA / "himalaya-g-excerpt.pdf"
DZONGKHA = DATA / "dzongkha-calligraphic-excerpt.pdf"
MSTT = DATA / "mstt-dagpo-excerpt.pdf"
CHENREZIG = DATA / "chenrezig-chosgyal-excerpt.pdf"
PUA_FREE = FONT_LOOKUP_DIR.parent / "font_lookup_gid_pua_free"


def _patched_text(path: Path, *, font_lookup_dir: Path | None = None) -> str:
    import fitz

    kwargs = {}
    if font_lookup_dir is not None:
        kwargs["font_lookup_dir"] = font_lookup_dir
    doc = fitz.open(str(path))
    try:
        records, _ = collect_font_merges(doc, **kwargs)
        apply_font_merges_to_doc(doc, records)
        data = doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()
    reopened = fitz.open(stream=data, filetype="pdf")
    try:
        return reopened.load_page(0).get_text()
    finally:
        reopened.close()


def _records(path: Path, *, font_lookup_dir: Path | None = None) -> list[dict]:
    import fitz

    kwargs = {}
    if font_lookup_dir is not None:
        kwargs["font_lookup_dir"] = font_lookup_dir
    doc = fitz.open(str(path))
    try:
        records, _ = collect_font_merges(doc, **kwargs)
    finally:
        doc.close()
    return records


@pytest.mark.skipif(not MONLAM.is_file(), reason="Monlam excerpt not present")
def test_monlam_ouchan2_excerpt_recovers_title() -> None:
    recs = _records(MONLAM)
    hit = next(r for r in recs if "MonlamUniOuChan2" in (r.get("pdf_font_name") or ""))
    assert hit["db_name_matched"] == "monlamuniouchan2"
    assert hit["changed"] > 0
    text = _patched_text(MONLAM)
    # Title of the Kagyu hundred-siddha namthar (ASCII stacks must be gone).
    assert "\u0f56\u0f40\u0f60\u0f0b\u0f56\u0f62\u0f92\u0fb1\u0f74\u0f51" in text
    assert "\u0f42\u0fb2\u0f74\u0f56\u0f0b\u0f50\u0f7c\u0f56" in text
    assert "\u0f56\u0f62\u0f92\u0fb1\u0f0b\u0f62\u0fa9\u0f60\u0f72" in text
    assert "\u0f53\u0f0b\u0f58\u0f7c\u0f0b\u0f42\u0f74\u0f0b\u0f62\u0f74\u0f0d" in text
    assert "\u0f56\u0f40\u0f60\u0f0b\u0f56(\u0f51\u0f0b" not in text


@pytest.mark.skipif(not HIMALAYA.is_file(), reason="Himalaya-G excerpt not present")
@pytest.mark.skipif(not PUA_FREE.is_dir(), reason="PUA-free GID lookup not shipped")
def test_himalaya_g_excerpt_recovers_title() -> None:
    recs = _records(HIMALAYA, font_lookup_dir=PUA_FREE)
    hit = next(r for r in recs if "Himalaya-G" in (r.get("pdf_font_name") or ""))
    assert hit["db_name_matched"] == "himalayag"
    assert hit["changed"] > 0
    assert hit["merged"][5] == "\u0f51"
    assert hit["merged"][6] == "\u0f42\u0f7a"
    text = _patched_text(HIMALAYA, font_lookup_dir=PUA_FREE)
    assert "\u0f51\u0f42\u0f7a\u0f0b\u0f66\u0fb3\u0f7c\u0f44\u0f0b\u0f58\u0f0b\u0f51\u0f54\u0f63\u0f0b\u0f58\u0f7c" in text
    assert "\u0f62\u0fa3\u0f58\u0f0b\u0f50\u0f62\u0f0b\u0f44\u0f7a\u0f66" in text
    assert "\u0f60\u0f56\u0fb1\u0f74\u0f44\u0f0b\u0f62\u0f92\u0fb1\u0f74\u0f51" in text
    assert "\u0f46\u0f7c\u0f66\u0f0b\u0f42\u0f4f\u0f58\u0f0b\u0f5e\u0f7a\u0f66" in text
    assert "\u0f51\x06\u0f0b" not in text


@pytest.mark.skipif(not DZONGKHA.is_file(), reason="Dzongkha Calligraphic excerpt not present")
def test_dzongkha_calligraphic_excerpt_recovers_title() -> None:
    recs = _records(DZONGKHA)
    hit = next(r for r in recs if "DzongkhaCalligraphic" in (r.get("pdf_font_name") or "") and r.get("changed", 0) > 10)
    assert hit["db_name_matched"] == "TibetanCalligraphic"
    assert hit["merged"][68] == "\u0f62\u0f9f"
    assert hit["merged"][105] == "\u0f42\u0fb2"
    assert hit["merged"][166] == "\u0f74"
    text = _patched_text(DZONGKHA)
    assert "\u0f50\u0f7c\u0f51\u0f0b\u0f54\u0f0b\u0f56\u0f5f\u0f44\u0f0b\u0f44\u0f53\u0f0b\u0f56\u0f62\u0f9f\u0f42\u0f66\u0f0b\u0f50\u0f56\u0f66" in text
    assert "\u0f51\u0f44\u0f7c\u0f66\u0f0b\u0f42\u0fb2\u0f74\u0f56" in text
    assert "\u0f66\u0fa4\u0fb1\u0f72\u0f62" in text
    assert "\u0f56\u0f9f\u0f42\u0f66" not in text
    assert "\u00a6" not in text
    assert "\u20ab" not in text


@pytest.mark.skipif(not MSTT.is_file(), reason="MSTT Dagpo excerpt not present")
def test_mstt_excerpt_uses_pdf_byte_reviewed_table() -> None:
    recs = _records(MSTT)
    hit = next(r for r in recs if r.get("db_name_matched") == "MSTT31c37a")
    assert hit["changed"] > 100
    assert hit["merged"][49] == "\u0f21"
    assert hit["merged"][124] == "\u0f11"
    assert hit["merged"][251] == "\u0f14"
    text = _patched_text(MSTT)
    assert "\u0f04\u0f05\u0f0d \u0f0d\u0f62\u0f97\u0f7a\u0f0b\u0f56\u0f59\u0f74\u0f53" in text


@pytest.mark.skipif(not CHENREZIG.is_file(), reason="ChosGyal excerpt not present")
def test_chosgyal_and_mangala_raw_bytes_recover_clean_text() -> None:
    recs = _records(CHENREZIG)
    by = {r["db_name_matched"]: r for r in recs if r.get("db_name_matched")}
    assert set(by) >= {
        "TibetanMangala-Normal",
        "TibetanChosGyalSkt1",
        "TibetanChosGyalSkt2",
        "TibetanChosGyalSkt3",
    }
    assert by["TibetanMangala-Normal"]["merged"][1] == "\u0f58"
    assert by["TibetanChosGyalSkt2"]["merged"][1] == "\u0f56\u0fb7"
    assert by["TibetanChosGyalSkt3"]["merged"][20] == "\u0f85"
    text = _patched_text(CHENREZIG)
    assert "\u0f58\u0f44\u0f0b\u0f50\u0f7c\u0f66\u0f0b\u0f56\u0fb3\u0f7c\u0f0b\u0f56\u0f5f\u0f44" in text
    assert "\u0f66\u0fa4\u0fb1\u0f53\u0f0b\u0f62\u0f66\u0f0b\u0f42\u0f5f\u0f72\u0f42\u0f66" in text
    assert not any(ord(ch) < 32 and ch not in "\n\t\r" for ch in text)
