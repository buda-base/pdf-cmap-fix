"""Regression tests for the two bugs4 PDFs that now extract cleanly.

Fixtures are page 1 of the MonlamUniOuChan2 Kagyu namthar (gname fallback
from the default gid collector) and the Himalaya-G Palmo namthar
(embedded-GID map against the PUA-free lookup).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_cmap_fix.gid.extractor import FONT_LOOKUP_DIR, collect_font_merges
from pdf_cmap_fix.tounicode_core import apply_font_merges_to_doc

DATA = Path(__file__).resolve().parent / "data"
MONLAM = DATA / "monlam-ouchan2-excerpt.pdf"
HIMALAYA = DATA / "himalaya-g-excerpt.pdf"
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
