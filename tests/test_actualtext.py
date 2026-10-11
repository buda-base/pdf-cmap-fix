"""Detection and removal of systematically corrupt PDF ActualText."""
from __future__ import annotations

import hashlib
from pathlib import Path

import fitz

from pdf_cmap_fix.gid.extractor import FONT_LOOKUP_DIR
from pdf_cmap_fix.tounicode_core import (
    _actualtext_corruption_metrics,
    _count_repeated_tibetan_combining_marks,
    _strip_actualtext_wrappers_in_stream,
    _strip_word_actualtext_sentinels_in_stream,
    extract_all,
    patch_pdf,
    strip_actualtext_wrappers,
)

ACTUALTEXT_PAGE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "kunsang-actualtext-page278.pdf"
)


def test_auto_detects_systematic_duplicate_tibetan_marks() -> None:
    honored = ("པོོ " * 120).strip()
    ignored = ("པོ " * 120).strip()

    metrics = _actualtext_corruption_metrics(
        honored, ignored, trusted_cmap=True
    )

    assert metrics["actualtext_repeated_marks_honored"] == 120
    assert metrics["actualtext_repeated_marks_ignored"] == 0
    assert metrics["actualtext_corrupt_detected"] == 1


def test_auto_preserves_genuine_painted_double_vowels() -> None:
    # Ignoring ActualText changes nothing when both vowel glyphs are painted.
    genuine = ("བོོ " * 120).strip()

    metrics = _actualtext_corruption_metrics(
        genuine, genuine, trusted_cmap=True
    )

    assert metrics["actualtext_corrupt_detected"] == 0


def test_auto_preserves_isolated_or_untrusted_duplicates() -> None:
    isolated = "བོོ"
    many = ("པོོ " * 120).strip()
    repaired = ("པོ " * 120).strip()

    assert (
        _actualtext_corruption_metrics(
            isolated, "བོ", trusted_cmap=True
        )["actualtext_corrupt_detected"]
        == 0
    )
    assert (
        _actualtext_corruption_metrics(
            many, repaired, trusted_cmap=False
        )["actualtext_corrupt_detected"]
        == 0
    )


def test_valid_ligature_actualtext_has_no_corruption_signal() -> None:
    metrics = _actualtext_corruption_metrics(
        "office", "ofﬁce", trusted_cmap=True
    )

    assert metrics["actualtext_corrupt_detected"] == 0


def test_strip_wrapper_preserves_painted_operators() -> None:
    stream = (
        b"BT /F1 12 Tf "
        b"/Span <</ActualText <FEFF0F540F7C>>> BDC "
        b"<00B6> Tj EMC <00D8> Tj ET"
    )

    stripped, count = _strip_actualtext_wrappers_in_stream(stream)

    assert count == 1
    assert b"ActualText" not in stripped
    assert b"<00B6> Tj" in stripped
    assert b"<00D8> Tj" in stripped


def test_automatic_strip_targets_tibetan_and_preserves_latin_actualtext() -> None:
    tibetan = (
        b"/Span <</ActualText <FEFF0F540F7C>>> BDC <00B6> Tj EMC "
    )
    latin = b"/Span <</ActualText <FEFF00660066>>> BDC <01> Tj EMC"

    stripped, count = _strip_actualtext_wrappers_in_stream(
        tibetan + latin, tibetan_only=True
    )

    assert count == 1
    assert b"<00B6> Tj" in stripped
    assert b"<FEFF00660066>" in stripped


def test_nested_marked_content_is_left_untouched() -> None:
    stream = (
        b"/Span <</ActualText <FEFF0F54>>> BDC "
        b"/Artifact BMC <00B6> Tj EMC EMC"
    )

    stripped, count = _strip_actualtext_wrappers_in_stream(stream)

    assert count == 0
    assert stripped == stream


def test_word_sentinel_stripping_counts_adjacent_wrappers_separately() -> None:
    wrapper = b"/Span <</ActualText <FEFFFFFD>>> BDC <0384> Tj EMC "
    stripped, count = _strip_word_actualtext_sentinels_in_stream(
        wrapper + wrapper
    )

    assert count == 2
    assert b"ActualText" not in stripped
    assert stripped.count(b"<0384> Tj") == 2


def test_strip_scans_form_xobject_streams_not_only_page_contents() -> None:
    class FakeDocument:
        def __init__(self) -> None:
            self.streams = {
                1: b"page content",
                2: (
                    b"/Span <</ActualText <FEFF0F540F7C>>> BDC "
                    b"<00B6> Tj EMC"
                ),
            }

        def xref_length(self) -> int:
            return 3

        def xref_stream(self, xref: int) -> bytes:
            return self.streams.get(xref, b"")

        def update_stream(self, xref: int, stream: bytes) -> None:
            self.streams[xref] = stream

    doc = FakeDocument()
    stats = strip_actualtext_wrappers(doc)  # type: ignore[arg-type]

    assert stats == {
        "actualtext_removed": 1,
        "actualtext_streams_changed": 1,
    }
    assert b"ActualText" not in doc.streams[2]
    assert b"<00B6> Tj" in doc.streams[2]


def test_auto_repairs_representative_actualtext_pdf_page() -> None:
    result = patch_pdf(
        ACTUALTEXT_PAGE,
        write_file=False,
        lookup_dir=FONT_LOOKUP_DIR,
        tier="gid",
        actualtext="auto",
    )
    stats = result["stats"]

    assert stats["actualtext_corrupt_detected"] == 1
    assert stats["actualtext_repeated_marks_honored"] >= 100
    assert stats["actualtext_repeated_marks_ignored"] == 0
    assert stats["actualtext_removed"] > 0

    before = fitz.open(ACTUALTEXT_PAGE)
    after = fitz.open(stream=result["pdf_bytes"], filetype="pdf")
    try:
        assert len(before) == len(after) == 1
        repaired_text = extract_all(after)
        before_pixels = before[0].get_pixmap().samples
        after_pixels = after[0].get_pixmap().samples
    finally:
        before.close()
        after.close()

    assert _count_repeated_tibetan_combining_marks(repaired_text) == 0
    assert "ཀུན་བཟང་བླ་མའི་ཞལ་ལུང་" in repaired_text
    # ActualText is extraction metadata; removing it must not alter rendering.
    assert hashlib.sha256(before_pixels).digest() == hashlib.sha256(
        after_pixels
    ).digest()
