"""Regression tests for ``_parse_tounicode``.

Before the array-form fix, the parser silently mis-handled
``<lo> <hi> [<u1> <u2> …]`` blocks: the regex it used scanned
across the array and grabbed two of its hex tokens as ``hi`` and
``base`` of a fictitious linear range, exploding ``existing`` to
millions of bogus entries on real-world Word PDFs (Microsoft
Himalaya was the worst case at 2.3 M phantom entries from a 6 KB
CMap). That cascaded into ``_merge`` reporting hundreds of
"missing" GIDs and ``apply_font_merges_to_doc`` rewriting the
ToUnicode stream of every subset, so a 25-PDF batch went from
~30 s to >12 min.
"""
from __future__ import annotations

from pdf_cmap_fix.tounicode_core import _parse_tounicode


def test_bfrange_linear() -> None:
    cmap = b"""begincmap
1 beginbfrange
<0010> <0014> <0041>
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {0x10: "A", 0x11: "B", 0x12: "C", 0x13: "D", 0x14: "E"}


def test_bfrange_array_form() -> None:
    """Each entry in the array maps to its corresponding GID in [lo, hi]."""
    cmap = b"""begincmap
1 beginbfrange
<0020> <0022> [<0F40> <0F410F71> <0F62>]
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {0x20: "\u0F40", 0x21: "\u0F41\u0F71", 0x22: "\u0F62"}


def test_bfrange_array_does_not_explode_into_phantom_ranges() -> None:
    """Reproducer for the bug that inflated CMaps to millions of entries.

    The old regex would grab ``<0F660F90>`` and ``<0F660F900F74>`` from
    the array as ``hi`` and ``base`` of a synthetic range with span
    > 16 trillion. Confirm the new parser keeps the entry count tight.
    """
    cmap = b"""begincmap
1 beginbfrange
<01F0> <01FF> [<0F660F90> <0F660F900F74> <0F660F900FB1>
              <0F660F900FB10F74> <0F660F900FB2> <0F660F900FB20F74>
              <0F660F900FB3> <0F660F900FB30F74> <0F66> <0F660F74>
              <0F660FB1> <0F660FB10F74> <0F660FB2> <0F660FB20F74>
              <0F660FB3> <0F660FB30F74>]
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert len(d) == 16, f"expected 16 entries, got {len(d)}"
    assert d[0x01F0] == "\u0F66\u0F90"
    assert d[0x01F1] == "\u0F66\u0F90\u0F74"
    assert d[0x01FE] == "\u0F66\u0FB3"
    assert d[0x01FF] == "\u0F66\u0FB3\u0F74"


def test_mixed_bfchar_and_bfrange() -> None:
    cmap = b"""begincmap
2 beginbfchar
<00DF> <0F7A>
<00F6> <0F00>
endbfchar
1 beginbfrange
<00FA> <00FB> <0F04>
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {
        0xDF: "\u0F7A",
        0xF6: "\u0F00",
        0xFA: "\u0F04",
        0xFB: "\u0F05",
    }


def test_multibyte_bfchar_destination() -> None:
    cmap = b"""begincmap
1 beginbfchar
<0001> <0F420FB7>
endbfchar
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {1: "\u0F42\u0FB7"}


def test_bfchar_quartz_spaced_hex_destinations() -> None:
    """macOS Quartz writes spaces inside a multi-codepoint hex string."""
    cmap = b"""begincmap
2 beginbfchar
<21><0f04 0f05>
<6f><0f7c 0f7e>
endbfchar
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {0x21: "\u0f04\u0f05", 0x6f: "\u0f7c\u0f7e"}


def test_bfrange_array_quartz_spaced_hex_destinations() -> None:
    cmap = b"""begincmap
1 beginbfrange
<0020> <0021> [<0f04 0f05> <0f7c 0f7e>]
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert d == {0x20: "\u0f04\u0f05", 0x21: "\u0f7c\u0f7e"}


def test_bfrange_with_huge_span_is_silently_dropped() -> None:
    """Sanity check: a malformed range whose span exceeds 0x10000 is ignored.

    Without the cap the parser would still happily allocate the entries
    one at a time, which is what made the 2.3 M-entry case so slow even
    when the eventual merge was a no-op.
    """
    cmap = b"""begincmap
1 beginbfrange
<0000> <FFFF> <0000>
endbfrange
endcmap
"""
    d = _parse_tounicode(cmap)
    assert len(d) == 0x10000
