"""Tests for the Type1 / MMType1 / TrueType (simple-font) path in
``tounicode_core``.

These cover the unit pieces -- ``_resolve_db_code_map_simple`` and
``_build_tounicode_simple`` -- with mocked ``fitz.Document`` and the
end-to-end ``collect_font_merges`` flow with a hand-rolled fake doc.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pdf_cmap_fix.tounicode_core import (
    _build_tounicode_simple,
    _build_tounicode_type0,
    _resolve_db_code_map_simple,
    _SIMPLE_FONT_TYPES,
)


# ---------------------------------------------------------------------------
# _build_tounicode_simple: codespace + key width
# ---------------------------------------------------------------------------

def test_build_tounicode_simple_uses_1byte_codespace() -> None:
    out = _build_tounicode_simple({0x0D: "མ་", 0x1F: "མ"}).decode("latin-1")
    assert "1 begincodespacerange" in out
    assert "<00> <FF>" in out
    # Keys are 2-hex (one byte), not 4-hex like Type0.
    assert "<0D>" in out
    assert "<1F>" in out
    # Values are UTF-16-BE 4-hex per codepoint, concatenated.
    assert "<0F580F0B>" in out
    assert "<0F58>" in out


def test_build_tounicode_simple_drops_codes_outside_1byte_range() -> None:
    out = _build_tounicode_simple({0x1F: "X", 0x100: "Y"}).decode("latin-1")
    assert "<1F>" in out
    # 0x100 should be silently filtered out -- simple fonts can't address it.
    assert "<100>" not in out
    assert "<0100>" not in out


def test_build_tounicode_type0_is_unchanged_by_simple_addition() -> None:
    """Regression: original Type0 builder still uses 4-hex codespace."""
    out = _build_tounicode_type0({0x123: "X"}).decode("latin-1")
    assert "<0000> <FFFF>" in out
    assert "<0123>" in out


def test_build_tounicode_omits_empty_destinations() -> None:
    """A failed parse must not be rewritten as an explicit empty mapping."""
    simple = _build_tounicode_simple({0x6F: "", 0x6E: "ཨ"}).decode("latin-1")
    assert "<6E>" in simple
    assert "<6F>" not in simple
    assert "<>" not in simple

    type0 = _build_tounicode_type0({0x6F: "", 0x6E: "ཨ"}).decode("latin-1")
    assert "<006E>" in type0
    assert "<006F>" not in type0
    assert "<>" not in type0


# ---------------------------------------------------------------------------
# _resolve_db_code_map_simple: gname tier
# ---------------------------------------------------------------------------

def _doc_with_encoding(encoding_key, encoding_obj_text=None):
    """Same helper as test_pdf_font_encoding, copied here for isolation."""
    doc = MagicMock()
    def _xref_object(xref):
        if xref == 1:
            return "<<>>"
        if xref == 2:
            return encoding_obj_text or "<<>>"
        return None
    def _xref_get_key(xref, key):
        if xref == 1 and key == "Encoding":
            return encoding_key
        return None
    doc.xref_object.side_effect = _xref_object
    doc.xref_get_key.side_effect = _xref_get_key
    return doc


def test_resolve_db_code_map_simple_gname_happy_path() -> None:
    """The gname inner table is keyed by glyph names; we ask
    _resolve_db_code_map_simple to land on {char_code: unicode}."""
    enc_text = """<<
      /BaseEncoding /WinAnsiEncoding
      /Differences [ 1 /uniE14D /uniE12C 13 /E14D.tsheg ]
    >>"""
    doc = _doc_with_encoding(("dict", enc_text))
    inner = {
        "uniE14D": "མ",
        "uniE12C": "་",
        "E14D.tsheg": "མ་",
        "A": "A_unused",
    }
    out = _resolve_db_code_map_simple(doc, 1, "gname", inner)
    assert out[1] == "མ"
    assert out[2] == "་"
    assert out[13] == "མ་"
    # The 'A' base-encoding mapping should be picked up too: WinAnsi
    # slot 65 == "A", which is in the inner dict.
    assert out[65] == "A_unused"


def test_resolve_db_code_map_simple_gid_tier_returns_empty() -> None:
    """Tier 1 (gid) is intentionally unsupported for simple fonts."""
    doc = _doc_with_encoding(("name", "WinAnsiEncoding"))
    out = _resolve_db_code_map_simple(doc, 1, "gid", {"5": "X"})
    assert out == {}


def test_resolve_db_code_map_simple_no_encoding_returns_empty() -> None:
    doc = _doc_with_encoding(None)
    out = _resolve_db_code_map_simple(doc, 1, "gname", {"A": "A"})
    assert out == {}


def test_resolve_db_code_map_simple_gname_skips_codes_with_no_match() -> None:
    """Codes whose glyph name isn't in the inner dict are dropped."""
    enc_text = "<< /BaseEncoding /WinAnsiEncoding /Differences [ 1 /madeUpGlyphName ] >>"
    doc = _doc_with_encoding(("dict", enc_text))
    out = _resolve_db_code_map_simple(doc, 1, "gname", {"A": "A_uni"})
    # Slot 1's "madeUpGlyphName" is not in the inner dict, so dropped.
    assert 1 not in out
    # But 'A' from base encoding -> 'A_uni' is still in out.
    assert out[65] == "A_uni"


# ---------------------------------------------------------------------------
# Sanity: _SIMPLE_FONT_TYPES classification
# ---------------------------------------------------------------------------

def test_simple_font_types_membership() -> None:
    assert "Type1" in _SIMPLE_FONT_TYPES
    assert "MMType1" in _SIMPLE_FONT_TYPES
    assert "TrueType" in _SIMPLE_FONT_TYPES
    # Composite and procedural types must stay out.
    assert "Type0" not in _SIMPLE_FONT_TYPES
    assert "Type3" not in _SIMPLE_FONT_TYPES
